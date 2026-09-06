"""Runtime compatibility checks for the Pi durable state and queue."""
from pathlib import Path
import os
os.environ.setdefault("EMS_DEVICE_ID", "00000000-0000-4000-8000-000000000001")
os.environ.setdefault("EMS_API_KEY", "test-key")
import json, sqlite3, sys, tempfile, hashlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pi_firmware'))

class Storage:
    def is_write_allowed(self, _): return True


def test_legacy_queue():
    import offline_queue
    with tempfile.TemporaryDirectory() as td:
        db=Path(td)/'legacy.sqlite'
        c=sqlite3.connect(db)
        c.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, slot TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL DEFAULT \'DELIVERED\', created_at TEXT)')
        c.commit(); c.close()
        offline_queue.DB_FILE=str(db)
        q=offline_queue.OfflineQueue(Storage())
        cols={r[1] for r in q.conn.execute('PRAGMA table_info(commands)').fetchall()}
        required={'delivered_at','started_at','hardware_verified_at','completed_at','acked_at','expires_at','attempt_count','last_error','config_version','hardware_verification','ack_status'}
        assert required <= cols, required-cols
        q.add_command('legacy-1','A','ACTIVATE','2026-09-06T00:00:00+00:00','2026-09-06T00:05:00+00:00')
        row=q.conn.execute("SELECT status FROM commands WHERE id='legacy-1'").fetchone()
        assert row[0]=='DELIVERED'
        assert q.get_interrupted()==[]
        q.close()


def test_legacy_state():
    import state
    # Build a valid version-4 document that intentionally omits all new counters.
    slots={k:{'commanded_state':'UNKNOWN','gpio_output_state':'UNKNOWN','feedback_state':'UNKNOWN','verification_state':'NOT_CONFIGURED','last_command_at':None} for k in state.SUPPORTED_SLOTS}
    body={'version':4,'generation':1,'active_slot':None,'system_state':'BOOT','slots':slots}
    body['sha256']=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert state.PiStateManager._verify_document(body)
    for data in slots.values():
        assert 'used_days' not in data and 'clicks' not in data
    # SlotState parser must safely default missing counters.
    obj=state.SlotState('A')
    assert obj.from_dict(slots['A'])
    assert obj.used_days==0 and obj.clicks==0

if __name__=='__main__':
    test_legacy_queue(); test_legacy_state(); print('PASS: legacy SQLite queue migration'); print('PASS: legacy STATE_VERSION=4 state compatibility')
