"""Signed EMS firmware staging with application-level A/B rollback.

The updater never executes an unverified payload. It verifies Ed25519 and
SHA-256 before writing the inactive slot, then atomically flips the active
marker. A boot-attempt marker lets the controller detect an unconfirmed boot
and roll back to the previous slot.
"""
import base64
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from logger import logger

OTA_ROOT = Path(os.getenv("EMS_OTA_ROOT", "/mnt/ems-data/ota"))
SLOT_A = OTA_ROOT / "slot_a"
SLOT_B = OTA_ROOT / "slot_b"
ACTIVE = OTA_ROOT / "active.json"
PENDING = OTA_ROOT / "pending.json"
PUBLIC_KEY_B64 = os.getenv("EMS_FIRMWARE_PUBLIC_KEY_B64", "")


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


def _verify(code: str, signature: str, sha256: str):
    raw = code.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("firmware SHA-256 mismatch")
    if not PUBLIC_KEY_B64:
        raise RuntimeError("EMS_FIRMWARE_PUBLIC_KEY_B64 is not configured")
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))
        key.verify(base64.b64decode(signature), raw)
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError("firmware Ed25519 signature verification failed") from exc


def stage_signed_firmware(manifest: dict) -> str:
    version = str(manifest.get("version", "")).strip()
    code = manifest.get("code")
    signature = manifest.get("signature")
    sha256 = manifest.get("sha256")
    if not version or not isinstance(code, str) or not signature or not sha256:
        raise ValueError("incomplete firmware manifest")
    _verify(code, signature, sha256)

    OTA_ROOT.mkdir(parents=True, exist_ok=True)
    active = "a"
    if ACTIVE.exists():
        try: active = json.loads(ACTIVE.read_text())["active"]
        except Exception: pass
    target = SLOT_B if active == "a" else SLOT_A
    previous = SLOT_A if active == "a" else SLOT_B

    temp = Path(tempfile.mkdtemp(prefix="ems-ota-", dir=str(OTA_ROOT)))
    try:
        (temp / "ems_controller.py").write_text(code, encoding="utf-8")
        with open(temp / "ems_controller.py", "rb") as f:
            os.fsync(f.fileno())
        if target.exists(): shutil.rmtree(target)
        os.replace(temp, target)
        _atomic_json(PENDING, {"version": version, "target": "b" if active == "a" else "a", "previous": active})
        _atomic_json(ACTIVE, {"active": "b" if active == "a" else "a", "version": version, "boot_confirmed": False})
        return version
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def confirm_boot():
    if ACTIVE.exists():
        data = json.loads(ACTIVE.read_text())
        data["boot_confirmed"] = True
        _atomic_json(ACTIVE, data)
        if PENDING.exists(): PENDING.unlink()


def rollback_if_unconfirmed() -> bool:
    if not ACTIVE.exists(): return False
    data = json.loads(ACTIVE.read_text())
    if data.get("boot_confirmed", True): return False
    previous = (PENDING.read_text() if PENDING.exists() else "")
    if not previous: return False
    pending = json.loads(previous)
    prev = pending.get("previous")
    if prev not in ("a", "b"): return False
    _atomic_json(ACTIVE, {"active": prev, "version": "rollback", "boot_confirmed": True})
    PENDING.unlink(missing_ok=True)
    logger.critical("Unconfirmed OTA boot detected; rolled back to slot %s", prev)
    return True
