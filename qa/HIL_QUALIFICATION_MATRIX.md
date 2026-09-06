# Physical HIL Qualification Matrix — Gate 1

Run only on the exact production Raspberry Pi, relay/contactor assembly, feedback wiring, power supply, and GPIO mapping.

| Test | Injection | Expected safe result | Pass evidence |
|---|---|---|---|
| Normal ON | Activate one slot | Break-before-make, then verified ON when feedback exists | Timestamped GPIO + feedback trace |
| Normal OFF | Deactivate active slot | Relay OFF and verified OFF when feedback exists | Trace |
| Welded ON | Force feedback ON while command requests OFF | `FAULT` / mismatch; no normal execution continues | Event + state + physical observation |
| Feedback open | Disconnect feedback during operation | Verification becomes unavailable/mismatch according to wiring; unsafe action is blocked | Event + trace |
| Power loss during 500 ms interlock | Remove Pi power during interlock | On reboot, no blind continuation; reconcile actual hardware before execution | Video + state/queue recovery log |
| Power loss after relay command | Remove Pi power immediately after GPIO mutation | Reboot reconciliation produces verified/unknown result, never a false verified claim | State + physical result |
| Stuck relay | Simulate commanded OFF but feedback remains ON | Mismatch detected and normal execution inhibited | Event + fault state |
| Cloud loss | Disconnect network | Local safety and durable queue remain functional; no repeated cloud command execution | Queue DB + logs |

**Release rule:** any unexplained mismatch, false verification, or unsafe energization is a hard fail.
