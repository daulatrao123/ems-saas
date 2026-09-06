import json, os, sqlite3, tempfile
os.environ.setdefault("EMS_DEVICE_ID", "qa-device")
os.environ.setdefault("EMS_API_KEY", "qa-api-key")
os.environ.setdefault("EMS_API_BASE_URL", "http://127.0.0.1:8000/api")
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pi_firmware'))
import offline_queue, config, state
from state import PiStateManager

class Storage:
    def is_write_allowed(self, _): return True

with tempfile.TemporaryDirectory() as td:
    db=Path(td)/'queue.sqlite'
    offline_queue.DB_FILE=str(db)
    conn=sqlite3.connect(db)
    conn.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, slot TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL DEFAULT \'DELIVERED\', created_at TEXT)')
    conn.commit(); conn.close()
    q=offline_queue.OfflineQueue(Storage())
    cols={r[1] for r in q.conn.execute('PRAGMA table_info(commands)')}
    required={'delivered_at','started_at','hardware_verified_at','completed_at','acked_at','expires_at','attempt_count','last_error','config_version','hardware_verification','ack_status'}
    assert required <= cols, (required-cols)
    assert q.add_command('x','A','ACTIVATE','2026-01-01T00:00:00+00:00','2999-01-01T00:00:00+00:00')
    assert q.claim_next()==('x','A','ACTIVATE')
    assert q.update_status('x','HARDWARE_VERIFIED','VERIFIED_ON')
    assert q.update_status('x','COMPLETED','VERIFIED_ON')
    assert q.update_status('x','ACKED','VERIFIED_ON') is True
    q.close()

with tempfile.TemporaryDirectory() as td:
    statefile=Path(td)/'state.json'
    body={'version':4,'generation':1,'timestamp':'2026-01-01T00:00:00+00:00','system_state':'READY','active_slot':None,'slots':{s:{'slot':s,'commanded_state':'UNKNOWN','gpio_output_state':'UNKNOWN','feedback_state':'UNKNOWN','verification_state':'NOT_CONFIGURED','last_command_at':None} for s in 'ABCD'}}
    import hashlib
    canonical=json.dumps(body,sort_keys=True,separators=(',',':')); body['sha256']=hashlib.sha256(canonical.encode()).hexdigest(); statefile.write_text(json.dumps(body))
    config.STATE_FILE=str(statefile); config.BACKUP_STATE_FILE=str(Path(td)/'backup.json'); config.RECOVERY_STATE_FILE=str(Path(td)/'recovery.json'); state.STATE_FILE=str(statefile); state.BACKUP_STATE_FILE=str(Path(td)/'backup.json'); state.RECOVERY_STATE_FILE=str(Path(td)/'recovery.json')
    mgr=PiStateManager(Storage())
    assert mgr.state_loaded
    assert all(mgr.slots[s].used_days==0 and mgr.slots[s].clicks==0 for s in 'ABCD')
print('PASS - legacy SQLite schema migration and lifecycle')
print('PASS - legacy STATE_VERSION=4 JSON compatibility')
