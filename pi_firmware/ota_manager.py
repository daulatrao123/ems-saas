"""Signed firmware staging with an application-level A/B slot model.

This module NEVER executes downloaded code. It verifies SHA-256 and Ed25519,
then atomically stages the artifact into the inactive slot. Activation and
bootloader rollback are intentionally explicit platform operations.
"""
import base64, hashlib, json, os, tempfile
from pathlib import Path

from config import DATA_DIR
from logger import logger

OTA_DIR=Path(DATA_DIR)/"ota"
SLOTS=(OTA_DIR/"slot-a", OTA_DIR/"slot-b")
ACTIVE=OTA_DIR/"active.json"
PENDING=OTA_DIR/"pending.json"
PUBLIC_KEY_ENV="EMS_FIRMWARE_PUBLIC_KEY"

class OTAVerificationError(RuntimeError): pass

def _message(version,digest,code): return (version+"\n"+digest+"\n"+code).encode()

def _atomic_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=path.name+".",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(obj,f,sort_keys=True,separators=(",",":")); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
        d=os.open(path.parent,os.O_DIRECTORY); os.fsync(d); os.close(d)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def _verify(artifact):
    required=("version","code","sha256","signature","key_id")
    if any(not artifact.get(k) for k in required): raise OTAVerificationError("Incomplete signed firmware manifest")
    code=artifact["code"]; digest=hashlib.sha256(code.encode()).hexdigest()
    if digest != artifact["sha256"]: raise OTAVerificationError("Firmware SHA-256 mismatch")
    raw=os.getenv(PUBLIC_KEY_ENV)
    if not raw: raise OTAVerificationError("Firmware public key is not configured")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try: key=Ed25519PublicKey.from_public_bytes(base64.b64decode(raw)); sig=base64.b64decode(artifact["signature"]); key.verify(sig,_message(artifact["version"],digest,code))
    except Exception as exc: raise OTAVerificationError("Firmware Ed25519 signature verification failed") from exc

def stage_signed_firmware(artifact):
    _verify(artifact)
    OTA_DIR.mkdir(parents=True,exist_ok=True)
    active="a"
    if ACTIVE.exists():
        try: active=json.loads(ACTIVE.read_text()).get("slot","a")
        except Exception: pass
    target="b" if active=="a" else "a"
    target_dir=OTA_DIR/f"slot-{target}"; target_dir.mkdir(parents=True,exist_ok=True)
    artifact_path=target_dir/f"firmware-{artifact['version']}.py"
    tmp=artifact_path.with_suffix(".tmp")
    tmp.write_text(artifact["code"],encoding="utf-8")
    with tmp.open("rb") as f: os.fsync(f.fileno())
    os.replace(tmp,artifact_path)
    manifest={k:artifact[k] for k in ("version","sha256","signature","key_id","changelog","forced") if k in artifact}
    manifest["slot"]=target; manifest["artifact"]=str(artifact_path)
    _atomic_json(target_dir/"manifest.json",manifest)
    _atomic_json(PENDING,{"slot":target,"version":artifact["version"],"staged_at":__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()})
    logger.info("Signed firmware staged in inactive OTA slot %s: %s",target,artifact["version"])
    return manifest
