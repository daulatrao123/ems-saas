"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
let ts;
try {
  ts = require("typescript");
} catch (_err) {
  ts = require(path.join(__dirname, "..", "frontend", "node_modules", "typescript"));
}

function makeJsxRuntime() {
  const pack = (type, props) => ({ type, props: { ...(props || {}) } });
  return { jsx: pack, jsxs: pack, Fragment: Symbol.for("react.fragment") };
}

function walk(node, visitor) {
  if (!node || typeof node !== "object") return;
  visitor(node);
  const children = node.props && node.props.children;
  if (Array.isArray(children)) children.forEach((c) => walk(c, visitor));
  else walk(children, visitor);
}

function byTestId(tree, testId) {
  let found;
  walk(tree, (node) => {
    if (!found && node.props && node.props["data-testid"] === testId) found = node;
  });
  if (!found) throw new Error(`Missing node data-testid=${testId}`);
  return found;
}

function allByPrefix(tree, prefix) {
  const out = [];
  walk(tree, (node) => {
    const id = node?.props?.["data-testid"];
    if (typeof id === "string" && id.startsWith(prefix)) out.push(node);
  });
  return out;
}

function createManualGenerationHarness() {
  let ManualGenerationEntry = null;
  let active = null;
  const React = {
    useState(initial) {
      if (!active) throw new Error("Hook called without active instance");
      const inst = active;
      const i = inst.hook_i++;
      if (!(i in inst.state)) inst.state[i] = typeof initial === "function" ? initial() : initial;
      const setState = (next) => {
        const prev = inst.state[i];
        inst.state[i] = typeof next === "function" ? next(prev) : next;
      };
      return [inst.state[i], setState];
    },
  };

  function mount(props) {
    const inst = {
      hook_i: 0,
      state: [],
      tree: null,
      props,
      render() {
        this.hook_i = 0;
        active = this;
        this.tree = ManualGenerationEntry(this.props);
        active = null;
        return this.tree;
      },
    };
    return inst;
  }

  function loadComponent() {
    const tsxPath = path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "ManualGenerationEntry.tsx");
    const source = fs.readFileSync(tsxPath, "utf8");
    const out = ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
      fileName: "ManualGenerationEntry.tsx",
    }).outputText;

    const module = { exports: {} };
    const req = (id) => {
      if (id === "react") return React;
      if (id === "react/jsx-runtime") return makeJsxRuntime();
      if (id === "../DashboardHeader") return { btn: "btn", input: "input", label: "label", tone: { amber: "amber" } };
      if (id === "./types") return {};
      throw new Error(`Unexpected import: ${id}`);
    };
    vm.runInNewContext(out, { module, exports: module.exports, require: req, console }, { filename: "ManualGenerationEntry.compiled.cjs" });
    ManualGenerationEntry = module.exports.ManualGenerationEntry;
  }

  return { mount, loadComponent };
}

function loadEnergyPanel() {
  const tsxPath = path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "EnergyPanel.tsx");
  const source = fs.readFileSync(tsxPath, "utf8");
  const out = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    fileName: "EnergyPanel.tsx",
  }).outputText;
  const module = { exports: {} };
  const req = (id) => {
    if (id === "react/jsx-runtime") return makeJsxRuntime();
    if (id === "react") return { useState: (initial) => [initial, () => {}] };
    if (id === "../DashboardHeader") return { btn: "btn", input: "input", label: "label", panel: "panel", tone: { gray: "gray" } };
    if (id === "./AllocationTimeline") return { AllocationTimeline: (p) => ({ type: "AllocationTimeline", props: p }) };
    if (id === "./GenerationCard") return { GenerationCard: (p) => ({ type: "GenerationCard", props: p }) };
    if (id === "./EnergyReferences") return { EnergyReferences: (p) => p.children };
    if (id === "./SocietyEnergyComparison") return { SocietyEnergyComparison: (p) => ({ type: "SocietyEnergyComparison", props: p }) };
    if (id === "./CalendarComparisonContext") return { CalendarComparisonContext: { Provider: (p) => p.children } };
    if (id === "./useCalendarComparison") return { useCalendarComparison: () => ({ data: null, error: "", loading: false }) };
    if (id === "./ComparisonPeriod") return { ComparisonPeriod: (p) => ({ type: "ComparisonPeriod", props: p }) };
    if (id === "./CalendarSocietyComparison") return { CalendarSocietyComparison: (p) => ({ type: "CalendarSocietyComparison", props: p }) };
    if (id === "./useEnergy") return { useEnergy: () => ({}) };
    if (id === "./types") return { fmtKwh: (v) => (v == null ? "UNAVAILABLE" : `${Number(v).toFixed(2)} kWh`) };
    throw new Error(`Unexpected import: ${id}`);
  };
  vm.runInNewContext(out, { module, exports: module.exports, require: req, console }, { filename: "EnergyPanel.compiled.cjs" });
  return module.exports.EnergyPanel;
}

test("ManualGenerationEntry enforces authoritative date field contract and null OPEN behavior", async () => {
  const calls = [];
  const onAdd = async (entry) => {
    calls.push(entry);
    return true;
  };
  const harness = createManualGenerationHarness();
  harness.loadComponent();
  const inst = harness.mount({ wing: "A", operatingDate: "2026-09-12", disabled: false, onAdd });

  let tree = inst.render();
  const dateInput = byTestId(tree, "manual-entry-date-A");
  assert.equal(dateInput.props.readOnly, true);
  assert.equal(dateInput.props.min, "2026-09-12");
  assert.equal(dateInput.props.max, "2026-09-12");

  byTestId(tree, "manual-entry-value-A").props.onChange({ target: { value: "0" } });
  byTestId(tree, "manual-entry-reason-A").props.onChange({ target: { value: "zero accepted" } });
  tree = inst.render();
  dateInput.props.value = "2026-09-11"; // local tamper, submit must still use prop
  await byTestId(tree, "manual-entry-form-A").props.onSubmit({ preventDefault() {} });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operating_date, "2026-09-12");
  assert.equal(calls[0].value_kwh, 0);

  inst.props = { ...inst.props, operatingDate: "2026-09-14" };
  tree = inst.render();
  assert.equal(byTestId(tree, "manual-entry-date-A").props.value, "2026-09-14", "prop update must be used; no stale local date state");

  const nullInst = harness.mount({ wing: "A", operatingDate: null, disabled: false, onAdd });
  tree = nullInst.render();
  assert.equal(byTestId(tree, "manual-entry-date-A").props.disabled, true);
  assert.equal(byTestId(tree, "manual-entry-submit-A").props.disabled, true);
  byTestId(tree, "manual-entry-value-A").props.onChange({ target: { value: "23" } });
  byTestId(tree, "manual-entry-reason-A").props.onChange({ target: { value: "should block" } });
  tree = nullInst.render();
  await byTestId(tree, "manual-entry-form-A").props.onSubmit({ preventDefault() {} });
  assert.equal(calls.length, 1, "no extra onAdd call when OPEN day unavailable");
  assert.equal(byTestId(tree, "manual-entry-date-unavailable-A").props.role, "status");
});

test("EnergyPanel excludes legacy manual generation activity from the new physical-generation view", () => {
  const EnergyPanel = loadEnergyPanel();
  const energy = {
    loading: false,
    saving: false,
    error: null,
    saveError: null,
    notice: null,
    entries: [
      { id: 1, kind: "MANUAL_GENERATION", source: "MANUAL", operating_date: "2026-09-12", wing: "A", value_kwh: 23, reason: "ok" },
      { id: 2, kind: "MANUAL_CONSUMPTION", source: "MANUAL", operating_date: "2026-09-12", wing: "A", value_kwh: 23, reason: "exclude" },
      { id: 3, kind: "MANUAL_GENERATION", source: "PHYSICAL", operating_date: "2026-09-12", wing: "A", value_kwh: 23, reason: "exclude" },
    ],
    events: [],
    monthly: { rows: [] },
    summary: {
      device_id: "dev-1",
      as_of_operating_date: "2026-09-12",
      calculation: { mode: "MANUAL" },
      generation_meter: { meter_id: "M1" },
      references: { grid: { enabled: true } },
      reset_period: "2026-09",
    },
    refresh() {},
    setMode() {},
  };
  const tree = EnergyPanel({ energy, readOnly: false, activeGenerationWing: "A", children: null, societyId: "1" });
  const rows = allByPrefix(tree, "manual-activity-").filter((n) => /^manual-activity-\d+$/.test(n.props["data-testid"]));
  assert.equal(rows.length, 0);
  assert.throws(() => byTestId(tree, "manual-common-provenance"));
});

test("OperationalDashboard uses mode comparison and no longer mounts manual generation entry", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "OperationalDashboard.tsx"), "utf8");
  assert.match(src, /comparison=\{energy\.comparison/);
  assert.doesNotMatch(src, /ManualGenerationEntry|manualEntry=/);
});
