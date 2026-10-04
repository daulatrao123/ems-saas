"""Phase 4E: the provisioning ZIP contains the current startup tree.

Uses a fixture credential only. Never calls a provisioning endpoint.
"""
import ast
import io
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import provisioning as package
from backend import provisioning_preflight as preflight

DID = "11111111-1111-1111-1111-111111111111"
FIXTURE_KEY = "NOT-A-LIVE-CREDENTIAL"
STARTUP = (
    "ems_controller.py",
    "device_obs.py",
    "offline_queue.py",
    "lcd_display.py",
    "hardware_capabilities.py",
    "firmware_release.py",
    "ota_manager.py",
)
ENERGY = (
    "energy/__init__.py",
    "energy/allocation.py",
    "energy/attribution.py",
    "energy/day_based.py",
    "energy/energy_ledger.py",
    "energy/energy_state.py",
    "energy/meter_bus.py",
    "energy/meter_manager.py",
    "energy/meter_registry.py",
    "energy/modbus_meter.py",
    "energy/register_maps.py",
)


def _local_name(current, node, is_package):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    if node.level:
        parts = current.split(".")
        if not is_package:
            parts = parts[:-1]
        parts = parts[: len(parts) - (node.level - 1)]
        if node.module:
            parts.append(node.module)
        return [".".join(part for part in parts if part)]
    return [node.module] if node.module else []


def _exists(root, module):
    rel = Path(*module.split("."))
    return (root / rel.with_suffix(".py")).is_file() or (root / rel / "__init__.py").is_file()


def startup_modules(root):
    seen = set()
    stack = ["ems_controller"]
    while stack:
        name = stack.pop()
        if name in seen or not _exists(root, name):
            continue
        seen.add(name)
        rel = Path(*name.split("."))
        path = root / rel.with_suffix(".py")
        if not path.is_file():
            path = root / rel / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for imported in _local_name(name, node, path.name == "__init__.py"):
                top = imported.split(".")[0]
                if _exists(root, top):
                    stack.append(imported if _exists(root, imported) else top)
    return seen


class Phase4EProvisioningTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket, "socket", side_effect=AssertionError("Network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        raw = package.build_provisioning_zip(DID, "OFFLINE", "FIXTURE", FIXTURE_KEY, "https://example.invalid/api")
        self.raw = raw
        self.archive = zipfile.ZipFile(io.BytesIO(raw))
        self.addCleanup(self.archive.close)
        self.prefix = package.ROOT + "/"

    def test_zip_contains_startup_modules_and_installer_requirements(self):
        names = set(self.archive.namelist())
        for name in (*STARTUP, *ENERGY):
            self.assertIn(self.prefix + "firmware/" + name, names)
        shell = self.archive.read(self.prefix + "install.sh").decode()
        for name in (*STARTUP, *ENERGY):
            self.assertIn('"firmware/' + name + '"', shell)
        self.assertIn("multi-file-v1", self.archive.read(self.prefix + "firmware/firmware_release.py").decode())

    def test_packaged_tree_imports_startup_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            extracted = Path(tmp) / "pkg"
            self.archive.extractall(extracted)
            firmware = extracted / package.ROOT / "firmware"
            modules = startup_modules(firmware)
            for name in ("device_obs", "offline_queue", "firmware_release", "ota_manager", "energy", "hardware_capabilities", "lcd_display"):
                self.assertIn(name, modules)
            env = os_env(firmware)
            probe = subprocess.run(
                [sys.executable, "-c", "import device_obs, offline_queue, firmware_release, ota_manager; assert firmware_release.AGENT == 'multi-file-v1'"],
                cwd=str(firmware),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)

    def test_preflight_dependencies_missing_file_and_ota_refusal(self):
        self.assertIn("cryptography", preflight.REQUIRED_PYTHON_MODULES)
        with tempfile.TemporaryDirectory() as tmp:
            extracted = Path(tmp) / "pkg"
            data = Path(tmp) / "data"
            dest = Path(tmp) / "opt" / "firmware"
            data.mkdir()
            dest.parent.mkdir()
            self.archive.extractall(extracted)
            package_dir = extracted / package.ROOT
            with patch.object(preflight.importlib.util, "find_spec", side_effect=lambda name: None if name == "cryptography" else object()), \
                 patch.object(preflight, "findmnt_value", side_effect=lambda p, field, **kw: "ext4" if field == "FSTYPE" else "uuid-a"), \
                 patch.object(preflight.shutil, "disk_usage", return_value=type("U", (), {"free": 2**30})()):
                with self.assertRaisesRegex(preflight.PreflightError, "cryptography"):
                    preflight.check_installation(package_dir, data, dest)
            (package_dir / "firmware" / "device_obs.py").unlink()
            with self.assertRaisesRegex(preflight.PreflightError, "device_obs.py"):
                preflight.check_installation(package_dir, data, dest, layout_only=True)
        for entry in ("active.json", "meta.json", "slot_a", "slot_b"):
            with tempfile.TemporaryDirectory() as tmp:
                extracted = Path(tmp) / "pkg"
                data = Path(tmp) / "data"
                (data / "ota" / entry).mkdir(parents=True)
                self.archive.extractall(extracted)
                with self.assertRaisesRegex(preflight.PreflightError, "Existing OTA layout detected"):
                    preflight.check_installation(extracted / package.ROOT, data, Path(tmp) / "fw", layout_only=True)

    def test_fixture_credential_is_the_installed_env_and_rotation_stays_transactional(self):
        env = self.archive.read(self.prefix + "ems-controller.env").decode()
        self.assertIn("EMS_API_KEY=" + FIXTURE_KEY, env)
        self.assertIn("EMS_DEVICE_ID=" + DID, env)
        shell = self.archive.read(self.prefix + "install.sh").decode()
        self.assertLess(shell.index('"$HERE/ems-controller.env"'), shell.index('"$ENV_DST"'))
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        start = source.index("def provisioning_package")
        end = source.index("@app.post", start + 1)
        body = source[start:end]
        self.assertLess(body.index("validate_runtime_sources()"), body.index("issue_device_credential("))
        self.assertLess(body.index("issue_device_credential("), body.index("build_provisioning_zip("))
        self.assertLess(body.index("build_provisioning_zip("), body.index("conn.commit()"))
        self.assertIn("conn.rollback()", body)
        credential = source[source.index("def issue_device_credential"):source.index("def revoke_device_credentials")]
        self.assertIn("status='revoked'", credential)
        self.assertIn("status='active'", credential)
        self.assertNotIn("ems-saas", self.raw.decode("latin1"))


def os_env(firmware):
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = str(firmware)
    env["EMS_DEVICE_ID"] = "qa-device"
    env["EMS_API_KEY"] = "qa-api-key"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


if __name__ == "__main__":
    unittest.main()
