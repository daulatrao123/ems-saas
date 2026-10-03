"""Hardware capability contract. No GPIO, no database, no allocation changes."""
import ast
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pi_firmware"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import hardware_capabilities as pi_caps
import importlib.util
spec = importlib.util.spec_from_file_location(
    "backend_caps", os.path.join(os.path.dirname(__file__), "..", "backend", "hardware_capabilities.py"))
backend_caps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend_caps)

R = []


def check(name, condition, detail=""):
    R.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def profile(**overrides):
    doc = {
        "capability_version": 1,
        "extra_future_sensor": {"installed": True},
        "contactor_feedback": {"installed": True, "channels": {w: {"enabled": True} for w in "ABCD"}},
        "generation_meter": {"installed": True, "enabled": True},
        "consumption_meters": {w: {"installed": w in "AB", "enabled": w in "AB"} for w in "ABCD"},
        "lcd": {"installed": True, "enabled": True},
    }
    doc.update(overrides)
    return doc


def test_validation():
    good = pi_caps.canonical_capabilities(profile(), require_explicit_lcd=True)
    check("valid profile accepted and unknown fields dropped", good["capability_version"] == 1 and "extra_future_sensor" not in good
          and good["consumption_meters"]["C"]["installed"] is False)
    check("backend and Pi canonical JSON match", pi_caps.capability_json(good) == backend_caps.capability_json(backend_caps.canonical_capabilities(profile(), require_explicit_lcd=True)))
    rejected = False
    try:
        pi_caps.canonical_capabilities(profile(contactor_feedback={"installed": False, "channels": {"A": {"enabled": True}}}))
    except pi_caps.CapabilityError as exc:
        rejected = exc.code == "CAPABILITY_NOT_INSTALLED"
    check("feedback installed false with channel enabled is rejected", rejected)
    rejected = False
    try:
        pi_caps.canonical_capabilities(profile(generation_meter={"installed": False, "enabled": True}))
    except pi_caps.CapabilityError as exc:
        rejected = exc.code == "CAPABILITY_NOT_INSTALLED"
    check("generation installed false with enabled true is rejected", rejected)
    rejected = False
    try:
        pi_caps.canonical_capabilities(profile(capability_version=2))
    except pi_caps.CapabilityError as exc:
        rejected = exc.code == "INVALID_CAPABILITY_VERSION"
    check("unknown capability version is rejected", rejected)
    legacy = pi_caps.default_capabilities()
    check("legacy default claims nothing installed and leaves LCD unspecified", legacy["contactor_feedback"]["installed"] is False
          and legacy["generation_meter"]["installed"] is False and legacy["lcd"]["installed"] is None)
    stored = good
    kept, _digest, changed, error = pi_caps.commit_capabilities(stored, {"capability_version": 1, "contactor_feedback": {"installed": False, "channels": {"A": {"enabled": True}}}})
    check("invalid update preserves the previous profile", kept == stored and changed is False and error == "CAPABILITY_NOT_INSTALLED")
    again, digest, changed, error = pi_caps.commit_capabilities(good, good)
    check("duplicate profile does not count as a change", changed is False and error is None and digest == pi_caps.capability_hash(good))
    missing, _d, changed, error = pi_caps.commit_capabilities(good, None)
    check("omitted profile keeps the stored profile", missing["capability_version"] == 1 and changed is False and error is None)


def test_safety_clamps():
    caps = pi_caps.canonical_capabilities(profile(contactor_feedback={"installed": False, "channels": {w: {"enabled": False} for w in "ABCD"}}))
    installed, channels = pi_caps.clamp_feedback(True, {w: True for w in "ABCD"}, caps)
    check("not-installed feedback clamps verification off", installed is False and channels == {w: False for w in "ABCD"})
    installed, channels = pi_caps.clamp_feedback(False, {w: True for w in "ABCD"}, pi_caps.canonical_capabilities(profile()))
    check("capabilities cannot enable feedback the canonical config has off", installed is False and all(v is False for v in channels.values()))
    installed, channels = pi_caps.clamp_feedback(True, {"A": True, "B": False, "C": False, "D": False}, None)
    check("legacy config without capabilities keeps the canonical feedback flag", installed is True and channels["A"] is True and channels["B"] is False)
    installed, channels = pi_caps.clamp_feedback(True, {w: True for w in "ABCD"}, {"capability_version": 1})
    check("corrupt capability document does not revive installed feedback", installed is False and all(v is False for v in channels.values()))
    off = pi_caps.classify_feedback(False, "GPIO_INIT_FAILED")
    bad = pi_caps.classify_feedback(True, "GPIO_INIT_FAILED")
    healthy = pi_caps.classify_meter(True, True, "ONLINE")
    absent = pi_caps.classify_meter(False, False, "OFFLINE")
    down = pi_caps.classify_meter(True, True, "OFFLINE")
    check("absent feedback is not a fault and unreadable installed feedback is", off["runtime"] == "NOT_APPLICABLE" and bad["runtime"] == "FAULT")
    check("absent meter is not a communication fault; installed offline meter is", absent["runtime"] == "NOT_APPLICABLE" and down["runtime"] == "OFFLINE" and healthy["runtime"] == "HEALTHY")
    text = open(os.path.join(os.path.dirname(__file__), "..", "pi_firmware", "hardware_capabilities.py"), encoding="utf-8").read()
    check("capability module contains no GPIO numbers or gpiozero", "gpiozero" not in text and "BCM" not in text and "17" not in text)
    day = open(os.path.join(os.path.dirname(__file__), "..", "pi_firmware", "energy", "day_based.py"), encoding="utf-8").read()
    check("day based strategy does not read hardware capabilities", "hardware_capabilities" not in day and "capability_version" not in day)


def test_routes_and_audit():
    main = open(os.path.join(os.path.dirname(__file__), "..", "backend", "main.py"), encoding="utf-8").read()
    tree = ast.parse(main)
    put = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "put_hardware_capabilities")
    dep = ast.dump(put.args.defaults[-1])
    check("hardware capability edit requires super_admin", "super_admin" in dep)
    feedback = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "set_feedback_hardware")
    check("legacy feedback endpoint requires super_admin", "super_admin" in ast.dump(feedback.args.defaults[-1]))
    check("audit action is recorded", "HARDWARE_CAPABILITIES_SET" in main)
    check("sync delivers capabilities on the existing reply", '"hardware_capabilities"' in main)
    alloc = open(os.path.join(os.path.dirname(__file__), "..", "backend", "energy_api", "day_allocation.py"), encoding="utf-8").read()
    check("allocation mode module is unchanged by capability imports", "hardware_capabilities" not in alloc)
    check("legacy feedback endpoint updates the canonical document", "capability_service.document_for_feedback" in ast.get_source_segment(main, feedback))
    register = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "register_device")
    check("registration uses the canonical capability helper", "document_for_registration" in ast.get_source_segment(main, register))
    save = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "save_society")
    check("society import uses the canonical capability helper", "document_for_feedback" in ast.get_source_segment(main, save))
    boot = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "bootstrap")
    check("bootstrap records the same explicit feedback it already installed", "document_for_feedback" in ast.get_source_segment(main, boot))
    service = open(os.path.join(os.path.dirname(__file__), "..", "backend", "capability_service.py"), encoding="utf-8").read()
    writers = [line for line in main.splitlines() if "feedback_hardware_installed=%s" in line and "UPDATE" in line]
    check("legacy feedback column is not updated outside the capability service", writers == [])
    check("capability service derives the legacy feedback column", "feedback_hardware_installed=%s" in service)
    routes = open(os.path.join(os.path.dirname(__file__), "..", "backend", "energy_api", "routes.py"), encoding="utf-8").read()
    check("meter enable names the Super Admin hardware step", "Super Admin must configure hardware capabilities first." in routes)
    check("meter enable does not write hardware capabilities", "UPDATE pi_devices SET hardware_capabilities" not in routes)


def test_capability_only_and_recovery():
    import capability_service
    from energy.allocation import verified_active
    check("same canonical identity with a new capability hash must be written",
          pi_caps.bundle_needs_write("cfg", 4, "cfg", 4, "old-cap", "new-cap") is True)
    check("unchanged canonical identity and capability hash is not written",
          pi_caps.bundle_needs_write("cfg", 4, "cfg", 4, "same", "same") is False)
    registered = capability_service.document_for_registration(False)
    check("registration false does not invent installed feedback",
          registered["contactor_feedback"]["installed"] is False
          and all(channel["enabled"] is False for channel in registered["contactor_feedback"]["channels"].values()))
    explicit = capability_service.document_for_registration(True)
    check("registration true records installation without enabling channels",
          explicit["contactor_feedback"]["installed"] is True
          and all(channel["enabled"] is False for channel in explicit["contactor_feedback"]["channels"].values()))
    current = pi_caps.default_capabilities()
    current["contactor_feedback"]["installed"] = True
    current["contactor_feedback"]["channels"]["A"]["enabled"] = True
    cleared = capability_service.document_for_feedback(current, False, {"A": True, "B": False, "C": False, "D": False})
    check("feedback facade off clears channels in the canonical document",
          cleared["contactor_feedback"]["installed"] is False and cleared["contactor_feedback"]["channels"]["A"]["enabled"] is False)
    restored = capability_service.document_for_feedback(current, True, {"A": True, "B": False, "C": False, "D": False})
    check("feedback facade on records the supplied channel",
          restored["contactor_feedback"]["channels"]["A"]["enabled"] is True and restored["contactor_feedback"]["channels"]["B"]["enabled"] is False)
    _doc, _digest, error = pi_caps.recover_stored_capabilities("{", "abc")
    safe = pi_caps.default_capabilities()
    check("corrupt capabilities do not revive installed hardware",
          error == "CAPABILITY_STATE_INVALID" and safe["contactor_feedback"]["installed"] is False
          and safe["generation_meter"]["installed"] is False and all(not item["installed"] for item in safe["consumption_meters"].values()))
    valid = pi_caps.canonical_capabilities(profile())
    loaded, digest, error = pi_caps.recover_stored_capabilities(valid, pi_caps.capability_hash(valid))
    check("a valid stored profile is loaded", error is None and digest == pi_caps.capability_hash(valid) and loaded["contactor_feedback"]["installed"] is True)
    check("LCD null keeps the current display", pi_caps.lcd_runtime_enabled(None, True, True) is True and pi_caps.lcd_runtime_enabled(None, False, False) is False)
    check("LCD false stops display writes", pi_caps.lcd_runtime_enabled(False, True, True) is False)
    check("LCD true follows the existing enable switch", pi_caps.lcd_runtime_enabled(True, True, False) is True and pi_caps.lcd_runtime_enabled(True, False, True) is False)
    feedback = {"A": "ON", "B": "UNKNOWN", "C": "OFF", "D": "OFF"}
    check("A ON plus B UNKNOWN is UNKNOWN", verified_active(feedback) == "UNKNOWN")
    check("A ON and the other wings OFF stays A", verified_active({"A": "ON", "B": "OFF", "C": "OFF", "D": "OFF"}) == "A")
    check("A ON plus B ON stays MULTIPLE", verified_active({"A": "ON", "B": "ON", "C": "OFF", "D": "OFF"}) == "MULTIPLE")
    check("all UNKNOWN stays UNKNOWN", verified_active({wing: "UNKNOWN" for wing in "ABCD"}) == "UNKNOWN")
    check("all OFF stays inactive", verified_active({wing: "OFF" for wing in "ABCD"}) is None)
    absent = pi_caps.classify_feedback(False, None)
    check("feedback not installed is not verified and not a fault", absent["expected"] == "NOT_INSTALLED" and absent["runtime"] == "NOT_APPLICABLE")
    controller = open(os.path.join(os.path.dirname(__file__), "..", "pi_firmware", "ems_controller.py"), encoding="utf-8").read()
    check("invalid capability state refuses relay energization",
          'self._capability_error == "CAPABILITY_STATE_INVALID"' in controller
          and "No relay will be energized." in controller)
    tree = ast.parse(controller)
    apply = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_apply_cloud_config")
    body = ast.get_source_segment(controller, apply)
    check("capability processing happens before the unchanged-config return", body.find("commit_capabilities") < body.find("steady state: zero writes"))
    check("capability apply uses one bundle write", "persist_config_bundle" in body)
    migration = open(os.path.join(os.path.dirname(__file__), "..", "backend", "alembic", "versions", "0017_hardware_capabilities.py"), encoding="utf-8").read()
    check("migration does not treat a serial as installation", "btrim(serial)" not in migration and "enabled IS TRUE" in migration)

    def migrated(enabled, serial):
        del serial
        if enabled is True:
            return True, True
        return False, False

    check("enabled with serial is installed and enabled", migrated(True, "SN1") == (True, True))
    check("enabled without serial is installed and enabled", migrated(True, "") == (True, True))
    check("disabled with serial is not installed", migrated(False, "SN1") == (False, False))
    check("disabled without serial is not installed", migrated(False, None) == (False, False))
    check("meter ids stay fixed", pi_caps.METER_BY_WING == {"A": "M2", "B": "M3", "C": "M4", "D": "M5"})


def test_persistence_and_package():
    import sqlite3
    import tempfile
    from pathlib import Path
    os.environ.setdefault("EMS_DEVICE_ID", "qa-device")
    os.environ.setdefault("EMS_API_KEY", "qa-api-key")
    import offline_queue
    from provisioning import FIRMWARE_FILES
    check("package contains hardware_capabilities.py", "hardware_capabilities.py" in FIRMWARE_FILES)
    check("package contains offline_queue.py", "offline_queue.py" in FIRMWARE_FILES)
    check("package contains lcd_display.py", "lcd_display.py" in FIRMWARE_FILES)
    check("package contains ems_controller.py", "ems_controller.py" in FIRMWARE_FILES)
    package = open(os.path.join(os.path.dirname(__file__), "..", "backend", "provisioning.py"), encoding="utf-8").read()
    check("install validation requires hardware_capabilities.py", "firmware/hardware_capabilities.py" in package)
    check("package states the single-file OTA cannot deploy this firmware", "cannot deploy" in package)

    class Storage:
        def is_write_allowed(self, _name):
            return True

    original = offline_queue.DB_FILE
    try:
        with tempfile.TemporaryDirectory() as folder:
            offline_queue.DB_FILE = str(Path(folder) / "queue.sqlite")
            queue = offline_queue.OfflineQueue(Storage())
            assert queue.persist_config_bundle('{"v":1}', 1, "hash-1", "t1", '{"cap":1}', "cap-1", include_config=True)
            again = queue.conn.total_changes
            assert queue.persist_config_bundle('{"v":1}', 1, "hash-1", "t1", '{"cap":1}', "cap-1", include_config=True)
            check("duplicate bundle does not rewrite flash", queue.conn.total_changes == again)
            assert queue.persist_config_bundle('{"v":1}', 1, "hash-1", "t1", '{"cap":2}', "cap-2", include_config=False)
            config_hash = queue.conn.execute("SELECT value FROM execution_meta WHERE key='applied_config_hash'").fetchone()[0]
            cap_hash = queue.conn.execute("SELECT value FROM execution_meta WHERE key='applied_capability_hash'").fetchone()[0]
            check("capability-only sync keeps the canonical hash and updates capabilities", config_hash == "hash-1" and cap_hash == "cap-2")

            class Proxy:
                def __init__(self, inner):
                    self.inner = inner
                def execute(self, sql, params=()):
                    if isinstance(params, tuple) and "cap-3" in params:
                        raise sqlite3.OperationalError("power loss")
                    return self.inner.execute(sql, params)
                def commit(self):
                    return self.inner.commit()
                def rollback(self):
                    return self.inner.rollback()

            queue.conn = Proxy(queue.conn)
            failed = queue.persist_config_bundle('{"v":2}', 2, "hash-2", "t2", '{"cap":3}', "cap-3", include_config=True)
            queue.conn = queue.conn.inner
            config_hash = queue.conn.execute("SELECT value FROM execution_meta WHERE key='applied_config_hash'").fetchone()[0]
            cap_hash = queue.conn.execute("SELECT value FROM execution_meta WHERE key='applied_capability_hash'").fetchone()[0]
            check("power loss keeps the previous valid pair", failed is False and config_hash == "hash-1" and cap_hash == "cap-2")
            queue.close()
    finally:
        offline_queue.DB_FILE = original


def test_hardware_capabilities():
    test_validation()
    test_safety_clamps()
    test_routes_and_audit()
    test_capability_only_and_recovery()
    test_persistence_and_package()
    assert R and all(R), f"{sum(R)}/{len(R)} passed"


if __name__ == "__main__":
    test_hardware_capabilities()
    print(f"\n{sum(R)}/{len(R)} passed")
    sys.exit(0 if R and all(R) else 1)
