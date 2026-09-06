# Hardware-in-the-Loop Qualification Plan

Run only on a controlled bench with the actual production Raspberry Pi, relay/contactor assembly, feedback wiring, fuse/protection and production power supply.

## HIL-01 Break-before-make
- Command active slot A -> B.
- Verify A output is OFF before B output becomes ON.
- Measure minimum 500 ms interlock.
- Pass: no overlap and target feedback reaches expected state within timeout.

## HIL-02 Welded contactor simulation
- Simulate an ON contactor that remains ON after an OFF command.
- Pass: feedback mismatch is detected, system enters `FAULT`, and no conflicting activation is attempted.

## HIL-03 Missing feedback
- Disable/remove feedback input while relay output remains controllable.
- Pass: system reports `NOT_AVAILABLE`/`NOT_CONFIGURED` or `GPIO_CONFIRMED` as designed; it never claims physical contactor verification.

## HIL-04 Power loss during 500 ms interlock
- Start an A -> B transition.
- Remove Pi power during the interlock window.
- Restore power.
- Pass: boot reconciliation inspects actual hardware, interrupted command becomes reconciled or `UNKNOWN_AFTER_REBOOT`, and no unsafe automatic assumption is made.

## HIL-05 Reboot during EXECUTING
- Cut power while a command is executing.
- Pass: local durable queue + physical reconciliation produce a deterministic safe state or explicit unknown state.

Record: firmware version, OS image hash, hardware profile, GPIO mapping, measured timings, oscilloscope/current traces, result, operator and date.
