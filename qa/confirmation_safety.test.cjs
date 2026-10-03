"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require(path.join(__dirname, "..", "frontend", "node_modules", "typescript"));

function loadSession() {
  const file = path.join(__dirname, "..", "frontend", "src", "components", "ops", "confirmationSession.ts");
  const out = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: "confirmationSession.ts",
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(out, { module, exports: module.exports }, { filename: "confirmationSession.cjs" });
  return module.exports;
}

const sessionApi = loadSession();
const read = (rel) => fs.readFileSync(path.join(__dirname, "..", rel), "utf8");

test("opening, cancel, escape, and backdrop do not execute", () => {
  const opened = sessionApi.openSession();
  assert.equal(opened.executions, 0);
  assert.equal(opened.status, "open");
  for (const dismiss of [sessionApi.requestCancel, sessionApi.requestEscape, sessionApi.requestBackdrop]) {
    const decision = dismiss(sessionApi.openSession());
    assert.equal(decision.executed, false);
    assert.equal(decision.session.status, "closed");
    assert.equal(decision.session.executions, 0);
  }
});

test("confirm executes once and a second confirm while running does not", () => {
  const first = sessionApi.requestConfirm(sessionApi.openSession());
  assert.equal(first.executed, true);
  assert.equal(first.session.executions, 1);
  assert.equal(first.session.status, "running");
  const second = sessionApi.requestConfirm(first.session);
  assert.equal(second.executed, false);
  assert.equal(second.session.executions, 1);
});

test("typed confirmation blocks the wrong phrase and allows the exact phrase", () => {
  const wrong = sessionApi.openSession();
  wrong.typed = "revoke device";
  const blocked = sessionApi.requestConfirm(wrong, "REVOKE DEVICE");
  assert.equal(blocked.executed, false);
  assert.equal(blocked.session.status, "open");
  const right = sessionApi.openSession();
  right.typed = "REVOKE DEVICE";
  const allowed = sessionApi.requestConfirm(right, "REVOKE DEVICE");
  assert.equal(allowed.executed, true);
  assert.equal(allowed.session.executions, 1);
});

test("a failed confirm can be retried once and does not count the failure as a second success", () => {
  const running = sessionApi.requestConfirm(sessionApi.openSession()).session;
  const released = sessionApi.releaseAfterFailure(running);
  assert.equal(released.status, "open");
  assert.equal(released.executions, 1);
  const retry = sessionApi.requestConfirm(released);
  assert.equal(retry.executed, true);
  assert.equal(retry.session.executions, 2);
});

test("dialog source keeps cancel as the focused action and does not confirm from the backdrop", () => {
  const src = read("frontend/src/components/ops/ConfirmDialog.tsx");
  assert.match(src, /requestConfirm/);
  assert.match(src, /requestCancel/);
  assert.match(src, /cancelRef\.current\?\.focus\(\)/);
  assert.match(src, /event\.target === event\.currentTarget/);
  assert.match(src, /event\.key === "Enter"/);
  assert.match(src, /type="button"/);
  assert.match(src, /data-testid="confirm-failure"/);
  assert.match(src, /result === false/);
  assert.doesNotMatch(src, /window\.confirm|alert\(/);
});

test("high-risk controls ask before they call the existing action", () => {
  const slot = read("frontend/src/components/ops/SlotCard.tsx");
  assert.match(slot, /Activate Wing/);
  assert.match(slot, /physical contactor state/);
  assert.doesNotMatch(slot, /onClick=\{\(\) => queue\(/);
  const controls = read("frontend/src/components/ops/SystemControls.tsx");
  assert.match(controls, /Turn Everything OFF/);
  assert.match(controls, /severity: "CRITICAL"/);
  assert.match(controls, /Restart Controller/);
  assert.match(controls, /Reboot Device/);
  const day = read("frontend/src/components/ops/energy/DayAllocationPanel.tsx");
  assert.match(day, /Apply DAY_BASED schedule\?/);
  assert.match(day, /onClick=\{\(\) => void calculate\(\)\}/);
  assert.match(day, /onConfirm: \(\) => apply\(\)/);
  const energy = read("frontend/src/components/ops/energy/EnergyPanel.tsx");
  assert.match(energy, /Change allocation mode\?/);
  assert.match(energy, /Change energy calculation mode\?/);
  assert.match(energy, /Energy Calculation Mode/);
  assert.match(energy, /Allocation Mode/);
  const hardware = read("frontend/src/components/provisioning/HardwareCapabilities.tsx");
  assert.match(hardware, /save\(true\)/);
  assert.match(hardware, /Confirm Hardware Configuration/);
  assert.match(hardware, /SAVE HARDWARE CONFIGURATION/);
  const provisioning = read("frontend/src/components/provisioning/ProvisioningCenter.tsx");
  assert.match(provisioning, /typed: "REVOKE DEVICE"/);
  assert.match(provisioning, /Rotate Credentials/);
  assert.match(provisioning, /runConfirmed\(setConfirm/);
  const dashboard = read("frontend/src/components/ops/OperationalDashboard.tsx");
  assert.match(dashboard, /!readOnly && <SystemControls/);
  assert.match(dashboard, /ask=\{setConfirm\}/);
  assert.equal((dashboard.match(/<LcdMessagePanel/g) || []).length, 1);
});

test("backend confirmation flag and unchanged safety modules stay in place", () => {
  const hardware = read("frontend/src/components/provisioning/HardwareCapabilities.tsx");
  assert.match(hardware, /confirm, capabilities: profile/);
  const gpio = read("pi_firmware/gpio_manager.py");
  assert.match(gpio, /active_high=not relay_active_low/);
  const allocation = read("pi_firmware/energy/allocation.py");
  assert.match(allocation, /Any required wing that is not ON or OFF -> 'UNKNOWN'/);
  const frontend = read("frontend/src/components/ops/ConfirmDialog.tsx");
  assert.doesNotMatch(frontend, /confirmed\s*=\s*true/);
  const otaCallers = ["frontend/src/components/provisioning/ProvisioningCenter.tsx", "frontend/src/components/ops/OperationalDashboard.tsx"];
  for (const file of otaCallers) assert.doesNotMatch(read(file), /\/firmware|stage_signed_firmware|install firmware/i);
});
