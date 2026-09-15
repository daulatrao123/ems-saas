# EMS CI checks

Workflow: `.github/workflows/qa.yml` — **EMS Strict QA**, on pushes and pull requests.

## Independent frontend validation

Job ID: `frontend-validation`. Check name: **Frontend validation**.
This job has no dependency on the existing Python/energy jobs, so it reports
frontend failures even when a separate safety gate fails. It does not replace
or bypass any existing check.

It validates the checked-out revision in this order:

1. Record the actual commit SHA and Node/npm versions.
2. Use Node **22**, supported by the locked Next.js 16.2.12 requirement
   (`>=20.9.0`), and run `npm ci --no-audit --no-fund` from `frontend/`.
   The existing `frontend/package-lock.json` is authoritative; dependency or
   lockfile changes are not part of this CI addition.
3. Run the installed `next typegen` to generate route types on a fresh runner.
4. Run the installed `tsc --noEmit --incremental false` against the whole project.
5. Run these existing offline Node regression suites explicitly:
   - `qa/bill_editor_changes.test.cjs`
   - `qa/controller_health.test.cjs`
   - `qa/generation_adjustment_date.test.cjs`
   - `qa/manual_generation_policy.test.cjs`
   - `qa/mode_consumption_comparison.test.cjs`
6. Run `npm run build` with the existing Next.js configuration.
7. Confirm HEAD is still the recorded SHA and tracked files/index were not
   changed by installation, tests or build. Record success only after all pass.

No step uses `continue-on-error` or an ignored exit status. Generated untracked
Next.js output is expected, but it does not replace the tracked-source checks.
Only npm's download cache is used; `node_modules` and `.next` build/type output
are not restored as job artifacts or caches.

### Build environment and boundaries

- `EMS_BACKEND_URL=http://127.0.0.1:9` is a **CI-only unused rewrite target**
  required by `next.config.ts`. No backend is started or authenticated, no
  production URL/credential is needed, and this build artifact is not deployed.
- Next telemetry is disabled for this job. Dependency installation and the
  existing Next/font build can require normal registry/font network access;
  this does not make the frontend regression tests live API tests.
- Regression API/data boundaries are mocked/in-memory. The job does not execute
  firmware, GPIO, RS485, contactors, migrations, or physical-device tests.
- Pinning the CI Node major does not change the hosting runtime. Confirm the
  hosting Node version separately when assessing exact environment parity.
- For a pull request, the standard checkout validates the revision supplied by
  GitHub (normally its PR merge revision), not a forced checkout of `main`.

### Local validation recorded (2026-09-15)

A clean, isolated archive of application/QA source at
`55b089749d7b044ab5fe74edc2be03ab4de52bda` passed locked installation, route
type generation, whole-project TypeScript, all **17** tests in the five listed
suites, and the real production build under **Node 22.23.2 / npm 10.8.2**.
The copy did not reuse the workspace's `.env`, `node_modules`, or `.next` output.
Tracked-file hashes matched before/after validation.

A negative control removed the `ReactNode` type import **only in the disposable
copy**: TypeScript exited nonzero with TS2304. Restoring its exact bytes restored
the passing check. The actual repository's application and test files were not
edited. Workflow/YAML and shell syntax, original-job preservation and final
revision-check structure were also validated. This is not a hosted Actions run;
GitHub checkout/output/required-check enforcement still need hosted verification.

## Existing safety and backend checks remain authoritative

The `python-static` and `energy-regression` jobs are unchanged. They retain the
strict gate, E1/E3, ephemeral PostgreSQL E2 and E4 checks. Their existing runtime
requirements and outcomes are independent of the frontend job.

At the pre-change revision `55b089749d7b044ab5fe74edc2be03ab4de52bda`, a read-only
comparison found protected hash mismatches for GPIO/logger in
`qa/strict_6_4_1_check.py` and GPIO in E4. This is not a full hosted CI result.
The new job does **not** resolve or authorize those discrepancies. Do not change
protected hashes, delete assertions or ignore failures just to obtain green CI.
Baseline changes require their separate evidence/authorization process.

## Making the frontend check required

Publishing this workflow creates a check; it does **not** configure repository
branch protection or rulesets. After its first hosted run, a repository
administrator must select the emitted **Frontend validation** check as required
for the protected branch, while retaining the existing required safety checks.
Verify that the requirement applies to the current PR revision. Ruleset and
branch-protection settings cannot be established from the workflow file alone.

No hosted run, required-check enforcement, overall release readiness, hardware
safety or physical endurance is claimed solely from local validation.