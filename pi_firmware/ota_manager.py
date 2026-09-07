"""Signed EMS firmware staging with application-level A/B rollback.

Release manifest (served by /api/pi/firmware-download):
    {version, code, sha256, signature, key_id, min_firmware_version}

Signed payload = canonical JSON {"key_id","min_firmware_version","sha256","version"}
(sort_keys, compact) so the signature binds version + artifact hash + key identity;
a valid artifact cannot be replayed under another version. Signature = Ed25519
(cryptography). Private keys never touch the Pi or the repo.

Trusted key set (rotation-ready): env EMS_FIRMWARE_TRUSTED_KEYS='{"k1":"<b64 pub>","k2":"..."}'
Legacy: EMS_FIRMWARE_PUBLIC_KEY_B64 is trusted as key_id "k1".

States (pending.json): DOWNLOADING -> VERIFIED -> STAGED -> ACTIVATING -> HEALTH_CHECK -> ACTIVE
Failure -> FAILED (bounded attempts per version); unconfirmed boot -> ROLLED_BACK by ota_boot.sh.
All persistence is event-driven (a handful of small JSON writes per OTA, none per sync).
"""
import base64
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from logger import logger

OTA_ROOT = Path(os.getenv("EMS_OTA_ROOT", "/mnt/ems-data/ota"))
SLOT_A = OTA_ROOT / "slot_a"
SLOT_B = OTA_ROOT / "slot_b"
ACTIVE = OTA_ROOT / "active.json"
PENDING = OTA_ROOT / "pending.json"
ATTEMPTS = OTA_ROOT / "attempts.json"

MAX_ATTEMPTS_PER_VERSION = 3
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


class OTAVerificationError(ValueError):
    pass


def trusted_keys() -> dict:
    keys = {}
    raw = os.getenv("EMS_FIRMWARE_TRUSTED_KEYS", "")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                keys.update({str(k): str(v) for k, v in parsed.items()})
        except ValueError:
            logger.critical("EMS_FIRMWARE_TRUSTED_KEYS is not valid JSON; ignored")
    legacy = os.getenv("EMS_FIRMWARE_PUBLIC_KEY_B64", "")
    if legacy and "k1" not in keys:
        keys["k1"] = legacy
    return keys


def signed_payload(version: str, sha256: str, key_id: str, min_firmware_version: str) -> bytes:
    return json.dumps(
        {"key_id": key_id, "min_firmware_version": min_firmware_version or "", "sha256": sha256, "version": version},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _version_tuple(v: str):
    try:
        return tuple(int(p) for p in str(v).strip().split("."))
    except ValueError:
        raise OTAVerificationError("INVALID_VERSION")


def verify_manifest(manifest: dict, running_version: str) -> dict:
    """Full verification; returns normalised fields or raises OTAVerificationError(code)."""
    if not isinstance(manifest, dict):
        raise OTAVerificationError("INVALID_MANIFEST")
    version = str(manifest.get("version", "")).strip()
    code = manifest.get("code")
    sha256 = str(manifest.get("sha256", "")).strip().lower()
    signature = manifest.get("signature")
    key_id = str(manifest.get("key_id", "")).strip()
    min_fw = str(manifest.get("min_firmware_version") or "").strip()
    if not version or not isinstance(code, str) or not code or not signature or not sha256 or not key_id:
        raise OTAVerificationError("INVALID_MANIFEST")
    _version_tuple(version)
    raw = code.encode("utf-8")
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise OTAVerificationError("ARTIFACT_TOO_LARGE")
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise OTAVerificationError("SHA256_MISMATCH")
    keys = trusted_keys()
    if not keys:
        raise OTAVerificationError("NO_TRUSTED_KEYS")
    if key_id not in keys:
        raise OTAVerificationError("UNTRUSTED_KEY_ID")
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[key_id]))
        pub.verify(base64.b64decode(signature), signed_payload(version, sha256, key_id, min_fw))
    except (InvalidSignature, ValueError, TypeError):
        raise OTAVerificationError("SIGNATURE_INVALID")
    if version == running_version:
        raise OTAVerificationError("VERSION_ALREADY_RUNNING")
    if min_fw and _version_tuple(running_version) < _version_tuple(min_fw):
        raise OTAVerificationError("BELOW_MIN_FIRMWARE")
    return {"version": version, "code": code, "sha256": sha256, "key_id": key_id}


# ---------------------------------------------------------------- state files

def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, separators=(",", ":"), sort_keys=True)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def active_info() -> dict:
    return _read_json(ACTIVE)


def pending_info() -> dict:
    return _read_json(PENDING)


def running_version(default: str) -> str:
    """Version of the slot that is executing right now (confirmed or under health check)."""
    v = active_info().get("version")
    return str(v) if v and v != "rollback" else default


def attempts_for(version: str) -> int:
    return int(_read_json(ATTEMPTS).get(version, 0))


def record_failure(version: str, code: str):
    """One small write per failed attempt (event-driven, bounded by MAX_ATTEMPTS)."""
    data = _read_json(ATTEMPTS)
    data[version] = int(data.get(version, 0)) + 1
    data["last_error"] = code
    data["last_version"] = version
    _atomic_json(ATTEMPTS, data)
    _atomic_json(PENDING, {"version": version, "state": "FAILED", "error": code, "attempts": data[version]})


def exhausted(version: str) -> bool:
    return attempts_for(version) >= MAX_ATTEMPTS_PER_VERSION


# ---------------------------------------------------------------- staging / activation

def stage_signed_firmware(manifest: dict, running: str) -> str:
    """verify -> write inactive slot (temp dir, fsync, atomic rename) -> pending STAGED.
    Never touches the active slot; activation is a separate explicit step."""
    fields = verify_manifest(manifest, running)
    version = fields["version"]
    OTA_ROOT.mkdir(parents=True, exist_ok=True)
    _atomic_json(PENDING, {"version": version, "state": "VERIFIED", "sha256": fields["sha256"], "key_id": fields["key_id"]})
    active = active_info().get("active", "a")
    target = SLOT_B if active == "a" else SLOT_A
    for stale in OTA_ROOT.glob("ems-ota-*"):  # bounded disk: remove interrupted temp dirs
        shutil.rmtree(stale, ignore_errors=True)
    temp = Path(tempfile.mkdtemp(prefix="ems-ota-", dir=str(OTA_ROOT)))
    try:
        (temp / "ems_controller.py").write_text(fields["code"], encoding="utf-8")
        with open(temp / "ems_controller.py", "rb") as f:
            os.fsync(f.fileno())
        if hashlib.sha256((temp / "ems_controller.py").read_bytes()).hexdigest() != fields["sha256"]:
            raise OTAVerificationError("STAGED_HASH_MISMATCH")
        if target.exists(): shutil.rmtree(target)
        os.replace(temp, target)
        _atomic_json(PENDING, {"version": version, "state": "STAGED", "target": "b" if active == "a" else "a",
                               "previous": active, "sha256": fields["sha256"], "key_id": fields["key_id"]})
        return version
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def activate_staged(running: str = "") -> bool:
    """Flip active slot to the STAGED target with boot_confirmed=False. The process must
    then restart (ota_boot.sh execs the active slot). Idempotent: a second call while
    ACTIVATING is a no-op, so duplicate OTA commands cannot double-activate."""
    p = pending_info()
    if p.get("state") == "ACTIVATING":
        return True
    if p.get("state") != "STAGED" or p.get("target") not in ("a", "b"):
        return False
    slot = SLOT_A if p["target"] == "a" else SLOT_B
    if not (slot / "ems_controller.py").exists():
        return False
    _atomic_json(PENDING, {**p, "state": "ACTIVATING"})
    _atomic_json(ACTIVE, {"active": p["target"], "version": p["version"], "boot_confirmed": False,
                          "previous": p["previous"], "previous_version": running})
    return True


def confirm_boot():
    """Health check passed on the new firmware -> mark ACTIVE, clear pending."""
    data = active_info()
    if not data or data.get("boot_confirmed", True):
        return False
    data["boot_confirmed"] = True
    _atomic_json(ACTIVE, data)
    PENDING.unlink(missing_ok=True)
    return True


def rollback_if_unconfirmed() -> bool:
    """Called by ota_boot.sh BEFORE exec: an unconfirmed previous boot means the new
    firmware failed its health check (or crashed) -> revert to the previous slot."""
    data = active_info()
    if not data or data.get("boot_confirmed", True):
        return False
    if not data.get("boot_started"):
        # First start of the newly activated slot: let it run its health check.
        data["boot_started"] = True
        _atomic_json(ACTIVE, data)
        _atomic_json(PENDING, {**pending_info(), "state": "HEALTH_CHECK"})
        return False
    # Second start while still unconfirmed => the new firmware crashed / failed boot().
    prev = data.get("previous") or pending_info().get("previous")
    if prev not in ("a", "b"):
        prev = "a" if data.get("active") == "b" else "b"
    failed_version = str(data.get("version", ""))
    _atomic_json(ACTIVE, {"active": prev, "version": data.get("previous_version") or "rollback", "boot_confirmed": True,
                          "rolled_back_from": failed_version})
    if failed_version:
        record_failure(failed_version, "HEALTH_CHECK_FAILED")
    logger.critical("Unconfirmed OTA boot detected; rolled back to slot %s", prev)
    return True
