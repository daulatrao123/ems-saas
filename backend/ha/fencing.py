"""
Provider fencing interface for the old writer (Neon).

Pure module: no network, no I/O, no environment reads at import or call time.

Promotion of a standby is only safe once the old writer is fenced at the
PROVIDER level, so that no connection, session or endpoint can still write.
This module defines:

  * FencingAdapter        abstract provider operation (request a fence)
  * FencingReceipt        the independently verifiable proof a provider returns
  * verify_fencing_receipt  the only way a receipt can count as a fence

Rules enforced here:
  * A receipt counts only if its provider is in VERIFIED_FENCING_PROVIDERS AND
    the adapter's provider-authentication check accepts it AND it matches the
    expected target and epoch, is inside its validity window, and has not been
    consumed before (replay protection).
  * VERIFIED_FENCING_PROVIDERS is a module constant. It is NOT read from
    configuration, environment variables or caller input.
  * The Neon adapter reports UNSUPPORTED and can never verify a receipt, because
    no documented Neon provider fencing operation with a verifiable receipt has
    been established.
  * A failed health check, an unreachable writer, a read-only probe on one
    connection, or a caller-supplied boolean is NOT a fence.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional


class FenceStatus(str, Enum):
    VERIFIED = "verified"        # adapter claims success; a receipt must still be verified
    UNSUPPORTED = "unsupported"  # no provider mechanism implemented or verified
    REFUSED = "refused"          # provider declined the operation
    ERROR = "error"              # transport or provider error; state unknown


# Providers whose fencing receipts can be independently verified. EMPTY on purpose.
# Adding a provider requires a documented fencing operation and a verifier in code
# review. Nothing may populate this set at runtime.
VERIFIED_FENCING_PROVIDERS: frozenset = frozenset()


@dataclass(frozen=True)
class FencingReceipt:
    provider: str
    operation_id: str          # unique per fencing operation; consumed once
    fenced_target: str         # which writer was fenced, e.g. "neon"
    epoch: int                 # the epoch this fence protects
    issued_at: float           # provider-issued timestamp (epoch seconds)
    expires_at: float          # receipt is invalid at or after this time
    proof: str                 # opaque provider proof, checked by the adapter


@dataclass(frozen=True)
class FencingResult:
    status: FenceStatus
    reasons: tuple = ()
    receipt: Optional[FencingReceipt] = None


class FencingAdapter(ABC):
    """One adapter per provider. Implementations must be bounded in time and must
    close any connection they open."""

    provider: str = ""

    @abstractmethod
    def request_fence(self, operation_id: str, epoch: int) -> FencingResult:
        """Ask the provider to fence the old writer for this epoch."""

    @abstractmethod
    def authenticate_receipt(self, receipt: FencingReceipt) -> bool:
        """Return True only if the provider can authenticate this receipt.
        Must not trust fields of the receipt merely because they are present."""


class NeonFencingAdapter(FencingAdapter):
    """Fail-closed adapter. Neon has no verified fencing operation in this build."""

    provider = "neon"

    def request_fence(self, operation_id: str, epoch: int) -> FencingResult:
        return FencingResult(
            FenceStatus.UNSUPPORTED,
            ("no documented Neon provider fencing operation with a verifiable receipt "
             "is implemented; automatic promotion is disabled",),
        )

    def authenticate_receipt(self, receipt: FencingReceipt) -> bool:
        return False


def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def verify_fencing_receipt(
    adapter: Optional[FencingAdapter],
    result: Optional[FencingResult],
    *,
    now: float,
    expected_target: str,
    expected_epoch: int,
    expected_operation_id: Optional[str],
    consumed_operation_ids: Iterable[str],
) -> tuple[bool, tuple]:
    """Pure check. Returns (True, ()) only if the result carries a receipt that
    satisfies every condition. Any other outcome is a refusal with reasons."""
    reasons: list[str] = []

    if adapter is None:
        return False, ("no fencing adapter",)
    if result is None or not isinstance(result, FencingResult):
        return False, ("no fencing result",)
    if result.status is not FenceStatus.VERIFIED:
        return False, (f"fence status {result.status.value}: not a fence",)

    r = result.receipt
    if r is None or not isinstance(r, FencingReceipt):
        return False, ("verified status without a receipt is not a fence",)

    if r.provider != adapter.provider:
        reasons.append("receipt provider does not match adapter")
    if r.provider not in VERIFIED_FENCING_PROVIDERS:
        reasons.append(f"provider {r.provider!r} has no verified fencing mechanism")
    if r.fenced_target != expected_target:
        reasons.append("receipt fences a different target")
    if r.epoch != expected_epoch:
        reasons.append("receipt epoch does not match expected epoch")
    if not isinstance(r.operation_id, str) or not r.operation_id.strip():
        reasons.append("receipt has no operation id")
    elif expected_operation_id is None or r.operation_id != expected_operation_id:
        reasons.append("receipt is for a different fencing operation")
    if not isinstance(r.proof, str) or not r.proof.strip():
        reasons.append("receipt has no provider proof")

    if not _finite(now):
        reasons.append("verification clock not finite")
    elif not (_finite(r.issued_at) and _finite(r.expires_at)):
        reasons.append("receipt timestamps not finite")
    else:
        if now < r.issued_at:
            reasons.append("receipt not yet valid")
        if now >= r.expires_at:
            reasons.append("receipt expired")
        if r.expires_at <= r.issued_at:
            reasons.append("receipt validity window empty or inverted")

    if r.operation_id in set(consumed_operation_ids):
        reasons.append("receipt replayed: operation id already consumed")

    if not reasons and not adapter.authenticate_receipt(r):
        reasons.append("provider did not authenticate receipt")

    if reasons:
        return False, tuple(reasons)
    return True, ()
