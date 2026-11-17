"""Package/source + mocked preflight validation ONLY; never run installer/firmware.

bash -n parses shell text without executing it. All installation paths, mount
queries, dependency discovery and disk capacity are in-memory mocked below.

Endpoint tests (EndpointCredentialOrderingTests) execute the REAL provisioning_package
function from backend/main.py, extracted by AST, with fake database, credential and
audit collaborators. main.py is not imported, so psycopg and the other backend
dependencies are not required and no database, device, network or credential is used.
"""
import ast
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


def _bash():
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    return "bash"


# Regression tests for service-account validation (daulat123). These call the production
# validator on mutated unit text; they never touch a database, device, or credential.
GOOD_UNIT = (package.FIRMWARE_DIR / "ems-controller.service").read_text()


class ServiceAccountValidationTests(unittest.TestCase):
    """Documented behaviour of _validate_service_unit (backend/provisioning.py).

    - Directives are normalized (whitespace around '=' is ignored), so 'User = pi',
      'User =pi' and 'User= pi' are refused exactly like 'User=pi'.
    - Account directives are honoured only inside [Service]; one in any other section is refused.
    - Each of User, Group and SupplementaryGroups must appear exactly once in [Service]
      with its required value (daulat123, daulat123, 'gpio i2c').
    - Commented lines (# or ;) are ignored: a commented-out 'User=pi' is not a false positive.
    """

    def assertRejected(self, text):
        with self.assertRaises(RuntimeError):
            package._validate_service_unit(text)

    def test_correct_daulat123_configuration_passes(self):
        package._validate_service_unit(GOOD_UNIT)
        lines = [ln.strip() for ln in GOOD_UNIT.splitlines()]
        for line in package.REQUIRED_SERVICE_LINES:
            self.assertIn(line, lines)

    def test_user_pi_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("User=daulat123", "User=pi"))

    def test_user_with_spaces_around_equals_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("User=daulat123", "User = pi"))

    def test_user_space_before_equals_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("User=daulat123", "User =pi"))

    def test_user_space_after_equals_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("User=daulat123", "User= pi"))

    def test_group_pi_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("Group=daulat123", "Group=pi"))

    def test_group_with_spaces_around_equals_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("Group=daulat123", "Group = pi"))

    def test_group_space_after_equals_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("Group=daulat123", "Group= pi"))

    def test_supplementary_groups_with_spaces_around_equals_is_accepted_when_value_is_required(self):
        # Whitespace around '=' is normalized, so this is the same required semantic value.
        package._validate_service_unit(GOOD_UNIT.replace("SupplementaryGroups=gpio i2c", "SupplementaryGroups = gpio i2c"))

    def test_supplementary_groups_with_spaces_but_wrong_value_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("SupplementaryGroups=gpio i2c", "SupplementaryGroups = gpio"))

    def test_extra_legacy_user_line_is_rejected(self):
        self.assertRejected(GOOD_UNIT + "\nUser=pi\n")

    def test_duplicate_user_directive_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("User=daulat123", "User=daulat123\nUser=daulat123"))

    def test_duplicate_user_directive_with_legacy_value_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("User=daulat123", "User=daulat123\nUser = pi"))

    def test_duplicate_group_directive_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("Group=daulat123", "Group=daulat123\nGroup=daulat123"))

    def test_duplicate_supplementary_groups_directive_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("SupplementaryGroups=gpio i2c", "SupplementaryGroups=gpio i2c\nSupplementaryGroups=gpio i2c"))

    def test_account_directive_in_another_section_is_rejected(self):
        self.assertRejected(GOOD_UNIT + "\n[Install]\nUser=pi\n")

    def test_commented_legacy_directives_do_not_trigger_false_positive(self):
        package._validate_service_unit(GOOD_UNIT + "\n# User=pi\n; Group=pi\n")

    def test_missing_supplementary_groups_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("SupplementaryGroups=gpio i2c", ""))

    def test_wrong_supplementary_groups_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("SupplementaryGroups=gpio i2c", "SupplementaryGroups=gpio"))

    def test_wrong_working_directory_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("WorkingDirectory=/mnt/ems-data", "WorkingDirectory=/opt/ems/pi_firmware"))

    def test_wrong_gpio_factory_is_rejected(self):
        self.assertRejected(GOOD_UNIT.replace("Environment=GPIOZERO_PIN_FACTORY=lgpio", "Environment=GPIOZERO_PIN_FACTORY=rpigpio"))

    def test_modified_packaged_duplicate_is_rejected(self):
        bad = GOOD_UNIT.replace("User=daulat123", "User=pi").encode()
        with self.assertRaises(RuntimeError):
            package._validate_packaged_service_copies({
                "firmware/ems-controller.service": GOOD_UNIT.encode(),
                "systemd/ems-controller.service": bad,
            })

    def test_identical_packaged_copies_pass(self):
        data = GOOD_UNIT.encode()
        package._validate_packaged_service_copies({"firmware/ems-controller.service": data, "systemd/ems-controller.service": data})


class _FakeCursor:
    def __init__(self, log, device_row):
        self.log = log
        self.device_row = device_row

    def execute(self, sql, params=None):
        self.log.append(("execute", sql.split()[0]))

    def fetchone(self):
        return self.device_row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, log, device_row):
        self.log = log
        self.device_row = device_row

    def cursor(self, **kwargs):
        return _FakeCursor(self.log, self.device_row)

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))

    def close(self):
        self.log.append(("close",))


def _load_provisioning_endpoint():
    """Return the real provisioning_package function from backend/main.py without importing main.py."""
    main_path = Path(__file__).resolve().parents[1] / "backend" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "provisioning_package":
            node.decorator_list = []  # drop @app.get(...) which needs FastAPI
            module = ast.Module(body=[node], type_ignores=[])
            return module
    raise AssertionError("provisioning_package not found in backend/main.py")


DEVICE_ID = "11111111-1111-1111-1111-111111111111"


class EndpointCredentialOrderingTests(unittest.TestCase):
    """Run the real endpoint with fakes. Asserts validation, credential issuance, package build and commit order.

    No real database, credential, device, network or psycopg is used. The fake connection
    records commit, rollback and close; the fake issuer records every issuance.
    """

    def setUp(self):
        guard = patch.object(socket, "socket", side_effect=AssertionError("Network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        self.log = []
        self.issued = []
        self.device_row = {"name": "OFFLINE-PI", "society_id": 1}
        self.namespace = self._namespace()

    def _namespace(self):
        from types import SimpleNamespace as NS
        log = self.log
        issued = self.issued
        device_row = self.device_row
        test = self

        def fake_get_db():
            return _FakeConn(log, device_row)

        def fake_issue(cur, device_id, user, origin, **kwargs):
            log.append(("issue_device_credential", origin))
            issued.append(origin)
            return {"key_id": "FIXTURE-KEY", "api_key": "NOT-A-LIVE-CREDENTIAL"}

        def fake_log_audit(cur, user, society_id, action, details):
            log.append(("log_audit", action))

        def fake_require_uuid(value, what):
            return value

        def fake_require_role(*roles):
            return lambda: {"role": "super_admin", "username": "test"}

        def fake_depends(dep):
            return dep

        def fake_build(device_id, device_name, key_id, api_key, api_url):
            log.append(("build_provisioning_zip",))
            return b"ZIPBYTES"

        def fake_service_unit_sha256():
            return "0" * 64

        def fake_validate_runtime_sources():
            log.append(("validate_runtime_sources",))

        class _HTTPException(Exception):
            def __init__(self, status_code, detail=""):
                super().__init__(detail)
                self.status_code = status_code

        class _Response:
            def __init__(self, content=b"", media_type=None, headers=None):
                self.content = content

        class _Request:
            base_url = "https://example.invalid/"

        ns = {
            "dict_row": object(),  # stands in for psycopg.rows.dict_row; the fake cursor ignores it
            "os": SimpleNamespace(environ={"EMS_PUBLIC_API_URL": "https://example.invalid/api"}),
            "Request": _Request,
            "Depends": fake_depends,
            "require_role": fake_require_role,
            "require_uuid": fake_require_uuid,
            "get_db": fake_get_db,
            "issue_device_credential": fake_issue,
            "log_audit": fake_log_audit,
            "build_provisioning_zip": fake_build,
            "service_unit_sha256": fake_service_unit_sha256,
            "validate_runtime_sources": fake_validate_runtime_sources,
            "HTTPException": _HTTPException,
            "Response": _Response,
            "__builtins__": __builtins__,
        }
        return ns

    def _run_endpoint(self):
        module = _load_provisioning_endpoint()
        code = compile(module, "backend/main.py::provisioning_package", "exec")
        exec(code, self.namespace)
        fn = self.namespace["provisioning_package"]
        return fn(DEVICE_ID, _Request_stub(), user={"role": "super_admin"})

    def test_a_runtime_validation_failure_refuses_before_credential_and_commit(self):
        def refuse():
            self.log.append(("validate_runtime_sources",))
            raise RuntimeError("stale service unit")
        self.namespace["validate_runtime_sources"] = refuse
        with self.assertRaisesRegex(RuntimeError, "stale service unit"):
            self._run_endpoint()
        self.assertEqual(self.issued, [], "issue_device_credential must not be called")
        self.assertFalse([e for e in self.log if e[0] == "commit"], "commit must not be called")
        self.assertFalse([e for e in self.log if e[0] == "issue_device_credential"])
        self.assertFalse([e for e in self.log if e[0] == "build_provisioning_zip"])

    def test_b_package_source_validation_failure_refuses_before_credential_and_commit(self):
        # validate_runtime_sources is the shared preflight: source files and both service copies.
        def refuse_source():
            self.log.append(("validate_runtime_sources",))
            raise FileNotFoundError("missing source files")
        self.namespace["validate_runtime_sources"] = refuse_source
        with self.assertRaises(FileNotFoundError):
            self._run_endpoint()
        self.assertEqual(self.issued, [])
        self.assertFalse([e for e in self.log if e[0] == "commit"])
        self.assertFalse([e for e in self.log if e[0] == "issue_device_credential"])

    def test_b2_service_account_refusal_from_real_preflight_refuses_before_issuance(self):
        # Use the real shared preflight against a tampered unit to prove the endpoint refuses before issuance.
        def real_preflight_on_bad_unit():
            self.log.append(("validate_runtime_sources",))
            package._validate_service_unit(GOOD_UNIT.replace("User=daulat123", "User = pi"))
        self.namespace["validate_runtime_sources"] = real_preflight_on_bad_unit
        with self.assertRaises(RuntimeError):
            self._run_endpoint()
        self.assertEqual(self.issued, [])
        self.assertFalse([e for e in self.log if e[0] == "commit"])

    def test_c_zip_construction_failure_after_issuance_rolls_back_and_never_commits(self):
        def fail_build(*args, **kwargs):
            self.log.append(("build_provisioning_zip",))
            raise RuntimeError("zip assembly failed")
        self.namespace["build_provisioning_zip"] = fail_build
        with self.assertRaisesRegex(RuntimeError, "zip assembly failed"):
            self._run_endpoint()
        self.assertEqual(self.issued, ["provisioning"], "credential was issued before the post-issuance failure")
        self.assertIn(("rollback",), self.log, "rollback must run after post-issuance failure")
        self.assertFalse([e for e in self.log if e[0] == "commit"], "commit must not run")
        self.assertIn(("close",), self.log)

    def test_d_successful_flow_issues_builds_then_commits(self):
        response = self._run_endpoint()
        self.assertEqual(self.issued, ["provisioning"])
        order = [e[0] for e in self.log if e[0] in ("validate_runtime_sources", "issue_device_credential", "build_provisioning_zip", "commit")]
        self.assertEqual(order, ["validate_runtime_sources", "issue_device_credential", "build_provisioning_zip", "commit"])
        self.assertFalse([e for e in self.log if e[0] == "rollback"])
        self.assertEqual(response.content, b"ZIPBYTES")


def _Request_stub():
    return SimpleNamespace(base_url="https://example.invalid/")


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

    def test_day_based_firmware_is_packaged_and_integrity_checked(self):
        import tempfile
        z, root = self.archive, package.ROOT + "/"
        for name in ("firmware/energy/day_based.py", "firmware/energy/meter_manager.py", "firmware/energy/allocation.py", "firmware/ems_controller.py", "BUILD_INFO.json", "MANIFEST.sha256"):
            self.assertIn(root + name, z.namelist())
        info = json.loads(z.read(root + "BUILD_INFO.json"))
        self.assertIn("energy/day_based.py", info["runtime_files"])
        for name in package.FIRMWARE_FILES:
            self.assertIn(name, info["runtime_files"])
            self.assertEqual(z.read(root + "firmware/" + name), (package.FIRMWARE_DIR / name).read_bytes())
        for row in z.read(root + "MANIFEST.sha256").decode().splitlines():
            digest, name = row.split("  ", 1)
            self.assertEqual(hashlib.sha256(z.read(root + name)).hexdigest(), digest)
        with tempfile.TemporaryDirectory() as tmp:
            extracted = Path(tmp) / "pkg"
            data = Path(tmp) / "data"
            dest = Path(tmp) / "opt" / "firmware"
            data.mkdir()
            dest.parent.mkdir()
            z.extractall(extracted)
            fingerprint = preflight.check_installation(extracted / package.ROOT, data, dest, layout_only=True)
        self.assertEqual(fingerprint, info["runtime_sha256"])
        self.assertIn('"firmware/energy/day_based.py"', z.read(root + "install.sh").decode())

    def test_installer_syntax_safe_order_no_deletion_or_storage_bootstrap(self):
        shell = self.archive.read(package.ROOT + "/install.sh").decode()
        # Parser only: absolutely no bash execution of the script.
        parsed = subprocess.run([_bash(), "-n"], input=shell, text=True, capture_output=True, check=False)
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

    def test_installer_ownership_and_service_account_match_daulat123(self):
        shell = self.archive.read(package.ROOT + "/install.sh").decode()
        unit = self.archive.read(package.ROOT + "/systemd/ems-controller.service").decode()
        # The Pi has no 'pi' account: the staged firmware must be root:root, never root:pi.
        self.assertIn('chown -R root:root "$STAGE"', shell)
        self.assertNotIn("root:pi", shell)
        self.assertNotIn("getent passwd pi", shell)
        # The controller runs as the existing daulat123 account, not pi.
        self.assertIn("User=daulat123", unit.splitlines())
        self.assertIn("Group=daulat123", unit.splitlines())
        self.assertNotIn("User=pi", unit.splitlines())

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
        def key(path):
            return Path(str(path)).as_posix()
        def read_bytes(path): return files[key(path)]
        with patch.object(Path, "exists", lambda p: key(p) in dirs or key(p) in files), \
             patch.object(Path, "is_dir", lambda p: key(p) in dirs), \
             patch.object(Path, "is_symlink", lambda p: key(p) == symlink), \
             patch.object(Path, "read_bytes", read_bytes), \
             patch.object(Path, "read_text", lambda p: read_bytes(p).decode()), \
             patch.object(Path, "stat", lambda p: SimpleNamespace(st_size=len(files[key(p)]))), \
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
        for name in ("requests", "gpiozero", "lgpio", "minimalmodbus", "serial", "cryptography"):
            with self.subTest(name=name), self.assertRaisesRegex(preflight.PreflightError, "dependencies unavailable"):
                self.check_memory_layout(missing_module=name)
        with self.assertRaisesRegex(preflight.PreflightError, "Insufficient space"):
            self.check_memory_layout(free=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
