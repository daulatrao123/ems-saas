# Storage Endurance Qualification Plan

Software budgets are not NAND endurance ratings. Qualification must use the exact production Pi OS, filesystem, USB device, controller and EMS workload.

## Measurements
At 0 h, 24 h, 7 d, 14 d and 30 d record:
- application log bytes written
- SQLite/WAL bytes written
- total filesystem/block-device writes
- storage free bytes
- write latency / failed writes
- USB disconnect/reconnect events
- filesystem read-only events
- device SMART/UAS health where supported
- estimated system write amplification (physical or block-device writes / application writes)

## Pass criteria
- No filesystem corruption or unexpected read-only remount.
- No unexplained USB disconnects.
- Application log budget <= 10 MB/day.
- System physical-write budget remains within the qualified production-device endurance model.
- Recovery after controlled power interruption succeeds.

Do not convert the 128 GB capacity figure into a 10-year endurance claim without the measured endurance rating and measured workload.
