# EMS publication inventory (from the 19 Alembic migrations)

Source: `backend/alembic/versions/0001` to `0019`, read at commit `ea57e0c`.
Status: **static derivation from migration DDL. Not verified against a live database.**
All DDL is raw `op.execute` SQL; no migration uses `create_table(`.

## 1. Tables at Alembic head

24 base tables exist after `alembic upgrade head` on a database that started empty.

| # | Table | Created in | Primary key | Foreign keys (ON DELETE) |
|---|---|---|---|---|
| 1 | societies | 0001 | `id` SERIAL | none |
| 2 | users | 0001 | `id` SERIAL | society_id -> societies (SET NULL) |
| 3 | pi_devices | 0001 | `id` UUID | society_id -> societies (SET NULL) |
| 4 | slot_configs | 0001 (renamed from wing_configs) | (device_id, slot) | device_id -> pi_devices (CASCADE) |
| 5 | slot_state | 0001 (renamed from wing_state) | (device_id, slot) | device_id -> pi_devices (CASCADE) |
| 6 | pi_state | 0001 | device_id UUID | device_id -> pi_devices (CASCADE) |
| 7 | pi_events | 0001 | `id` SERIAL | device_id -> pi_devices (CASCADE) |
| 8 | pi_commands | 0001 | `id` UUID | device_id -> pi_devices (CASCADE) |
| 9 | firmware_versions | 0001 | `version` TEXT | none |
| 10 | audit_log | 0001 | `id` SERIAL | none |
| 11 | pi_commands_archive | 0007 (LIKE pi_commands) | `id` UUID | none (no FK declared) |
| 12 | auth_refresh_tokens | 0002 | `id` UUID | user_id -> users (CASCADE) |
| 13 | pi_device_credentials | 0005 | `id` UUID | device_id -> pi_devices (CASCADE) |
| 14 | lcd_messages | 0010 | `id` BIGSERIAL | device_id -> pi_devices (CASCADE); created_by -> users (SET NULL) |
| 15 | energy_meters | 0011 | (device_id, meter_id) | device_id -> pi_devices (CASCADE) |
| 16 | energy_meter_readings | 0011 | (device_id, meter_id, ts) | device_id -> pi_devices (CASCADE) |
| 17 | energy_daily | 0011 | (device_id, operating_date) | device_id -> pi_devices (CASCADE) |
| 18 | energy_bill_history | 0011 | `id` BIGSERIAL | device_id -> pi_devices (CASCADE); created_by -> users (SET NULL) |
| 19 | energy_generation_targets | 0011 | `id` BIGSERIAL | device_id -> pi_devices (CASCADE); created_by and updated_by -> users (SET NULL) |
| 20 | energy_adjustments | 0011 | `id` BIGSERIAL | device_id -> pi_devices (CASCADE); created_by -> users (SET NULL) |
| 21 | energy_sync_state | 0015 | device_id UUID | device_id -> pi_devices (CASCADE) |
| 22 | energy_day_quarantine | 0015 | (device_id, date_key) | device_id -> pi_devices (CASCADE) |
| 23 | firmware_releases | 0018 | `version` TEXT | none |
| 24 | ota_operations | 0018 | `id` UUID | device_id -> pi_devices (no ON DELETE action) |

Notes:
- `pi_commands_archive` is created by 0007 `upgrade()` and is dropped only by its `downgrade()`, so it exists at head.
- Tables renamed away in 0001 (`wing_configs`, `wing_state`, and their `_old` variants) do not remain under those names. Where both old and new names existed, 0001 leaves `*_abandoned` copies behind (Risk R3).
- No table is dropped by an upgrade path in 0002 to 0019 except `ota_operations` / `firmware_releases` via their own downgrades.

## 2. Sequences (not replicated by logical replication)

| Owner | Column | Kind |
|---|---|---|
| societies | id | SERIAL |
| users | id | SERIAL |
| pi_events | id | SERIAL |
| audit_log | id | SERIAL |
| lcd_messages | id | BIGSERIAL |
| energy_bill_history | id | BIGSERIAL |
| energy_generation_targets | id | BIGSERIAL |
| energy_adjustments | id | BIGSERIAL |

No IDENTITY columns. `backend/` contains no `nextval`, `setval` or `currval`.
`pi_devices.next_command_sequence` (0003) is an ordinary BIGINT column, not a sequence object, so it replicates as data.

## 3. Credential and secret-bearing data

These hold verifiers or credentials. Replicating them to Aiven is an **owner decision**; they are excluded from the proposed default scope.

- `users.password` (TEXT): hash status unconfirmed; treat as secret.
- `auth_refresh_tokens.token_hash` (TEXT).
- `pi_devices.api_key_hash` (TEXT, legacy). 0005 says `pi_device_credentials` replaces it as the only consulted verifier, but the column still exists.
- `pi_device_credentials.secret_hash`, `key_id`, `hash_alg`.

## 4. Replica identity

Every base table above has a primary key, so default replica identity works for UPDATE and DELETE. No table is missing a PK in the migrations.

## 5. Not carried by logical replication

- **DDL.** Schema changes do not replicate. Each migration must be applied to Aiven identically before the subscription is created.
- **Sequence values** (Section 2).
- **Triggers, functions, views:** none are created by the migrations.
- **TRUNCATE:** not published unless configured.

## 6. Proposed publication scope (for owner review; not created)

Proposed: all 24 tables in Section 1, excluding the credential data in Section 3 until the owner decides.
PostgreSQL 15+ supports column lists in `CREATE PUBLICATION`, so `users.password` and `pi_devices.api_key_hash` can be excluded without dropping the tables. That needs PostgreSQL 15 or later on the publisher, which is satisfied by 16 (Neon runtime version still to be confirmed).

## 7. Risks

- **R1.** Counts are static. No database has been built from these migrations in this work.
- **R2.** Credential verifiers replicate unless excluded (Section 3). Owner decision needed.
- **R3.** `*_abandoned` and `wing_*_old` tables depend on the history of each database. Check per environment before creating a publication.
- **R4.** Sequences are not replicated. Cutover without a sequence sync can cause duplicate-key errors on insert.
- **R5.** DDL is not replicated. A migration applied only to Neon breaks the subscriber silently until fixed.
