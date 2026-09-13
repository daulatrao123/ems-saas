#!/bin/bash
# Energy regression (E1 Pi core, E3 allocation policy: self-contained; E2: needs live backend :8001 + PostgreSQL :5433/ems_fresh).
# Usage: qa/run_energy_regression.sh            -> E1 + E3
#        qa/run_energy_regression.sh --with-e2  -> E1 + E3 + E2
set -u
cd "$(dirname "$0")/.."
export EMS_DEVICE_ID="${EMS_DEVICE_ID:-t}" EMS_API_KEY="${EMS_API_KEY:-t}"
SUITES="energy_e1_test energy_e3_test"
[ "${1:-}" = "--with-e2" ] && SUITES="$SUITES energy_e2_test"
rc=0
for t in $SUITES; do
  echo "=== $t"
  python3 "qa/$t.py" > "/tmp/$t.log" 2>&1; r=$?
  grep -E "^FAIL|passed|Traceback" "/tmp/$t.log" | tail -5
  [ $r -ne 0 ] && { echo "!!! $t FAILED (exit $r)"; rc=1; }
done
echo "=== DONE rc=$rc"; exit $rc
