"""
Failover state machine (pure transitions) and a crash-safe, CAS-versioned state store.

The controller decides, it never acts. Promotion, subscription changes and routing
updates are operator or runner actions, and the controller only permits them through
explicit states. Every transition is a pure function of (state, observation, inputs).

Key invariants (see qa/test_ha_failover.py for the tests):
  * writer is "primary" (Neon) or "standby" (Aiven). It changes ONLY on the edge
    PROMOTION_PENDING -> STANDBY_ACTIVE, which requires a PromotionEvidence object
    accepted by db_routing.verify_promotion_evidence.
  * Any unmet condition yields HOLD or MANUAL_INTERVENTION_REQUIRED. Neither moves
    the writer.
  * Failback is never automatic. RECOVERY_DETECTED never leaves for FAILBACK_PENDING
    in this build; FAILBACK_PENDING is reserved and unreachable.
  * Fence receipts are verified by fencing.verify_fencing_receipt. A verified fence
    alone does not promote: promotion still needs PromotionEvidence.
  * Operation ids are derived from (epoch, version) so a retried step reuses the same
    id, and a consumed id can never authorize a second fence.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Callable, Iterable, Optional, Tuple

from db_routing import (
    FAILOVER_ELIGIBLE,
    NEVER_FAILOVER,
    DbConfig,
    FenceProof,
    PromotionEvidence,
    ProbeKind,
    verify_promotion_evidence,
)
from ha.fencing import (
    FencingAdapter,
    FencingResult,
    FenceStatus,
    verify_fencing_receipt,
)
from ha.replication import SlotLossKind, decide_lag, LagAction


class State(str, Enum):
    PRIMARY_ACTIVE = "PRIMARY_ACTIVE"
    PRIMARY_SUSPECT = "PRIMARY_SUSPECT"
    PRIMARY_UNAVAILABLE = "PRIMARY_UNAVAILABLE"
    FENCING_REQUIRED = "FENCING_REQUIRED"
    FENCED = "FENCED"
    REPLICATION_VALIDATION = "REPLICATION_VALIDATION"
    PROMOTION_PENDING = "PROMOTION_PENDING"
    STANDBY_ACTIVE = "STANDBY_ACTIVE"
    RECOVERY_DETECTED = "RECOVERY_DETECTED"
    FAILBACK_PENDING = "FAILBACK_PENDING"   # reserved: unreachable in this build
    HOLD = "HOLD"
    MANUAL_INTERVENTION_REQUIRED = "MANUAL_INTERVENTION_REQUIRED"


S = State

# Every edge the controller may take. step() refuses to produce any other edge.
ALLOWED_EDGES: frozenset = frozenset({
    (S.PRIMARY_ACTIVE, S.PRIMARY_ACTIVE), (S.PRIMARY_ACTIVE, S.PRIMARY_SUSPECT),
    (S.PRIMARY_ACTIVE, S.PRIMARY_UNAVAILABLE), (S.PRIMARY_ACTIVE, S.HOLD),
    (S.PRIMARY_SUSPECT, S.PRIMARY_ACTIVE), (S.PRIMARY_SUSPECT, S.PRIMARY_SUSPECT),
    (S.PRIMARY_SUSPECT, S.PRIMARY_UNAVAILABLE), (S.PRIMARY_SUSPECT, S.HOLD),
    (S.PRIMARY_UNAVAILABLE, S.PRIMARY_ACTIVE), (S.PRIMARY_UNAVAILABLE, S.FENCING_REQUIRED),
    (S.PRIMARY_UNAVAILABLE, S.HOLD), (S.PRIMARY_UNAVAILABLE, S.PRIMARY_UNAVAILABLE),
    (S.FENCING_REQUIRED, S.FENCING_REQUIRED), (S.FENCING_REQUIRED, S.FENCED),
    (S.FENCING_REQUIRED, S.HOLD), (S.FENCING_REQUIRED, S.MANUAL_INTERVENTION_REQUIRED),
    (S.FENCED, S.REPLICATION_VALIDATION), (S.FENCED, S.HOLD),
    (S.FENCED, S.MANUAL_INTERVENTION_REQUIRED),
    (S.REPLICATION_VALIDATION, S.PROMOTION_PENDING), (S.REPLICATION_VALIDATION, S.HOLD),
    (S.REPLICATION_VALIDATION, S.MANUAL_INTERVENTION_REQUIRED),
    (S.PROMOTION_PENDING, S.STANDBY_ACTIVE), (S.PROMOTION_PENDING, S.HOLD),
    (S.PROMOTION_PENDING, S.MANUAL_INTERVENTION_REQUIRED),
    (S.STANDBY_ACTIVE, S.STANDBY_ACTIVE), (S.STANDBY_ACTIVE, S.RECOVERY_DETECTED),
    (S.STANDBY_ACTIVE, S.HOLD),
    (S.RECOVERY_DETECTED, S.RECOVERY_DETECTED), (S.RECOVERY_DETECTED, S.STANDBY_ACTIVE),
    (S.RECOVERY_DETECTED, S.HOLD),
    (S.FAILBACK_PENDING, S.FAILBACK_PENDING), (S.FAILBACK_PENDING, S.HOLD),
    (S.FAILBACK_PENDING, S.MANUAL_INTERVENTION_REQUIRED),
    (S.HOLD, S.HOLD), (S.HOLD, S.MANUAL_INTERVENTION_REQUIRED),
    (S.HOLD, S.PRIMARY_ACTIVE), (S.HOLD, S.STANDBY_ACTIVE),
    (S.MANUAL_INTERVENTION_REQUIRED, S.MANUAL_INTERVENTION_REQUIRED),
    (S.MANUAL_INTERVENTION_REQUIRED, S.HOLD),
})

WRITER_PRIMARY = "primary"
WRITER_STANDBY = "standby"


@dataclass(frozen=True)
class ControllerState:
    state: State = State.PRIMARY_ACTIVE
    writer: str = WRITER_PRIMARY
    epoch: int = 0
    version: int = 0
    failures: int = 0
    fence_operation_id: Optional[str] = None
    consumed_operation_ids: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()
    last_observed_at: Optional[float] = None


@dataclass(frozen=True)
class Observation:
    primary_kind: ProbeKind
    primary_failures: int
    standby_kind: ProbeKind
    standby_lag_s: Optional[float]
    standby_slot: SlotLossKind
    operator_hold: bool = False
    operator_clear_ticket: Optional[str] = None   # exits HOLD / MANUAL only


PromotionVerifier = Callable[..., Tuple[bool, tuple]]


def authoritative_writer(cs: ControllerState) -> str:
    return cs.writer


def _edge_check(old: State, new: State) -> State:
    if (old, new) not in ALLOWED_EDGES:
        raise AssertionError(f"illegal transition {old.value} -> {new.value}")
    return new


def _hold(cs: ControllerState, reasons: Iterable[str], now: float) -> ControllerState:
    return replace(cs, state=_edge_check(cs.state, State.HOLD),
                   reasons=tuple(reasons), last_observed_at=now)


def _to(cs: ControllerState, new: State, reasons: Iterable[str], now: float, **kw) -> ControllerState:
    return replace(cs, state=_edge_check(cs.state, new), reasons=tuple(reasons),
                   last_observed_at=now, **kw)


def _stay(cs: ControllerState, reasons: Iterable[str], now: float, **kw) -> ControllerState:
    return replace(cs, reasons=tuple(reasons), last_observed_at=now, **kw)


def step(
    cs: ControllerState,
    obs: Observation,
    *,
    cfg: DbConfig,
    now: float,
    max_age_s: float = 60.0,
    fence_adapter: Optional[FencingAdapter] = None,
    fence_result: Optional[FencingResult] = None,
    promotion_evidence: Optional[PromotionEvidence] = None,
    promotion_completed: bool = False,
) -> ControllerState:
    """Deterministic, idempotent transition. Same inputs always give the same output.

    fence_result: result of a provider fence request made by the runner. None means
      not yet requested. Ignored outside FENCING_REQUIRED.
    promotion_evidence: live observations taken by the runner. Refused unless
      promotion_verifier accepts it. The real verifier refuses every fence method.
    promotion_completed: runner attests the post-promotion steps (stop subscription,
      sequence reconcile, promote, writable check, atomic routing update) finished.
      Only consulted after promotion_verifier has accepted the evidence.
    """
    cur = cs.state
    new_failures = (
        cs.failures if obs.primary_kind is ProbeKind.UNKNOWN else
        0 if obs.primary_kind is ProbeKind.OK else
        obs.primary_failures if obs.primary_kind in FAILOVER_ELIGIBLE else cs.failures
    )
    base = replace(cs, failures=new_failures)

    # --- Operator controls: HOLD and MANUAL exits are explicit and never promote.
    if cur is State.MANUAL_INTERVENTION_REQUIRED:
        if obs.operator_clear_ticket and obs.operator_clear_ticket.strip():
            return _to(base, State.HOLD, ("operator acknowledged manual state",), now)
        return _stay(base, ("manual intervention required; no automatic action",), now)

    if cur is State.HOLD:
        if obs.operator_hold:
            return _stay(base, ("operator hold in effect",), now)
        if obs.operator_clear_ticket and obs.operator_clear_ticket.strip():
            target = State.STANDBY_ACTIVE if cs.writer == WRITER_STANDBY else State.PRIMARY_ACTIVE
            return _to(base, target, ("operator cleared hold; re-monitoring",), now)
        return _stay(base, ("hold: awaiting operator clearance",), now)

    if obs.operator_hold:
        return _hold(base, ("operator hold in effect",), now)

    # --- Writer is the primary (Neon).
    if cur in (State.PRIMARY_ACTIVE, State.PRIMARY_SUSPECT, State.PRIMARY_UNAVAILABLE):
        if obs.primary_kind in NEVER_FAILOVER:
            return _hold(base, (f"primary {obs.primary_kind.value}: not an outage; no failover",), now)
        if obs.primary_kind is ProbeKind.OK:
            return _to(base, State.PRIMARY_ACTIVE, ("primary healthy",), now, failures=0)
        if obs.primary_kind not in FAILOVER_ELIGIBLE:
            return _stay(base, (f"primary {obs.primary_kind.value}: unclassified, retry",), now)
        # Failover-eligible failure. Threshold comes from config.
        if cur is State.PRIMARY_UNAVAILABLE:
            # Already unavailable: stay unavailable until the threshold triggers fencing.
            # (Failures are monotonic while failing, so this is a defensive branch.)
            if new_failures < cfg.failure_threshold:
                return _stay(base, (f"primary failures {new_failures} < threshold {cfg.failure_threshold}",), now)
        if new_failures < cfg.failure_threshold:
            return _to(base, State.PRIMARY_SUSPECT,
                       (f"primary failures {new_failures} < threshold {cfg.failure_threshold}",), now)
        if cur is State.PRIMARY_UNAVAILABLE:
            # Threshold met and already unavailable: begin fencing (never promotion).
            # The operation id is derived from (epoch, version), so a retried step reuses it.
            op = f"fence-{cs.epoch}-{cs.version}"
            return _to(base, State.FENCING_REQUIRED,
                       ("primary unavailable past threshold; fencing required",), now,
                       fence_operation_id=op)
        return _to(base, State.PRIMARY_UNAVAILABLE,
                   (f"primary failures {new_failures} reached threshold {cfg.failure_threshold}",), now)

    # --- Fencing. The fence must be verified before any promotion is considered.
    if cur is State.FENCING_REQUIRED:
        if obs.primary_kind is ProbeKind.OK:
            # The fence request may already have reached the writer. Do not continue
            # on a writer that answers again; hold for an operator.
            return _hold(base, ("primary recovered while fencing pending; operator review",), now)
        if fence_result is None:
            return _stay(base, ("fence request pending",), now)
        ok, why = verify_fencing_receipt(
            fence_adapter, fence_result, now=now,
            expected_target="neon", expected_epoch=cs.epoch,
            expected_operation_id=cs.fence_operation_id,
            consumed_operation_ids=cs.consumed_operation_ids,
        )
        if ok:
            consumed = tuple(sorted(set(cs.consumed_operation_ids) | {cs.fence_operation_id}))
            return _to(base, State.FENCED, ("provider fence verified",), now,
                       consumed_operation_ids=consumed)
        if fence_result.status is FenceStatus.VERIFIED:
            # Provider claimed a fence but the receipt failed verification: inconsistent.
            return _to(base, State.MANUAL_INTERVENTION_REQUIRED, why, now)
        return _hold(base, why, now)

    if cur is State.FENCED:
        if obs.primary_kind is ProbeKind.OK:
            return _hold(base, ("primary healthy after fence; operator review",), now)
        return _to(base, State.REPLICATION_VALIDATION, ("replication validation",), now)

    # --- Replication validation before promotion.
    if cur is State.REPLICATION_VALIDATION:
        reasons = []
        if obs.standby_kind is not ProbeKind.OK:
            reasons.append(f"standby probe {obs.standby_kind.value}")
        if obs.standby_slot is SlotLossKind.LOST:
            return _to(base, State.MANUAL_INTERVENTION_REQUIRED,
                       ("replication slot missing: resync required",), now)
        if obs.standby_slot is not SlotLossKind.OK and obs.standby_slot is not SlotLossKind.INACTIVE_NOT_LOST:
            reasons.append(f"slot state {obs.standby_slot.value}")
        lag = decide_lag(obs.standby_lag_s, warn_s=cfg.max_replication_lag_s,
                         ceiling_s=cfg.max_replication_lag_s)
        if lag.action is LagAction.BLOCK:
            reasons.append(lag.reason)
        if reasons:
            return _hold(base, reasons, now)
        return _to(base, State.PROMOTION_PENDING, ("replication validated",), now)

    # --- Promotion: gated by the existing verifier. Never reached in this build.
    if cur is State.PROMOTION_PENDING:
        if promotion_evidence is None:
            return _hold(base, ("no promotion evidence",), now)
        # Fixed verifier: caller input cannot substitute an accepting check.
        ok, why = verify_promotion_evidence(
            promotion_evidence, now=now, max_age_s=max_age_s,
            max_replication_lag_s=cfg.max_replication_lag_s,
            expected_epoch=cs.epoch + 1,
        )
        if not ok:
            return _hold(base, why, now)
        if not promotion_completed:
            return _hold(base, ("post-promotion steps not attested complete",), now)
        return _to(base, State.STANDBY_ACTIVE, ("standby promoted",), now,
                   writer=WRITER_STANDBY, epoch=cs.epoch + 1)

    # --- Standby is the writer.
    if cur is State.STANDBY_ACTIVE:
        if obs.standby_kind is not ProbeKind.OK:
            return _hold(base, (f"writer (standby) probe {obs.standby_kind.value}; no failback",), now)
        if obs.primary_kind is ProbeKind.OK:
            return _to(base, State.RECOVERY_DETECTED,
                       ("old primary reachable: reconciliation required, no automatic failback",), now)
        return _stay(base, ("standby active",), now)

    if cur is State.RECOVERY_DETECTED:
        if obs.standby_kind is not ProbeKind.OK:
            return _hold(base, (f"writer (standby) probe {obs.standby_kind.value}",), now)
        if obs.primary_kind is not ProbeKind.OK:
            return _to(base, State.STANDBY_ACTIVE, ("old primary unreachable again",), now)
        return _stay(base, ("failback disabled: controlled resynchronisation required",), now)

    if cur is State.FAILBACK_PENDING:
        # Reserved. Not reachable from any edge in this build.
        return _hold(base, ("failback not implemented",), now)

    raise AssertionError(f"unhandled state {cur}")  # pragma: no cover


def fence_proof_from(result: FencingResult, *, old_epoch: int, new_epoch: int, now: float) -> FenceProof:
    """Build the FenceProof for the existing promotion verifier from a fence receipt.
    The method is always 'provider_endpoint_disabled', which the verifier refuses."""
    return FenceProof(method="provider_endpoint_disabled", observed_at=now,
                      old_epoch=old_epoch, new_epoch=new_epoch,
                      receipt_id=result.receipt.operation_id if result.receipt else None)


# ---------------------------------------------------------------------------
# Persistence: CAS-versioned JSON file with atomic replace and an exclusive lock file
# ---------------------------------------------------------------------------

class StoreError(RuntimeError):
    pass


class StoreBusy(StoreError):
    pass


class StaleVersion(StoreError):
    pass


_THREAD_LOCK = threading.Lock()


class FileStateStore:
    """Single JSON state file. Writes are atomic (temp file + os.replace) and guarded by
    an O_EXCL lock file, so two processes cannot interleave a read-modify-write. A stale
    lock fails closed (StoreBusy); it is never broken automatically."""

    def __init__(self, path: str):
        self.path = path
        self.lock_path = path + ".lock"

    def load(self) -> ControllerState:
        if not os.path.exists(self.path):
            return ControllerState()
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return _from_json(raw)

    def compare_and_swap(self, expected_version: int, new: ControllerState) -> ControllerState:
        with _THREAD_LOCK:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise StoreBusy("state lock held; refusing to proceed (fail closed)")
            try:
                os.close(fd)
                current = self.load()
                if current.version != expected_version:
                    raise StaleVersion(
                        f"expected version {expected_version}, found {current.version}"
                    )
                stored = replace(new, version=expected_version + 1)
                self._atomic_write(stored)
                return stored
            finally:
                try:
                    os.remove(self.lock_path)
                except FileNotFoundError:
                    pass

    def _atomic_write(self, cs: ControllerState) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(prefix=".ha_state.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(_to_json(cs), fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


def _to_json(cs: ControllerState) -> dict:
    d = asdict(cs)
    d["state"] = cs.state.value
    d["consumed_operation_ids"] = list(cs.consumed_operation_ids)
    d["reasons"] = list(cs.reasons)
    return d


def _from_json(raw: dict) -> ControllerState:
    return ControllerState(
        state=State(raw["state"]),
        writer=raw["writer"],
        epoch=int(raw["epoch"]),
        version=int(raw["version"]),
        failures=int(raw["failures"]),
        fence_operation_id=raw.get("fence_operation_id"),
        consumed_operation_ids=tuple(raw.get("consumed_operation_ids", [])),
        reasons=tuple(raw.get("reasons", [])),
        last_observed_at=raw.get("last_observed_at"),
    )
