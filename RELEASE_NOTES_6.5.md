# EMS SaaS Industrial 6.5 — Hardened Candidate

> ## IMPLEMENTATION STATUS — READ FIRST
>
> This document describes the **intended 6.5 hardening target**.
> It is **NOT** a certification or a statement that every listed security
> control is currently implemented in the repository.
>
> Current status must be verified against the actual source tree.
> As of the T1–T3 baseline (June 2026) the following items below are
> **target only / not yet present in code**: startup-DDL removal
> (`ensure_db_schema()` still runs), HttpOnly cookie authentication,
> refresh-token rotation, logout revocation, Origin guard, `auth_sessions`
> table, firmware SHA-256/Ed25519 signing and signed OTA manifests.
> Present in code: Alembic single-root migration (`0001_ems_baseline`),
> migration-first `start.sh`/`render.yaml`, strict command FSM, retired-device
> guard, feedback-capability clamping.

## What changed

### P0 database migration architecture
- Removed all application-startup `CREATE TABLE` / `ALTER TABLE` / migration DDL from `backend/main.py`.
- Added Alembic under `backend/alembic/` with a versioned PostgreSQL migration.
- Render startup now runs `alembic upgrade head` before Uvicorn.
- Added `auth_sessions` and firmware signature columns to the versioned schema.
- Legacy wing/slot normalization is performed by Alembic, not by the web process.

### P1 browser authentication
- Removed JWT access-token storage from browser `localStorage`.
- Access and refresh tokens are HttpOnly cookies.
- Access token lifetime defaults to 15 minutes.
- Refresh tokens default to 7 days and rotate on every refresh; previous refresh sessions are revoked server-side.
- Added logout session revocation.
- Axios sends credentials automatically and refreshes an expired access session once.
- Added an Origin guard for cookie-authenticated state-changing browser requests.

### P1 signed OTA foundation
- Super Admin firmware uploads are SHA-256 hashed and Ed25519 signed using `EMS_FIRMWARE_SIGNING_PRIVATE_KEY`.
- Pi firmware download is authenticated against the requesting device, not merely the presence of a header.
- Download returns a signed manifest rather than raw source only.
- Pi verifies SHA-256 + Ed25519 before staging to the inactive application slot.
- Added explicit operator-controlled `ems-ota-stage.py`.
- No downloaded code is executed automatically.

### Additional lifecycle hardening
- Strict command FSM retains `QUEUED → DELIVERED → EXECUTING → HARDWARE_VERIFIED → COMPLETED → ACKED` with reboot ambiguity handling.
- Retired Pi devices cannot be reassigned through society configuration.
- Feedback capability is consistently clamped to the physical device capability in dashboards and Pi sync responses.

## Qualification status

**This is a hardened release candidate, not a production certification.**

Still mandatory before industrial release:
1. HIL qualification on the exact relay/contactor/feedback/PSU assembly.
2. 7–30 day storage endurance soak on the exact production Pi + USB device + OS image.
3. Production secret/key custody review and signing-key rotation procedure.
4. Real Raspberry Pi bootloader-level A/B rollback qualification. The included OTA module provides verified inactive-slot application staging; it does not falsely claim to have proven a hardware boot-partition rollback.
5. Clean deployment from an empty database using `alembic upgrade head`, followed by `/api/bootstrap`.
6. Frontend production build and browser cookie/CORS test from the deployed Vercel origin.

## Required production environment variables

Backend:
- `DATABASE_URL`
- `SECRET_KEY`
- `REFRESH_SECRET` (recommended separate secret)
- `EMS_BOOTSTRAP_PASSWORD` (only during controlled bootstrap)
- `COOKIE_SECURE=true`
- `COOKIE_SAMESITE=none`
- `EMS_FIRMWARE_SIGNING_PRIVATE_KEY` (base64-encoded 32-byte Ed25519 private key, secret)
- `EMS_FIRMWARE_KEY_ID`

Pi:
- `EMS_DEVICE_ID`
- `EMS_API_KEY`
- `EMS_API_URL`
- `EMS_FIRMWARE_PUBLIC_KEY` (base64-encoded Ed25519 public key; not secret)
