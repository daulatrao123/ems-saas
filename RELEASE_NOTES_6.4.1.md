# EMS SaaS 6.4.1 — Strict Hardened Candidate

## Main objective
Move database schema ownership out of the FastAPI runtime and into versioned Alembic
migrations while preserving the previously hardened Pi command/storage architecture.

## Key changes
- Added `backend/alembic/` with a versioned `0001_industrial_schema` adoption migration.
- Added `backend/alembic.ini` and `backend/alembic/env.py` using `DATABASE_URL`.
- Render startup now executes `alembic upgrade head` before `uvicorn`.
- Removed the FastAPI startup schema initializer and all runtime DDL from `backend/main.py`.
- Tightened the command FSM to reject `queued -> executing`.
- Added retired-device assignment protection and feedback capability consistency.
- Added a safety gate preventing hardware execution when durable EXECUTING state cannot be persisted.
- Added a self-contained 6.4.1 static release gate.

## Explicit non-claims

This release does **not** certify physical contactor behavior, USB flash endurance,
secure OTA, browser authentication against XSS, or production deployment safety.
Those remain mandatory qualification gates.
