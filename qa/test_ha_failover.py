"""Tests for the automatic failover controller: health thresholds, fencing receipts,
state machine transitions, promotion gating, persistence and the exactly-one-writer
invariant. Pure logic and a temporary state file only. No database, no network."""

import dataclasses
import itertools
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from db_routing import (  # noqa: E402
    DbConfig, FenceProof, PromotionEvidence, ProbeKind, verify_promotion_evidence,
)
from ha.fencing import (  # noqa: E402
    FenceStatus, FencingAdapter, FencingReceipt, FencingResult, NeonFencingAdapter,
    VERIFIED_FENCING_PROVIDERS, verify_fencing_receipt,
)
from ha.health import HealthMonitor, TargetHealth, record_observation, threshold_met  # noqa: E402
from ha.controller import (  # noqa: E402
    ALLOWED_EDGES, ControllerState, FileStateStore, Observation, State, StaleVersion,
    StoreBusy, WRITER_PRIMARY, WRITER_STANDBY, step,
)
from ha.replication import SlotLossKind  # noqa: E402

NOW = 1_000_000.0
CFG = DbConfig(primary_url="neon", standby_url="aiven", health_monitor=False,
               failure_threshold=3, max_replication_lag_s=30.0, probe_timeout_s=5.0)
OK_OBS = Observation(ProbeKind.OK, 0, ProbeKind.OK, 1.0, SlotLossKind.OK)


def obs(primary=ProbeKind.OK, failures=0, standby=ProbeKind.OK, lag=1.0,
        slot=SlotLossKind.OK, hold=False, clear=None):
    return Observation(primary, failures, standby, lag, slot, hold, clear)


def run(cs, o, **kw):
    kw.setdefault("cfg", CFG)
    kw.setdefault("now", NOW)
    return step(cs, o, **kw)


class FakeAdapter(FencingAdapter):
    """Test double. Authenticates only receipts it issued, so forgery is rejected."""
    provider = "fake"

    def __init__(self, issued=()):
        self.issued = set(issued)

    def request_fence(self, operation_id, epoch):
        return FencingResult(FenceStatus.UNSUPPORTED, ("test double",))

    def authenticate_receipt(self, receipt):
        return receipt.proof in self.issued


def receipt(adapter_provider="fake", target="neon", epoch=1, op="fence-0-0",
            issued=NOW - 10, expires=NOW + 60, proof="p1"):
    return FencingReceipt(provider=adapter_provider, operation_id=op, fenced_target=target,
                          epoch=epoch, issued_at=issued, expires_at=expires, proof=proof)


def verified(r):
    return FencingResult(FenceStatus.VERIFIED, (), r)


# ---------------------------------------------------------------------------
class HealthThresholdTests(unittest.TestCase):
    def test_threshold_requires_consecutive_failures(self):
        h = TargetHealth("primary")
        for _ in range(2):
            h = record_observation(h, ProbeKind.UNAVAILABLE, NOW)
        self.assertFalse(threshold_met(h, 3))
        h = record_observation(h, ProbeKind.UNAVAILABLE, NOW)
        self.assertTrue(threshold_met(h, 3))

    def test_intermittent_failure_resets_streak(self):
        h = TargetHealth("primary")
        seq = [ProbeKind.TIMEOUT, ProbeKind.TIMEOUT, ProbeKind.OK,
               ProbeKind.TIMEOUT, ProbeKind.TIMEOUT]
        for k in seq:
            h = record_observation(h, k, NOW)
        self.assertEqual(h.consecutive_failures, 2)
        self.assertFalse(threshold_met(h, 3))

    def test_auth_config_readonly_never_count_as_outage(self):
        for k in (ProbeKind.AUTH, ProbeKind.CONFIG, ProbeKind.READ_ONLY, ProbeKind.REPLICATION):
            h = TargetHealth("primary")
            for _ in range(10):
                h = record_observation(h, k, NOW)
            self.assertEqual(h.consecutive_failures, 0, k)
            self.assertFalse(threshold_met(h, 1), k)

    def test_unknown_keeps_streak_and_is_not_healthy(self):
        h = TargetHealth("primary")
        h = record_observation(h, ProbeKind.UNAVAILABLE, NOW)
        h = record_observation(h, ProbeKind.UNKNOWN, NOW)
        self.assertEqual(h.consecutive_failures, 1)

    def test_monitor_records_probe_exception_as_unknown(self):
        def boom():
            raise RuntimeError("driver bug")
        m = HealthMonitor(probes={"primary": boom, "standby": lambda: ProbeKind.OK},
                          clock=lambda: NOW)
        state = m.sample_once()
        self.assertEqual(state["primary"].last_kind, ProbeKind.UNKNOWN)
        self.assertEqual(state["standby"].last_kind, ProbeKind.OK)

    def test_monitor_rejects_non_probekind_return(self):
        m = HealthMonitor(probes={"primary": lambda: "ok"}, clock=lambda: NOW)
        self.assertEqual(m.sample_once()["primary"].last_kind, ProbeKind.UNKNOWN)

    def test_monitor_makes_one_call_per_probe_per_round(self):
        calls = {"n": 0}

        def probe():
            calls["n"] += 1
            return ProbeKind.OK
        m = HealthMonitor(probes={"primary": probe}, clock=lambda: NOW)
        m.sample_once()
        m.sample_once()
        self.assertEqual(calls["n"], 2)


# ---------------------------------------------------------------------------
class FencingReceiptTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeAdapter(issued={"p1"})

    def check(self, result, adapter=None, consumed=(), epoch=1, op="fence-0-0"):
        return verify_fencing_receipt(
            adapter if adapter is not None else self.adapter, result, now=NOW,
            expected_target="neon", expected_epoch=epoch, expected_operation_id=op,
            consumed_operation_ids=consumed,
        )

    def test_neon_adapter_is_unsupported_and_never_verifies(self):
        n = NeonFencingAdapter()
        res = n.request_fence("fence-0-0", 1)
        self.assertIs(res.status, FenceStatus.UNSUPPORTED)
        ok, _ = verify_fencing_receipt(n, res, now=NOW, expected_target="neon",
                                       expected_epoch=1, expected_operation_id="fence-0-0",
                                       consumed_operation_ids=())
        self.assertFalse(ok)

    def test_neon_adapter_refuses_forged_verified_receipt(self):
        n = NeonFencingAdapter()
        forged = verified(receipt(adapter_provider="neon", proof="anything"))
        ok, reasons = verify_fencing_receipt(n, forged, now=NOW, expected_target="neon",
                                             expected_epoch=1, expected_operation_id="fence-0-0",
                                             consumed_operation_ids=())
        self.assertFalse(ok)
        self.assertTrue(any("no verified fencing mechanism" in r for r in reasons))

    def test_verified_provider_allowlist_is_empty(self):
        self.assertEqual(VERIFIED_FENCING_PROVIDERS, frozenset())

    def test_valid_receipt_passes_only_when_provider_verified(self):
        # Simulate a future verified provider by patching the module constant, to prove
        # the rest of the checks pass. The real constant stays empty.
        import ha.fencing as f
        original = f.VERIFIED_FENCING_PROVIDERS
        try:
            f.VERIFIED_FENCING_PROVIDERS = frozenset({"fake"})
            ok, reasons = self.check(verified(receipt()))
            self.assertTrue(ok, reasons)
        finally:
            f.VERIFIED_FENCING_PROVIDERS = original

    def test_real_allowlist_refuses_even_a_valid_looking_receipt(self):
        ok, reasons = self.check(verified(receipt()))
        self.assertFalse(ok)
        self.assertTrue(any("no verified fencing mechanism" in r for r in reasons))

    def test_not_verified_status_is_never_a_fence(self):
        for st in (FenceStatus.UNSUPPORTED, FenceStatus.REFUSED, FenceStatus.ERROR):
            ok, _ = self.check(FencingResult(st, ("x",), receipt()))
            self.assertFalse(ok, st)

    def test_verified_without_receipt_is_refused(self):
        ok, _ = self.check(FencingResult(FenceStatus.VERIFIED, ()))
        self.assertFalse(ok)

    def test_none_adapter_or_result_refused(self):
        self.assertFalse(verify_fencing_receipt(None, verified(receipt()), now=NOW,
                         expected_target="neon", expected_epoch=1,
                         expected_operation_id="fence-0-0", consumed_operation_ids=())[0])
        self.assertFalse(self.check(None)[0])

    def test_expired_and_not_yet_valid_receipts_refused(self):
        expired = verified(receipt(expires=NOW - 1))
        future = verified(receipt(issued=NOW + 5, expires=NOW + 60))
        self.assertFalse(self.check(expired)[0])
        self.assertFalse(self.check(future)[0])

    def test_inverted_window_refused(self):
        self.assertFalse(self.check(verified(receipt(issued=NOW - 1, expires=NOW - 2)))[0])

    def test_nonfinite_timestamps_refused(self):
        for bad in (float("nan"), float("inf")):
            self.assertFalse(self.check(verified(receipt(expires=bad)))[0])
            self.assertFalse(self.check(verified(receipt(issued=bad)))[0])

    def test_wrong_target_epoch_or_operation_refused(self):
        self.assertFalse(self.check(verified(receipt(target="aiven")))[0])
        self.assertFalse(self.check(verified(receipt(epoch=2)), epoch=1)[0])
        self.assertFalse(self.check(verified(receipt(op="other")), op="fence-0-0")[0])
        self.assertFalse(self.check(verified(receipt()), op=None)[0])

    def test_replayed_operation_refused(self):
        ok, reasons = self.check(verified(receipt()), consumed=("fence-0-0",))
        self.assertFalse(ok)
        self.assertTrue(any("replayed" in r for r in reasons))

    def test_forged_receipt_not_authenticated_by_provider_refused(self):
        import ha.fencing as f
        original = f.VERIFIED_FENCING_PROVIDERS
        try:
            f.VERIFIED_FENCING_PROVIDERS = frozenset({"fake"})
            ok, reasons = self.check(verified(receipt(proof="forged")))
            self.assertFalse(ok)
            self.assertIn("provider did not authenticate receipt", reasons)
        finally:
            f.VERIFIED_FENCING_PROVIDERS = original


# ---------------------------------------------------------------------------
class StateMachineTests(unittest.TestCase):
    def test_healthy_primary_stays_active(self):
        cs = run(ControllerState(), OK_OBS)
        self.assertEqual(cs.state, State.PRIMARY_ACTIVE)
        self.assertEqual(cs.writer, WRITER_PRIMARY)

    def test_below_threshold_failure_is_suspect_not_failover(self):
        cs = run(ControllerState(), obs(primary=ProbeKind.UNAVAILABLE, failures=1))
        self.assertEqual(cs.state, State.PRIMARY_SUSPECT)
        self.assertEqual(cs.writer, WRITER_PRIMARY)

    def test_threshold_reaches_unavailable_then_fencing(self):
        cs = ControllerState()
        cs = run(cs, obs(primary=ProbeKind.TIMEOUT, failures=3))
        self.assertEqual(cs.state, State.PRIMARY_UNAVAILABLE)
        cs = run(cs, obs(primary=ProbeKind.TIMEOUT, failures=4))
        self.assertEqual(cs.state, State.FENCING_REQUIRED)
        self.assertEqual(cs.writer, WRITER_PRIMARY)

    def test_intermittent_failures_never_reach_unavailable(self):
        cs = ControllerState()
        for f, kind in [(1, ProbeKind.TIMEOUT), (0, ProbeKind.OK),
                        (1, ProbeKind.TIMEOUT), (0, ProbeKind.OK)]:
            cs = run(cs, obs(primary=kind, failures=f))
        self.assertNotIn(cs.state, (State.PRIMARY_UNAVAILABLE, State.FENCING_REQUIRED))

    def test_neon_recovery_from_suspect_returns_active(self):
        cs = run(ControllerState(), obs(primary=ProbeKind.UNAVAILABLE, failures=1))
        cs = run(cs, OK_OBS)
        self.assertEqual(cs.state, State.PRIMARY_ACTIVE)

    def test_auth_error_on_primary_holds_without_failover(self):
        cs = run(ControllerState(), obs(primary=ProbeKind.AUTH, failures=0))
        self.assertEqual(cs.state, State.HOLD)
        self.assertEqual(cs.writer, WRITER_PRIMARY)

    def test_fencing_without_verified_receipt_holds_not_promotes(self):
        cs = ControllerState(state=State.FENCING_REQUIRED, fence_operation_id="fence-0-0", version=5)
        cs = run(cs, obs(primary=ProbeKind.TIMEOUT, failures=5),
                 fence_result=FencingResult(FenceStatus.UNSUPPORTED, ("x",)),
                 fence_adapter=NeonFencingAdapter())
        self.assertEqual(cs.state, State.HOLD)
        self.assertEqual(cs.writer, WRITER_PRIMARY)

    def test_fencing_pending_without_result_waits(self):
        cs = ControllerState(state=State.FENCING_REQUIRED, fence_operation_id="fence-0-0")
        cs = run(cs, obs(primary=ProbeKind.TIMEOUT, failures=5))
        self.assertEqual(cs.state, State.FENCING_REQUIRED)

    def test_primary_recovers_during_fencing_holds(self):
        cs = ControllerState(state=State.FENCING_REQUIRED, fence_operation_id="fence-0-0")
        cs = run(cs, OK_OBS)
        self.assertEqual(cs.state, State.HOLD)

    def test_verified_status_with_bad_receipt_is_manual_intervention(self):
        cs = ControllerState(state=State.FENCING_REQUIRED, fence_operation_id="fence-0-0")
        cs = run(cs, obs(primary=ProbeKind.TIMEOUT, failures=5),
                 fence_result=verified(receipt(adapter_provider="neon")),
                 fence_adapter=NeonFencingAdapter())
        self.assertEqual(cs.state, State.MANUAL_INTERVENTION_REQUIRED)

    def test_replication_validation_blocks_on_slot_loss(self):
        cs = ControllerState(state=State.FENCED)
        cs = run(cs, obs(primary=ProbeKind.TIMEOUT, standby=ProbeKind.OK,
                         slot=SlotLossKind.LOST))
        self.assertEqual(cs.state, State.REPLICATION_VALIDATION)
        cs = run(cs, obs(primary=ProbeKind.TIMEOUT, slot=SlotLossKind.LOST))
        self.assertEqual(cs.state, State.MANUAL_INTERVENTION_REQUIRED)

    def test_replication_validation_blocks_on_excessive_lag(self):
        cs = run(ControllerState(state=State.FENCED), obs(primary=ProbeKind.TIMEOUT))
        cs = run(cs, obs(primary=ProbeKind.TIMEOUT, lag=999.0))
        self.assertEqual(cs.state, State.HOLD)

    def test_replication_validation_blocks_on_unknown_lag_and_nonfinite(self):
        for lag in (None, float("nan"), float("inf"), -1.0):
            cs = run(ControllerState(state=State.REPLICATION_VALIDATION),
                     obs(primary=ProbeKind.TIMEOUT, lag=lag))
            self.assertEqual(cs.state, State.HOLD, lag)

    def test_aiven_unavailable_blocks_validation(self):
        cs = run(ControllerState(state=State.REPLICATION_VALIDATION),
                 obs(primary=ProbeKind.TIMEOUT, standby=ProbeKind.UNAVAILABLE))
        self.assertEqual(cs.state, State.HOLD)

    def test_missing_promotion_evidence_holds(self):
        cs = run(ControllerState(state=State.PROMOTION_PENDING), obs(primary=ProbeKind.TIMEOUT))
        self.assertEqual(cs.state, State.HOLD)
        self.assertEqual(cs.writer, WRITER_PRIMARY)

    def test_real_promotion_verifier_refuses_every_evidence(self):
        ev = PromotionEvidence(
            candidate_observed_at=NOW, candidate_in_recovery=False, candidate_read_write=True,
            candidate_replication_lag_s=1.0,
            fence=FenceProof(method="provider_endpoint_disabled", observed_at=NOW,
                             old_epoch=0, new_epoch=1, receipt_id="r"),
            operator_change_id="CHG-1",
        )
        cs = run(ControllerState(state=State.PROMOTION_PENDING), obs(primary=ProbeKind.TIMEOUT),
                 promotion_evidence=ev, promotion_completed=True)
        self.assertEqual(cs.state, State.HOLD)
        self.assertEqual(cs.writer, WRITER_PRIMARY)

    def test_promotion_requires_completion_attestation_even_with_passing_verifier(self):
        # Caller-supplied verifiers are no longer accepted; the fixed verifier refuses.
        ev = PromotionEvidence(NOW, False, True, 1.0, None, "CHG-1")
        cs = run(ControllerState(state=State.PROMOTION_PENDING), obs(primary=ProbeKind.TIMEOUT),
                 promotion_evidence=ev, promotion_completed=False)
        self.assertEqual(cs.state, State.HOLD)
        self.assertEqual(cs.writer, WRITER_PRIMARY)

    def test_caller_cannot_inject_promotion_verifier(self):
        with self.assertRaises(TypeError):
            run(ControllerState(state=State.PROMOTION_PENDING), obs(primary=ProbeKind.TIMEOUT),
                promotion_verifier=lambda *a, **k: (True, ()))

    def test_promotion_edge_unreachable_even_with_completed_flag_and_valid_looking_evidence(self):
        # The fixed verifier refuses all evidence in this build, so the writer cannot move.
        ev = PromotionEvidence(NOW, False, True, 1.0, None, "CHG-1")
        cs = run(ControllerState(state=State.PROMOTION_PENDING, epoch=0), obs(primary=ProbeKind.TIMEOUT),
                 promotion_evidence=ev, promotion_completed=True)
        self.assertEqual(cs.state, State.HOLD)
        self.assertEqual(cs.writer, WRITER_PRIMARY)
        self.assertEqual(cs.epoch, 0)

    def test_standby_active_never_fails_back_automatically(self):
        cs = ControllerState(state=State.STANDBY_ACTIVE, writer=WRITER_STANDBY, epoch=1)
        cs = run(cs, obs(primary=ProbeKind.OK, standby=ProbeKind.OK))
        self.assertEqual(cs.state, State.RECOVERY_DETECTED)
        self.assertEqual(cs.writer, WRITER_STANDBY)
        for _ in range(5):
            cs = run(cs, obs(primary=ProbeKind.OK, standby=ProbeKind.OK))
            self.assertEqual(cs.writer, WRITER_STANDBY)
            self.assertNotEqual(cs.state, State.FAILBACK_PENDING)
            self.assertNotEqual(cs.state, State.PRIMARY_ACTIVE)

    def test_recovery_detected_stays_on_standby_until_resync(self):
        cs = ControllerState(state=State.RECOVERY_DETECTED, writer=WRITER_STANDBY, epoch=1)
        cs = run(cs, obs(primary=ProbeKind.OK))
        self.assertEqual(cs.state, State.RECOVERY_DETECTED)
        self.assertEqual(cs.writer, WRITER_STANDBY)

    def test_standby_probe_failure_holds_writer_on_standby(self):
        cs = ControllerState(state=State.STANDBY_ACTIVE, writer=WRITER_STANDBY, epoch=1)
        cs = run(cs, obs(standby=ProbeKind.UNAVAILABLE))
        self.assertEqual(cs.state, State.HOLD)
        self.assertEqual(cs.writer, WRITER_STANDBY)

    def test_operator_hold_and_manual_exit_never_promote(self):
        cs = run(ControllerState(), obs(hold=True))
        self.assertEqual(cs.state, State.HOLD)
        cs = run(cs, obs(clear="CHG-9"))
        self.assertEqual(cs.state, State.PRIMARY_ACTIVE)
        self.assertEqual(cs.writer, WRITER_PRIMARY)

    def test_manual_intervention_is_sticky(self):
        cs = ControllerState(state=State.MANUAL_INTERVENTION_REQUIRED)
        for _ in range(3):
            cs = run(cs, OK_OBS)
            self.assertEqual(cs.state, State.MANUAL_INTERVENTION_REQUIRED)

    def test_determinism_and_idempotent_repeat(self):
        cs = ControllerState()
        o = obs(primary=ProbeKind.UNAVAILABLE, failures=1)
        a = run(cs, o)
        b = run(cs, o)
        self.assertEqual(a, b)
        self.assertEqual(run(a, o), run(a, o))

    def test_illegal_edge_raises_not_silently_taken(self):
        from ha.controller import _edge_check
        with self.assertRaises(AssertionError):
            _edge_check(State.PRIMARY_ACTIVE, State.STANDBY_ACTIVE)

    def test_failback_pending_unreachable_from_every_state(self):
        for src in State:
            self.assertNotIn((src, State.FAILBACK_PENDING), ALLOWED_EDGES - {(State.FAILBACK_PENDING, State.FAILBACK_PENDING), (State.FAILBACK_PENDING, State.HOLD), (State.FAILBACK_PENDING, State.MANUAL_INTERVENTION_REQUIRED)})

    def test_no_edge_moves_writer_except_promotion(self):
        # Enumerate every observation combination across every state and assert the
        # writer changes only on the PROMOTION_PENDING -> STANDBY_ACTIVE transition.
        kinds = [ProbeKind.OK, ProbeKind.TIMEOUT, ProbeKind.UNAVAILABLE, ProbeKind.AUTH]
        slots = [SlotLossKind.OK, SlotLossKind.LOST, SlotLossKind.UNOBSERVED]
        lags = [None, 1.0, 999.0]
        for st in State:
            for writer in (WRITER_PRIMARY, WRITER_STANDBY):
                base = ControllerState(state=st, writer=writer, epoch=1, fence_operation_id="fence-1-0")
                for pk, sk, sl, lg, hold in itertools.product(kinds, kinds, slots, lags, (False, True)):
                    o = Observation(pk, 5, sk, lg, sl, hold, None)
                    try:
                        nxt = run(base, o)
                    except AssertionError:
                        self.fail(f"illegal transition from {st} with {o}")
                    if nxt.writer != writer:
                        self.assertEqual((st, nxt.state), (State.PROMOTION_PENDING, State.STANDBY_ACTIVE))

    def _explore(self, start_states, _unused, *, completed):
        # Breadth-first closure over every (state, writer) pair reachable from the
        # given starting configurations, using the real step() with every observation
        # class. Each frontier item carries the full ControllerState, so the edge
        # PROMOTION_PENDING -> STANDBY_ACTIVE is reached only when the verifier accepts.
        kinds = [ProbeKind.OK, ProbeKind.TIMEOUT, ProbeKind.AUTH]
        slots = [SlotLossKind.OK, SlotLossKind.LOST]
        lags = [1.0, None]
        seen = set()
        frontier = list(start_states)
        for s in frontier:
            seen.add((s.state, s.writer))
        while frontier:
            cur = frontier.pop()
            for pk, sk, sl, lg in itertools.product(kinds, kinds, slots, lags):
                o = Observation(pk, 5, sk, lg, sl)
                nxt = run(cur, o, fence_adapter=NeonFencingAdapter(),
                          fence_result=FencingResult(FenceStatus.UNSUPPORTED, ("x",)),
                          promotion_evidence=PromotionEvidence(NOW, False, True, 1.0, None, "CHG-1"),
                          promotion_completed=completed)
                key = (nxt.state, nxt.writer)
                if key not in seen:
                    seen.add(key)
                    frontier.append(nxt)
        return seen

    def test_exactly_one_writer_invariant_with_real_verifier(self):
        # Seed the search from EVERY state (with both writers) so the closure covers
        # all reachable configurations. With the real verifier, no state may reach a
        # standby writer, so the invariant holds unconditionally.
        starts = [ControllerState(state=s, writer=w, epoch=0, fence_operation_id="fence-0-0")
                  for s in State for w in (WRITER_PRIMARY, WRITER_STANDBY)]
        seen = self._explore(starts, verify_promotion_evidence, completed=True)
        writers_by_state = {}
        for st, w in seen:
            writers_by_state.setdefault(st, set()).add(w)
        # Seeds with the standby writer are legitimate post-promotion configurations.
        # From primary-writer seeds, the real verifier must never produce a standby writer.
        primary_seeded = self._explore(
            [ControllerState(state=s, writer=WRITER_PRIMARY, epoch=0, fence_operation_id="fence-0-0")
             for s in State],
            verify_promotion_evidence, completed=True)
        self.assertFalse(any(w == WRITER_STANDBY for _, w in primary_seeded))

    def test_exactly_one_writer_invariant_from_promotion_pending(self):
        # With the fixed verifier, no evidence from PROMOTION_PENDING reaches a standby
        # writer. The closure must stay on the primary writer.
        starts = [ControllerState(state=State.PROMOTION_PENDING, writer=WRITER_PRIMARY, epoch=0)]
        seen = self._explore(starts, None, completed=True)
        self.assertFalse(any(w == WRITER_STANDBY for _, w in seen))


# ---------------------------------------------------------------------------
class ForgedEvidenceTests(unittest.TestCase):
    def test_caller_fenced_flag_in_evidence_refused(self):
        ev = PromotionEvidence(NOW, False, True, 1.0, None, "CHG-1")
        ok, _ = verify_promotion_evidence(ev, now=NOW, max_age_s=60,
                                          max_replication_lag_s=30, expected_epoch=1)
        self.assertFalse(ok)

    def test_env_style_strings_cannot_stand_in_for_fence(self):
        ev = PromotionEvidence(NOW, False, True, 1.0,
                               FenceProof(method="PROMOTION_VERIFIED=on", observed_at=NOW,
                                          old_epoch=0, new_epoch=1), "CHG-1")
        ok, reasons = verify_promotion_evidence(ev, now=NOW, max_age_s=60,
                                                max_replication_lag_s=30, expected_epoch=1)
        self.assertFalse(ok)
        self.assertIn("fence method not recognised", reasons)

    def test_read_only_probe_fence_refused_even_with_all_else_valid(self):
        ev = PromotionEvidence(NOW, False, True, 1.0,
                               FenceProof(method="write_probe_read_only", observed_at=NOW,
                                          old_epoch=0, new_epoch=1), "CHG-1")
        ok, _ = verify_promotion_evidence(ev, now=NOW, max_age_s=60,
                                          max_replication_lag_s=30, expected_epoch=1)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ha_state_test_")
        self.path = os.path.join(self.dir, "state.json")
        self.store = FileStateStore(self.path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_load_missing_returns_initial_state(self):
        self.assertEqual(self.store.load(), ControllerState())

    def test_cas_roundtrip_increments_version(self):
        s1 = self.store.compare_and_swap(0, ControllerState(state=State.PRIMARY_SUSPECT))
        self.assertEqual(s1.version, 1)
        self.assertEqual(self.store.load().state, State.PRIMARY_SUSPECT)

    def test_stale_version_refused(self):
        self.store.compare_and_swap(0, ControllerState())
        with self.assertRaises(StaleVersion):
            self.store.compare_and_swap(0, ControllerState(state=State.HOLD))

    def test_two_workers_only_one_wins_same_version(self):
        self.store.compare_and_swap(0, ControllerState())
        # Worker A commits version 1 -> 2.
        self.store.compare_and_swap(1, ControllerState(state=State.HOLD))
        # Worker B, still holding version 1, must be refused: no duplicate promotion.
        with self.assertRaises(StaleVersion):
            self.store.compare_and_swap(1, ControllerState(state=State.STANDBY_ACTIVE, writer=WRITER_STANDBY))
        self.assertEqual(self.store.load().writer, WRITER_PRIMARY)

    def test_lock_held_fails_closed(self):
        open(self.store.lock_path, "w").close()
        with self.assertRaises(StoreBusy):
            self.store.compare_and_swap(0, ControllerState())
        self.assertTrue(os.path.exists(self.store.lock_path))  # not broken automatically

    def test_lock_released_after_success_and_failure(self):
        self.store.compare_and_swap(0, ControllerState())
        self.assertFalse(os.path.exists(self.store.lock_path))
        with self.assertRaises(StaleVersion):
            self.store.compare_and_swap(0, ControllerState())
        self.assertFalse(os.path.exists(self.store.lock_path))

    def test_no_temp_files_left_behind(self):
        self.store.compare_and_swap(0, ControllerState())
        leftovers = [f for f in os.listdir(self.dir) if f.startswith(".ha_state.")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()


class HealthMonitorRunRoundsTests(unittest.TestCase):
    """Bounded monitor loop: finite rounds, injected sleep, no real waiting."""

    def _monitor(self, probe):
        from ha.health import HealthMonitor
        return HealthMonitor(probes={"primary": probe}, clock=lambda: 0.0)

    def test_runs_exactly_requested_rounds_and_sleeps_between(self):
        from ha.health import ProbeKind as PK
        calls, sleeps = [], []
        m = self._monitor(lambda: (calls.append(1), PK.TIMEOUT)[1])
        m.run_rounds(3, 2.0, sleeps.append)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [2.0, 2.0])

    def test_rejects_unbounded_or_invalid_arguments(self):
        from ha.health import ProbeKind as PK, MAX_ROUNDS
        m = self._monitor(lambda: PK.OK)
        for rounds in (0, -1, True, MAX_ROUNDS + 1, 2.5):
            with self.assertRaises(ValueError, msg=repr(rounds)):
                m.run_rounds(rounds, 1.0, lambda s: None)
        for interval in (0, -1.0, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError, msg=repr(interval)):
                m.run_rounds(1, interval, lambda s: None)

    def test_probe_exception_in_loop_is_unknown_not_healthy(self):
        from ha.health import ProbeKind as PK
        def boom():
            raise RuntimeError("driver")
        m = self._monitor(boom)
        snap = m.run_rounds(2, 1.0, lambda s: None)
        self.assertNotEqual(snap["primary"].last_kind, PK.OK)
