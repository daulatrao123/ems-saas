"""Phase 2B: an empty command queue must not open a SQLite write transaction."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi_firmware"))
os.environ.setdefault("EMS_DEVICE_ID", "qa-device")
os.environ.setdefault("EMS_API_KEY", "qa-api-key")
os.environ.setdefault("EMS_API_BASE_URL", "http://127.0.0.1:8000/api")

import device_obs
import offline_queue


class Storage:
    def is_write_allowed(self, _category):
        return True


class IdleClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = offline_queue.DB_FILE
        offline_queue.DB_FILE = str(Path(self.tmp.name) / "queue.sqlite")
        self.queue = offline_queue.OfflineQueue(Storage())
        self.sql = []
        self.queue.conn.set_trace_callback(self.sql.append)
        for name in ("claim_next_total", "claim_next_empty", "claim_next_command", "claim_next_commit", "claim_next_tx_started", "claim_next_rollback"):
            device_obs._counts[name] = 0

    def tearDown(self):
        self.queue.close()
        offline_queue.DB_FILE = self.original_db
        self.tmp.cleanup()

    def _rows(self):
        return self.queue.conn.execute(
            "SELECT id, status, last_error, attempt_count FROM commands ORDER BY id"
        ).fetchall()

    def test_empty_queue_is_read_only(self):
        before = self._rows()
        self.sql.clear()
        self.assertIsNone(self.queue.claim_next())
        self.assertFalse(self.queue.conn.in_transaction)
        self.assertNotIn("BEGIN", "\n".join(self.sql).upper())
        self.assertNotIn("UPDATE", "\n".join(self.sql).upper())
        self.assertEqual(self._rows(), before)
        self.assertEqual(device_obs._counts["claim_next_empty"], 1)
        self.assertEqual(device_obs._counts["claim_next_commit"], 0)
        self.assertEqual(device_obs._counts["claim_next_tx_started"], 0)

    def test_real_claim_stays_durable_and_is_not_repeated(self):
        self.assertTrue(self.queue.add_command(
            "cmd-1", "A", "ACTIVATE", "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", sequence_no=7,
        ))
        self.sql.clear()
        self.assertEqual(self.queue.claim_next(), ("cmd-1", "A", "ACTIVATE"))
        self.assertIn("BEGIN IMMEDIATE", "\n".join(self.sql).upper())
        self.assertFalse(self.queue.conn.in_transaction)
        status, attempts = self.queue.conn.execute(
            "SELECT status, attempt_count FROM commands WHERE id='cmd-1'"
        ).fetchone()
        self.assertEqual(status, "EXECUTING")
        self.assertEqual(attempts, 1)
        sequence = self.queue.conn.execute(
            "SELECT value FROM execution_meta WHERE key='last_executed_sequence'"
        ).fetchone()
        self.assertEqual(sequence[0], "7")
        self.sql.clear()
        self.assertIsNone(self.queue.claim_next())
        self.assertNotIn("BEGIN", "\n".join(self.sql).upper())

    def test_expired_delivered_command_is_not_executed(self):
        self.assertTrue(self.queue.add_command(
            "old", "A", "ACTIVATE", "2020-01-01T00:00:00+00:00", "2020-01-01T00:05:00+00:00", sequence_no=1,
        ))
        self.assertTrue(self.queue.add_command(
            "live", "B", "DEACTIVATE", "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", sequence_no=2,
        ))
        self.sql.clear()
        self.assertEqual(self.queue.claim_next(), ("live", "B", "DEACTIVATE"))
        self.assertIn("BEGIN IMMEDIATE", "\n".join(self.sql).upper())
        expired = self.queue.conn.execute("SELECT status, last_error FROM commands WHERE id='old'").fetchone()
        self.assertEqual(expired, ("EXPIRED", "COMMAND_EXPIRED"))
        self.assertIsNone(self.queue.claim_next())

    def test_two_claimers_cannot_take_the_same_command(self):
        self.assertTrue(self.queue.add_command(
            "once", "C", "ACTIVATE", "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", sequence_no=3,
        ))
        results = []

        def claim():
            results.append(self.queue.claim_next())

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results, key=lambda item: item is None), [("once", "C", "ACTIVATE"), None])
        self.assertEqual(self.queue.conn.execute("SELECT status FROM commands WHERE id='once'").fetchone()[0], "EXECUTING")

    def test_claim_error_still_rolls_back(self):
        self.assertTrue(self.queue.add_command(
            "cmd-r", "A", "ACTIVATE", "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", sequence_no=4,
        ))
        self.queue.conn.execute(
            """CREATE TRIGGER fail_claim BEFORE UPDATE ON commands
               WHEN NEW.status='EXECUTING'
               BEGIN SELECT RAISE(ABORT, 'injected claim failure'); END"""
        )
        self.assertIsNone(self.queue.claim_next())
        self.assertEqual(self.queue.conn.execute("SELECT status FROM commands WHERE id='cmd-r'").fetchone()[0], "DELIVERED")
        self.assertFalse(self.queue.conn.in_transaction)
        self.assertEqual(device_obs._counts["claim_next_rollback"], 1)


if __name__ == "__main__":
    unittest.main()
