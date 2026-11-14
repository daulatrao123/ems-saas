"""
Pure replication SQL builders and decision functions (Neon -> Aiven).

Scope:
  * Build SQL TEXT only. Nothing here connects to a database or executes SQL.
  * Identifier escaping for PostgreSQL (double-quote quoting, embedded quotes doubled).
  * Slot-loss classification from observed slot state.
  * Replication-lag decisions with fail-closed handling of unknown or non-finite values.

Not in scope: enabling wal_level=logical on Neon, creating publications or
subscriptions on a live system, or any promotion or fencing. Those are operator
actions gated elsewhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence


class ReplicationError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Identifier and literal escaping
# ---------------------------------------------------------------------------

_MAX_IDENT_BYTES = 63  # NAMEDATALEN - 1
_NUL = "\x00"


def quote_ident(name: str) -> str:
    """Return a PostgreSQL-quoted identifier. Always quoted, so reserved words and
    mixed case are safe. Embedded double quotes are doubled. Names must be non-empty,
    contain no NUL, and fit in 63 bytes (longer names are silently truncated by the
    server, so they are refused here)."""
    if not isinstance(name, str) or name == "":
        raise ReplicationError("identifier must be a non-empty string")
    if _NUL in name:
        raise ReplicationError("identifier must not contain NUL")
    if len(name.encode("utf-8")) > _MAX_IDENT_BYTES:
        raise ReplicationError(f"identifier exceeds {_MAX_IDENT_BYTES} bytes")
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Return a PostgreSQL string literal. Single quotes are doubled. Backslashes are
    left as-is because standard_conforming_strings is on; NUL is refused."""
    if not isinstance(value, str):
        raise ReplicationError("literal must be a string")
    if _NUL in value:
        raise ReplicationError("literal must not contain NUL")
    return "'" + value.replace("'", "''") + "'"


_SAFE_CONNINFO_KEYS = frozenset({
    "host", "port", "dbname", "user", "password", "sslmode", "sslrootcert",
    "application_name", "connect_timeout",
})


def build_conninfo(params: dict) -> str:
    """Build a libpq keyword/value conninfo string with values single-quoted.
    Only known keys are accepted, so an arbitrary key cannot be injected."""
    parts = []
    for key in sorted(params):
        if key not in _SAFE_CONNINFO_KEYS:
            raise ReplicationError(f"conninfo key not allowed: {key!r}")
        val = str(params[key])
        if _NUL in val:
            raise ReplicationError("conninfo value must not contain NUL")
        escaped = val.replace("\\", "\\\\").replace("'", "\\'")
        parts.append(f"{key}='{escaped}'")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Publisher / subscriber SQL builders (text only)
# ---------------------------------------------------------------------------

def build_create_publication(name: str, tables: Sequence[str]) -> str:
    """CREATE PUBLICATION over explicit tables. Refuses an empty table list
    (FOR ALL TABLES is never generated, so new tables cannot leak in silently)."""
    if not tables:
        raise ReplicationError("publication needs at least one table")
    table_list = ", ".join(quote_ident(t) for t in tables)
    return f"CREATE PUBLICATION {quote_ident(name)} FOR TABLE {table_list};"


def build_create_subscription_aiven(
    name: str, conninfo: str, publication: str, slot: str, *, enabled: bool = True,
) -> str:
    """Aiven subscriber call through aiven_extras, which creates the slot.
    Arguments are passed as literals; identifiers are quoted into literals too."""
    enabled_sql = "true" if enabled else "false"
    return (
        "SELECT aiven_extras.pg_create_subscription("
        f"{quote_literal(name)}, {quote_literal(conninfo)}, "
        f"{quote_literal(publication)}, {quote_literal(slot)}, "
        f"true, {enabled_sql});"
    )


def build_drop_subscription(name: str) -> str:
    return f"DROP SUBSCRIPTION IF EXISTS {quote_ident(name)};"


def build_slot_lag_query() -> str:
    """Read-only query, run on the publisher, for per-slot retained WAL.
    Returns no user input, so it is a fixed string."""
    return (
        "SELECT slot_name, active, "
        "pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn) AS retained_bytes "
        "FROM pg_replication_slots WHERE slot_type = 'logical';"
    )


def build_subscription_stats_query() -> str:
    """Read-only query, run on the subscriber, for apply status per subscription."""
    return (
        "SELECT subname, pid, received_lsn, latest_end_lsn, latest_end_time "
        "FROM pg_stat_subscription;"
    )


def build_is_in_recovery_query() -> str:
    return "SELECT pg_is_in_recovery();"


def build_sequence_sync(sequence: str, value: int) -> str:
    """setval for a sequence whose name is fully qualified by the caller.
    The value must be a non-negative integer and is formatted, never interpolated raw."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReplicationError("sequence value must be a non-negative integer")
    return f"SELECT setval({quote_literal(sequence)}, {value}, true);"


# ---------------------------------------------------------------------------
# Slot-loss classification
# ---------------------------------------------------------------------------

class SlotState(str, Enum):
    PRESENT_ACTIVE = "present_active"
    PRESENT_INACTIVE = "present_inactive"
    MISSING = "missing"                    # slot not found on the publisher
    UNKNOWN = "unknown"                    # could not be observed


class SlotLossKind(str, Enum):
    OK = "ok"
    INACTIVE_NOT_LOST = "inactive_not_lost"  # present but idle: monitor, do not resync
    LOST = "lost"                            # missing: must resync from scratch
    UNOBSERVED = "unobserved"                # cannot tell: hold, do not assume healthy


@dataclass(frozen=True)
class SlotObservation:
    state: SlotState
    retained_bytes: Optional[int]


def classify_slot_loss(obs: SlotObservation) -> SlotLossKind:
    """A missing slot means the subscriber cannot resume from its position: a resync is
    required. An unknown slot state is never treated as healthy."""
    if obs.state is SlotState.MISSING:
        return SlotLossKind.LOST
    if obs.state is SlotState.UNKNOWN:
        return SlotLossKind.UNOBSERVED
    if obs.state is SlotState.PRESENT_INACTIVE:
        return SlotLossKind.INACTIVE_NOT_LOST
    return SlotLossKind.OK


# ---------------------------------------------------------------------------
# Lag decisions
# ---------------------------------------------------------------------------

class LagAction(str, Enum):
    OK = "ok"
    WARN = "warn"          # over the warning threshold
    BLOCK = "block"        # over the hard ceiling, or unknown: do not treat as fresh


@dataclass(frozen=True)
class LagDecision:
    action: LagAction
    reason: str


def _finite_nonneg(x: object) -> bool:
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(float(x))
        and float(x) >= 0.0
    )


def decide_lag(lag_s: Optional[float], *, warn_s: float, ceiling_s: float) -> LagDecision:
    """Fail-closed lag decision. Unknown, non-finite, negative or invalid thresholds
    block rather than pass. A ceiling below the warning threshold is a configuration
    error and blocks."""
    if not (_finite_nonneg(warn_s) and _finite_nonneg(ceiling_s)):
        return LagDecision(LagAction.BLOCK, "threshold invalid (not a finite non-negative number)")
    if warn_s > ceiling_s:
        return LagDecision(LagAction.BLOCK, "warn threshold above ceiling (configuration error)")
    if lag_s is None:
        return LagDecision(LagAction.BLOCK, "lag unknown")
    if not _finite_nonneg(lag_s):
        return LagDecision(LagAction.BLOCK, "lag not a finite non-negative number")
    if lag_s > ceiling_s:
        return LagDecision(LagAction.BLOCK, f"lag {lag_s:.1f}s exceeds ceiling {ceiling_s:.1f}s")
    if lag_s > warn_s:
        return LagDecision(LagAction.WARN, f"lag {lag_s:.1f}s above warning {warn_s:.1f}s")
    return LagDecision(LagAction.OK, "lag within limits")


def decide_freshness_for_promotion(lag_s: Optional[float], *, ceiling_s: float) -> bool:
    """True only when the standby lag is known, finite and within the ceiling. Used as
    one input to the promotion validator. It never grants promotion by itself."""
    if not _finite_nonneg(ceiling_s) or lag_s is None or not _finite_nonneg(lag_s):
        return False
    return lag_s <= ceiling_s
