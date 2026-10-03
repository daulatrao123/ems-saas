"""Multi-file firmware release contract.

The 7.0.0 OTA agent stages only ems_controller.py. A device that does not report
ota agent "multi-file-v1" cannot safely install this release. Re-provision that
device with the current firmware tree first. Do not treat a frontend confirmation
as authorization, and do not embed a signing private key.

Signed bytes are canonical JSON of the manifest identity (no file contents).
The private key stays outside this repository. The Pi verifies with
EMS_FIRMWARE_TRUSTED_KEYS / EMS_FIRMWARE_PUBLIC_KEY_B64.
"""
import base64
import hashlib
import json
import posixpath

AGENT = "multi-file-v1"
SCHEMA_VERSION = 1
BOOTSTRAP_MESSAGE = "Bootstrap required: existing firmware 7.0.0 cannot safely receive this multi-file OTA release."
HARDWARE_PROFILE = "EMS-4CH-v1"
MAX_PACKAGE_BYTES = 8 * 1024 * 1024

TOP_LEVEL = (
    "ems_controller.py",
    "firmware_release.py",
    "gpio_manager.py",
    "hardware_capabilities.py",
    "lcd_display.py",
    "offline_queue.py",
    "ota_manager.py",
)
ENERGY_FILES = (
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
REQUIRED = TOP_LEVEL + ENERGY_FILES
ALLOWED = frozenset(REQUIRED)
IN_PROGRESS = frozenset({"REQUESTED", "DOWNLOADING", "VERIFYING", "STAGED", "INSTALLING", "RESTARTING", "HEALTH_CHECK", "ROLLING_BACK"})
TERMINAL = frozenset({"SUCCESS", "FAILED", "ROLLED_BACK"})
RELEASE_STATUS = frozenset({"DRAFT", "APPROVED", "REVOKED"})


class ReleaseError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def normalize_path(path) -> str:
    if not isinstance(path, str) or not path or path != path.strip():
        raise ReleaseError("PATH_REJECTED")
    if path.startswith(("/", "\\")) or "\\" in path or ":" in path:
        raise ReleaseError("PATH_REJECTED")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ReleaseError("PATH_REJECTED")
    norm = posixpath.normpath(path)
    if norm != path or norm not in ALLOWED:
        raise ReleaseError("PATH_NOT_ALLOWED" if norm == path else "PATH_REJECTED")
    return norm


def package_sha256(files: list) -> str:
    lines = [f"{item['path']}\n{item['sha256']}\n{int(item['size'])}\n" for item in sorted(files, key=lambda item: item["path"])]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def signed_document(manifest: dict) -> bytes:
    body = {
        "schema_version": SCHEMA_VERSION,
        "version": manifest["version"],
        "hardware_profile": manifest["hardware_profile"],
        "package_sha256": manifest["package_sha256"],
        "key_id": manifest["key_id"],
        "min_firmware_version": manifest.get("min_firmware_version") or "",
        "files": [{"path": item["path"], "sha256": item["sha256"], "size": int(item["size"])} for item in sorted(manifest["files"], key=lambda item: item["path"])],
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _file_entry(path: str, content: bytes) -> dict:
    rel = normalize_path(path)
    return {"path": rel, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content), "content_b64": base64.b64encode(content).decode("ascii")}


def build_release(root_files: dict, version: str, hardware_profile: str, key_id: str, signature: str, min_firmware_version: str = "", notes: str = "") -> dict:
    missing = [path for path in REQUIRED if path not in root_files]
    if missing:
        raise ReleaseError("MISSING_REQUIRED_FILE")
    files = [_file_entry(path, root_files[path]) for path in REQUIRED]
    total = sum(item["size"] for item in files)
    if total > MAX_PACKAGE_BYTES:
        raise ReleaseError("ARTIFACT_TOO_LARGE")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "version": str(version).strip(),
        "hardware_profile": str(hardware_profile).strip(),
        "created_at": None,
        "files": files,
        "package_sha256": package_sha256(files),
        "key_id": str(key_id).strip(),
        "min_firmware_version": str(min_firmware_version or "").strip(),
        "signature": str(signature or ""),
        "notes": str(notes or "")[:4000],
        "ota_agent": AGENT,
    }
    if not manifest["version"] or not manifest["key_id"]:
        raise ReleaseError("INVALID_MANIFEST")
    return manifest


def verify_release(manifest: dict, running_version: str, hardware_profile: str, verify_signature) -> dict:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseError("INVALID_MANIFEST")
    if manifest.get("ota_agent") not in (None, AGENT):
        raise ReleaseError("INVALID_MANIFEST")
    version = str(manifest.get("version") or "").strip()
    profile = str(manifest.get("hardware_profile") or "").strip()
    key_id = str(manifest.get("key_id") or "").strip()
    signature = manifest.get("signature")
    if not version or not key_id or not isinstance(signature, str) or not signature:
        raise ReleaseError("INVALID_MANIFEST")
    if profile != hardware_profile:
        raise ReleaseError("HARDWARE_PROFILE_MISMATCH")
    if version == running_version:
        raise ReleaseError("VERSION_ALREADY_RUNNING")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ReleaseError("INVALID_MANIFEST")
    seen = set()
    checked = []
    total = 0
    for item in files:
        if not isinstance(item, dict):
            raise ReleaseError("INVALID_MANIFEST")
        rel = normalize_path(item.get("path"))
        if rel in seen:
            raise ReleaseError("PATH_NOT_ALLOWED")
        seen.add(rel)
        raw_b64 = item.get("content_b64")
        if not isinstance(raw_b64, str) or not raw_b64:
            raise ReleaseError("MISSING_REQUIRED_FILE")
        try:
            content = base64.b64decode(raw_b64, validate=True)
        except ValueError:
            raise ReleaseError("INVALID_MANIFEST")
        digest = hashlib.sha256(content).hexdigest()
        claimed = str(item.get("sha256") or "").strip().lower()
        size = item.get("size")
        if claimed != digest or size != len(content):
            raise ReleaseError("FILE_SHA256_MISMATCH")
        total += len(content)
        if total > MAX_PACKAGE_BYTES:
            raise ReleaseError("ARTIFACT_TOO_LARGE")
        checked.append({"path": rel, "sha256": digest, "size": len(content), "content": content})
    if seen != ALLOWED:
        raise ReleaseError("MISSING_REQUIRED_FILE")
    expected_package = package_sha256(checked)
    if str(manifest.get("package_sha256") or "").strip().lower() != expected_package:
        raise ReleaseError("PACKAGE_SHA256_MISMATCH")
    identity = dict(manifest)
    identity["files"] = checked
    identity["package_sha256"] = expected_package
    if verify_signature(key_id, signature, signed_document(identity)) is not True:
        raise ReleaseError("SIGNATURE_INVALID")
    return {"version": version, "hardware_profile": profile, "key_id": key_id, "package_sha256": expected_package, "files": checked, "operation_id": str(manifest.get("operation_id") or "")}


def install_decision(release: dict | None, device: dict, operation: dict | None) -> dict:
    """Pure gate. Confirmation is not an input. Returns the offer the UI may show."""
    current = str((device or {}).get("firmware_version") or "")
    agent = (device or {}).get("ota_agent")
    profile = str((device or {}).get("hardware_profile") or "")
    credential = (device or {}).get("credential_state") or "none"
    state = (operation or {}).get("state")
    base = {
        "current_firmware": current or None,
        "available_firmware": None,
        "status": state or "NONE",
        "release_notes": "",
        "install_allowed": False,
        "bootstrap_required": agent != AGENT,
        "bootstrap_message": BOOTSTRAP_MESSAGE if agent != AGENT else "",
        "operation_id": (operation or {}).get("id"),
        "reason": "",
    }
    if credential != "active":
        base["reason"] = "DEVICE_REVOKED"
        return base
    if agent != AGENT:
        base["reason"] = "BOOTSTRAP_REQUIRED"
        return base
    if state in IN_PROGRESS:
        base["available_firmware"] = (operation or {}).get("firmware_version")
        base["reason"] = "UPDATE_IN_PROGRESS"
        return base
    if not release or release.get("status") != "APPROVED":
        base["reason"] = "RELEASE_NOT_APPROVED" if release else "NO_RELEASE"
        if release and release.get("status") == "REVOKED":
            base["reason"] = "RELEASE_REVOKED"
        return base
    if str(release.get("hardware_profile") or "") != profile:
        base["reason"] = "HARDWARE_PROFILE_MISMATCH"
        return base
    if str(release.get("version") or "") == current:
        base["status"] = "SUCCESS" if state == "SUCCESS" else "NONE"
        base["available_firmware"] = release.get("version")
        base["reason"] = "VERSION_ALREADY_RUNNING"
        return base
    if not release.get("signature") or not release.get("key_id"):
        base["reason"] = "UNSIGNED_RELEASE"
        return base
    base.update({
        "available_firmware": release.get("version"),
        "release_notes": release.get("notes") or "",
        "install_allowed": True,
        "status": "UPDATE_AVAILABLE",
        "reason": "",
    })
    return base
