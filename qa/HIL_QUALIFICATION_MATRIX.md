# EMS Hardware-in-the-Loop Qualification — P0 Gate

This document is a qualification procedure, not a simulated pass. A release is **not production-certified** until every row has evidence from the exact production Raspberry Pi, relay/contactor assembly, feedback circuit, PSU and wiring.

| Test | Injection | Required result | Evidence |
|---|---|---|---|
| Normal break-before-make | Switch A→B | A OFF, 500 ms interlock, B ON; feedback verified when installed | timestamped GPIO + feedback trace |
| Power loss during interlock | Remove Pi power inside 500 ms interlock | On reboot, no automatic unsafe make; state enters reconciliation path | video + event log + state file |
| Welded ON contactor | Force A feedback ON while command is OFF | MISMATCH_OFF_ON; SystemState.FAULT; no normal execution | feedback trace + event |
| Failed-to-energize | Command ON, feedback remains OFF | timeout/mismatch; command FAILED; no false VERIFIED_ON | trace |
| Feedback absent | Disable feedback hardware | GPIO_CONFIRMED may be reported, never VERIFIED_ON/OFF | API snapshot |
| Feedback disappears | Remove feedback while running | UNKNOWN/MISMATCH handling; controller remains conservative | event + snapshot |
| Reboot during EXECUTING | Cut power during hardware operation | UNKNOWN_AFTER_REBOOT; inspect physical state before reconciliation | queue + GPIO trace |
| Stale cloud command | Hold network down >120 s after delivery | lease can be reclaimed; absolute 300 s expiry remains | backend DB evidence |

**Acceptance:** zero unsafe simultaneous energization, zero false physical verification, deterministic FAULT on welded/fault feedback cases, and reproducible recovery after power loss.
