"""Simulated multi-file OTA. This does not boot a Raspberry Pi or switch a relay."""
import base64
import hashlib
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi_firmware"))
sys.path.insert(0, str(ROOT / "backend"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import firmware_release as rel


def tree_bytes():
    files = {}
    for path in rel.REQUIRED:
        files[path] = (ROOT / "pi_firmware" / path).read_bytes()
    return files


def signed_manifest(version="7.1.0", files=None, profile="EMS-4CH-v1"):
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    body = rel.build_release(files or tree_bytes(), version, profile, "k-test", "pending", "7.1.0", "Simulated release")
    body["signature"] = base64.b64encode(key.sign(rel.signed_document(body))).decode()
    return body, public


class ReleaseContract(unittest.TestCase):
    def test_backend_copy_matches_pi_contract(self):
        pi = (ROOT / "pi_firmware" / "firmware_release.py").read_bytes()
        backend = (ROOT / "backend" / "firmware_release.py").read_bytes()
        self.assertEqual(pi, backend)

    def test_path_traversal_and_missing_file_are_rejected(self):
        for path in ("../ems_controller.py", "/tmp/x", "C:/ems_controller.py", "energy/../../ota_manager.py"):
            with self.assertRaises(rel.ReleaseError):
                rel.normalize_path(path)
        files = tree_bytes()
        del files["gpio_manager.py"]
        with self.assertRaises(rel.ReleaseError) as caught:
            rel.build_release(files, "7.1.0", "EMS-4CH-v1", "k", "sig")
        self.assertEqual(caught.exception.code, "MISSING_REQUIRED_FILE")

    def test_file_and_package_hash_and_signature(self):
        manifest, public = signed_manifest()
        os.environ["EMS_FIRMWARE_TRUSTED_KEYS"] = json.dumps({"k-test": public})

        def verify(key_id, signature, payload):
            self.assertEqual(key_id, "k-test")
            key = Ed25519PrivateKey.generate()  # wrong key must not be used
            del key
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.exceptions import InvalidSignature
            try:
                Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(base64.b64decode(signature), payload)
                return True
            except InvalidSignature:
                return False

        checked = rel.verify_release(manifest, "7.0.0", "EMS-4CH-v1", verify)
        self.assertEqual(checked["version"], "7.1.0")
        broken = json.loads(json.dumps(manifest))
        broken["files"][0]["content_b64"] = base64.b64encode(b"tamper").decode()
        with self.assertRaises(rel.ReleaseError) as mismatch:
            rel.verify_release(broken, "7.0.0", "EMS-4CH-v1", verify)
        self.assertEqual(mismatch.exception.code, "FILE_SHA256_MISMATCH")
        shifted = json.loads(json.dumps(manifest))
        shifted["package_sha256"] = "0" * 64
        with self.assertRaises(rel.ReleaseError) as package:
            rel.verify_release(shifted, "7.0.0", "EMS-4CH-v1", verify)
        self.assertEqual(package.exception.code, "PACKAGE_SHA256_MISMATCH")
        shifted = json.loads(json.dumps(manifest))
        shifted["signature"] = base64.b64encode(b"x" * 64).decode()
        with self.assertRaises(rel.ReleaseError) as signature:
            rel.verify_release(shifted, "7.0.0", "EMS-4CH-v1", verify)
        self.assertEqual(signature.exception.code, "SIGNATURE_INVALID")

    def test_install_gate(self):
        device = {"firmware_version": "7.0.0", "ota_agent": None, "hardware_profile": "EMS-4CH-v1", "credential_state": "active"}
        approved = {"status": "APPROVED", "version": "7.1.0", "hardware_profile": "EMS-4CH-v1", "signature": "sig", "key_id": "k", "notes": "notes"}
        self.assertEqual(rel.install_decision(approved, device, None)["reason"], "BOOTSTRAP_REQUIRED")
        self.assertIn("Bootstrap required", rel.install_decision(approved, device, None)["bootstrap_message"])
        device["ota_agent"] = rel.AGENT
        self.assertTrue(rel.install_decision(approved, device, None)["install_allowed"])
        for status, reason in (("DRAFT", "RELEASE_NOT_APPROVED"), ("REVOKED", "RELEASE_REVOKED")):
            blocked = dict(approved, status=status)
            self.assertEqual(rel.install_decision(blocked, device, None)["reason"], reason)
            self.assertFalse(rel.install_decision(blocked, device, None)["install_allowed"])
        self.assertEqual(rel.install_decision(dict(approved, hardware_profile="OTHER"), device, None)["reason"], "HARDWARE_PROFILE_MISMATCH")
        self.assertFalse(rel.install_decision(approved, dict(device, credential_state="none"), None)["install_allowed"])
        busy = rel.install_decision(approved, device, {"id": "op-1", "state": "INSTALLING", "firmware_version": "7.1.0"})
        self.assertFalse(busy["install_allowed"])
        self.assertEqual(busy["operation_id"], "op-1")


class SimulatedSlot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["EMS_DEVICE_ID"] = "00000000-0000-0000-0000-000000000001"
        os.environ["EMS_API_KEY"] = "simulated-ota-test-key"
        os.environ["EMS_OTA_ROOT"] = self.tmp.name
        self.manifest, public = signed_manifest()
        self.manifest["operation_id"] = "op-sim-1"
        os.environ["EMS_FIRMWARE_TRUSTED_KEYS"] = json.dumps({"k-test": public})
        import ota_manager
        self.ota = importlib.reload(ota_manager)
        root = Path(self.tmp.name)
        (root / "slot_a").mkdir()
        (root / "slot_a" / "ems_controller.py").write_text("previous-controller", encoding="utf-8")
        (root / "preserved-state.txt").write_text("keep-me", encoding="utf-8")
        self.ota._atomic_json(self.ota.ACTIVE, {"active": "a", "version": "7.0.0", "boot_confirmed": True, "previous_version": "7.0.0"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_stage_keeps_active_slot_and_is_idempotent(self):
        version = self.ota.stage_signed_firmware(self.manifest, "7.0.0", "EMS-4CH-v1")
        self.assertEqual(version, "7.1.0")
        root = Path(self.tmp.name)
        self.assertEqual((root / "slot_a" / "ems_controller.py").read_text(encoding="utf-8"), "previous-controller")
        self.assertEqual((root / "preserved-state.txt").read_text(encoding="utf-8"), "keep-me")
        staged = (root / "slot_b" / "gpio_manager.py").read_bytes()
        self.assertEqual(self.ota.stage_signed_firmware(self.manifest, "7.0.0", "EMS-4CH-v1"), "7.1.0")
        self.assertEqual((root / "slot_b" / "gpio_manager.py").read_bytes(), staged)
        self.assertEqual(self.ota.pending_info()["state"], "STAGED")

    def test_failed_health_restores_previous_slot(self):
        self.ota.stage_signed_firmware(self.manifest, "7.0.0", "EMS-4CH-v1")
        self.assertTrue(self.ota.activate_staged("7.0.0"))
        slot = Path(self.tmp.name) / "slot_b" / "hardware_capabilities.py"
        slot.unlink()
        ok, reason = self.ota.release_health("7.1.0")
        self.assertFalse(ok)
        self.assertEqual(reason, "MISSING_REQUIRED_FILE")
        self.assertTrue(self.ota.fail_health(reason))
        self.assertEqual(self.ota.active_info()["active"], "a")
        self.assertEqual(self.ota.pending_info()["state"], "ROLLED_BACK")
        self.assertEqual((Path(self.tmp.name) / "slot_a" / "ems_controller.py").read_text(encoding="utf-8"), "previous-controller")
        self.assertEqual((Path(self.tmp.name) / "preserved-state.txt").read_text(encoding="utf-8"), "keep-me")

    def test_successful_health_confirms_without_removing_previous_slot(self):
        self.ota.stage_signed_firmware(self.manifest, "7.0.0", "EMS-4CH-v1")
        self.assertTrue(self.ota.activate_staged("7.0.0"))
        ok, reason = self.ota.release_health("7.1.0")
        self.assertTrue(ok, reason)
        self.assertTrue(self.ota.confirm_boot())
        self.assertTrue(self.ota.active_info()["boot_confirmed"])
        self.assertEqual((Path(self.tmp.name) / "slot_a" / "ems_controller.py").read_text(encoding="utf-8"), "previous-controller")


class UnchangedSafety(unittest.TestCase):
    def test_allocation_and_gpio_markers_remain(self):
        allocation = (ROOT / "pi_firmware" / "energy" / "allocation.py").read_text(encoding="utf-8")
        gpio = (ROOT / "pi_firmware" / "gpio_manager.py").read_text(encoding="utf-8")
        self.assertIn("Any required wing that is not ON or OFF -> 'UNKNOWN'", allocation)
        self.assertIn("active_high=not relay_active_low", gpio)


if __name__ == "__main__":
    unittest.main()
