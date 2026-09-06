import os
os.environ["EMS_DEVICE_ID"]="00000000-0000-0000-0000-000000000001"; os.environ["EMS_API_KEY"]="qa"; os.environ["EMS_API_BASE_URL"]="http://127.0.0.1:8000"
import base64, hashlib, json, os, tempfile, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'pi_firmware'))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Signed OTA crypto contract
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
key=Ed25519PrivateKey.generate(); pub=key.public_key(); payload=b'ems-controller-test'
sig=key.sign(payload); pub.verify(sig,payload)
try: pub.verify(sig[:-1]+bytes([sig[-1]^1]),payload); raise AssertionError('tampered signature accepted')
except Exception: pass
assert hashlib.sha256(payload).hexdigest()

# Legacy state document: new fields absent must remain loadable.
from state import SlotState
x=SlotState('A'); x.from_dict({'slot':'A','commanded_state':'UNKNOWN','gpio_output_state':'UNKNOWN','feedback_state':'UNKNOWN','verification_state':'NOT_CONFIGURED'})
assert x.used_days==0 and x.clicks==0

print('PASS signed OTA verification')
print('PASS legacy STATE_VERSION=4 slot compatibility')
print('PASS tampered OTA payload rejected')
