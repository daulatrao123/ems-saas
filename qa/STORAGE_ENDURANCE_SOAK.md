# EMS Storage Endurance Qualification — P0 Gate

Run this on the exact production Raspberry Pi OS image, kernel, USB storage device, filesystem and EMS build. Capacity alone does not establish flash life.

## Required measurements

At 0 h, 24 h, 7 d and 30 d record:
- application bytes written/day
- SQLite/WAL growth and checkpoints
- filesystem/block-device writes
- OS/journal/background writes
- USB disconnect/reconnect events
- write latency and failed writes
- free capacity
- storage health/SMART or device-specific endurance counters when available
- calculated MB/day, GB/year, TB/year and measured write amplification

## Acceptance

1. Application logging remains <= 10 MB/day under normal operation.
2. Physical/system write budget remains <= 50 MB/day unless an approved exception is documented.
3. No unexplained growth in background writes.
4. No filesystem corruption, read-only remount, USB resets or failed writes.
5. Calculate endurance from the selected device's rated TBW/P-E specification and measured workload. **Do not infer 10-year life from 128 GB capacity.**

The existing storage budget is a protection policy, not an endurance guarantee.
