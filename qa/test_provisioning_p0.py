"""Package/source + mocked preflight validation ONLY; never run installer/firmware.

bash -n parses shell text without executing it. All installation paths, mount
queries, dependency discovery and disk capacity are in-memory mocked below.
"""
import hashlib
import io
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from backend import provisioning as package
from backend import provisioning_preflight as preflight


class ProvisioningP0Tests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket, "socket", side_effect=AssertionError("Network forbidden"))
        guard.start(); self.addCleanup(guard.stop)
        raw = package.build_provisioning_zip("11111111-1111-1111-1111-111111111111", "OFFLINE", "FIXTURE", "NOT-A-LIVE-CREDENTIAL", "https://example.invalid/api")
        self.archive = zipfile.ZipFile(io.BytesIO(raw))
        self.addCleanup(self.archive.close)

    def test_complete_artifact_manifest_sibling_units_and_fingerprint(self):
        z, root = self.archive, package.ROOT + "/"
        self.assertEqual(len(z.namelist()), len(set(z.namelist())))
        self.assertIsNone(z.testzip())
        manifest = z.read(root + "MANIFEST.sha256").decode().splitlines()
        covered = set()
        for row in manifest:
            digest, name = row.split("  ", 1)
            self.assertEqual(hashlib.sha256(z.read(root + name)).hexdigest(), digest)
            covered.add(root + name)
        self.assertEqual(covered, set(z.namelist()) - {root + "MANIFEST.sha256"})
        info = json.loads(z.read(root + "BUILD_INFO.json"))
        self.assertEqual(info["package_schema"], 2)
        self.assertEqual(set(info["runtime_files"]), set(package.FIRMWARE_FILES) | set(package.SYSTEMD_FILES))
        digest = hashlib.sha256()
        for name in sorted(info["runtime_files"]):
            data = z.read(root + "firmware/" + name)
            self.assertEqual(data, (package.FIRMWARE_DIR / name).read_bytes())
            digest.update(name.encode() + b"\0" + data)
        self.assertEqual(digest.hexdigest(), info["runtime_sha256"])
        for name in package.SYSTEMD_FILES:
            self.assertEqual(z.read(root + "firmware/" + name), z.read(root + "systemd/" + name))
        self.assertEqual(z.read(root + "tools/preflight.py"), package.PREFLIGHT_SOURCE.read_bytes())
        self.assertEqual(info["installer_sha256"], hashlib.sha256(package.INSTALL_SH.encode()).hexdigest())
        print("MOCKED_CURRENT_PACKAGE", json.dumps({"entries": len(z.namelist()), "runtime_files": len(info["runtime_files"]), "runtime_sha256": info["runtime_sha256"]}))

    def test_installer_syntax_safe_order_no_deletion_or_storage_bootstrap(self):
        shell = self.archive.read(package.ROOT + "/install.sh").decode()
        # Parser only: absolutely no bash execution of the script.
        parsed = subprocess.run(["bash", "-n"], input=shell, text=True, capture_output=True, check=False)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        first = shell.index('/usr/bin/python3 "$HERE/tools/preflight.py"')
        stage = shell.index('STAGE="$(mktemp')
        stop = shell.index('systemctl stop ems-controller.service', shell.index('INSTALL_STARTED=1'))
        second = shell.index('/usr/bin/python3 "$HERE/tools/preflight.py"', first + 1)
        replace = shell.index('mv "$FW_DST" "$BACKUP_DIR/firmware"')
        self.assertTrue(first < stage < stop < second < replace)
        self.assertNotIn('rm -', shell)
        self.assertNotIn('bash "$HERE/firmware/setup_pi.sh"', shell)
        self.assertIn('trap on_exit EXIT', shell)
        self.assertIn('"$INSTALL_SUCCESS" != "1"', shell)
        self.assertIn('systemctl stop ems-controller.service || true', shell)
        self.assertIn('INSTALL_SUCCESS=1', shell)
        self.assertLess(shell.index('mv "$STAGE" "$FW_DST"'), shell.index('"$HERE/ems-controller.env" \\\n  "$ENV_DST"'))

    def test_storage_policy_and_findmnt_fail_closed(self):
        preflight.check_storage("ext4", "uuid-a", "uuid-a")
        for fs, actual, configured in (("", "a", "a"), ("vfat", "a", "a"), ("ext4", "", "a"),
                                       ("ext4", "a", ""), ("ext4", "a", "b")):
            with self.subTest(fs=fs, actual=actual, configured=configured), self.assertRaises(preflight.PreflightError):
                preflight.check_storage(fs, actual, configured)
        for code, output in ((1, ""), (0, ""), (0, "a\nb")):
            with patch.object(preflight.subprocess, "run", return_value=SimpleNamespace(returncode=code, stdout=output)), self.assertRaises(preflight.PreflightError):
                preflight.findmnt_value("/FAKE/data", "UUID")
        with patch.object(preflight.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=" a\n")) as run:
            self.assertEqual(preflight.findmnt_value("/FAKE/data", "UUID", fstab=True), "a")
            self.assertIn("--fstab", run.call_args.args[0])
            self.assertIn("--mountpoint", run.call_args.args[0])

    def check_memory_layout(self, *, ota=None, symlink=None, missing_module=None, free=2**30, corrupt=False, data_exists=True):
        root = package.ROOT + "/"
        files = {"/pkg/" + name[len(root):]: self.archive.read(name) for name in self.archive.namelist()}
        if corrupt: files["/pkg/firmware/ems_controller.py"] += b"\n# corrupted fixture\n"
        dirs = {"/", "/opt", "/opt/ems"} | ({"/data"} if data_exists else set())
        if ota: dirs.add("/data/ota/" + ota)
        def read_bytes(path): return files[str(path)]
        with patch.object(Path, "exists", lambda p: str(p) in dirs or str(p) in files), \
             patch.object(Path, "is_dir", lambda p: str(p) in dirs), \
             patch.object(Path, "is_symlink", lambda p: str(p) == symlink), \
             patch.object(Path, "read_bytes", read_bytes), \
             patch.object(Path, "read_text", lambda p: read_bytes(p).decode()), \
             patch.object(Path, "stat", lambda p: SimpleNamespace(st_size=len(files[str(p)]))), \
             patch.object(preflight, "findmnt_value", side_effect=lambda p, field, **kw: "ext4" if field == "FSTYPE" else "uuid-a"), \
             patch.object(preflight.importlib.util, "find_spec", side_effect=lambda name: None if name == missing_module else object()), \
             patch.object(preflight.shutil, "disk_usage", return_value=SimpleNamespace(free=free)):
            return preflight.check_installation("/pkg", "/data", "/opt/ems/pi_firmware")

    def test_prepared_base_install_passes_without_any_real_io_or_dependency_imports(self):
        digest = self.check_memory_layout()
        self.assertEqual(len(digest), 64)

    def test_existing_ota_missing_storage_symlinks_and_bad_build_are_refused(self):
        for params in ({"ota": "active.json"}, {"ota": "meta.json"}, {"ota": "slot_a"}, {"ota": "slot_b"},
                       {"symlink": "/data/ota/active.json"}, {"symlink": "/opt/ems/pi_firmware"},
                       {"symlink": "/data"}, {"symlink": "/pkg/firmware/ems_controller.py"},
                       {"data_exists": False}, {"corrupt": True}):
            with self.subTest(params=params), self.assertRaises(preflight.PreflightError):
                self.check_memory_layout(**params)

    def test_missing_dependencies_or_low_space_cannot_proceed(self):
        for name in ("requests", "gpiozero", "lgpio", "minimalmodbus", "serial"):
            with self.subTest(name=name), self.assertRaisesRegex(preflight.PreflightError, "dependencies unavailable"):
                self.check_memory_layout(missing_module=name)
        with self.assertRaisesRegex(preflight.PreflightError, "Insufficient space"):
            self.check_memory_layout(free=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)