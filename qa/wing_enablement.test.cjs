"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
let ts;
try { ts = require("typescript"); }
catch (_err) { ts = require(path.join(__dirname, "..", "frontend", "node_modules", "typescript")); }

function makeJsxRuntime() {
  const pack = (type, props) => {
    const p = { ...(props || {}) };
    return typeof type === "function" ? type(p) : { type, props: p };
  };
  return { jsx: pack, jsxs: pack, Fragment: Symbol.for("react.fragment") };
}

function walk(node, visitor) {
  if (Array.isArray(node)) return node.forEach((child) => walk(child, visitor));
  if (!node || typeof node !== "object") return;
  visitor(node);
  const children = node.props && node.props.children;
  if (Array.isArray(children)) children.forEach((child) => walk(child, visitor));
  else walk(children, visitor);
}

function testIds(tree) {
  const ids = [];
  walk(tree, (node) => { if (node.props && node.props["data-testid"]) ids.push(node.props["data-testid"]); });
  return ids;
}

function loadTsx(relPath, exportName, mocks) {
  mocks["react/jsx-runtime"] = makeJsxRuntime();
  const full = path.join(__dirname, "..", relPath);
  const out = ts.transpileModule(fs.readFileSync(full, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    fileName: path.basename(full),
  }).outputText;
  const module = { exports: {} };
  const req = (id) => {
    if (mocks[id]) return mocks[id];
    throw new Error(`Unexpected import: ${id}`);
  };
  vm.runInNewContext(out, {
    module, exports: module.exports, require: req, console,
    React: mocks.react, react: mocks.react,
    ...makeJsxRuntime(),
  }, { filename: `${exportName}.compiled.cjs` });
  return module.exports[exportName];
}

const react = { useState: (value) => [value, () => {}] };
const header = { btn: "", input: "", label: "", panel: "", tone: {} };

function slotCard() {
  return loadTsx("frontend/src/components/ops/SlotCard.tsx", "SlotCard", {
    react,
    "./types": {},
    "./useOperations": {},
    "./DashboardHeader": header,
    "./LastResponse": { LastResponse: () => null },
    "./energy/WingEnergyCard": { WingEnergyCard: ({ code }) => ({ type: "section", props: { "data-testid": `energy-wing-card-${code}`, children: "meter" } }) },
    "./allocationMode": { allocationVisibility: () => ({}) },
    "./energy/types": {},
  });
}

function panel(posts) {
  return loadTsx("frontend/src/components/ops/energy/DayAllocationPanel.tsx", "DayAllocationPanel", {
    react,
    "@/lib/api": { __esModule: true, default: { post: async (url, body) => { posts.push({ url, body }); return { data: { days: { A: 10, B: 10, C: 10 }, status: "APPLYING" } }; } } },
    "../DashboardHeader": header,
    "./types": {},
  });
}

function device(disabled) {
  return {
    id: "d1", name: "Pi", connected: true, active_slot: null, hardware_fault: null, feedback_hardware_installed: false,
    slots: Object.fromEntries(["A", "B", "C", "D"].map((code) => [code, { display_name: `Wing ${code}`, disabled: disabled[code], target_days: 0, used_days: 0, physical_toggle: "UNKNOWN" }])),
  };
}

function wing(code, enabled) {
  return { wing: code, consumption_meter: { meter_id: { A: "M2", B: "M3", C: "M4", D: "M5" }[code], enabled, comm_status: enabled ? "ONLINE" : "DISABLED", serial: null, last_seen: null, power_kw: null } };
}

test("DAY_BASED inputs follow logical enablement and do not send disabled wings", async () => {
  const posts = [];
  const DayAllocationPanel = panel(posts);
  const tree = DayAllocationPanel({
    societyId: "1", deviceId: "d1", readOnly: false, refresh: async () => {}, wings: ["A", "B", "C"],
    summary: { day_allocation: { cycle_days: 30, application: null } },
  });
  const ids = testIds(tree);
  assert.deepEqual(ids.filter((id) => id.startsWith("day-input-")), ["day-input-A", "day-input-B", "day-input-C"]);
  assert.equal(ids.includes("day-input-D"), false);
  const calculate = [];
  walk(tree, (node) => { if (node.props && node.props["data-testid"] === "day-calculate") calculate.push(node); });
  await calculate[0].props.onClick();
  assert.equal(JSON.stringify(posts[0].body.wings), JSON.stringify({ A: 0, B: 0, C: 0 }));
  assert.equal(Object.hasOwn(posts[0].body.wings, "D"), false);
});

test("a disabled physical meter still renders its energy card, independent of the logical slot", () => {
  const SlotCard = slotCard();
  const base = { device: device({ A: false, B: false, C: false, D: true }), queue: () => {}, setSlotConfig: async () => true, isPending: () => false, readOnly: true, allocation: null, allocationMode: "AUTO", manualControlLabel: "Manual override", excessEnabled: false };
  const enabledMeter = testIds(SlotCard({ ...base, code: "A", slot: base.device.slots.A, wing: wing("A", true) }));
  const disabledMeter = testIds(SlotCard({ ...base, code: "B", slot: base.device.slots.B, wing: wing("B", false) }));
  const disabledLogical = testIds(SlotCard({ ...base, code: "D", slot: base.device.slots.D, wing: wing("D", true) }));
  assert.equal(enabledMeter.includes("energy-wing-card-A"), true);
  assert.equal(enabledMeter.includes("slot-enabled-A"), true);
  assert.equal(enabledMeter.includes("energy-meter-disabled-A"), false);
  assert.equal(disabledMeter.includes("energy-wing-card-B"), true);
  assert.equal(disabledMeter.includes("energy-meter-disabled-B"), false);
  assert.equal(disabledMeter.includes("slot-enabled-B"), true);
  assert.equal(disabledLogical.includes("slot-enabled-D"), true);
  assert.equal(disabledLogical.includes("energy-wing-card-D"), true);
  assert.equal(disabledLogical.includes("slot-disabled-data-D"), true);
  assert.equal(disabledLogical.includes("slot-exclusion-D"), true);
  assert.equal(disabledLogical.includes("energy-meter-disabled-D"), false);
});
