"""
Health tracking for the Neon (active) and Aiven (standby) databases.

Pure module. The monitor never opens a connection itself: it calls an injected
probe function, which the operator's runtime supplies. This keeps the logic
testable and guarantees no hidden database access at import or in tests.

Rules:
  * Only connectivity-class failures (DNS, TIMEOUT, UNAVAILABLE) increase the
    consecutive-failure counter that can justify a failover evaluation.
  * AUTH, CONFIG, READ_ONLY and REPLICATION are recorded but never count toward
    failover: they are not outages.
  * UNKNOWN is recorded and does not reset the counter, and never counts as healthy.
  * A single OK probe resets the counter. Intermittent failures below the
    threshold therefore never trigger a failover evaluation on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Optional

from db_routing import FAILOVER_ELIGIBLE, ProbeKind


@dataclass(frozen=True)
class TargetHealth:
    name: str                        # "primary" or "standby" (role names, not providers)
    last_kind: Optional[ProbeKind] = None
    consecutive_failures: int = 0    # failover-eligible failures in a row
    total_observations: int = 0
    last_nonconnectivity_kind: Optional[ProbeKind] = None   # auth/config/read_only/...
    last_observed_at: Optional[float] = None


def record_observation(
    prev: TargetHealth, kind: ProbeKind, observed_at: float,
) -> TargetHealth:
    """Pure transition. Returns a new TargetHealth."""
    total = prev.total_observations + 1
    if kind is ProbeKind.OK:
        return replace(prev, last_kind=kind, consecutive_failures=0,
                       total_observations=total, last_observed_at=observed_at)
    if kind in FAILOVER_ELIGIBLE:
        return replace(prev, last_kind=kind, consecutive_failures=prev.consecutive_failures + 1,
                       total_observations=total, last_observed_at=observed_at)
    if kind is ProbeKind.UNKNOWN:
        # Not an outage signal we can trust, but not healthy either: keep the streak.
        return replace(prev, last_kind=kind, total_observations=total,
                       last_observed_at=observed_at)
    # AUTH, CONFIG, READ_ONLY, REPLICATION: recorded, not counted as outage.
    return replace(prev, last_kind=kind, total_observations=total,
                   last_nonconnectivity_kind=kind, last_observed_at=observed_at)


def threshold_met(health: TargetHealth, failure_threshold: int) -> bool:
    if failure_threshold < 1:
        raise ValueError("failure_threshold must be >= 1")
    return health.consecutive_failures >= failure_threshold


@dataclass
class HealthMonitor:
    """Holds TargetHealth for each role and runs one bounded sampling round.

    probes maps role name -> zero-argument callable returning a ProbeKind. Each
    callable is expected to enforce its own timeout. A probe that raises is
    recorded as UNKNOWN, never as OK.
    """
    probes: Dict[str, Callable[[], ProbeKind]]
    clock: Callable[[], float]
    state: Dict[str, TargetHealth] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for role in self.probes:
            self.state.setdefault(role, TargetHealth(name=role))

    def sample_once(self) -> Dict[str, TargetHealth]:
        now = self.clock()
        for role, probe in self.probes.items():
            try:
                kind = probe()
            except Exception:  # probe bug or unexpected driver error: never treat as healthy
                kind = ProbeKind.UNKNOWN
            if not isinstance(kind, ProbeKind):
                kind = ProbeKind.UNKNOWN
            self.state[role] = record_observation(self.state[role], kind, now)
        return dict(self.state)

    def run_rounds(self, rounds: int, interval_s: float,
                   sleep: Callable[[float], None]) -> Dict[str, TargetHealth]:
        """Run a finite number of sampling rounds with an injected sleep.

        The loop is bounded: rounds must be a positive int no larger than
        MAX_ROUNDS, and interval_s must be finite and positive. The caller
        supplies sleep, so no real waiting happens in tests.
        """
        import math
        if isinstance(rounds, bool) or not isinstance(rounds, int) or not (1 <= rounds <= MAX_ROUNDS):
            raise ValueError("rounds must be an int in [1, MAX_ROUNDS]")
        if (isinstance(interval_s, bool) or not isinstance(interval_s, (int, float))
                or not math.isfinite(interval_s) or interval_s <= 0):
            raise ValueError("interval_s must be finite and positive")
        snapshot: Dict[str, TargetHealth] = dict(self.state)
        for i in range(rounds):
            snapshot = self.sample_once()
            if i < rounds - 1:
                sleep(float(interval_s))
        return snapshot


MAX_ROUNDS = 10_000
