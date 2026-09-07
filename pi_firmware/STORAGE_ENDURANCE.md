# Pi Flash Storage Endurance — Phase 0.5

Scope: software write-budget control, retention cleaning and storage-protection
bands for the EMS Raspberry Pi controller. This document does **not** certify
NAND endurance; it describes what the firmware does and what the OS image must
provide.

## 1. Write policy (what writes, when)

| Source | Trigger | Category | Under pressure |
|---|---|---|---|
| Command queue (`ems_queue.sqlite`, WAL, `synchronous=FULL`) | per command lifecycle (~6 commits) | `queue_db` | **always written** |
| `state/current_state.json` (tmp + fsync + rename) | command execution, daily accounting | `state` | **always written** |
| `logs/critical.log` (fsync per record) | ERROR/CRITICAL | `critical_log` | **always written** |
| `logs/ems_app.log` | INFO/WARNING, daily byte budget | `normal_log` | dropped at CRITICAL and above |
| `telemetry/*.json` counters | hourly (`persist_counters`) + shutdown | `other` | dropped at CRITICAL and above |
| `telemetry/daily_*.csv` | daily | `telemetry` | dropped at CRITICAL and above |
| diagnostics | on demand | `diagnostics` | dropped at STORAGE_PROTECTION |
| `/api/pi/sync` (every 60 s) | — | — | **never writes** (RAM + SQLite reads only) |

A full or failed filesystem never converts a safety-critical write into an
in-memory-only operation: `queue_db`/`state`/`critical_log` are always
attempted and their failure is surfaced (`add_command()` returns `False`, the
controller logs CRITICAL, sync reports `storageState=STORAGE_FAILED`).

## 2. Storage bands (`resource_guard.ResourceGuard`)

| Usage of `/mnt/ems-data` | State | File cleanup | Optional writes |
|---:|---|---|---|
| `< 70%` | `NORMAL` | no | all |
| `70% ≤ u < 80%` | `WARNING` | no | all |
| `80% ≤ u < 90%` | `CLEANUP_ELIGIBLE` | hourly, retention constants | all |
| `90% ≤ u < 95%` | `CRITICAL` | + logs 7 d, telemetry 30 d | diagnostics only |
| `≥ 95%` | `STORAGE_PROTECTION` | + logs 2 d, telemetry 7 d, diagnostics 7 d | none |
| `statvfs` failure | `STORAGE_FAILED` | none (never delete blind) | none |

Boundaries are inclusive on the lower edge (80.0% is `CLEANUP_ELIGIBLE`, not
`WARNING`). Daily write-budget overrun or memory pressure additionally sets
`write_reduced` (diagnostics only) without changing the reported band.
`critical.log*` files are never deleted by capacity cleanup.

## 3. Cloud visibility

Every sync carries `storageState`, `storageUsedPercent` and `diskFreeMB`
(`free bytes / 1024²`, one decimal). `diskFreeMB = -1.0` is an explicit
sentinel meaning `statvfs` failed — it is never a silent `0`.

Each band **transition** produces exactly one `events[]` entry
(`type: "storage"`, `eventId: uuid4`, message `STORAGE_STATE A -> B used=..% free=..MB`).
Staying in a band produces no event. Events live in RAM (bounded to 20), are
resent until a sync succeeds and are deduplicated server-side by
`pi_events.event_id` (`ON CONFLICT DO NOTHING`). Recovery transitions
(`STORAGE_PROTECTION -> CRITICAL -> ...`) are reported the same way.

## 4. ACKED command retention (`OfflineQueue.cleanup_acked`)

```
KEEP an ACKED row if
    acked_at >= now - 30 days
    OR its rank among ACKED rows (newest first) <= 500
```
whichever retains **more**. Only `ack_status='ACKED'` rows are counted or
deleted; DELIVERED / EXECUTING / HARDWARE_VERIFIED / UNKNOWN_AFTER_REBOOT and
unacknowledged COMPLETED/FAILED/EXPIRED rows are never touched. Deletion runs
hourly, bounded to 100 rows per run (a backlog drains gradually), uses SQL
ordering (not Python), and issues `PRAGMA wal_checkpoint(PASSIVE)` only when
rows were actually deleted. WAL mode and `synchronous=FULL` are unchanged.

## 5. Measured software write budget (simulations, June 2026)

`test_reports/phase05_24h_sim.py` (10 commands/day, all bands exercised):
≈ **0.73 MB/day** process writes (`/proc/self/io wchar`), 1.2 MB/day at the
block layer → ≈ 2.6 GB / 10 years.
`test_reports/phase05_30d_sim.py 40` (40 commands/day, 500-row and 30-day
boundaries crossed): ≈ **3.0 MB/day** → ≈ 10.6 GB / 10 years.
Budget: 50 MB/day (≈ 178 GB / 10 years). Both runs are well inside.

**These are software-generated write volumes and projections, not endurance
proofs.** Real NAND wear depends on the card's controller / FTL write
amplification, workload, power-loss behaviour and SMART telemetry.

## 6. OS image requirements (documentation only — not changed by firmware)

### 6.1 `noatime` on the data mount
Add `noatime` (and `nodiratime`) to the `/mnt/ems-data` entry in `/etc/fstab`
so the ~2,880 daily SQLite reads and log reads do not rewrite inode
access-time metadata:

```
UUID=<data-uuid>  /mnt/ems-data  ext4  defaults,noatime,nodiratime,commit=30  0  2
```
`commit=30` lengthens the ext4 journal flush interval for non-fsync'd data;
safety-critical files are fsync'd explicitly by the firmware and are not
affected. Do **not** use `data=writeback` or `nobarrier`.

### 6.2 SMART / device health sampling
The firmware records `/proc/diskstats` block-write counters (`lifetime_block_writes`)
and the device serial (`lsblk -o SERIAL`). It does **not** call `smartctl`.
For NVMe/USB-SSD deployments provision `smartmontools` in the image and sample
once per day (cron/systemd timer), writing to `telemetry/` only when the
storage state is below `CRITICAL`:

```
smartctl -A -j /dev/<device> > /mnt/ems-data/telemetry/smart_$(date +%F).json
```
SD cards generally do not expose SMART; for those, rely on the firmware's
`lifetime_block_writes`, the vendor's TBW rating and periodic `fsck`.

### 6.3 Other image-level guidance
- Journald: `Storage=volatile` or `SystemMaxUse=32M` on the root filesystem.
- Swap: disable `dphys-swapfile`; the firmware's memory monitor treats swap
  usage > 10 MB as a warning.
- Use industrial / high-endurance (pSLC or MLC) cards sized so the data
  partition stays below 70% in normal operation.
