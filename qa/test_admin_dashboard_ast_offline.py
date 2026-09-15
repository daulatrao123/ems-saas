"""Offline AST-extracted tests for list_society_devices/admin_dashboard.

Do not import backend.main at runtime startup.
"""

from __future__ import annotations

import ast
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest


MAIN_PATH = Path(__file__).resolve().parents[1] / "backend" / "main.py"


def load_functions(names: set[str]):
    source = MAIN_PATH.read_text()
    tree = ast.parse(source)
    picked = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in names:
            fn = copy.deepcopy(n)
            fn.decorator_list = []
            picked.append(fn)
    module = ast.Module(body=picked, type_ignores=[])
    namespace: dict = {
        "Request": object,
        "Depends": lambda fn: fn,
        "get_current_user": lambda: None,
        "require_society_access": lambda: None,
    }
    code = compile(module, str(MAIN_PATH), "exec")
    exec(code, namespace)
    return namespace


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self._rows = []
        self._one = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        params = params or ()
        q = " ".join(sql.lower().split())
        self._rows, self._one = [], None
        if q.startswith("select d.id, d.name, d.status, d.hardware_profile"):
            sid = int(params[0])
            self._rows = [r for r in self.state["list_rows"] if int(r["society_id"]) == sid]
            return
        if q.startswith("select name, location, plan, society_code, status, reset_day from societies"):
            self._one = self.state["society"]
            return
        if q.startswith("select id, name, firmware_version, feedback_hardware_installed from pi_devices"):
            self._rows = list(self.state["dashboard_devices"])
            return
        if q.startswith("select * from pi_state where device_id = %s"):
            did = params[0]
            self._one = self.state["pi_state"].get(did)
            return
        if q.startswith("select * from slot_configs where device_id = %s"):
            did = params[0]
            self._rows = list(self.state["slot_configs"].get(did, []))
            return
        if q.startswith("select * from slot_state where device_id = %s and slot = %s"):
            did, slot = params
            self._one = self.state["slot_state"].get((did, slot))
            return
        raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchall(self):
        return [dict(r) for r in self._rows]

    def fetchone(self):
        return dict(self._one) if isinstance(self._one, dict) else self._one


class FakeConn:
    def __init__(self, state):
        self.state = state

    def cursor(self, row_factory=None):
        return FakeCursor(self.state)

    def close(self):
        return None


class DashboardAstOfflineRegression(unittest.TestCase):
    def setUp(self):
        ns = load_functions({"is_pi_online", "list_society_devices", "admin_dashboard"})
        self.is_pi_online = ns["is_pi_online"]
        self.list_society_devices = ns["list_society_devices"]
        self.admin_dashboard = ns["admin_dashboard"]

        now = datetime.now(timezone.utc)
        self.state = {
            "list_rows": [
                {
                    "society_id": 1,
                    "id": "dev-1",
                    "name": "Pi A",
                    "status": "ASSIGNED",
                    "hardware_profile": "EMS-4CH-v1",
                    "feedback_hardware_installed": True,
                    "firmware_version": "6.5.0",
                    "key_id": "kid-1",
                    "last_seen": now - timedelta(days=5),
                    "last_sync": now - timedelta(seconds=30),
                    "config_state": "APPLIED",
                    "ota_state": "ACTIVE",
                    "disk_free_mb": 128.0,
                }
            ],
            "society": {"name": "S1", "location": "L", "plan": "P", "society_code": "C", "status": "active", "reset_day": 15},
            "dashboard_devices": [
                {"id": "dev-1", "name": "Pi A", "firmware_version": "6.5.0", "feedback_hardware_installed": False},
            ],
            "pi_state": {
                "dev-1": {
                    "active_slot": "A",
                    "hardware_fault": None,
                    "storage_health": {},
                    "last_sync": now - timedelta(seconds=20),
                    "uptime_seconds": 100,
                    "disk_free_mb": 10,
                }
            },
            "slot_configs": {
                "dev-1": [
                    {"slot": "A", "target_days": 3, "display_name": "Wing A", "disabled": False, "feedback_enabled": True},
                    {"slot": "D", "target_days": 3, "display_name": "Wing D", "disabled": True, "feedback_enabled": False},
                ]
            },
            "slot_state": {("dev-1", "A"): {"used_days": 1, "physical_toggle": "UNKNOWN", "toggle_input": "UNKNOWN"}},
        }

        # Inject globals required by extracted functions.
        shared = {
            "datetime": datetime,
            "timezone": timezone,
            "PI_ONLINE_THRESHOLD_SECONDS": 120,
            "DEFAULT_RESET_DAY": 15,
            "dict_row": object(),
            "Request": object,
            "Depends": lambda x: x,
            "HTTPException": type("HTTPException", (Exception,), {"__init__": lambda self, code, detail: setattr(self, "status_code", code) or setattr(self, "detail", detail)}),
            "get_current_user": lambda: None,
            "require_society_access": lambda: None,
            "slot_is_visible": lambda c, _st: c.get("disabled") is not True,
            "health_read_model": SimpleNamespace(dashboard_view=lambda *_: {"cpu": {"celsius": None}, "boot": {"count": None}}),
            "get_db": lambda: FakeConn(self.state),
            "is_pi_online": self.is_pi_online,
        }
        self.list_society_devices.__globals__.update(shared)
        self.admin_dashboard.__globals__.update(shared)

    def test_list_society_devices_uses_last_sync_for_online_and_exposes_feedback_flag(self):
        req = SimpleNamespace(query_params={"society_id": "1"})
        out = self.list_society_devices(req, user={"role": "society_admin", "society_id": 1})
        self.assertEqual(out["society_id"], 1)
        self.assertEqual(len(out["devices"]), 1)
        device = out["devices"][0]
        self.assertTrue(device["online"], "online must come from pi_state.last_sync recency")
        self.assertTrue(device["feedback_hardware_installed"])

    def test_admin_dashboard_exposes_device_feedback_and_slot_feedback_without_inventory_dependency(self):
        out = self.admin_dashboard("1", user={"role": "society_admin", "society_id": 1})
        self.assertEqual(out["society_id"], 1)
        self.assertEqual(out["reset_day"], 15)
        dev = out["devices"][0]
        self.assertFalse(dev["feedback_hardware_installed"], "device flag must be direct from pi_devices row")
        self.assertTrue(dev["connected"])
        self.assertTrue(dev["slots"]["A"]["feedback_enabled"], "slot feedback_enabled must be surfaced from slot_configs")
        self.assertTrue(dev["slots"]["D"]["disabled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
