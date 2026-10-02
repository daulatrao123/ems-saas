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
console.log(JSON.stringify({ modes: m.ALLOCATION_MODES, matrix, balance, target, bad: m.isAllocationMode("AUTOX") }));
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

    dash = read(FE / "OperationalDashboard.tsx")
    panel = read(FE / "energy" / "EnergyPanel.tsx")
    wing = read(FE / "energy" / "WingEnergyCard.tsx")
    slot = read(FE / "SlotCard.tsx")
    types = read(FE / "energy" / "types.ts")
    check("dashboard defaults AllocationMode to AUTO and uses the shared helper", 'useState<AllocationMode>("AUTO")' in dash and "allocationVisibility(allocationMode)" in dash and "excessPresentationEnabled(allocationMode," in dash)
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
    frontend_hits = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "frontend").rglob("*.ts*") if "DAY_BASED" in read(p))
    check("DAY_BASED stays in the allocation helper, its selector, and the summary type", frontend_hits == ["frontend/src/components/ops/allocationMode.ts", "frontend/src/components/ops/energy/EnergyPanel.tsx", "frontend/src/components/ops/energy/types.ts"], ", ".join(frontend_hits))
    print("ALLOCATION_MODE_VISIBILITY_OK")


if __name__ == "__main__":
    main()
