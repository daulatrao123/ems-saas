"""Unit tests for backend/ha/replication.py: SQL generation, identifier escaping,
slot-loss classification and lag decisions. Pure logic only; no database is used."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from ha.replication import (  # noqa: E402
    LagAction,
    ReplicationError,
    SlotLossKind,
    SlotObservation,
    SlotState,
    build_conninfo,
    build_create_publication,
    build_create_subscription_aiven,
    build_drop_subscription,
    build_is_in_recovery_query,
    build_sequence_sync,
    build_slot_lag_query,
    build_subscription_stats_query,
    classify_slot_loss,
    decide_freshness_for_promotion,
    decide_lag,
    quote_ident,
    quote_literal,
)


class QuoteIdentTests(unittest.TestCase):
    def test_plain_name_is_quoted(self):
        self.assertEqual(quote_ident("users"), '"users"')

    def test_reserved_word_and_mixed_case_are_quoted(self):
        self.assertEqual(quote_ident("User"), '"User"')
        self.assertEqual(quote_ident("select"), '"select"')

    def test_embedded_double_quote_is_doubled(self):
        self.assertEqual(quote_ident('a"b'), '"a""b"')

    def test_injection_attempt_stays_one_identifier(self):
        out = quote_ident('x"; DROP TABLE users; --')
        self.assertEqual(out, '"x""; DROP TABLE users; --"')

    def test_empty_refused(self):
        with self.assertRaises(ReplicationError):
            quote_ident("")

    def test_nul_refused(self):
        with self.assertRaises(ReplicationError):
            quote_ident("a\x00b")

    def test_63_bytes_accepted_64_refused(self):
        self.assertEqual(quote_ident("a" * 63), '"' + "a" * 63 + '"')
        with self.assertRaises(ReplicationError):
            quote_ident("a" * 64)

    def test_multibyte_length_counted_in_bytes(self):
        # 22 characters of 3 bytes each = 66 bytes, over the limit.
        with self.assertRaises(ReplicationError):
            quote_ident("\u20ac" * 22)

    def test_non_string_refused(self):
        with self.assertRaises(ReplicationError):
            quote_ident(None)  # type: ignore[arg-type]


class QuoteLiteralTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(quote_literal("abc"), "'abc'")

    def test_single_quote_doubled(self):
        self.assertEqual(quote_literal("O'Brien"), "'O''Brien'")

    def test_injection_stays_literal(self):
        self.assertEqual(quote_literal("' OR '1'='1"), "''' OR ''1''=''1'")

    def test_nul_refused(self):
        with self.assertRaises(ReplicationError):
            quote_literal("a\x00")


class ConninfoTests(unittest.TestCase):
    def test_values_quoted_and_sorted(self):
        out = build_conninfo({"host": "h.example", "dbname": "app", "sslmode": "require"})
        self.assertEqual(out, "dbname='app' host='h.example' sslmode='require'")

    def test_quote_and_backslash_escaped_in_value(self):
        out = build_conninfo({"password": "p'a\\ss"})
        self.assertEqual(out, "password='p\\'a\\\\ss'")

    def test_unknown_key_refused(self):
        with self.assertRaises(ReplicationError):
            build_conninfo({"host": "h", "options": "-c x=y"})

    def test_nul_value_refused(self):
        with self.assertRaises(ReplicationError):
            build_conninfo({"host": "h\x00"})


class PublicationSqlTests(unittest.TestCase):
    def test_explicit_tables_only(self):
        sql = build_create_publication("ems_pub", ["users", "pi_devices"])
        self.assertEqual(sql, 'CREATE PUBLICATION "ems_pub" FOR TABLE "users", "pi_devices";')

    def test_for_all_tables_never_generated(self):
        sql = build_create_publication("p", ["t"])
        self.assertNotIn("ALL TABLES", sql)

    def test_empty_table_list_refused(self):
        with self.assertRaises(ReplicationError):
            build_create_publication("p", [])

    def test_hostile_table_name_is_one_identifier(self):
        sql = build_create_publication("p", ['t"; DROP SCHEMA public; --'])
        self.assertEqual(sql, 'CREATE PUBLICATION "p" FOR TABLE "t""; DROP SCHEMA public; --";')


class SubscriptionSqlTests(unittest.TestCase):
    def test_aiven_call_uses_literals_and_creates_slot(self):
        sql = build_create_subscription_aiven("sub1", "host='h'", "ems_pub", "ems_slot")
        self.assertEqual(
            sql,
            "SELECT aiven_extras.pg_create_subscription('sub1', 'host=''h''', "
            "'ems_pub', 'ems_slot', true, true);",
        )

    def test_disabled_flag(self):
        sql = build_create_subscription_aiven("s", "c", "p", "sl", enabled=False)
        self.assertTrue(sql.endswith("true, false);"))

    def test_drop_quotes_identifier(self):
        self.assertEqual(build_drop_subscription("sub1"), 'DROP SUBSCRIPTION IF EXISTS "sub1";')


class ReadOnlyQueryTests(unittest.TestCase):
    def test_slot_lag_query_is_read_only(self):
        sql = build_slot_lag_query().upper()
        for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE"):
            self.assertNotIn(word, sql)
        self.assertIn("PG_REPLICATION_SLOTS", sql)

    def test_stats_and_recovery_queries(self):
        self.assertIn("pg_stat_subscription", build_subscription_stats_query())
        self.assertEqual(build_is_in_recovery_query(), "SELECT pg_is_in_recovery();")


class SequenceSyncTests(unittest.TestCase):
    def test_setval_formats_value(self):
        self.assertEqual(build_sequence_sync("public.users_id_seq", 42),
                         "SELECT setval('public.users_id_seq', 42, true);")

    def test_negative_refused(self):
        with self.assertRaises(ReplicationError):
            build_sequence_sync("s", -1)

    def test_float_and_bool_refused(self):
        with self.assertRaises(ReplicationError):
            build_sequence_sync("s", 1.5)  # type: ignore[arg-type]
        with self.assertRaises(ReplicationError):
            build_sequence_sync("s", True)  # type: ignore[arg-type]


class SlotLossTests(unittest.TestCase):
    def test_missing_slot_is_lost(self):
        self.assertIs(classify_slot_loss(SlotObservation(SlotState.MISSING, None)),
                      SlotLossKind.LOST)

    def test_inactive_present_slot_is_not_lost(self):
        self.assertIs(classify_slot_loss(SlotObservation(SlotState.PRESENT_INACTIVE, 10)),
                      SlotLossKind.INACTIVE_NOT_LOST)

    def test_active_slot_ok(self):
        self.assertIs(classify_slot_loss(SlotObservation(SlotState.PRESENT_ACTIVE, 0)),
                      SlotLossKind.OK)

    def test_unknown_is_never_ok(self):
        self.assertIs(classify_slot_loss(SlotObservation(SlotState.UNKNOWN, None)),
                      SlotLossKind.UNOBSERVED)


class LagDecisionTests(unittest.TestCase):
    def test_within_warn_is_ok(self):
        self.assertIs(decide_lag(1.0, warn_s=5, ceiling_s=30).action, LagAction.OK)

    def test_between_warn_and_ceiling_warns(self):
        self.assertIs(decide_lag(10.0, warn_s=5, ceiling_s=30).action, LagAction.WARN)

    def test_equal_to_ceiling_is_warn_not_block(self):
        self.assertIs(decide_lag(30.0, warn_s=5, ceiling_s=30).action, LagAction.WARN)

    def test_above_ceiling_blocks(self):
        self.assertIs(decide_lag(30.1, warn_s=5, ceiling_s=30).action, LagAction.BLOCK)

    def test_unknown_blocks(self):
        self.assertIs(decide_lag(None, warn_s=5, ceiling_s=30).action, LagAction.BLOCK)

    def test_nan_and_infinity_block(self):
        for v in (float("nan"), float("inf"), float("-inf")):
            self.assertIs(decide_lag(v, warn_s=5, ceiling_s=30).action, LagAction.BLOCK)

    def test_negative_lag_blocks(self):
        self.assertIs(decide_lag(-1.0, warn_s=5, ceiling_s=30).action, LagAction.BLOCK)

    def test_bool_lag_blocks(self):
        self.assertIs(decide_lag(True, warn_s=5, ceiling_s=30).action, LagAction.BLOCK)  # type: ignore[arg-type]

    def test_invalid_thresholds_block_even_with_good_lag(self):
        self.assertIs(decide_lag(0.0, warn_s=float("nan"), ceiling_s=30).action, LagAction.BLOCK)
        self.assertIs(decide_lag(0.0, warn_s=5, ceiling_s=float("inf")).action, LagAction.BLOCK)
        self.assertIs(decide_lag(0.0, warn_s=5, ceiling_s=-1).action, LagAction.BLOCK)

    def test_warn_above_ceiling_is_config_error_block(self):
        d = decide_lag(0.0, warn_s=40, ceiling_s=30)
        self.assertIs(d.action, LagAction.BLOCK)
        self.assertIn("configuration", d.reason)


class FreshnessForPromotionTests(unittest.TestCase):
    def test_known_lag_within_ceiling_true(self):
        self.assertTrue(decide_freshness_for_promotion(3.0, ceiling_s=30))

    def test_equal_to_ceiling_true(self):
        self.assertTrue(decide_freshness_for_promotion(30.0, ceiling_s=30))

    def test_over_ceiling_false(self):
        self.assertFalse(decide_freshness_for_promotion(30.5, ceiling_s=30))

    def test_unknown_or_non_finite_false(self):
        self.assertFalse(decide_freshness_for_promotion(None, ceiling_s=30))
        self.assertFalse(decide_freshness_for_promotion(float("nan"), ceiling_s=30))
        self.assertFalse(decide_freshness_for_promotion(1.0, ceiling_s=float("nan")))


if __name__ == "__main__":
    unittest.main()
