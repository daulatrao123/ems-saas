# Storage Endurance Qualification — Gate 2

Use the exact production Raspberry Pi OS image, kernel, filesystem, USB storage device, EMS build, systemd service, and configuration.

Measure at day 0, 24h, 7d, and 30d:

- application bytes written by EMS
- SQLite/WAL bytes and checkpoints
- filesystem/block-device writes where available
- USB/media health counters where available
- free capacity
- write latency and failed writes
- read-only filesystem events
- USB disconnect/reconnect events
- OS/background write contribution
- EMS `system_waf` / equivalent measured write amplification

Calculate:

`GB/year = measured_daily_physical_bytes * 365 / 1e9`

Do not convert application-log budget directly into flash life. Endurance acceptance must use the production media's rated endurance plus measured total workload and an engineering safety margin.

**Release rule:** no 10-year endurance claim until the exact workload is measured and the selected media endurance rating supports it with margin.
