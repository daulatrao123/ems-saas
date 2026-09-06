#!/bin/bash
set -euo pipefail
ROOT=/mnt/ems-data/ota
BASE=/opt/ems/pi_firmware
export PYTHONPATH="$BASE:${PYTHONPATH:-}"

if [[ -f "$ROOT/active.json" ]]; then
  ACTIVE=$(python3 - <<'PY'
import json
from pathlib import Path
p=Path('/mnt/ems-data/ota/active.json')
try:
    v=json.loads(p.read_text()).get('active')
    print(v if v in ('a','b') else '')
except Exception:
    print('')
PY
)
  if [[ "$ACTIVE" == "a" || "$ACTIVE" == "b" ]]; then
    python3 -c 'from ota_manager import rollback_if_unconfirmed; rollback_if_unconfirmed()'
    ACTIVE=$(python3 - <<'PY2'
import json
from pathlib import Path
try: print(json.loads(Path('/mnt/ems-data/ota/active.json').read_text()).get('active',''))
except Exception: print('')
PY2
)
    if [[ "$ACTIVE" == "a" || "$ACTIVE" == "b" ]] && [[ -f "$ROOT/slot_${ACTIVE}/ems_controller.py" ]]; then
      exec python3 "$ROOT/slot_${ACTIVE}/ems_controller.py"
    fi
  fi
fi
exec /usr/bin/python3 "$BASE/ems_controller.py"
