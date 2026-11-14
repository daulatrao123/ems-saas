"""DB routing and failover decision tests. Imports the production module
backend/db_routing.py. No database, no network, no provider contact.

Run:  python qa/test_db_failover_decision.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from db_routing import (  # noqa: E402
    Action, ConfigError, DbConfig, FenceProof, HealthState, ProbeKind, PromotionEvidence,
    active_target, classify_probe_error, decide, load_db_config,
    resolve_authoritative_url, verify_promotion_evidence, FENCE_METHODS,
)

NEON = "postgresql://u:p@neon.example/ems?sslmode=require"
AIVEN = "postgresql://u:p@aiven.example/ems?sslmode=require"


def cfg(**over):
    base = dict(primary_url=NEON, standby_url=AIVEN, health_monitor=True,
                failure_threshold=3, max_replication_lag_s=30.0, probe_timeout_s=5.0)
    base.update(over)
    return DbConfig(**base)


def down(kind=ProbeKind.UNAVAILABLE, n=3):
    return HealthState(kind=kind, consecutive_failures=n)


def healthy_standby(**over):
    s = dict(kind=ProbeKind.OK, consecutive_failures=0, replication_lag_s=1.0,
             is_read_only=True, fenced=False)
    s.update(over)
    return HealthState(**s)


# Every fence method is refused in this build (see FENCE_METHODS). Boundary tests
# assert that the fence refusal is the ONLY reason left, which keeps lag and
# freshness boundaries checked without treating the fence as proven.
FENCE_REFUSAL = "refused: a single-connection read-only probe"


def non_fence_reasons(ok, reasons):
    """Assert the fence refusal is present, then return every OTHER reason."""
    assert not ok, "expected refusal from the fence gate"
    assert any(FENCE_REFUSAL in r for r in reasons), reasons
    return tuple(r for r in reasons if FENCE_REFUSAL not in r)


class LegacyConfigTests(unittest.TestCase):
    def test_database_url_only_is_single_mode(self):
        c = load_db_config({"DATABASE_URL": NEON})
        self.assertFalse(c.dual)
        self.assertEqual(c.primary_url, NEON)
        self.assertIsNone(c.standby_url)

    def test_legacy_ignores_standby_flags_only_when_active_unset(self):
        env = {"DATABASE_URL": NEON}
        self.assertEqual(active_target(load_db_config(env), env), NEON)

    def test_legacy_with_active_standby_fails_closed(self):
        env = {"DATABASE_URL": NEON, "DATABASE_ACTIVE": "standby"}
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env)

    def test_missing_database_url_raises(self):
        with self.assertRaises(ConfigError):
            load_db_config({})

    def test_health_monitor_requires_standby(self):
        with self.assertRaises(ConfigError):
            load_db_config({"DATABASE_URL": NEON, "DATABASE_HEALTH_MONITOR": "on"})

    def test_standby_equal_to_primary_rejected(self):
        with self.assertRaises(ConfigError):
            load_db_config({"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": NEON})

    def test_bad_numeric_and_bool_rejected(self):
        with self.assertRaises(ConfigError):
            load_db_config({"DATABASE_URL": NEON, "DATABASE_FAILURE_THRESHOLD": "0"})
        with self.assertRaises(ConfigError):
            load_db_config({"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN,
                            "DATABASE_HEALTH_MONITOR": "maybe"})


class RoutingTests(unittest.TestCase):
    NOW = 1_000_000.0

    def test_dual_defaults_to_primary(self):
        env = {"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN}
        self.assertEqual(active_target(load_db_config(env), env), NEON)

    def test_active_standby_env_alone_is_refused(self):
        env = {"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN, "DATABASE_ACTIVE": "standby"}
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env)

    def test_promotion_marker_env_cannot_enable_standby_writes(self):
        # Even the old marker name, set to on, must not grant write authority.
        env = {"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN, "DATABASE_ACTIVE": "standby",
               "DATABASE_STANDBY_PROMOTION_VERIFIED": "on"}
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env)

    def test_env_cannot_supply_evidence_object(self):
        # Only a PromotionEvidence instance argument can unlock the standby.
        env = {"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN, "DATABASE_ACTIVE": "standby",
               "PROMOTION_EVIDENCE": "{\"fence\": true}"}
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env)

    def test_active_standby_without_standby_url_rejected(self):
        env = {"DATABASE_URL": NEON, "DATABASE_ACTIVE": "standby"}
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env)

    def test_invalid_active_rejected(self):
        env = {"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN, "DATABASE_ACTIVE": "auto"}
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env)

    def test_health_monitor_flag_fails_closed_as_unimplemented(self):
        with self.assertRaises(ConfigError):
            load_db_config({"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN,
                            "DATABASE_HEALTH_MONITOR": "on"})

    def _verified_evidence(self, **over):
        base = dict(
            candidate_observed_at=self.NOW - 5, candidate_in_recovery=False,
            candidate_read_write=True, candidate_replication_lag_s=1.0,
            fence=FenceProof("write_probe_read_only", self.NOW - 5, old_epoch=4, new_epoch=5),
            operator_change_id="CHG-1",
        )
        base.update(over)
        return PromotionEvidence(**base)

    def _standby_env(self):
        return {"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN, "DATABASE_ACTIVE": "standby"}

    def test_otherwise_valid_evidence_still_refused_by_write_probe(self):
        env = self._standby_env()
        with self.assertRaises(ConfigError) as ctx:
            active_target(load_db_config(env), env, self._verified_evidence(),
                          now=self.NOW, expected_epoch=5)
        self.assertIn("write_probe_read_only", str(ctx.exception))

    def test_refused_without_evidence_object(self):
        env = self._standby_env()
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env, None, now=self.NOW, expected_epoch=5)

    def test_refused_when_candidate_still_in_recovery(self):
        env = self._standby_env()
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env,
                          self._verified_evidence(candidate_in_recovery=True),
                          now=self.NOW, expected_epoch=5)

    def test_refused_when_candidate_not_write_verified(self):
        env = self._standby_env()
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env,
                          self._verified_evidence(candidate_read_write=None),
                          now=self.NOW, expected_epoch=5)

    def test_refused_when_fence_missing(self):
        env = self._standby_env()
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env,
                          self._verified_evidence(fence=None), now=self.NOW, expected_epoch=5)

    def test_unreachable_old_writer_is_not_a_fence(self):
        env = self._standby_env()
        bad = self._verified_evidence(fence=FenceProof("unreachable", self.NOW - 5, 4, 5))
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env, bad, now=self.NOW, expected_epoch=5)

    def test_provider_fence_without_receipt_refused(self):
        env = self._standby_env()
        bad = self._verified_evidence(
            fence=FenceProof("provider_endpoint_disabled", self.NOW - 5, 4, 5, receipt_id=None))
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env, bad, now=self.NOW, expected_epoch=5)

    def test_stale_evidence_refused(self):
        env = self._standby_env()
        stale = self._verified_evidence(candidate_observed_at=self.NOW - 3600)
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env, stale, now=self.NOW, expected_epoch=5)

    def test_stale_fence_refused(self):
        env = self._standby_env()
        stale = self._verified_evidence(
            fence=FenceProof("write_probe_read_only", self.NOW - 3600, 4, 5))
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env, stale, now=self.NOW, expected_epoch=5)

    def test_epoch_mismatch_refused(self):
        env = self._standby_env()
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env, self._verified_evidence(),
                          now=self.NOW, expected_epoch=6)

    def test_missing_change_ticket_refused(self):
        env = self._standby_env()
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env,
                          self._verified_evidence(operator_change_id=None),
                          now=self.NOW, expected_epoch=5)

    def test_unknown_replication_lag_refused(self):
        env = self._standby_env()
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env,
                          self._verified_evidence(candidate_replication_lag_s=None),
                          now=self.NOW, expected_epoch=5)

    def test_evidence_alone_without_active_standby_still_primary(self):
        env = {"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN}
        url = active_target(load_db_config(env), env, self._verified_evidence(),
                            now=self.NOW, expected_epoch=5)
        self.assertEqual(url, NEON)


class VerifyPromotionEvidenceTests(unittest.TestCase):
    """Direct tests of the pure verifier, independent of active_target."""
    NOW = 1_000_000.0

    def _ev(self, **over):
        base = dict(
            candidate_observed_at=self.NOW - 5, candidate_in_recovery=False,
            candidate_read_write=True, candidate_replication_lag_s=0.5,
            fence=FenceProof("write_probe_read_only", self.NOW - 5, old_epoch=4, new_epoch=5),
            operator_change_id="CHG-2",
        )
        base.update(over)
        return PromotionEvidence(**base)

    def _verify(self, ev, expected_epoch=5, max_lag=30.0):
        return verify_promotion_evidence(ev, now=self.NOW, max_age_s=60.0,
                                         max_replication_lag_s=max_lag,
                                         expected_epoch=expected_epoch)

    def test_complete_evidence_refused_by_write_probe_fence(self):
        ok, reasons = self._verify(self._ev())
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 1)
        self.assertIn(FENCE_REFUSAL, reasons[0])

    def test_none_evidence_refused(self):
        ok, reasons = self._verify(None)
        self.assertFalse(ok)
        self.assertTrue(reasons)

    def test_candidate_in_recovery_refused(self):
        ok, _ = self._verify(self._ev(candidate_in_recovery=True))
        self.assertFalse(ok)

    def test_unknown_recovery_state_refused(self):
        ok, _ = self._verify(self._ev(candidate_in_recovery=None))
        self.assertFalse(ok)

    def test_future_candidate_observation_refused(self):
        ok, _ = self._verify(self._ev(candidate_observed_at=self.NOW + 10))
        self.assertFalse(ok)

    def test_unknown_fence_method_refused(self):
        ev = self._ev(fence=FenceProof("trust_me", self.NOW - 5, 4, 5))
        ok, reasons = self._verify(ev)
        self.assertFalse(ok)
        self.assertTrue(any("fence method" in r for r in reasons))

    def test_provider_fence_with_receipt_refused(self):
        # A receipt string is an unverified claim; it must never authorize promotion.
        ev = self._ev(fence=FenceProof("provider_endpoint_disabled", self.NOW - 5, 4, 5,
                                       receipt_id="RCPT-9"))
        ok, reasons = self._verify(ev)
        self.assertFalse(ok)
        self.assertTrue(any("unverifiable" in r for r in reasons))

    def test_provider_fence_without_receipt_refused_direct(self):
        ev = self._ev(fence=FenceProof("provider_endpoint_disabled", self.NOW - 5, 4, 5))
        ok, _ = self._verify(ev)
        self.assertFalse(ok)

    def test_fence_epoch_not_advancing_refused(self):
        ev = self._ev(fence=FenceProof("write_probe_read_only", self.NOW - 5, 5, 5))
        ok, _ = self._verify(ev)
        self.assertFalse(ok)

    def test_fence_epoch_mismatch_with_expected_refused(self):
        ok, _ = self._verify(self._ev(), expected_epoch=7)
        self.assertFalse(ok)

    def test_future_fence_observation_refused(self):
        ev = self._ev(fence=FenceProof("write_probe_read_only", self.NOW + 10, 4, 5))
        ok, _ = self._verify(ev)
        self.assertFalse(ok)

    def test_unknown_lag_refused(self):
        ok, _ = self._verify(self._ev(candidate_replication_lag_s=None))
        self.assertFalse(ok)

    def test_write_probe_not_confirmed_refused(self):
        ok, _ = self._verify(self._ev(candidate_read_write=False))
        self.assertFalse(ok)

    def test_empty_change_ticket_refused(self):
        ok, _ = self._verify(self._ev(operator_change_id=""))
        self.assertFalse(ok)

    def test_whitespace_only_change_ticket_refused(self):
        for ticket in ("   ", "\t", "\n", " \t \r\n "):
            ok, reasons = self._verify(self._ev(operator_change_id=ticket))
            self.assertFalse(ok, repr(ticket))
            self.assertTrue(any("change ticket" in r for r in reasons), repr(ticket))

    def test_padded_change_ticket_accepted(self):
        ok, reasons = self._verify(self._ev(operator_change_id="  CHG-2  "))
        self.assertFalse(ok)
        self.assertFalse(any("change ticket" in r for r in reasons))
        self.assertTrue(any(FENCE_REFUSAL in r for r in reasons))

    def test_all_failures_are_reported_together(self):
        ev = self._ev(candidate_in_recovery=True, operator_change_id=None, fence=None)
        ok, reasons = self._verify(ev)
        self.assertFalse(ok)
        self.assertGreaterEqual(len(reasons), 3)


class NonFiniteAndLagBoundaryTests(unittest.TestCase):
    """NaN/infinity refusal, the replication-lag ceiling, and both failover directions."""
    NOW = 1_000_000.0
    NAN = float("nan")
    INF = float("inf")

    def _ev(self, **over):
        base = dict(
            candidate_observed_at=self.NOW - 5, candidate_in_recovery=False,
            candidate_read_write=True, candidate_replication_lag_s=0.5,
            fence=FenceProof("write_probe_read_only", self.NOW - 5, old_epoch=4, new_epoch=5),
            operator_change_id="CHG-3",
        )
        base.update(over)
        return PromotionEvidence(**base)

    def _verify(self, ev, max_lag=30.0, expected_epoch=5, max_age=60.0, now=None):
        return verify_promotion_evidence(
            ev, now=self.NOW if now is None else now, max_age_s=max_age,
            max_replication_lag_s=max_lag, expected_epoch=expected_epoch)

    def _reasons_contain(self, reasons, fragment):
        return any(fragment in r for r in reasons)

    # --- NaN / infinity in every timestamp and lag value ---

    def test_nan_candidate_timestamp_refused_with_reason(self):
        ok, reasons = self._verify(self._ev(candidate_observed_at=self.NAN))
        self.assertFalse(ok)
        self.assertTrue(self._reasons_contain(reasons, "candidate observation timestamp not finite"))

    def test_positive_infinity_candidate_timestamp_refused(self):
        ok, reasons = self._verify(self._ev(candidate_observed_at=self.INF))
        self.assertFalse(ok)
        self.assertTrue(self._reasons_contain(reasons, "not finite"))

    def test_negative_infinity_candidate_timestamp_refused(self):
        ok, reasons = self._verify(self._ev(candidate_observed_at=-self.INF))
        self.assertFalse(ok)
        self.assertTrue(self._reasons_contain(reasons, "not finite"))

    def test_nan_fence_timestamp_refused_with_reason(self):
        fence = FenceProof("write_probe_read_only", self.NAN, old_epoch=4, new_epoch=5)
        ok, reasons = self._verify(self._ev(fence=fence))
        self.assertFalse(ok)
        self.assertTrue(self._reasons_contain(reasons, "fence observation timestamp not finite"))

    def test_infinite_fence_timestamp_refused(self):
        for v in (self.INF, -self.INF):
            fence = FenceProof("write_probe_read_only", v, old_epoch=4, new_epoch=5)
            ok, _ = self._verify(self._ev(fence=fence))
            self.assertFalse(ok, v)

    def test_invalid_ceiling_refused_even_when_candidate_lag_missing(self):
        for ceiling in (self.NAN, self.INF, -self.INF, -1.0):
            ok, reasons = self._verify(self._ev(candidate_replication_lag_s=None), max_lag=ceiling)
            self.assertFalse(ok, ceiling)
            self.assertTrue(self._reasons_contain(reasons, "ceiling invalid"), ceiling)

    def test_invalid_ceiling_refused_even_when_candidate_lag_nan(self):
        for ceiling in (self.NAN, self.INF, -self.INF, -1.0):
            ok, reasons = self._verify(self._ev(candidate_replication_lag_s=self.NAN), max_lag=ceiling)
            self.assertFalse(ok, ceiling)
            self.assertTrue(self._reasons_contain(reasons, "ceiling invalid"), ceiling)
            self.assertTrue(self._reasons_contain(reasons, "candidate lag not finite"))

    def test_invalid_ceiling_refused_with_finite_lag(self):
        ok, reasons = self._verify(self._ev(candidate_replication_lag_s=0.5), max_lag=self.NAN)
        self.assertFalse(ok)
        self.assertTrue(self._reasons_contain(reasons, "ceiling invalid"))

    def test_valid_ceiling_with_missing_lag_still_refused_for_lag_only(self):
        ok, reasons = self._verify(self._ev(candidate_replication_lag_s=None), max_lag=30.0)
        self.assertEqual(non_fence_reasons(ok, reasons), ("candidate lag unknown",))

    def test_zero_ceiling_is_valid_boundary(self):
        self.assertEqual(non_fence_reasons(*self._verify(
            self._ev(candidate_replication_lag_s=0.0), max_lag=0.0)), ())

    def test_nan_candidate_lag_refused_with_reason(self):
        ok, reasons = self._verify(self._ev(candidate_replication_lag_s=self.NAN))
        self.assertFalse(ok)
        self.assertTrue(self._reasons_contain(reasons, "candidate lag not finite"))

    def test_infinite_candidate_lag_refused(self):
        for v in (self.INF, -self.INF):
            ok, _ = self._verify(self._ev(candidate_replication_lag_s=v))
            self.assertFalse(ok, v)

    def test_negative_candidate_lag_refused(self):
        ok, reasons = self._verify(self._ev(candidate_replication_lag_s=-0.1))
        self.assertFalse(ok)
        self.assertTrue(self._reasons_contain(reasons, "negative"))

    def test_non_finite_clock_refused(self):
        for now in (self.NAN, self.INF, -self.INF):
            ok, reasons = self._verify(self._ev(), now=now)
            self.assertFalse(ok, now)
            self.assertTrue(self._reasons_contain(reasons, "verification clock not finite"))

    def test_non_finite_freshness_window_refused(self):
        for window in (self.NAN, self.INF, -1.0):
            ok, reasons = self._verify(self._ev(), max_age=window)
            self.assertFalse(ok, window)
            self.assertTrue(self._reasons_contain(reasons, "freshness window invalid"))

    def test_non_finite_lag_ceiling_refused(self):
        for ceiling in (self.NAN, self.INF, -1.0):
            ok, reasons = self._verify(self._ev(), max_lag=ceiling)
            self.assertFalse(ok, ceiling)
            self.assertTrue(self._reasons_contain(reasons, "lag ceiling invalid"))

    # --- replication-lag ceiling boundaries ---

    def test_lag_exactly_at_ceiling_passes(self):
        self.assertEqual(non_fence_reasons(*self._verify(
            self._ev(candidate_replication_lag_s=30.0), max_lag=30.0)), ())

    def test_lag_just_above_ceiling_refused(self):
        ok, reasons = self._verify(self._ev(candidate_replication_lag_s=30.000001), max_lag=30.0)
        self.assertFalse(ok)
        self.assertTrue(self._reasons_contain(reasons, "exceeds max"))

    def test_lag_far_above_ceiling_refused(self):
        ok, _ = self._verify(self._ev(candidate_replication_lag_s=3600.0), max_lag=30.0)
        self.assertFalse(ok)

    def test_zero_lag_zero_ceiling_passes(self):
        self.assertEqual(non_fence_reasons(*self._verify(
            self._ev(candidate_replication_lag_s=0.0), max_lag=0.0)), ())

    # --- freshness boundaries (existing check, boundary pinned) ---

    def test_observation_exactly_at_max_age_passes(self):
        ev = self._ev(candidate_observed_at=self.NOW - 60.0)
        self.assertEqual(non_fence_reasons(*self._verify(ev)), ())

    def test_observation_just_older_than_max_age_refused(self):
        ok, _ = self._verify(self._ev(candidate_observed_at=self.NOW - 60.000001))
        self.assertFalse(ok)

    def test_observation_at_now_passes(self):
        self.assertEqual(non_fence_reasons(*self._verify(
            self._ev(candidate_observed_at=self.NOW))), ())

    # --- every failure is reported, with explicit reasons ---

    def test_nan_everywhere_reports_every_reason(self):
        fence = FenceProof("write_probe_read_only", self.NAN, old_epoch=4, new_epoch=5)
        ev = self._ev(candidate_observed_at=self.NAN, candidate_replication_lag_s=self.NAN,
                      fence=fence)
        ok, reasons = self._verify(ev)
        self.assertFalse(ok)
        self.assertGreaterEqual(len(reasons), 3)

    # --- both failover directions (promotion of standby -> primary, and back) ---

    def test_forward_direction_standby_promotion_requires_all_gates(self):
        ok, _ = self._verify(self._ev(candidate_replication_lag_s=self.NAN))
        self.assertFalse(ok)
        self.assertEqual(non_fence_reasons(*self._verify(self._ev())), ())

    def test_reverse_direction_fence_epoch_must_advance(self):
        # Failing back: the old standby-side writer must be fenced at the new epoch.
        fence = FenceProof("write_probe_read_only", self.NOW - 5, old_epoch=5, new_epoch=5)
        ok, reasons = self._verify(self._ev(fence=fence), expected_epoch=5)
        self.assertFalse(ok)
        self.assertTrue(self._reasons_contain(reasons, "epoch"))


class DecisionNonFiniteLagTests(unittest.TestCase):
    """decide(): NaN/infinite standby lag must hold, in both directions."""

    def _fenced_down(self):
        return HealthState(kind=ProbeKind.UNAVAILABLE, consecutive_failures=3, fenced=True)

    def _standby(self, lag):
        return HealthState(kind=ProbeKind.OK, consecutive_failures=0,
                           replication_lag_s=lag, is_read_only=True, fenced=False)

    def test_nan_standby_lag_holds(self):
        d = decide(DbConfig(NEON, AIVEN, False, 3, 30.0, 5.0), self._fenced_down(),
                   self._standby(float("nan")))
        self.assertIs(d.action, Action.HOLD)
        self.assertTrue(any("not finite" in r for r in d.reasons))

    def test_infinite_standby_lag_holds(self):
        for v in (float("inf"), -float("inf")):
            d = decide(DbConfig(NEON, AIVEN, False, 3, 30.0, 5.0), self._fenced_down(),
                       self._standby(v))
            self.assertIs(d.action, Action.HOLD, v)

    def test_negative_standby_lag_holds(self):
        d = decide(DbConfig(NEON, AIVEN, False, 3, 30.0, 5.0), self._fenced_down(),
                   self._standby(-1.0))
        self.assertIs(d.action, Action.HOLD)

    def test_standby_lag_at_ceiling_is_candidate(self):
        d = decide(DbConfig(NEON, AIVEN, False, 3, 30.0, 5.0), self._fenced_down(),
                   self._standby(30.0))
        self.assertIs(d.action, Action.HOLD)
        self.assertTrue(any("not independently verified" in r for r in d.reasons))

    def test_standby_lag_above_ceiling_holds(self):
        d = decide(DbConfig(NEON, AIVEN, False, 3, 30.0, 5.0), self._fenced_down(),
                   self._standby(30.000001))
        self.assertIs(d.action, Action.HOLD)

    def test_failback_direction_not_promoted_when_active_is_standby(self):
        # Original primary healthy again: the decision must never promote the
        # standby; at most it offers a fail-back candidate for an operator.
        healthy = HealthState(kind=ProbeKind.OK, consecutive_failures=0, fenced=False)
        d = decide(DbConfig(NEON, AIVEN, False, 3, 30.0, 5.0), healthy,
                   self._standby(float("nan")), active_is_original_primary=False)
        self.assertIsNot(d.action, Action.PROMOTION_CANDIDATE)


class HealthMonitorFlagTests(unittest.TestCase):
    def test_non_finite_env_floats_refused(self):
        base = {"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN}
        for name in ("DATABASE_MAX_REPLICATION_LAG_S", "DATABASE_PROBE_TIMEOUT_S"):
            for raw in ("nan", "NaN", "inf", "-inf", "Infinity", "-Infinity"):
                with self.assertRaises(ConfigError, msg=f"{name}={raw}"):
                    load_db_config({**base, name: raw})

    def test_finite_lag_ceiling_boundary_accepted(self):
        base = {"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN}
        self.assertEqual(load_db_config({**base, "DATABASE_MAX_REPLICATION_LAG_S": "0"})
                         .max_replication_lag_s, 0.0)
        with self.assertRaises(ConfigError):
            load_db_config({**base, "DATABASE_MAX_REPLICATION_LAG_S": "-0.5"})

    def test_monitor_off_is_accepted(self):
        c = load_db_config({"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN,
                            "DATABASE_HEALTH_MONITOR": "off"})
        self.assertFalse(c.health_monitor)

    def test_monitor_on_forms_all_rejected(self):
        for raw in ("1", "true", "on", "yes", "ON"):
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                load_db_config({"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN,
                                "DATABASE_HEALTH_MONITOR": raw})


class PrimaryUrlTests(unittest.TestCase):
    def test_primary_url_only_is_accepted(self):
        env = {"DATABASE_PRIMARY_URL": NEON}
        c = load_db_config(env)
        self.assertFalse(c.dual)
        self.assertEqual(c.primary_url, NEON)
        self.assertEqual(resolve_authoritative_url(env), NEON)

    def test_both_urls_equal_is_accepted(self):
        env = {"DATABASE_URL": NEON, "DATABASE_PRIMARY_URL": NEON}
        self.assertEqual(resolve_authoritative_url(env), NEON)

    def test_both_urls_conflicting_fails_closed(self):
        env = {"DATABASE_URL": NEON, "DATABASE_PRIMARY_URL": AIVEN}
        with self.assertRaises(ConfigError):
            load_db_config(env)
        with self.assertRaises(ConfigError):
            resolve_authoritative_url(env)

    def test_neither_url_fails_closed(self):
        with self.assertRaises(ConfigError):
            resolve_authoritative_url({})
        with self.assertRaises(ConfigError):
            resolve_authoritative_url({"DATABASE_URL": "  ", "DATABASE_PRIMARY_URL": ""})

    def test_primary_only_with_standby_routes_to_primary(self):
        env = {"DATABASE_PRIMARY_URL": NEON, "DATABASE_STANDBY_URL": AIVEN}
        self.assertEqual(resolve_authoritative_url(env), NEON)

    def test_standby_equal_to_primary_url_rejected_for_new_var(self):
        with self.assertRaises(ConfigError):
            load_db_config({"DATABASE_PRIMARY_URL": NEON, "DATABASE_STANDBY_URL": NEON})


class AuthoritativeTargetTests(unittest.TestCase):
    """Alembic and the application must resolve the same target (defect 1)."""

    def test_legacy_value_passed_through_unchanged(self):
        raw = "postgresql://u:[redacted:password]@h/db?sslmode=require"
        self.assertEqual(resolve_authoritative_url({"DATABASE_URL": raw}), raw)

    def test_alembic_env_uses_the_shared_resolver(self):
        env_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                              "backend", "alembic", "env.py")
        with open(env_py, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("from db_routing import resolve_authoritative_url", src)
        self.assertIn("resolve_authoritative_url(os.environ)", src)
        self.assertNotIn('os.getenv("DATABASE_URL")', src)

    def test_main_uses_the_shared_resolver(self):
        main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                               "backend", "main.py")
        with open(main_py, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("resolve_authoritative_url(os.environ)", src)
        self.assertIn("psycopg.connect(DB_TARGET_URL", src)


class StandbyResolutionRegressionTests(unittest.TestCase):
    """resolve_authoritative_url() must never return the standby without evidence."""

    def test_standby_active_with_verified_flag_and_promotion_evidence_env_is_refused(self):
        env = {
            "DATABASE_URL": NEON,
            "DATABASE_STANDBY_URL": AIVEN,
            "DATABASE_ACTIVE": "standby",
            "DATABASE_STANDBY_PROMOTION_VERIFIED": "on",
            "PROMOTION_EVIDENCE": '{"fence": "write_probe_read_only", "epoch": 1}',
        }
        with self.assertRaises(ConfigError) as ctx:
            resolve_authoritative_url(env)
        self.assertNotIn(AIVEN, str(ctx.exception))
        self.assertNotIn(NEON, str(ctx.exception))

    def test_standby_active_never_returns_aiven_for_any_promotion_env_value(self):
        for raw in ("", "1", "on", "true", "yes", "verified", "provider_endpoint_disabled"):
            env = {
                "DATABASE_URL": NEON,
                "DATABASE_STANDBY_URL": AIVEN,
                "DATABASE_ACTIVE": "standby",
                "DATABASE_STANDBY_PROMOTION_VERIFIED": "on",
                "PROMOTION_EVIDENCE": raw,
            }
            with self.subTest(promotion_evidence=raw):
                with self.assertRaises(ConfigError):
                    resolve_authoritative_url(env)

    def test_primary_routing_still_works_when_active_is_primary(self):
        env = {
            "DATABASE_URL": NEON,
            "DATABASE_STANDBY_URL": AIVEN,
            "DATABASE_ACTIVE": "primary",
            "DATABASE_STANDBY_PROMOTION_VERIFIED": "on",
            "PROMOTION_EVIDENCE": '{"fence": "write_probe_read_only"}',
        }
        self.assertEqual(resolve_authoritative_url(env), NEON)


class ClassificationTests(unittest.TestCase):
    class Err(Exception):
        def __init__(self, msg, sqlstate=None):
            super().__init__(msg)
            self.sqlstate = sqlstate

    def test_sqlstate_auth(self):
        self.assertIs(classify_probe_error(self.Err("x", "28P01")), ProbeKind.AUTH)

    def test_sqlstate_read_only(self):
        self.assertIs(classify_probe_error(self.Err("x", "25006")), ProbeKind.READ_ONLY)

    def test_sqlstate_shutdown_is_unavailable(self):
        self.assertIs(classify_probe_error(self.Err("x", "57P01")), ProbeKind.UNAVAILABLE)

    def test_password_failure_is_auth_not_outage(self):
        self.assertIs(classify_probe_error(Exception("password authentication failed for user")),
                      ProbeKind.AUTH)

    def test_dns(self):
        self.assertIs(classify_probe_error(Exception("could not translate host name 'x'")),
                      ProbeKind.DNS)

    def test_timeout(self):
        self.assertIs(classify_probe_error(TimeoutError("timed out")), ProbeKind.TIMEOUT)

    def test_refused_is_unavailable(self):
        self.assertIs(classify_probe_error(Exception("connection refused")), ProbeKind.UNAVAILABLE)

    def test_unknown_is_unknown(self):
        self.assertIs(classify_probe_error(Exception("something odd")), ProbeKind.UNKNOWN)


class DecisionSymmetryTests(unittest.TestCase):
    """The same rules must hold in both directions."""

    def test_neon_primary_down_promotes_aiven_candidate(self):
        d = decide(cfg(), HealthState(ProbeKind.UNAVAILABLE, 3, fenced=True),
                   healthy_standby(), active_is_original_primary=True)
        self.assertIs(d.action, Action.HOLD)

    def test_aiven_primary_down_promotes_neon_candidate(self):
        # Same function, roles swapped: the "active" is Aiven, standby is Neon.
        d = decide(cfg(), HealthState(ProbeKind.UNAVAILABLE, 3, fenced=True),
                   healthy_standby(), active_is_original_primary=False)
        self.assertIs(d.action, Action.HOLD)

    def test_failback_candidate_when_original_primary_recovers(self):
        d = decide(cfg(), HealthState(ProbeKind.OK), healthy_standby(), active_is_original_primary=False)
        self.assertIs(d.action, Action.FAILBACK_CANDIDATE)


class FailoverGateTests(unittest.TestCase):
    def fenced_down(self, **over):
        s = dict(kind=ProbeKind.UNAVAILABLE, consecutive_failures=3, fenced=True)
        s.update(over)
        return HealthState(**s)

    def test_below_threshold_retries_only(self):
        d = decide(cfg(), down(n=2), healthy_standby(fenced=True))
        self.assertIs(d.action, Action.RETRY)

    def test_threshold_with_all_gates_met_still_holds_without_provider_fence(self):
        d = decide(cfg(), self.fenced_down(), healthy_standby())
        self.assertIs(d.action, Action.HOLD)
        self.assertTrue(any("automatic promotion disabled" in r for r in d.reasons))

    def test_unfenced_active_holds(self):
        d = decide(cfg(), down(), healthy_standby())
        self.assertIs(d.action, Action.HOLD)
        self.assertTrue(any("fenced" in r for r in d.reasons))

    def test_stale_replication_holds(self):
        d = decide(cfg(), self.fenced_down(), healthy_standby(replication_lag_s=120.0))
        self.assertIs(d.action, Action.HOLD)
        self.assertTrue(any("lag" in r for r in d.reasons))

    def test_unknown_replication_lag_holds(self):
        d = decide(cfg(), self.fenced_down(), healthy_standby(replication_lag_s=None))
        self.assertIs(d.action, Action.HOLD)

    def test_unknown_read_only_state_holds(self):
        d = decide(cfg(), self.fenced_down(), healthy_standby(is_read_only=None))
        self.assertIs(d.action, Action.HOLD)

    def test_standby_unreachable_holds(self):
        d = decide(cfg(), self.fenced_down(), HealthState(ProbeKind.UNAVAILABLE, 3))
        self.assertIs(d.action, Action.HOLD)

    def test_operator_hold_overrides_everything(self):
        d = decide(cfg(), self.fenced_down(), healthy_standby(), operator_hold=True)
        self.assertIs(d.action, Action.HOLD)

    def test_retry_bounded_by_threshold_not_unbounded(self):
        # At threshold, retry stops and a decision is made.
        d = decide(cfg(failure_threshold=2), down(n=2), healthy_standby())
        self.assertIsNot(d.action, Action.RETRY)


class NeverFailoverTests(unittest.TestCase):
    """Auth, config and read-only problems are not outages and must never promote."""

    def test_auth_failure_never_promotes(self):
        for n in (0, 3, 10):
            d = decide(cfg(), HealthState(ProbeKind.AUTH, n, fenced=True), healthy_standby())
            self.assertIs(d.action, Action.ALERT_CONFIG)

    def test_config_failure_never_promotes(self):
        d = decide(cfg(), HealthState(ProbeKind.CONFIG, 9, fenced=True), healthy_standby())
        self.assertIs(d.action, Action.ALERT_CONFIG)

    def test_read_only_active_never_promotes(self):
        d = decide(cfg(), HealthState(ProbeKind.READ_ONLY, 9, fenced=True), healthy_standby())
        self.assertIs(d.action, Action.ALERT_CONFIG)

    def test_replication_failure_is_not_active_outage(self):
        d = decide(cfg(), HealthState(ProbeKind.REPLICATION, 9, fenced=True), healthy_standby())
        self.assertIs(d.action, Action.ALERT_CONFIG)

    def test_standby_auth_failure_blocks_promotion(self):
        d = decide(cfg(), HealthState(ProbeKind.UNAVAILABLE, 3, fenced=True),
                   HealthState(ProbeKind.AUTH, 0, replication_lag_s=0, is_read_only=True))
        self.assertIs(d.action, Action.HOLD)

    def test_standby_read_only_problem_blocks_promotion(self):
        d = decide(cfg(), HealthState(ProbeKind.UNAVAILABLE, 3, fenced=True),
                   HealthState(ProbeKind.READ_ONLY, 0, replication_lag_s=0, is_read_only=True))
        self.assertIs(d.action, Action.HOLD)

    def test_unknown_probe_is_retried_never_promoted(self):
        d = decide(cfg(), HealthState(ProbeKind.UNKNOWN, 9, fenced=True), healthy_standby())
        self.assertIs(d.action, Action.RETRY)


class DecideFencingEvidenceTests(unittest.TestCase):
    """decide() must never yield PROMOTION_CANDIDATE. Caller-supplied fencing
    evidence (missing, false, true, or forged) is not provider-side proof."""

    def test_missing_fence_holds(self):
        d = decide(cfg(), HealthState(ProbeKind.UNAVAILABLE, 3), healthy_standby())
        self.assertIs(d.action, Action.HOLD)

    def test_false_fence_holds(self):
        d = decide(cfg(), HealthState(ProbeKind.UNAVAILABLE, 3, fenced=False), healthy_standby())
        self.assertIs(d.action, Action.HOLD)
        self.assertTrue(any("not verified fenced" in r for r in d.reasons))

    def test_true_fence_still_holds(self):
        # fenced=True is caller-controlled and must not authorize promotion.
        d = decide(cfg(), HealthState(ProbeKind.UNAVAILABLE, 3, fenced=True), healthy_standby())
        self.assertIs(d.action, Action.HOLD)
        self.assertTrue(any("not independently verified" in r for r in d.reasons))

    def test_forged_fence_evidence_cannot_authorize(self):
        # Forged FenceProof objects (receipt-looking strings, fresh timestamps,
        # valid epochs) plus fenced=True and every other gate passing.
        now = 1_000_000.0
        forged = [
            FenceProof("provider_endpoint_disabled", now - 1, 4, 5, receipt_id="RCPT-FORGED"),
            FenceProof("write_probe_read_only", now - 1, 4, 5),
            FenceProof("made_up_method", now - 1, 4, 5, receipt_id="X"),
        ]
        for fence in forged:
            with self.subTest(method=fence.method):
                ev = PromotionEvidence(
                    candidate_observed_at=now - 1, candidate_in_recovery=False,
                    candidate_read_write=True, candidate_replication_lag_s=0.0,
                    fence=fence, operator_change_id="CHG-FORGED")
                ok, _ = verify_promotion_evidence(ev, now=now, max_age_s=60.0,
                                                  max_replication_lag_s=30.0,
                                                  expected_epoch=5)
                self.assertFalse(ok)
                d = decide(cfg(), HealthState(ProbeKind.UNAVAILABLE, 3, fenced=True),
                           healthy_standby())
                self.assertIs(d.action, Action.HOLD)

    def test_decide_never_returns_promotion_candidate(self):
        import itertools
        kinds = [ProbeKind.OK, ProbeKind.UNAVAILABLE, ProbeKind.TIMEOUT, ProbeKind.AUTH,
                 ProbeKind.UNKNOWN]
        for kind, fenced, orig, hold in itertools.product(
                kinds, (False, True), (True, False), (False, True)):
            with self.subTest(kind=kind, fenced=fenced, orig=orig, hold=hold):
                d = decide(cfg(), HealthState(kind, 9, fenced=fenced), healthy_standby(),
                           active_is_original_primary=orig, operator_hold=hold)
                self.assertIsNot(d.action, Action.PROMOTION_CANDIDATE)

    def test_decide_has_no_evidence_parameter(self):
        import inspect
        params = inspect.signature(decide).parameters
        self.assertNotIn("evidence", params)
        self.assertNotIn("fence", params)


class LegacyBehaviourTests(unittest.TestCase):
    def test_no_standby_means_no_failover_plan_needed(self):
        # Legacy config produces no standby; routing still yields the primary URL.
        c = load_db_config({"DATABASE_URL": NEON})
        self.assertEqual(active_target(c, {"DATABASE_URL": NEON}), NEON)


class FenceMethodRefusalTests(unittest.TestCase):
    """Neither fence method may authorize promotion, even with otherwise-valid evidence."""
    NOW = 1_000_000.0

    def _valid_ev(self, fence):
        return PromotionEvidence(
            candidate_observed_at=self.NOW - 5, candidate_in_recovery=False,
            candidate_read_write=True, candidate_replication_lag_s=0.5,
            fence=fence, operator_change_id="CHG-FENCE")

    def _verify(self, ev, expected_epoch=5):
        return verify_promotion_evidence(ev, now=self.NOW, max_age_s=60.0,
                                         max_replication_lag_s=30.0,
                                         expected_epoch=expected_epoch)

    def _standby_env(self):
        return {"DATABASE_URL": NEON, "DATABASE_STANDBY_URL": AIVEN,
                "DATABASE_ACTIVE": "standby"}

    def test_no_fence_method_is_accepted(self):
        self.assertEqual(FENCE_METHODS, frozenset())
        self.assertNotIn("write_probe_read_only", FENCE_METHODS)
        self.assertNotIn("provider_endpoint_disabled", FENCE_METHODS)

    def test_write_probe_alone_cannot_authorize_with_valid_evidence(self):
        fence = FenceProof("write_probe_read_only", self.NOW - 5, old_epoch=4, new_epoch=5)
        ok, reasons = self._verify(self._valid_ev(fence))
        self.assertFalse(ok)
        self.assertTrue(any("write_probe_read_only refused" in r for r in reasons))
        self.assertFalse(any("not recognised" in r for r in reasons))

    def test_provider_disabled_with_receipt_cannot_authorize(self):
        fence = FenceProof("provider_endpoint_disabled", self.NOW - 5, 4, 5,
                           receipt_id="RCPT-LOOKS-VALID")
        ok, reasons = self._verify(self._valid_ev(fence))
        self.assertFalse(ok)
        self.assertTrue(any("provider_endpoint_disabled refused" in r for r in reasons))

    def test_provider_disabled_without_receipt_cannot_authorize(self):
        fence = FenceProof("provider_endpoint_disabled", self.NOW - 5, 4, 5)
        ok, _ = self._verify(self._valid_ev(fence))
        self.assertFalse(ok)

    def test_active_target_refuses_write_probe_with_valid_evidence(self):
        fence = FenceProof("write_probe_read_only", self.NOW - 5, old_epoch=4, new_epoch=5)
        env = self._standby_env()
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env, self._valid_ev(fence),
                          now=self.NOW, expected_epoch=5)

    def test_active_target_refuses_provider_disabled_with_valid_evidence(self):
        fence = FenceProof("provider_endpoint_disabled", self.NOW - 5, 4, 5,
                           receipt_id="RCPT-1")
        env = self._standby_env()
        with self.assertRaises(ConfigError):
            active_target(load_db_config(env), env, self._valid_ev(fence),
                          now=self.NOW, expected_epoch=5)

    def test_fence_gate_is_the_only_blocker_for_valid_evidence(self):
        # Control: with the write-probe method temporarily allowed, the same
        # otherwise-valid evidence passes. So the refusal above comes from the
        # fence gate alone, not from a defect in the other fixtures.
        from unittest import mock
        import db_routing
        fence = FenceProof("write_probe_read_only", self.NOW - 5, old_epoch=4, new_epoch=5)
        with mock.patch.object(db_routing, "FENCE_METHODS",
                               frozenset({"write_probe_read_only"})):
            self.assertEqual(self._verify(self._valid_ev(fence)), (True, ()))
        self.assertFalse(self._verify(self._valid_ev(fence))[0])


if __name__ == "__main__":
    unittest.main()
