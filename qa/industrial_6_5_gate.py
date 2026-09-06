"""Offline industrial release gate for EMS 6.5.

Checks source invariants only. It does not certify HIL, storage endurance,
external deployment, cryptographic key custody, or bootloader rollback.
"""
from pathlib import Path
import ast, hashlib
ROOT=Path(__file__).resolve().parents[1]

def text(p): return (ROOT/p).read_text(encoding='utf-8')
def require(blob,label,*need):
    for x in need:
        if x not in blob: raise SystemExit(f'FAIL {label}: missing {x}')

backend=text('backend/main.py'); api=text('pi_firmware/api_client.py'); ota=text('pi_firmware/ota_manager.py'); render=text('backend/render.yaml'); alembic=text('backend/alembic/versions/0001_industrial_schema.py'); frontend=text('frontend/src/lib/api.ts')+text('frontend/src/app/login/page.tsx')
require(backend,'FSM','"queued": {"delivered", "expired"}','"hardware_verified": {"completed", "failed"}','COMMAND_DELIVERY_LEASE_SECONDS = 120','COMMAND_EXPIRY_SECONDS = 300')
require(backend,'migration','def get_db():','@app.post("/api/auth/refresh")','auth_sessions','EMS_FIRMWARE_SIGNING_PRIVATE_KEY')
if '@app.on_event("startup")' in backend or 'CREATE TABLE IF NOT EXISTS' in backend or 'ALTER TABLE ' in backend: raise SystemExit('FAIL backend contains runtime DDL')
require(render,'deployment','alembic upgrade head && uvicorn')
require(alembic,'alembic schema','CREATE TABLE IF NOT EXISTS societies','CREATE TABLE IF NOT EXISTS auth_sessions','ADD COLUMN IF NOT EXISTS reset_day')
require(frontend,'cookie auth','withCredentials: true','/api/auth/refresh')
if 'localStorage.setItem("token"' in frontend or 'localStorage.getItem("token")' in frontend: raise SystemExit('FAIL JWT token still stored in localStorage')
require(ota,'signed OTA','Ed25519PublicKey','sha256','inactive slot','signature')
require(api,'OTA client','download_firmware')
for f in ['backend/main.py','backend/alembic/env.py','backend/alembic/versions/0001_industrial_schema.py','pi_firmware/api_client.py','pi_firmware/ota_manager.py']:
    ast.parse(text(f),filename=f)
print('PASS: EMS 6.5 offline industrial gate')
print('PASS: runtime DDL removed; Alembic required before app')
print('PASS: HttpOnly cookie + rotating refresh-session architecture')
print('PASS: Ed25519 firmware signing + Pi-side verification/staging')
print('NOTE: HIL, 7-30 day endurance, key custody and real bootloader rollback remain physical/deployment qualification gates')
