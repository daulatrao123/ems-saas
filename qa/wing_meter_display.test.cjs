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
  if (typeof node === "string" || typeof node === "number" || !node || typeof node !== "object") return;
  visitor(node);
  const children = node.props && node.props.children;
  if (Array.isArray(children)) children.forEach((child) => walk(child, visitor));
  else walk(children, visitor);
}

function textUnder(node, testId) {
  let found = null;
  walk(node, (item) => { if (item.props && item.props["data-testid"] === testId) found = item; });
  const parts = [];
  const collect = (value) => {
    if (typeof value === "string" || typeof value === "number") parts.push(String(value));
    else if (Array.isArray(value)) value.forEach(collect);
    else if (value && value.props) collect(value.props.children);
  };
  collect(found);
  return parts.join("");
}

function loadComponent(relPath, exportName) {
  const cache = new Map();
  const mocks = {
    "react/jsx-runtime": makeJsxRuntime(),
    react: { useContext: () => null, createContext: () => ({ Provider: () => null }) },
    "../DashboardHeader": { btn: "", input: "", label: "", panel: "", tone: {} },
    "./BillHistoryButton": { BillHistoryButton: () => null },
    "./EnergyComparisonChart": { EnergyComparisonChart: () => null, signedKwh: (v) => (v == null ? "UNAVAILABLE" : String(v)) },
    "./DashboardHeader": { btn: "", input: "", label: "", panel: "", tone: {} },
    "./LastResponse": { LastResponse: () => null },
  };
  function loadFile(full) {
    if (cache.has(full)) return cache.get(full).exports;
    const module = { exports: {} };
    cache.set(full, module);
    const out = ts.transpileModule(fs.readFileSync(full, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
      fileName: path.basename(full),
    }).outputText;
    const req = (id) => {
      if (mocks[id]) return mocks[id];
      if (!id.startsWith(".")) throw new Error(`Unexpected import: ${id} from ${full}`);
      const base = path.resolve(path.dirname(full), id);
      const file = [base, `${base}.ts`, `${base}.tsx`].find((candidate) => fs.existsSync(candidate));
      if (!file) throw new Error(`Missing import ${id} from ${full}`);
      return loadFile(file);
    };
    vm.runInNewContext(out, { module, exports: module.exports, require: req, console }, { filename: full });
    return module.exports;
  }
  return loadFile(path.join(__dirname, "..", relPath))[exportName];
}

const periods = (today) => ({
  today, yesterday: { kwh: null, days: 0, status: "UNAVAILABLE" }, this_month: { kwh: null, days: 0, status: "UNAVAILABLE" },
  previous_month: { kwh: null, days: 0, status: "UNAVAILABLE" }, this_year: { kwh: null, days: 0, status: "UNAVAILABLE" },
  lifetime: { kwh: null, days: 0, status: "UNAVAILABLE" }, reset_period: { kwh: null, days: 0, status: "UNAVAILABLE", period: "2026-10" },
});

function wing(code, meter, consumption, generation) {
  return {
    wing: code, consumption_meter: meter, consumption, generation,
    required_generation: { target_kwh_per_day: null, adjustment_percent: null, base_daily_average_kwh: null, effective_from: null, generated_today_kwh: null, achievement_percent: null, status: "UNAVAILABLE" },
    source: "UNAVAILABLE",
  };
}

function render(WingEnergyCard, code, row, comparison) {
  return WingEnergyCard({
    code, wing: row, comparison, mode: "AUTO", allocationMode: "AUTO", allocation: null, excessEnabled: false,
  });
}

test("each wing renders its own consumption meter without collapsing disabled meters", () => {
  const WingEnergyCard = loadComponent("frontend/src/components/ops/energy/WingEnergyCard.tsx", "WingEnergyCard");
  const unavailable = { status: "UNAVAILABLE", reason: "consumption meter disabled" };
  const rows = {
    A: wing("A", { meter_id: "M2", enabled: false, comm_status: "DISABLED", serial: "SN-M2", last_seen: null, power_kw: null }, unavailable, unavailable),
    B: wing("B", { meter_id: "M3", enabled: true, comm_status: "ONLINE", serial: "SN-M3", last_seen: null, power_kw: 3.25 }, periods({ kwh: 4.2, days: 1, status: "PHYSICAL" }), unavailable),
    C: wing("C", { meter_id: "M4", enabled: true, comm_status: "OFFLINE", serial: "", last_seen: null, power_kw: null }, { status: "UNAVAILABLE", reason: "consumption meter not ONLINE" }, unavailable),
    D: wing("D", { meter_id: "M5", enabled: false, comm_status: "DISABLED", serial: null, last_seen: null, power_kw: 9 }, unavailable, periods({ kwh: 9.5, days: 1, status: "PHYSICAL" })),
  };
  const cards = {
    A: render(WingEnergyCard, "A", rows.A),
    B: render(WingEnergyCard, "B", rows.B, { today: { date: "2026-10-03", consumed_kwh: 4.2, generated_kwh: null, generation_source: "UNAVAILABLE", consumption_source: "PHYSICAL", generation_minus_consumption_kwh: null }, rows: [] }),
    C: render(WingEnergyCard, "C", rows.C),
    D: render(WingEnergyCard, "D", rows.D),
  };

  assert.match(textUnder(cards.A, "energy-wing-card-A"), /Wing A/);
  assert.match(textUnder(cards.A, "energy-wing-owner-A"), /M2/);
  assert.match(textUnder(cards.A, "energy-wing-enabled-A"), /DISABLED/);
  assert.equal(textUnder(cards.A, "energy-wing-serial-A"), "Physical Meter No. · SN-M2");
  assert.equal(textUnder(cards.A, "energy-wing-health-A"), "DISABLED");
  assert.equal(textUnder(cards.A, "energy-wing-power-A"), "");
  assert.match(textUnder(cards.A, "energy-wing-consumption-A"), /UNAVAILABLE/);
  assert.doesNotMatch(textUnder(cards.A, "energy-wing-consumption-A"), /4\.20|3\.25|9\.50/);

  assert.match(textUnder(cards.B, "energy-wing-owner-B"), /M3/);
  assert.equal(textUnder(cards.B, "energy-wing-enabled-B"), "ENABLED");
  assert.equal(textUnder(cards.B, "energy-wing-health-B"), "ONLINE");
  assert.equal(textUnder(cards.B, "energy-wing-serial-B"), "Physical Meter No. · SN-M3");
  assert.equal(textUnder(cards.B, "energy-wing-power-B"), "3.25 kW");
  assert.match(textUnder(cards.B, "energy-wing-consumption-B"), /4\.20 kWh/);
  assert.doesNotMatch(textUnder(cards.B, "energy-wing-owner-B"), /DISABLED/);

  assert.match(textUnder(cards.C, "energy-wing-owner-C"), /M4/);
  assert.equal(textUnder(cards.C, "energy-wing-enabled-C"), "ENABLED");
  assert.equal(textUnder(cards.C, "energy-wing-health-C"), "OFFLINE");
  assert.equal(textUnder(cards.C, "energy-wing-serial-C"), "Physical Meter No. · Not configured");
  assert.doesNotMatch(textUnder(cards.C, "energy-wing-enabled-C"), /DISABLED/);
  assert.doesNotMatch(textUnder(cards.C, "energy-wing-serial-C"), /M4|SN-/);
  assert.match(textUnder(cards.C, "energy-wing-consumption-C"), /UNAVAILABLE/);

  assert.match(textUnder(cards.D, "energy-wing-owner-D"), /M5/);
  assert.equal(textUnder(cards.D, "energy-wing-enabled-D"), "DISABLED");
  assert.equal(textUnder(cards.D, "energy-wing-serial-D"), "Physical Meter No. · Not configured");
  assert.equal(textUnder(cards.D, "energy-wing-power-D"), "");
  assert.match(textUnder(cards.D, "energy-wing-consumption-D"), /UNAVAILABLE/);
  assert.match(textUnder(cards.D, "energy-wing-generation-D"), /9\.50 kWh/);
  assert.doesNotMatch(textUnder(cards.D, "energy-wing-consumption-D"), /9\.50/);

  const foreign = render(WingEnergyCard, "A", wing("A", { meter_id: "M1", enabled: true, comm_status: "ONLINE", serial: "GEN-1", last_seen: null, power_kw: 8 }, unavailable, unavailable));
  assert.match(textUnder(foreign, "energy-wing-owner-A"), /UNAVAILABLE/);
  assert.doesNotMatch(textUnder(foreign, "energy-wing-owner-A"), /M1|GEN-1/);
});
