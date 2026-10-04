"""New-Pi preflight reports installer prerequisites and does not inspect a Pi."""
import inspect
import tempfile
import unittest
from pathlib import Path

from backend import new_pi_preflight as preflight
from backend import provisioning
from backend import provisioning_preflight
from backend.provisioning_preflight import PreflightError

IDS = (
    "raspberry-pi-os",
    "data-mount",
    "filesystem",
    "python-modules",
    "application-paths",
    "ota-layout",
    "firmware-runtime",
    "credential",
)


def _by_id(report):
    return {item["id"]: item for item in report["items"]}


class NewPiPreflightTests(unittest.TestCase):
    def test_checklist_matches_the_current_installer(self):
        report = preflight.new_pi_preflight_report()
        items = _by_id(report)
        self.assertEqual(list(items), list(IDS))
        self.assertFalse(report["device_inspected"])
        self.assertFalse(report["compliant"])
        self.assertFalse(report["cloud_record"]["used_as_verification"])
        self.assertIn("Raspberry Pi OS", provisioning.INSTALL_SH)
        self.assertIn('uname -s', provisioning.INSTALL_SH)
        self.assertIn("Raspberry Pi OS", items["raspberry-pi-os"]["detail"])
        self.assertIn(preflight.DATA_MOUNT, provisioning.INSTALL_SH)
        self.assertEqual(items["data-mount"]["path"], "/mnt/ems-data")
        self.assertIn("WorkingDirectory=/mnt/ems-data", items["data-mount"]["detail"])
        self.assertEqual(items["filesystem"]["filesystem"], "ext4")
        provisioning_preflight.check_storage("ext4", "same-uuid", "same-uuid")
        with self.assertRaises(PreflightError):
            provisioning_preflight.check_storage("vfat", "same-uuid", "same-uuid")
        with self.assertRaises(PreflightError):
            provisioning_preflight.check_storage("ext4", "mount-uuid", "fstab-uuid")
        self.assertEqual(tuple(items["python-modules"]["modules"]), provisioning_preflight.REQUIRED_PYTHON_MODULES)
        for path in items["application-paths"]["paths"]:
            self.assertIn(path, provisioning.INSTALL_SH)
        self.assertEqual(tuple(items["application-paths"]["units"]), provisioning.SYSTEMD_FILES)
        self.assertEqual(tuple(items["firmware-runtime"]["runtime_files"]), provisioning.FIRMWARE_FILES)
        self.assertIn("16 * 1024 * 1024", inspect.getsource(provisioning_preflight.check_installation))
        self.assertIn("16 MiB", items["firmware-runtime"]["detail"])
        self.assertIn("EMS_API_KEY", items["credential"]["detail"])
        self.assertNotIn("=", items["credential"]["detail"].split("EMS_API_KEY", 1)[1][:12])

    def test_ota_names_are_the_live_preflight_conflicts(self):
        names = preflight.ota_conflict_names()
        self.assertEqual(tuple(_by_id(preflight.new_pi_preflight_report())["ota-layout"]["conflicts"]), names)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ota = root / "ota"
            ota.mkdir()
            for name in names:
                target = ota / name
                if name.startswith("slot_"):
                    target.mkdir()
                else:
                    target.write_bytes(b"x")
            (ota / "not-a-conflict.txt").write_text("leave me", encoding="utf-8")
            self.assertEqual(set(provisioning_preflight.ota_conflicts(root)), set(names))
            self.assertEqual(provisioning_preflight.ota_conflicts(root / "missing"), [])

    def test_cloud_record_does_not_verify_the_pi(self):
        report = preflight.new_pi_preflight_report({
            "online": True,
            "storage_state": "OK",
            "ota_state": "IDLE",
            "credential_state": "active",
            "last_sync": "2026-10-04T00:00:00+00:00",
            "firmware_version": "9.9.9",
        })
        self.assertFalse(report["device_inspected"])
        self.assertFalse(report["compliant"])
        self.assertFalse(report["cloud_record"]["used_as_verification"])
        for item in report["items"]:
            self.assertEqual(item["expectation"], "EXPECTED PREREQUISITE")
            self.assertEqual(item["verification"], "NOT VERIFIED")
            self.assertNotEqual(item["verification"], "VERIFIED BY DEVICE")
        blob = repr(report)
        self.assertNotIn("NOT-A-LIVE-CREDENTIAL", blob)
        self.assertNotIn("EMS_API_KEY=", blob)

    def test_route_is_read_only_and_separate_from_the_zip(self):
        source = Path(provisioning.__file__).read_text(encoding="utf-8")
        main = (Path(provisioning.__file__).parent / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/super-admin/provisioning-preflight")', main)
        self.assertIn("return new_pi_preflight_report()", main)
        self.assertNotIn("new_pi_preflight", source)
        self.assertNotIn("provisioning-preflight", source)
        packaged = (Path(provisioning.__file__).parent / "provisioning_preflight.py").read_text(encoding="utf-8")
        self.assertNotIn("EXPECTED PREREQUISITE", packaged)
        self.assertNotIn("new_pi_preflight", packaged)


if __name__ == "__main__":
    unittest.main()
