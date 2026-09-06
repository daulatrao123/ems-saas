#!/usr/bin/env python3
"""Operator-controlled signed OTA staging entry point.

Usage: EMS_PUBLIC_KEY=... python3 ems-ota-stage.py <version>
The downloaded artifact is verified before it is written to the inactive slot.
No reboot or activation is performed by this command.
"""
import sys
from api_client import ApiClient
from ota_manager import stage_signed_firmware, OTAVerificationError

if __name__ == "__main__":
    if len(sys.argv)!=2: raise SystemExit("usage: ems-ota-stage.py VERSION")
    artifact=ApiClient().download_firmware(sys.argv[1])
    if not artifact: raise SystemExit("firmware download failed")
    try: print(stage_signed_firmware(artifact))
    except OTAVerificationError as exc: raise SystemExit(str(exc))
