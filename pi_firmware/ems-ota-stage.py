#!/usr/bin/env python3
"""Operator-controlled signed OTA staging entry point (no activation, no reboot).

Usage: EMS_FIRMWARE_TRUSTED_KEYS='{"k1":"<b64>"}' python3 ems-ota-stage.py <version>
The manifest is downloaded with the device credential and fully verified
(sha256 + Ed25519 signature over version/hash/key_id) before the inactive slot is written.
"""
import sys
from api_client import ApiClient
from ota_manager import OTAVerificationError, running_version, stage_signed_firmware

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: ems-ota-stage.py VERSION")
    manifest = ApiClient().download_firmware(sys.argv[1])
    if not manifest:
        raise SystemExit("firmware manifest download failed")
    try:
        print("STAGED", stage_signed_firmware(manifest, running_version("7.0.0")))
    except OTAVerificationError as exc:
        raise SystemExit(f"REJECTED {exc}")
