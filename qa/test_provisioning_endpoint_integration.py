"""Integration tests that import the REAL FastAPI application (backend/main.py) and exercise the
actual POST /api/super-admin/devices/{device_id}/provisioning-package route through TestClient.

Isolation:
- psycopg / psycopg_pool are not installed here. They are replaced with MagicMock modules in sys.modules
  before the import. Nothing connects to a database.
- DATABASE_URL is a fake, unreachable in-memory value. Nothing connects at import or request time.
- get_db() is replaced with a fake connection that records commit / rollback / close.
- issue_device_credential and build_provisioning_zip are patched in the real module namespace.
- Authentication is bypassed with FastAPI dependency_overrides (the real require_role dependency
  is still wired into the route; only its result is supplied).
- No database or credential is reached: every DB touchpoint is a fake; issuance and ZIP build are mocked.
"""
import hashlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

DEVICE_ID = "11111111-1111-1111-1111-111111111111"
FAKE_DB_URL = "postgresql://fake-user:fake-pass@127.0.0.1:1/fake-db"  # unreachable, never used


def _install_stubs():
    """Stub only the missing optional DB driver modules, once, before importing the app."""
    for name in ("psycopg", "psycopg.types", "psycopg.types.json", "psycopg.rows", "psycopg_pool"):
        sys.modules.setdefault(name, MagicMock())
    sys.modules["psycopg.types.json"].Json = lambda value: value
    sys.modules["psycopg.rows"].dict_row = None


def _load_app():
    _install_stubs()
    os.environ.setdefault("DATABASE_URL", FAKE_DB_URL)
    os.environ.setdefault("SECRET_KEY", "test-only-secret-not-real-0123456789abcdef")
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import main  # the real application module
    return main


class _FakeCursor:
    def __init__(self, log, device_row):
        self.log = log
        self.device_row = device_row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append(("execute", " ".join(sql.split()[:2])))

    def fetchone(self):
        return self.device_row


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


class ProvisioningEndpointIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _load_app()
        from fastapi.testclient import TestClient
        cls.TestClient = TestClient

    def setUp(self):
        self.log = []
        self.device_row = {"name": "OFFLINE-PI", "society_id": 1}
        main = self.main

        def fake_get_db():
            return _FakeConn(self.log, self.device_row)

        def fake_issue(cur, device_id, user, origin, **kwargs):
            self.log.append(("issue_device_credential", origin))
            return {"device_id": device_id, "key_id": "FIXTURE-KEY", "api_key": "[redacted:secret]",
                    "revoked_key_ids": []}

        def fake_build(device_id, name, key_id, api_key, api_url):
            self.log.append(("build_provisioning_zip", key_id))
            return b"ZIPBYTES"

        def fake_audit(cur, user, society_id, action, details):
            self.log.append(("log_audit", action))

        self.validate = MagicMock(side_effect=lambda: self.log.append(("validate_runtime_sources",)))

        patches = [
            patch.object(main, "get_db", fake_get_db),
            patch.object(main, "issue_device_credential", fake_issue),
            patch.object(main, "build_provisioning_zip", fake_build),
            patch.object(main, "log_audit", fake_audit),
            patch.object(main, "validate_runtime_sources", self.validate),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        app = main.app
        # Override the exact dependency the real route declares (require_role returns a new closure per call).
        route = next(r for r in app.routes if getattr(r, "path", "") == "/api/super-admin/devices/{device_id}/provisioning-package")
        auth_dep = next(d.call for d in route.dependant.dependencies if d.call is not None and getattr(d.call, "__closure__", None))
        app.dependency_overrides[auth_dep] = lambda: {
            "id": 1, "role": "super_admin", "username": "test", "society_id": None}
        self.addCleanup(app.dependency_overrides.clear)
        self.client = self.TestClient(app, raise_server_exceptions=False)

    def _post(self):
        return self.client.post(f"/api/super-admin/devices/{DEVICE_ID}/provisioning-package")

    def _names(self):
        return [entry[0] for entry in self.log]

    def _issued(self):
        return [e for e in self.log if e[0] == "issue_device_credential"]

    # A. Runtime validation failure: no credential issuance, no commit.
    def test_a_runtime_validation_failure_issues_nothing_and_never_commits(self):
        self.validate.side_effect = RuntimeError("runtime source invalid")
        response = self._post()
        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(self._issued(), [], "no credential may be issued after a validation failure")
        self.assertNotIn("commit", self._names())
        self.assertNotIn("build_provisioning_zip", self._names())

    # B. Source / service validation failure (real preflight rejects the service account): no issuance, no commit.
    def test_b_service_account_validation_failure_issues_nothing_and_never_commits(self):
        from backend import provisioning as pkg  # noqa: F401  (same module object as main.provisioning)
        self.validate.side_effect = None
        with patch.object(self.main, "validate_runtime_sources",
                          side_effect=RuntimeError("Refusing: service account is not daulat123")):
            response = self._post()
        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(self._issued(), [])
        self.assertNotIn("commit", self._names())

    # C. ZIP construction failure after issuance: rollback called, commit not called.
    def test_c_zip_failure_after_issuance_rolls_back_and_never_commits(self):
        def boom(*args, **kwargs):
            raise RuntimeError("zip construction failed")
        with patch.object(self.main, "build_provisioning_zip", boom):
            response = self._post()
        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(len(self._issued()), 1, "issuance happened before the ZIP failure")
        self.assertIn("rollback", self._names())
        self.assertNotIn("commit", self._names())
        self.assertIn("close", self._names())

    # D. Successful execution: issued, ZIP built, commit only after successful ZIP construction.
    def test_d_success_issues_builds_then_commits_and_returns_zip(self):
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"ZIPBYTES")
        self.assertEqual(response.headers["content-type"], "application/zip")
        names = self._names()
        order = [n for n in names if n in ("validate_runtime_sources", "issue_device_credential",
                                           "build_provisioning_zip", "commit")]
        self.assertEqual(order, ["validate_runtime_sources", "issue_device_credential",
                                 "build_provisioning_zip", "commit"])
        self.assertNotIn("rollback", names)
        self.assertIn("close", names)

    # E. Credential helper transaction ownership: the REAL helper must not commit or roll back.
    def test_e_real_issue_device_credential_does_not_commit_or_rollback(self):
        events = []

        class RecordingCursor:
            def execute(self, sql, params=None):
                events.append(("execute", " ".join(sql.split()[:2])))

            def fetchone(self):
                return {"id": DEVICE_ID, "status": "ACTIVE"}

            def fetchall(self):
                return []

        # _REAL_ISSUE is the original helper, captured at module load before any patch.
        # log_audit is only invoked when user is not None; pass user=None to keep the audit path out.
        result = _REAL_ISSUE(RecordingCursor(), DEVICE_ID, None, "provisioning")
        self.assertNotIn(("commit",), events, "helper must not commit: caller owns the transaction")
        self.assertNotIn(("rollback",), events, "helper must not roll back: caller owns the transaction")
        self.assertIn(("execute", "SELECT id,"), events)
        self.assertIn(("execute", "UPDATE pi_device_credentials"), events)
        self.assertIn(("execute", "INSERT INTO"), events)
        self.assertIn("api_key", result)


class RuntimeFingerprintSnapshotTests(unittest.TestCase):
    """Regression: BUILD_INFO runtime_sha256 must describe the PACKAGED bytes, not a later disk read.

    The real build_provisioning_zip() is run. A proxy wraps the real Path.read_bytes so that
    ems-controller.service returns DIFFERENT bytes on every read after the first. The packaged
    service copies come from the single validated snapshot, so the fingerprint must still match
    the archive bytes. Before the fix the fingerprint re-read the file and hashed the mutated bytes.
    """

    @classmethod
    def setUpClass(cls):
        cls.pkg = _load_app()  # ensures backend/ is importable
        import provisioning as pkg
        cls.pkg = pkg

    def _build(self):
        pkg = self.pkg
        real = pkg.FIRMWARE_DIR / "ems-controller.service"
        original = real.read_bytes()
        calls = {"n": 0}
        real_read_bytes = Path.read_bytes

        def drifting_read_bytes(path_self):
            if path_self == real:
                calls["n"] += 1
                if calls["n"] == 1:
                    return original
                # any later read sees a different, still-plausible file
                return original.replace(b"User=daulat123", b"User=daulat123")  + b"# drift\n"
            return real_read_bytes(path_self)

        with patch.object(Path, "read_bytes", drifting_read_bytes):
            raw = pkg.build_provisioning_zip("11111111-1111-1111-1111-111111111111", "OFFLINE",
                                             "FIXTURE-KEY", "[redacted:secret]", "https://example.invalid/api")
        return raw, original, calls["n"]

    def test_fingerprint_uses_packaged_service_bytes_not_a_later_disk_read(self):
        import io, json, zipfile
        raw, original, reads = self._build()
        self.assertGreaterEqual(reads, 1)
        z = zipfile.ZipFile(io.BytesIO(raw))
        root = self.pkg.ROOT + "/"
        packaged_service = z.read(root + "systemd/ems-controller.service")
        self.assertEqual(packaged_service, z.read(root + "firmware/ems-controller.service"))
        self.assertEqual(packaged_service, original, "archive must carry the validated bytes")

        info = json.loads(z.read(root + "BUILD_INFO.json"))
        digest = hashlib.sha256()
        for name in sorted(info["runtime_files"]):
            if name == "ems-controller.service":
                data = packaged_service
            else:
                data = z.read(root + "firmware/" + name)
            digest.update(name.encode() + b"\0" + data)
        self.assertEqual(digest.hexdigest(), info["runtime_sha256"],
                         "runtime_sha256 must describe the packaged runtime bytes")

    def test_fingerprint_would_differ_if_computed_from_a_later_disk_read(self):
        import io, json, zipfile
        raw, original, _ = self._build()
        z = zipfile.ZipFile(io.BytesIO(raw))
        root = self.pkg.ROOT + "/"
        info = json.loads(z.read(root + "BUILD_INFO.json"))
        digest = hashlib.sha256()
        for name in sorted(info["runtime_files"]):
            if name == "ems-controller.service":
                data = original + b"# drift\n"  # what a later disk read would have produced
            else:
                data = z.read(root + "firmware/" + name)
            digest.update(name.encode() + b"\0" + data)
        self.assertNotEqual(digest.hexdigest(), info["runtime_sha256"],
                            "the fingerprint must NOT match a later-disk-read hash, proving the snapshot is used")


_REAL_ISSUE = None


def setUpModule():
    global _REAL_ISSUE
    _REAL_ISSUE = _load_app().issue_device_credential


if __name__ == "__main__":
    unittest.main()
