"""AllocationMode is frontend presentation state. It must not enter CalculationMode or the Pi."""
import json
import os
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
FE = ROOT / "frontend" / "src" / "components" / "ops"
HELPER = FE / "allocationMode.ts"


def read(path):
    return path.read_text(encoding="utf-8")


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not condition:
        raise SystemExit(1)


def compiled_matrix():
    with tempfile.TemporaryDirectory() as out:
        subprocess.run(
            ["node", str(ROOT / "frontend" / "node_modules" / "typescript" / "lib" / "tsc.js"), str(HELPER), "--outDir", out,
             "--module", "commonjs", "--target", "es2020", "--strict", "--skipLibCheck"],
            cwd=ROOT / "frontend", check=True,
        )
        script = r"""
const m = require(process.argv[1]);
const modes = ["AUTO", "MANUAL", "DAY_BASED"];
const matrix = Object.fromEntries(modes.map((mode) => [mode, m.allocationVisibility(mode)]));
const balance = {};
for (const mode of modes) {
  balance[mode] = {
    surplusOn: m.showBalance(mode, 4.5, true),
    surplusOff: m.showBalance(mode, 4.5, false),
    deficit: m.showBalance(mode, -1.25, true),
    zero: m.showBalance(mode, 0, false),
    missing: m.showBalance(mode, null, true),
  };
}
const target = {};
for (const mode of modes) target[mode] = { missing: m.showGenerationTarget(mode, null), present: m.showGenerationTarget(mode, 11) };
const sample = m.dayBasedPresentation({
  operatingDate: "2026-04-24", resetDay: 15,
  wings: [
    { wing: "A", enabled: true, assignedDays: 9 },
    { wing: "B", enabled: true, assignedDays: 12 },
    { wing: "C", enabled: true, assignedDays: 9 },
    { wing: "D", enabled: false, assignedDays: 5 },
  ],
});
const zero = m.dayBasedPresentation({
  operatingDate: "2026-04-24", resetDay: 15,
  wings: [
    { wing: "A", enabled: true, assignedDays: 9 },
    { wing: "B", enabled: true, assignedDays: 12 },
    { wing: "C", enabled: true, assignedDays: 0 },
    { wing: "D", enabled: true, assignedDays: 9 },
  ],
});
const skipped = m.dayBasedPresentation({
  operatingDate: "2026-04-24", resetDay: 15,
  wings: [
    { wing: "A", enabled: true, assignedDays: 9 },
    { wing: "B", enabled: false, assignedDays: 12 },
    { wing: "C", enabled: true, assignedDays: 9 },
    { wing: "D", enabled: true, assignedDays: 0 },
  ],
});
const bounds = {
  leap: m.dayBasedPresentation({ operatingDate: "2024-02-15", resetDay: 15, wings: [] }).cycleDays,
  common: m.dayBasedPresentation({ operatingDate: "2023-02-15", resetDay: 15, wings: [] }).cycleDays,
  january: m.dayBasedPresentation({ operatingDate: "2026-01-10", resetDay: 15, wings: [] }),
};
console.log(JSON.stringify({ modes: m.ALLOCATION_MODES, matrix, balance, target, bad: m.isAllocationMode("AUTOX"), index: m.cycleDayIndex("2026-04-15", 15), indexEnd: m.cycleDayIndex("2026-05-14", 15), bounds, sample, zero, skipped }));
"""
        node = subprocess.run(["node", "-e", script, os.path.join(out, "allocationMode.js")], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(node.stdout)


def main():
    result = compiled_matrix()
    check("modes are AUTO, MANUAL, DAY_BASED", result["modes"] == ["AUTO", "MANUAL", "DAY_BASED"])
    check("unknown mode is rejected", result["bad"] is False)
    expected = {
        "AUTO": dict(generationMeter=True, consumptionMeter=True, meterStatus=True, actualReadings=True, excessGeneration=True, generationTarget="show", dayAllocation=False, cycleSettings=False, manualControl=True, manualControlLabel="Manual override · emergency"),
        "MANUAL": dict(generationMeter=True, consumptionMeter=True, meterStatus=True, actualReadings=True, excessGeneration=True, generationTarget="optional", dayAllocation=False, cycleSettings=False, manualControl=True, manualControlLabel="Manual allocation"),
        "DAY_BASED": dict(generationMeter=True, consumptionMeter=True, meterStatus=True, actualReadings=True, excessGeneration=False, generationTarget="hide", dayAllocation=True, cycleSettings=True, manualControl=True, manualControlLabel="Manual override"),
    }
    for mode, row in expected.items():
        check(f"{mode} visibility matrix", result["matrix"][mode] == row, json.dumps(result["matrix"][mode]))
        check(f"{mode} deficit stays visible", result["balance"][mode]["deficit"] is True and result["balance"][mode]["zero"] is True and result["balance"][mode]["missing"] is True)
    check("AUTO and MANUAL show surplus only when the grid gate is on", result["balance"]["AUTO"]["surplusOn"] is True and result["balance"]["AUTO"]["surplusOff"] is False and result["balance"]["MANUAL"]["surplusOn"] is True)
    check("DAY_BASED hides surplus even when the grid gate is on", result["balance"]["DAY_BASED"]["surplusOn"] is False and result["balance"]["DAY_BASED"]["surplusOff"] is False)
    check("generation target AUTO always, MANUAL only when present, DAY_BASED never", result["target"] == {"AUTO": {"missing": True, "present": True}, "MANUAL": {"missing": False, "present": True}, "DAY_BASED": {"missing": False, "present": False}})
    check("cycle day 1 is the reset day and the sample month is 30 days", result["index"] == 1 and result["indexEnd"] == 30 and result["sample"]["cycleDays"] == 30 and result["sample"]["cycleDayIndex"] == 10)
    check("cycle length follows the same reset-day months as the backend", result["bounds"]["leap"] == 29 and result["bounds"]["common"] == 28 and result["bounds"]["january"]["cycleDays"] == 31 and result["bounds"]["january"]["cycleDayIndex"] == 27)
    by_wing = {row["wing"]: row for row in result["sample"]["wings"]}
    check("sequential ranges and current wing", result["sample"]["scheduledWing"] == "B" and by_wing["A"]["startDay"] == 1 and by_wing["A"]["endDay"] == 9 and by_wing["A"]["status"] == "COMPLETED" and by_wing["A"]["completedDays"] == 9 and by_wing["A"]["remainingDays"] == 0)
    check("current wing has not counted today as completed", by_wing["B"]["status"] == "CURRENT" and by_wing["B"]["isTodayScheduled"] is True and by_wing["B"]["startDay"] == 10 and by_wing["B"]["endDay"] == 21 and by_wing["B"]["completedDays"] == 0 and by_wing["B"]["remainingDays"] == 12 and by_wing["B"]["scheduleLabel"] == "CURRENT · TODAY")
    check("later wing is upcoming", by_wing["C"]["status"] == "UPCOMING" and by_wing["C"]["startDay"] == 22 and by_wing["C"]["endDay"] == 30 and by_wing["C"]["completedDays"] == 0 and by_wing["C"]["remainingDays"] == 9 and by_wing["C"]["scheduledWing"] == "B")
    check("disabled wing is excluded and does not take days", by_wing["D"]["status"] == "EXCLUDED" and by_wing["D"]["startDay"] is None and by_wing["D"]["enabled"] is False)
    zero = {row["wing"]: row for row in result["zero"]["wings"]}
    check("zero assigned days have no range and are not current", zero["C"]["assignedDays"] == 0 and zero["C"]["status"] == "EXCLUDED" and zero["C"]["scheduleLabel"] == "NOT SCHEDULED" and zero["C"]["startDay"] is None and zero["C"]["isTodayScheduled"] is False and zero["D"]["startDay"] == 22 and result["zero"]["scheduledWing"] == "B")
    skipped = {row["wing"]: row for row in result["skipped"]["wings"]}
    check("disabled wing is skipped in the sequence", skipped["B"]["status"] == "EXCLUDED" and skipped["B"]["startDay"] is None and skipped["C"]["status"] == "CURRENT" and skipped["C"]["startDay"] == 10 and skipped["C"]["endDay"] == 18)
    helper = read(HELPER)
    presentation = helper.split("export function dayBasedPresentation", 1)[1]
    check("day progress does not read legacy used_days, energy totals, or contactor feedback", "used_days" not in helper and "physical_toggle" not in helper and "feedback" not in helper and "calculation" not in presentation and "consumed" not in presentation and "generated" not in presentation)

    dash = read(FE / "OperationalDashboard.tsx")
    panel = read(FE / "energy" / "EnergyPanel.tsx")
    wing = read(FE / "energy" / "WingEnergyCard.tsx")
    slot = read(FE / "SlotCard.tsx")
    types = read(FE / "energy" / "types.ts")
    check("dashboard defaults AllocationMode to AUTO and uses the shared helper", 'useState<AllocationMode>("AUTO")' in dash and "allocationVisibility(allocationMode)" in dash and "excessPresentationEnabled(allocationMode," in dash)
    check("day card reads the summary operating date and reset day", "operatingDate={energy.summary?.as_of_operating_date ?? null}" in dash and "resetDay={energy.summary?.reset_day ?? null}" in dash)
    check("slot card derives ranges from enabled target_days", "dayBasedPresentation({" in slot and "assignedDays: device.slots[wingCode]?.target_days" in slot and "enabled: device.slots[wingCode]?.disabled === false" in slot)
    manual_gate = slot.split("{!readOnly && slot && !slot.disabled && (", 1)[1][:220]
    check("manual controls stay available without consulting the schedule", "manual-control-${code}" in manual_gate and "allocationMode" not in manual_gate and "dayBased" not in manual_gate and "ACTIVATE" in slot and "DEACTIVATE" in slot)
    contactor = slot.split("const contactor", 1)[1].split("const busyCfg", 1)[0]
    check("contactor and feedback stay on physical telemetry", "physical_toggle" in contactor and "scheduledWing" not in contactor and "dayBased" not in contactor)
    check("days-and-commands section keeps the legacy quota counter", "used_days is the legacy quota counter" in slot and "data-testid={`slot-used-${code}`}" in slot and "data-testid={`slot-operations-${code}`}" in slot)
    panel_days = read(FE / "energy" / "DayAllocationPanel.tsx")
    check("day allocation and cycle settings render only from helper flags", "visibility.dayAllocation &&" in dash and "visibility.cycleSettings &&" in dash and "<DayAllocationPanel" in dash)
    check("member readOnly still hides the cycle editor and manual buttons stay operator-only", "readOnly={readOnly}" in dash and "{!readOnly &&" in panel_days and "data-testid=\"day-apply\"" in panel_days and "{!readOnly && slot && !slot.disabled && (" in slot)
    check("manual commands are unchanged", 'queue(device.id, "set_active_slot", code)' in slot and 'queue(device.id, "off_slot", code)' in slot and 'queue(device.id, "set_days", code' in slot)
    check("meters and actual readings are not gated by AllocationMode", 'data-testid="energy-generation-card"' in read(FE / "energy" / "GenerationCard.tsx") and "allocationMode" not in read(FE / "energy" / "GenerationCard.tsx") and "ACTUAL GENERATION" in wing and "energy-wing-consumption-${w}" in wing and "showGenerationTarget(allocationMode" in wing)
    check("calculation mode select is still only AUTO and MANUAL", 'data-testid="energy-calculation-mode"' in panel and panel.split('data-testid="allocation-mode"')[0].count("DAY_BASED") == 0 and 'export type CalculationMode = "AUTO" | "MANUAL"' in types)
    check("calculation consumption label still follows CalculationMode", 'mode === "MANUAL" ? "DAILY REFERENCE"' in wing)

    backend = read(ROOT / "backend" / "energy_api" / "routes.py")
    migration = read(ROOT / "backend" / "alembic" / "versions" / "0013_energy_calculation_mode.py")
    policy = read(ROOT / "pi_firmware" / "energy" / "allocation.py")
    check("backend calculation mode still rejects anything except AUTO and MANUAL", 'if mode not in ("AUTO", "MANUAL")' in backend and "DAY_BASED" not in backend)
    check("calculation-mode migration and AllocationPolicy are unchanged by this mode", "DAY_BASED" not in migration and "CHECK (energy_calculation_mode IN ('AUTO', 'MANUAL'))" in migration and "DAY_BASED" not in policy)
    # DAY_BASED is an allocation-mode UI concept. It must stay out of CalculationMode.
    # The current surfaces are the helper, the energy selector, the day panel, and the unmounted allotment component.
    frontend_hits = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "frontend").rglob("*.ts*") if "DAY_BASED" in read(p))
    check("DAY_BASED stays in the allocation UI and out of calculation mode", frontend_hits == ["frontend/src/components/ops/UnitAllotment.tsx", "frontend/src/components/ops/allocationMode.ts", "frontend/src/components/ops/energy/DayAllocationPanel.tsx", "frontend/src/components/ops/energy/EnergyPanel.tsx", "frontend/src/components/ops/energy/types.ts"], ", ".join(frontend_hits))
    print("ALLOCATION_MODE_VISIBILITY_OK")


if __name__ == "__main__":
    main()
