"""
Dual-PostgreSQL configuration, routing and failover DECISION logic for EMS.

Scope (deliberately narrow):
  * parse configuration (legacy single DATABASE_URL stays the default)
  * classify probe failures into kinds
  * decide, from probe results, which action is permitted

What this module does NOT do:
  * It never promotes a database, never changes replication, never fences
    a writer. Those are provider operations performed by an operator.
  * It performs no I/O at import time.
  * Automatic promotion is never returned as an action. The most it can
    return is PROMOTION_CANDIDATE, which means "an operator may consider
    promotion now"; the operator still runs the promotion runbook.

Symmetry: the decision function is written in terms of "active" and
"standby" roles, not provider names. Neon-primary and Aiven-primary are
the same code path.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class DbConfig:
    primary_url: str
    standby_url: Optional[str]          # None => legacy single-database mode
    health_monitor: bool
    failure_threshold: int
    max_replication_lag_s: float
    probe_timeout_s: float

    @property
    def dual(self) -> bool:
        return self.standby_url is not None


def _int_env(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer")
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _float_env(env: Mapping[str, str], name: str, default: float, minimum: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ConfigError(f"{name} must be a finite number (NaN and infinity are refused)")
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    v = raw.strip().lower()
    if v in ("1", "true", "on", "yes"):
        return True
    if v in ("0", "false", "off", "no"):
        return False
    raise ConfigError(f"{name} must be on/off")


def load_db_config(env: Mapping[str, str]) -> DbConfig:
    """
    Legacy: DATABASE_URL only -> single-database mode. The value is passed through
            unchanged (not stripped), exactly as the pre-routing code did.
    Primary-only: DATABASE_PRIMARY_URL without DATABASE_URL is accepted.
    Both:   DATABASE_URL and DATABASE_PRIMARY_URL must be equal, else ConfigError.
    Dual:   primary + DATABASE_STANDBY_URL -> dual mode (standby is never
            writable unless promoted; see active_target).
    """
    legacy_raw = env.get("DATABASE_URL")
    primary_raw = env.get("DATABASE_PRIMARY_URL")
    legacy_s = (legacy_raw or "").strip()
    primary_s = (primary_raw or "").strip()

    if legacy_s and primary_s and legacy_s != primary_s:
        raise ConfigError(
            "DATABASE_URL and DATABASE_PRIMARY_URL are both set and differ; set only one"
        )
    if primary_s:
        primary = primary_raw
    elif legacy_s:
        primary = legacy_raw
    else:
        raise ConfigError("DATABASE_URL or DATABASE_PRIMARY_URL environment variable is required.")

    standby = (env.get("DATABASE_STANDBY_URL") or "").strip() or None
    if standby is not None and standby == (primary_s or legacy_s):
        raise ConfigError("DATABASE_STANDBY_URL must differ from the primary URL")

    health_monitor = _bool_env(env, "DATABASE_HEALTH_MONITOR", False)
    if health_monitor:
        # No monitor runs in this build. Accepting the flag would imply failover
        # protection that does not exist, so fail closed at startup instead.
        raise ConfigError("DATABASE_HEALTH_MONITOR is not implemented in this build; leave it unset")
    if health_monitor and standby is None:
        raise ConfigError("DATABASE_HEALTH_MONITOR requires DATABASE_STANDBY_URL")

    return DbConfig(
        primary_url=primary,
        standby_url=standby,
        health_monitor=health_monitor,
        failure_threshold=_int_env(env, "DATABASE_FAILURE_THRESHOLD", 3, 1),
        max_replication_lag_s=_float_env(env, "DATABASE_MAX_REPLICATION_LAG_S", 30.0, 0.0),
        probe_timeout_s=_float_env(env, "DATABASE_PROBE_TIMEOUT_S", 5.0, 0.1),
    )


# ---------------------------------------------------------------------------
# Probe classification
# ---------------------------------------------------------------------------

class ProbeKind(str, Enum):
    OK = "ok"
    DNS = "dns"                      # host does not resolve
    TIMEOUT = "timeout"              # connect or query timed out
    UNAVAILABLE = "unavailable"      # refused / server shutting down / unreachable
    AUTH = "auth"                    # bad credentials or missing privilege
    CONFIG = "config"                # malformed URL or other local config error
    READ_ONLY = "read_only"          # reachable but not writable (standby or fenced)
    REPLICATION = "replication"      # standby reports lag or replication failure
    UNKNOWN = "unknown"


# Kinds that may justify a failover evaluation.
FAILOVER_ELIGIBLE = frozenset({ProbeKind.DNS, ProbeKind.TIMEOUT, ProbeKind.UNAVAILABLE})
# Kinds that are retried a bounded number of times and then reported.
RETRYABLE = frozenset({ProbeKind.TIMEOUT, ProbeKind.UNAVAILABLE, ProbeKind.DNS, ProbeKind.UNKNOWN})
# Kinds that must never trigger failover: they are not outages.
NEVER_FAILOVER = frozenset({ProbeKind.AUTH, ProbeKind.CONFIG, ProbeKind.READ_ONLY, ProbeKind.REPLICATION})

_AUTH_SQLSTATES = {"28000", "28P01"}          # invalid_authorization / invalid_password
_READ_ONLY_SQLSTATES = {"25006"}              # read_only_sql_transaction
_SHUTDOWN_SQLSTATES = {"57P01", "57P02", "57P03"}  # admin/crash/cannot_connect_now


def classify_probe_error(exc: BaseException) -> ProbeKind:
    """Map a driver exception to a ProbeKind without importing the driver."""
    sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    if sqlstate in _AUTH_SQLSTATES:
        return ProbeKind.AUTH
    if sqlstate in _READ_ONLY_SQLSTATES:
        return ProbeKind.READ_ONLY
    if sqlstate in _SHUTDOWN_SQLSTATES:
        return ProbeKind.UNAVAILABLE

    name = type(exc).__name__
    msg = str(exc).lower()
    if name in ("ArgumentError",) or "invalid dsn" in msg or "invalid connection string" in msg:
        return ProbeKind.CONFIG
    if "password authentication failed" in msg or "authentication failed" in msg or "no pg_hba.conf" in msg:
        return ProbeKind.AUTH
    if "could not translate host name" in msg or "name or service not known" in msg \
            or "nodename nor servname" in msg or "getaddrinfo" in msg or "no such host" in msg:
        return ProbeKind.DNS
    if isinstance(exc, TimeoutError) or "timeout" in msg or "timed out" in msg:
        return ProbeKind.TIMEOUT
    if name in ("OperationalError", "ConnectionError", "InterfaceError") or "connection" in msg \
            or "refused" in msg or "server closed" in msg or "shutting down" in msg:
        return ProbeKind.UNAVAILABLE
    return ProbeKind.UNKNOWN


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

class Action(str, Enum):
    NONE = "none"                          # active healthy, nothing to do
    RETRY = "retry"                        # bounded retry of the active probe
    ALERT_CONFIG = "alert_config"          # auth/config/read-only: operator must look, no failover
    HOLD = "hold"                          # failure seen, but threshold/safety not met
    PROMOTION_CANDIDATE = "promotion_candidate"  # operator MAY promote now; never automatic
    FAILBACK_CANDIDATE = "failback_candidate"    # original primary healthy again; operator MAY fail back


@dataclass(frozen=True)
class HealthState:
    """Observations for one database, from the independent monitor."""
    kind: ProbeKind
    consecutive_failures: int = 0
    replication_lag_s: Optional[float] = None   # None = not measured
    is_read_only: Optional[bool] = None          # None = not measured
    fenced: bool = False                         # provider-side writer fence verified by operator


@dataclass(frozen=True)
class Decision:
    action: Action
    reasons: tuple = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Promotion authority
# ---------------------------------------------------------------------------
#
# An environment variable can never make a standby writable. The only way a
# standby becomes an authoritative writer is a PromotionEvidence object that
# verify_promotion_evidence() accepts. Evidence is gathered by live probes of
# the two databases, not declared in configuration. Nothing in the running
# application constructs evidence yet, so get_db() and Alembic cannot reach a
# standby through this module.

@dataclass(frozen=True)
class FenceProof:
    """Proof that the old writer cannot accept writes.

    method is one of:
      'write_probe_read_only': a fresh write attempt against the old writer was
          rejected with SQLSTATE 25006 (read_only_sql_transaction), observed by the
          monitor over its own connection.
      'provider_endpoint_disabled': the provider API reported the old endpoint
          disabled; receipt_id must name the provider's receipt.
    An unreachable old writer is NOT a fence: it may come back and accept writes.
    """
    method: str
    observed_at: float
    old_epoch: int
    new_epoch: int
    receipt_id: Optional[str] = None


@dataclass(frozen=True)
class PromotionEvidence:
    """Live observations taken immediately before a standby may become writable."""
    candidate_observed_at: float
    candidate_in_recovery: Optional[bool]        # pg_is_in_recovery(); must be False
    candidate_read_write: Optional[bool]         # write probe succeeded on candidate
    candidate_replication_lag_s: Optional[float]
    fence: Optional[FenceProof]
    operator_change_id: Optional[str]            # change ticket naming this promotion


FENCE_METHODS = frozenset({"write_probe_read_only", "provider_endpoint_disabled"})


def verify_promotion_evidence(
    ev: Optional[PromotionEvidence],
    *,
    now: float,
    max_age_s: float,
    max_replication_lag_s: float,
    expected_epoch: int,
) -> tuple[bool, tuple]:
    """
    Pure check. Returns (True, ()) only when every condition holds. Any unknown,
    missing, non-finite (NaN or infinity) or out-of-range value is a refusal with
    an explicit reason (fail closed). Comparisons involving NaN are false in
    Python, so every timestamp, lag and window is validated with math.isfinite
    before it is compared.
    """
    if ev is None:
        return False, ("no promotion evidence",)
    reasons: list[str] = []

    # The freshness window and the clock itself must be usable; otherwise the
    # staleness comparisons below would be silently skipped.
    window_ok = math.isfinite(max_age_s) and max_age_s >= 0.0
    clock_ok = math.isfinite(now)
    if not window_ok:
        reasons.append("freshness window invalid (not a finite non-negative number)")
    if not clock_ok:
        reasons.append("verification clock not finite")

    if not math.isfinite(ev.candidate_observed_at):
        reasons.append("candidate observation timestamp not finite")
    elif clock_ok and window_ok and (
        now - ev.candidate_observed_at > max_age_s or ev.candidate_observed_at > now
    ):
        reasons.append("candidate observation stale or from the future")

    if ev.candidate_in_recovery is not False:
        reasons.append("candidate is not confirmed promoted (pg_is_in_recovery != false)")
    if ev.candidate_read_write is not True:
        reasons.append("candidate write probe not confirmed")

    # The ceiling is validated on its own, so an invalid ceiling is refused even
    # when the candidate lag is missing or invalid.
    ceiling_ok = (
        isinstance(max_replication_lag_s, (int, float))
        and not isinstance(max_replication_lag_s, bool)
        and math.isfinite(max_replication_lag_s)
        and max_replication_lag_s >= 0.0
    )
    if not ceiling_ok:
        reasons.append("replication lag ceiling invalid (not a finite non-negative number)")

    lag = ev.candidate_replication_lag_s
    if lag is None:
        reasons.append("candidate lag unknown")
    elif not math.isfinite(lag):
        reasons.append("candidate lag not finite (NaN or infinity)")
    elif lag < 0.0:
        reasons.append("candidate lag negative (invalid evidence)")
    elif ceiling_ok and lag > max_replication_lag_s:
        reasons.append(
            f"candidate lag {lag:.1f}s exceeds max {max_replication_lag_s:.1f}s"
        )

    ticket = ev.operator_change_id.strip() if isinstance(ev.operator_change_id, str) else ""
    if not ticket:
        reasons.append("no change ticket for this promotion")

    f = ev.fence
    if f is None:
        reasons.append("old writer fence not verified")
    else:
        if f.method not in FENCE_METHODS:
            reasons.append("fence method not recognised")
        if f.method == "provider_endpoint_disabled" and not f.receipt_id:
            reasons.append("provider fence has no receipt")
        if not math.isfinite(f.observed_at):
            reasons.append("fence observation timestamp not finite")
        elif clock_ok and window_ok and (
            now - f.observed_at > max_age_s or f.observed_at > now
        ):
            reasons.append("fence observation stale or from the future")
        if f.new_epoch != expected_epoch or f.old_epoch >= f.new_epoch:
            reasons.append("fence epoch does not advance past the old writer")
    if reasons:
        return False, tuple(reasons)
    return True, ()


def active_target(
    cfg: DbConfig,
    env: Mapping[str, str],
    evidence: Optional[PromotionEvidence] = None,
    *,
    now: Optional[float] = None,
    max_age_s: float = 60.0,
    expected_epoch: Optional[int] = None,
) -> str:
    """
    Writer authority: return the single URL that get_db() and Alembic both use.

    * The primary is the writer by default (DATABASE_ACTIVE unset or 'primary').
    * DATABASE_ACTIVE=standby is refused in every case unless a PromotionEvidence
      object is supplied AND verify_promotion_evidence accepts it. Environment
      variables cannot supply evidence.
    * Any other DATABASE_ACTIVE value raises ConfigError.
    """
    choice = (env.get("DATABASE_ACTIVE") or "").strip().lower()
    if choice not in ("", "primary", "standby"):
        raise ConfigError("DATABASE_ACTIVE must be 'primary' or 'standby'")
    if choice != "standby":
        return cfg.primary_url
    if not cfg.dual:
        raise ConfigError("DATABASE_ACTIVE=standby requires DATABASE_STANDBY_URL")
    if evidence is None:
        raise ConfigError(
            "refusing to route writes to the standby: no verified promotion evidence. "
            "DATABASE_ACTIVE=standby cannot grant write authority on its own."
        )
    ok, reasons = verify_promotion_evidence(
        evidence,
        now=time.time() if now is None else now,
        max_age_s=max_age_s,
        max_replication_lag_s=cfg.max_replication_lag_s,
        expected_epoch=expected_epoch if expected_epoch is not None else -1,
    )
    if not ok:
        raise ConfigError("refusing to route writes to the standby: " + "; ".join(reasons))
    return cfg.standby_url  # type: ignore[return-value]


def resolve_authoritative_url(env: Mapping[str, str]) -> str:
    """
    The one entry point for the authoritative database URL. The application
    (backend/main.py) and Alembic (backend/alembic/env.py) both call this, so
    migrations can never target a different database from the running app.
    With no evidence supplied, a standby is never returned.
    """
    return active_target(load_db_config(env), env)


def decide(
    cfg: DbConfig,
    active: HealthState,
    standby: HealthState,
    *,
    operator_hold: bool = False,
    active_is_original_primary: bool = True,
) -> Decision:
    """
    Pure function. Given observations for the currently active database and its
    counterpart, return the single permitted action. Fail-closed: any unmet
    condition yields a non-promotion action.
    """
    reasons: list[str] = []

    if operator_hold:
        return Decision(Action.HOLD, ("operator hold in effect",))

    # Configuration / auth / read-only problems are never outages.
    if active.kind in NEVER_FAILOVER:
        return Decision(Action.ALERT_CONFIG, (f"active probe {active.kind.value}: not an outage",))

    if active.kind == ProbeKind.OK:
        if not active_is_original_primary and standby.kind == ProbeKind.OK:
            return Decision(Action.FAILBACK_CANDIDATE, ("original primary healthy; operator may fail back",))
        return Decision(Action.NONE, ())

    # Active has a failover-eligible failure from here on.
    if active.kind not in FAILOVER_ELIGIBLE:
        return Decision(Action.RETRY, (f"active probe {active.kind.value}: unclassified, retry",))

    if active.consecutive_failures < cfg.failure_threshold:
        return Decision(Action.RETRY, (
            f"active failures {active.consecutive_failures} < threshold {cfg.failure_threshold}",
        ))

    # Threshold met. Every promotion gate must hold; any miss => HOLD with reasons.
    if standby.kind != ProbeKind.OK:
        reasons.append(f"standby probe {standby.kind.value}")
    if standby.kind in NEVER_FAILOVER:
        reasons.append("standby configuration/auth/read-only problem")
    if standby.is_read_only is None:
        reasons.append("standby read-only state unknown")
    if standby.replication_lag_s is None:
        reasons.append("standby replication lag unknown")
    elif not math.isfinite(standby.replication_lag_s):
        reasons.append("standby replication lag not finite (NaN or infinity)")
    elif standby.replication_lag_s < 0.0:
        reasons.append("standby replication lag negative (invalid observation)")
    elif standby.replication_lag_s > cfg.max_replication_lag_s:
        reasons.append(
            f"standby lag {standby.replication_lag_s:.1f}s > max {cfg.max_replication_lag_s:.1f}s"
        )
    if not active.fenced:
        reasons.append("active writer not verified fenced")

    if reasons:
        return Decision(Action.HOLD, tuple(reasons))

    return Decision(Action.PROMOTION_CANDIDATE, (
        "active unreachable past threshold; standby healthy, fresh, and active fenced",
    ))
