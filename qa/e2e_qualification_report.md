# EMS SaaS end-to-end qualification

Evidence from one local run on commit `7b36940216234b63fa59109c76ec1a1f33407f55` (`feat: add new-pi provisioning preflight`). No production code was changed. Production database and the production Pi were not contacted. No commit and no push.

Started `2026-10-04T10:42:06Z`. Ended `2026-10-04T10:51:25Z`.

## Verdict

**SOFTWARE QUALIFICATION: CONDITIONAL**

**INDUSTRIAL QUALIFICATION: FAIL**

**PRODUCTION READINESS: BLOCKED**

Unit and source-contract coverage is real. The offline unittest and Node suites are green after the final QA cleanup. It is not an end-to-end qualification. Authentication, tenant isolation, seeded energy round-trip, browser flows, fleet storage, and physical hardware were not executed. Mock GPIO results are not physical proof.

## 1. Environment

| Item | Evidence |
|---|---|
| Git SHA | `7b36940216234b63fa59109c76ec1a1f33407f55` |
| Branch | `main`, even with `origin/main` |
| Working tree before this report | clean |
| Backend Python used | 3.11.9 (`.venv`) |
| System Python present | 3.13.15, not used for the suites |
| Node | v20.19.4 (CI workflow asks for Node 22) |
| npm | 10.8.2 |
| Test frameworks | `unittest`, Node `node:test`, script `check()` runners |
| pytest | not installed |
| Playwright | not installed |
| Cypress | not present |
| Database | PostgreSQL through `psycopg`. Alembic revisions `0001` through `0018` live under `backend/alembic/versions/`. |
| Connection source | `DATABASE_URL` only. It was unset. `psql` and `docker` are not on PATH. |
| Frontend rewrite | `EMS_BACKEND_URL` required by `frontend/next.config.ts`. It was unset. Frontend was not started. |
| Pi simulator | `pi_firmware/pi_simulator.py` needs `BACKEND_URL`, `PI_DEVICE_ID`, `PI_API_KEY`. Not started. `qa/phase05_sim_common.py` reads `os.statvfs` at import, which Windows Python does not provide. |
| Deployment config | `.github/workflows/qa.yml`, `backend/render.yaml`. Not exercised against a live deploy. |

Required variables seen in code and CI, none of which were pointed at production for this run: `DATABASE_URL`, `SECRET_KEY`, `EMS_BOOTSTRAP_PASSWORD`, `EMS_TRUSTED_ORIGINS`, `COOKIE_SECURE`, `EMS_BACKEND_URL`, `EMS_PUBLIC_API_URL`, `EMS_FIRMWARE_SIGNING_PRIVATE_KEY` / `EMS_FIRMWARE_TRUSTED_KEYS`, `EMS_DEVICE_ID`, `EMS_API_KEY`, `EMS_API_URL`, `EMS_OTA_ROOT`.

## 2. What was executed

Python unittest, after the final QA cleanup: **185 ran, 185 passed, 0 failed, 0 errors.** Before that cleanup the same suite was 179 passed, 5 failed, 1 error. No production file was changed.

Node `node:test`: **57 ran, 57 passed, 0 failed.** Before the dashboard harness fix it was 56 passed, 1 failed.

Script checks that finished with a count:

- hardware capabilities: **65/65 passed** (no GPIO, no database)
- mocked GPIO source-of-truth: **35/35 passed**. The script itself prints that Raspberry Pi/HIL was not performed.
- P3 feedback hardening, mocked GPIO and mocked clock: **68/68 passed** after assertions allowed four fail-closed `off()` events and still required zero `on()` events. This is not a physical HIL result.
- runtime SQLite compatibility: **2 passed**
- source-level signed OTA: **3 passed** (tampered payload rejected). This is not a physical OTA.

Script checks that stopped early:

- feedback failsafe, mocked GPIO: **19 checks passed, then `ctrl.boot()` was not true**. Later failsafe checks in that function did not run.
- allocation-mode visibility: **passed**, including the current `DAY_BASED` file list.
- `strict_6_4_1_check.py`: member read-only and backend role checks passed. The script then fails a pre-existing sha256 pin of `pi_firmware/gpio_manager.py`. That pin was not updated.

Harness crashes, not product measurements:

- `industrial_6_5_gate.py` looks for `backend/alembic/versions/0001_industrial_schema.py`. That file is not in the tree. Current baseline is `0001_ems_baseline.py`.
- `strict_6_5_check.py` looks for `0001_ems_current_schema.py`. Missing.
- `strict_6_4_check.py` looks for `20260906_0001_industrial_baseline.py`. Missing.

Blocked before any assertion:

- `qa/energy_e1_test.py` and `qa/energy_e3_test.py`: `os.statvfs` missing on Windows.

Not started, because they require a live PostgreSQL database or live credentials:

- `qa/energy_e2_test.py`
- `qa/energy_e4_test.py`
- `qa/energy_mode_test.py`
- `qa/energy_references_test.py`
- `frontend/test_system.py`

No 15-minute or 1-hour fleet simulation was run. No SMART sample was taken. No browser was opened.

Automated check tally for this run only: **456 passed, 25 assertion failures, 3 stale-gate crashes, 2 suites blocked on Windows.** These numbers are not end-to-end transactions.

## 3. Failures

### Critical for qualification, not a single bad assertion

Live API, cookie session, CSRF, RBAC, and tenant isolation were not executed. Hidden buttons were not bypassed with direct HTTP. A role matrix from this run would be invented, so it is not filled with PASS.

### Contract failures on this commit

| Test | Result |
|---|---|
| `test_issue1_manual_wing_entries_never_change_m1_allocation_or_grid` | corrected; expects physical `[10.0, 0.0, 0.0, 0.0]`. Now passes. |
| `test_unavailable_or_unqualified_m1_never_falls_back_to_manual_entries` | corrected; expects `None`, not manual `9000`. Now passes. |
| `test_utc_midnight_keeps_adjustments_on_pi_day_while_history_advances_month` | corrected; expects physical `5.0`. Now passes. |
| `test_state_build_load_flush_roundtrip_and_checksum` | Windows skips only `os.O_DIRECTORY` flush. Checksum reload passes. |
| `test_execute_local_transition_toggle_rejection_and_energy_gate` | corrected; stub sets `_capability_error`. ON with `CAPABILITY_STATE_INVALID` stays refused. Now passes. |
| `test_ast_method_parity_with_task_start_hash` | corrected; unchanged methods stay on `fc41d937`. The four fail-closed methods are frozen to `7b36940`. Now passes. |
| `test_state_sync_control_and_budget_cadences_unchanged_against_task_start` | corrected; newline-normalized source compare. Now passes. |
| `strict_6_4_1` | member UI check failed: control panels are not all rendered only behind `!readOnly` |
| allocation visibility | `DAY_BASED` was not found across the helper, selector, and summary type together |
| `test_feedback_failsafe` | mocked controller `boot()` was not true |
| P3 reconciliation | 12 mocked-GPIO checks failed, including timeout still allowing a later MAKE, repeated mixed/noise feedback verifying, mismatch and multiple-relay cases writing GPIO, and a queued transition after reconciliation failure |
| `dashboard_presentation.test.cjs` | corrected; renders `OperationalDashboard` through `qa/dashboard_layout.cjs` into a temp directory. Now passes. |

The P3 and failsafe results are mocked. They are not a physical FAIL of a contactor, and they are also not a physical PASS.

## 4. Inventory and coverage

Existing tests are contract, source, or mocked-firmware tests unless noted. None of the rows below were promoted to end-to-end because a source assertion is not an end-to-end test.

| Area | Existing tests | This run |
|---|---|---|
| AUTH | Cookie/`/api/auth/refresh` strings in `strict_6_4_1` and the stale 6.5 gate. `frontend/test_system.py` is a live script and was not run. | Source checks partially ran. Login, refresh, logout, expired session: **NOT RUN** |
| RBAC | `strict_6_4_1` role-hook and member read-only checks. One member check failed. | Direct API matrix **NOT RUN** |
| TENANT ISOLATION | No executed cross-society HTTP test | **NOT RUN** |
| SOCIETY / USER / DEVICE MANAGEMENT | Commissioning and provisioning unit tests are inside the 185. No failure named them. | API create/list **NOT RUN** |
| PI SYNC | `test_phase3_sync_writes`, `test_energy_sync_flow`, `test_cloud_limits_storage` are in the unittest run and did not appear in the failure list | Live sync **NOT RUN** |
| COMMANDS | `test_phase2a_command_reliability`, `test_phase2b_idle_claim` in the unittest run | Full queued→acked chain on a device **NOT RUN** |
| ENERGY | E1/E3 blocked. E2/E4 not started. Allocation and calendar unit tests are inside the 185/185 pass. | **CONDITIONAL / BLOCKED** |
| CALCULATION MODES | `test_operational_mode_contract` and the manual-generation node tests passed. Manual M1 unittest now passes on physical generation. | **PASS** offline |
| ALLOCATION / DAY BASED | `test_day_allocation`, allocation visibility (20 pass, 1 fail), node wing-enablement tests passed | Live batch apply **NOT RUN** |
| METERS | Hardware capability checks lock M1–M5 ids (65/65). | No Modbus device |
| HARDWARE CAPABILITIES | 65/65 local checks, including corrupt document, omitted document, hash change, capability-only update, power-loss persistence | Not applied on a Pi |
| FAILSAFE | 35/35 source-of-truth mock passed. Failsafe suite still aborts on `boot()`. P3 mock is 68/68. | **not physical** |
| STORAGE | `test_cloud_limits_storage` is an in-memory SQL mock inside the 185 | Measured growth **NOT RUN** |
| OTA | `test_firmware_release` in the 185 (no failure named). `runtime_6_4_1_test.py` 3/3. | Source-level only |
| PROVISIONING | `test_provisioning_p0`, `test_phase4e_provisioning_package`, `test_new_pi_preflight` are in the 185 and were not in the failure list | Installer-on-Pi **NOT RUN** |
| AUDIT | Capability route test expects an audit action, inside the 65 | Live audit row **NOT RUN** |
| OBSERVABILITY | `test_phase1_instrumentation` and controller-health node tests passed. State checksum unittest now passes, including the Windows reload path. | Exception-injection of every observer **NOT RUN** |
| UI | Node component tests 56/57. No Playwright. | Browser **NOT RUN** |
| RESOURCE / RECOVERY | Health-budget byte compare failed on CRLF. No soak. | **NOT RUN** as a measurement |

## 5. Phases that were not run

Phases 2 through 7, 9, 12, 13, 16, 17, 19, 20, 21, and 23 need a disposable database, a browser, or a Pi. They are **BLOCKED** or **NOT RUN**. No society `society-one` / `society-two`, no `sa@test.local`, and no 30-day golden JSON were created. Creating them without PostgreSQL would have been a fake dataset.

Phase 8 command lifecycle, phase 10 capability safety, phase 11 failsafe, phase 14 preflight, phase 15 OTA, phase 18 failure injection, and phase 22 observability are only as covered as the suites above. They are not complete matrices.

Phase 20 and 21, because this run was not on the production Pi and no SMART sample exists:

- application bytes/day: **UNKNOWN**
- SQLite WAL bytes/day: **UNKNOWN**
- block-device writes/day: **UNKNOWN**
- USB disconnects, remounts, write latency, failed writes: **UNKNOWN**
- device serial, model, TBW: **UNKNOWN**
- life trend: **LIFE TREND NOT YET ESTABLISHED**
- `life_years` was not calculated. A missing TBW is not a healthy drive, and a block I/O ratio would not be NAND write amplification.

No per-Pi/day or fleet-year projection is stated. A projection without a measurement would be an estimate presented as data.

## 6. Matrices

### Roles

| Action | super admin | society admin | member |
|---|---|---|---|
| Browser login and session refresh | NOT RUN | NOT RUN | NOT RUN |
| Direct HTTP allow | NOT RUN | NOT RUN | NOT RUN |
| Direct HTTP deny across society | NOT RUN | NOT RUN | NOT RUN |
| Source: member page guarded and `readOnly` | n/a | n/a | one structural check failed |

### Energy

| Contract | Result |
|---|---|
| M1 generation, M2–M5 wings A–D fixed by id | PASS in capability contract (65/65). Not a live ingest. |
| Manual wing entries must not replace M1 allocation | FAIL, two unittest cases |
| Midnight operating-day adjustment | FAIL, `5.0 != 8` |
| AUTO vs MANUAL calculation, same API contract across summary, graph, dashboard | NOT RUN |
| AUTO / MANUAL / DAY_BASED allocation uncoupled from calculation mode | partial source PASS, one visibility FAIL, no live batch |
| Zero vs missing vs unavailable | NOT RUN against a database |

### Commands and failsafe

| Claim | Result |
|---|---|
| Software command reaches `hardware_verified` | NOT RUN on a device |
| Duplicate ACK, expiry, retry, offline queue | covered only by existing unit tests that passed inside the 185; not re-listed as a full matrix |
| A ON then B ON with interlock, never both ON | 35/35 mocked source-of-truth PASS. P3 mock has failures. Physical: NOT RUN |
| Unknown feedback reported as verified | mocked tests that ran say no. Not physical proof. |

### OTA, split on purpose

| Gate | Result |
|---|---|
| SOURCE-LEVEL OTA | PASS for signature verify, tamper reject, and firmware-release unit tests that did not fail |
| PHYSICAL OTA | NOT RUN |

### Storage

| Measurement | Value |
|---|---|
| API requests/sec, p50/p95/p99, CPU, RAM | NOT RUN |
| DB rows inserted/changed/archived | NOT RUN |
| log bytes, telemetry bytes, WAL bytes, disk free | NOT RUN |
| 1 / 10 / 50 / 100 Pi projections | not calculated |

## 7. Gates

| Gate | Status |
|---|---|
| A Unit/contract | **PASS** for the offline suites (185/185 unittest, 57/57 node). `strict_6_4_1` still fails the `gpio_manager.py` sha256 pin. Three older gates still name missing migrations. |
| B API integration | **BLOCKED** (no disposable PostgreSQL) |
| C Browser E2E | **NOT RUN** (no Playwright/Cypress) |
| D RBAC/tenant | **NOT RUN** as HTTP. Member UI source checks now match the current components and still require backend role rejection. |
| E Energy correctness | **CONDITIONAL** — operational M1 and operating-day contract tests now pass. E1/E3 remain blocked on Windows. Live API energy tests were not run. |
| F Pi simulator | **BLOCKED** (`os.statvfs` on Windows) |
| G Failure injection | **NOT RUN** as the requested matrix |
| H Storage/resource | **NOT RUN** |
| I OTA simulation | **PASS** at source level only |
| J Physical HIL | **NOT RUN** |
| K Endurance | **NOT RUN** |
| L Production deployment | **NOT RUN** |

## 8. Evidence boundaries

- A passing unittest is not production proof.
- Mock GPIO is not a contactor result. The 35/35 hardware source test does not qualify installed relays.
- No capacity number was turned into endurance. SMART was not read, so the drive is not reported healthy.
- No HTTP 200 from this run is a physical execution. The live backend was not started.
- UI node tests are not authorization proof.
- Desired configuration was not shown to be applied configuration.
- A completed command was not shown to be physical verification.
- Preflight remains an expected-prerequisite report. This run did not inspect a Pi, and the preflight unit tests that ran do not rotate credentials. That is not an installer execution.

## 9. Blockers before the next level

1. A disposable PostgreSQL, migrated with Alembic, with `DATABASE_URL` set only to that database. Then seed the two-society dataset and run auth, RBAC, tenant, energy round-trip, and command lifecycle over HTTP.
2. Playwright (or an equivalent browser runner that is actually installed) for super admin, society admin, and member, including cookie session, refresh, logout, CSRF, and confirmation dialogs.
3. A Linux runner for E1/E3, or a harness that does not require `os.statvfs` at import. Windows cannot execute that simulator today.
4. The offline contract failures from the triage (M1, operating day, checksum, capability stub, AST freeze, CRLF, member UI, P3, dashboard layout path) are updated in tests only. `strict_6_4_1_check.py` still fails the existing `gpio_manager.py` sha256 pin. That pin was not moved.
5. Update or retire `industrial_6_5_gate.py`, `strict_6_4_check.py`, and `strict_6_5_check.py` so they read the migrations that exist (`0001_ems_baseline.py` through `0018_firmware_releases.py`).
6. Physical HIL on a non-production Pi: feedback, interlock, welded contactor, power loss, offline queue, and OTA rollback. Record that separately from source OTA.
7. A measured storage window on that Pi before any life model. Until SMART and TBW exist, life stays **LIFE TREND NOT YET ESTABLISHED**.
8. Production deployment verification only after the gates above, still without treating this report as that verification.

## 10. Final QA cleanup rerun

NO PRODUCTION CODE WAS CHANGED.

| Suite | Before | After |
|---|---|---|
| Python unittest | 179 passed, 5 failed, 1 error, 185 total | 185 passed, 0 failed, 0 errors |
| Node | 56 passed, 1 failed, 57 total | 57 passed, 0 failed |
| P3 | 68/68 | 68/68 |
| Hardware capabilities | 65/65 | 65/65 |
| Mocked GPIO source-of-truth | 35/35 | 35/35 |

PostgreSQL E2E, browser E2E, Linux Pi simulator, physical HIL, storage endurance, SMART, hardware life, and physical OTA were not run. They stay **BLOCKED**, **NOT RUN**, or **UNKNOWN**. Verdict is unchanged: software **CONDITIONAL**, industrial **FAIL**, production readiness **BLOCKED**.
