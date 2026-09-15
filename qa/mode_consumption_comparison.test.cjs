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

function loadTsx(relPath, exportName, mocks = {}) {
  const full = path.join(__dirname, "..", relPath);
  const source = fs.readFileSync(full, "utf8");
  const out = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
    fileName: path.basename(full),
  }).outputText;
  const module = { exports: {} };
  const req = (id) => {
    if (id in mocks) return mocks[id];
    throw new Error(`Unexpected import: ${id}`);
  };
  vm.runInNewContext(out, { module, exports: module.exports, require: req, console, URLSearchParams, setInterval: () => 1, clearInterval: () => {} }, { filename: `${exportName}.compiled.cjs` });
  return module.exports[exportName];
}

test("EnergyComparisonChart renders both series; missing bars stay absent; zero/negative handled", () => {
  const chartSrc = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "EnergyComparisonChart.tsx"), "utf8");
  assert.match(chartSrc, /value === null \? null : <rect/);
  assert.match(chartSrc, /Math\.max\(value === 0 \? 1 : 0, value \/ max \* height\)/);
  assert.match(chartSrc, /row\.generation_minus_consumption_kwh !== null && row\.generation_minus_consumption_kwh > 0 && !excessEnabled \? "—" : "N\/A"/);
  assert.match(chartSrc, /className=\{delta !== null && delta < 0 \? "text-red-300" : "text-cyan-300"\}/);

  const EnergyComparisonChart = loadTsx("frontend/src/components/ops/energy/EnergyComparisonChart.tsx", "EnergyComparisonChart", {
    "react/jsx-runtime": makeJsxRuntime(),
    "./types": { fmtKwh: (v) => (v == null ? "UNAVAILABLE" : `${Number(v).toFixed(2)} kWh`) },
  });

  const rows = [
    { date: "2026-04-01", generated_kwh: 5, consumed_kwh: 7, generation_source: "PHYSICAL", consumption_source: "PHYSICAL", generation_minus_consumption_kwh: -2 },
    { date: "2026-04-02", generated_kwh: 5, consumed_kwh: 0, generation_source: "PHYSICAL", consumption_source: "HISTORICAL", generation_minus_consumption_kwh: 5 },
    { date: "2026-04-03", generated_kwh: null, consumed_kwh: null, generation_source: "UNAVAILABLE", consumption_source: "UNAVAILABLE", generation_minus_consumption_kwh: null },
  ];

  const chartHiddenPos = EnergyComparisonChart({ scope: "society", rows, mode: "MANUAL", excessEnabled: false, compact: false });
  byTestId(chartHiddenPos, "comparison-society");
  assert.equal(byTestId(chartHiddenPos, "comparison-balance-society-2026-04-01").props.children, "-2.00");
  assert.equal(byTestId(chartHiddenPos, "comparison-balance-society-2026-04-02").props.children, "—");
  assert.equal(byTestId(chartHiddenPos, "comparison-society-consumed_kwh-2026-04-02").props.height, 1);
  assert.throws(() => byTestId(chartHiddenPos, "comparison-society-generated_kwh-2026-04-03"));
  const title = byTestId(chartHiddenPos, "comparison-society-generated_kwh-2026-04-01").props.children;
  assert.equal(typeof title.props.children, "string", "SVG title must be a single string for React SSR");

  const chartShownPos = EnergyComparisonChart({ scope: "society", rows, mode: "MANUAL", excessEnabled: true, compact: false });
  byTestId(chartShownPos, "comparison-society");
  assert.equal(byTestId(chartShownPos, "comparison-balance-society-2026-04-02").props.children, "+5.00");
});

test("WingEnergyCard keeps physical generation/targets and manual bills button behavior", () => {
  const WingEnergyCard = loadTsx("frontend/src/components/ops/energy/WingEnergyCard.tsx", "WingEnergyCard", {
    "react/jsx-runtime": makeJsxRuntime(),
    "../DashboardHeader": { label: "label" },
    "./BillHistoryButton": { BillHistoryButton: ({ wing }) => ({ type: "button", props: { "data-testid": `bills-open-${wing}`, children: "ADD BILLS / CONSUMPTION" } }) },
    "./EnergyComparisonChart": {
      EnergyComparisonChart: ({ scope }) => ({ type: "div", props: { "data-testid": `comparison-proxy-${scope}` } }),
      signedKwh: (v) => (v == null ? "UNAVAILABLE" : `${v > 0 ? "+" : ""}${v.toFixed(2)} kWh`),
    },
    "./types": {
      WING_METERS: { A: "M2", B: "M3", C: "M4", D: "M5" },
      fmtKwh: (v) => (v == null ? "UNAVAILABLE" : `${Number(v).toFixed(2)} kWh`),
      fmtPct: (v) => (v == null ? "—" : `${Number(v).toFixed(1)}%`),
      sourceLabel: (s) => s || "Unavailable",
      sourceTone: () => "tone",
      todayKwh: (metric) => (metric && metric.today && metric.today.status === "PHYSICAL" ? metric.today.kwh : null),
    },
  });

  const comparison = {
    today: { date: "2026-04-30", generated_kwh: 11, consumed_kwh: 20, generation_source: "PHYSICAL", consumption_source: "HISTORICAL", generation_minus_consumption_kwh: -9 },
    rows: [{ date: "2026-04-30", generated_kwh: 11, consumed_kwh: 20, generation_source: "PHYSICAL", consumption_source: "HISTORICAL", generation_minus_consumption_kwh: -9 }],
  };
  const wing = {
    wing: "A",
    consumption_meter: { meter_id: "M2", enabled: true, comm_status: "ONLINE" },
    generation: { today: { kwh: 11, status: "PHYSICAL" } },
    required_generation: { target_kwh_per_day: 25, achievement_percent: 44, status: "NOT_REACHED" },
  };
  const allocation = { enabled: true, sequence: ["A", "B", "C", "D"], wings: { A: { generation_attribution_enabled: true } } };

  const manualTree = WingEnergyCard({ code: "A", wing, comparison, mode: "MANUAL", allocation, activeGenerationWing: "A", excessEnabled: false });
  byTestId(manualTree, "energy-wing-card-A");
  byTestId(manualTree, "energy-wing-generation-A");
  byTestId(manualTree, "energy-wing-target-A");
  byTestId(manualTree, "energy-wing-target-status-A");
  byTestId(manualTree, "energy-wing-sequence-A");
  byTestId(manualTree, "bills-open-A");

  const autoTree = WingEnergyCard({ code: "A", wing, comparison, mode: "AUTO", allocation, activeGenerationWing: "A", excessEnabled: false });
  assert.throws(() => byTestId(autoTree, "bills-open-A"), "AUTO mode should not show bill action button in wing card");
  for (const [index, code] of ["A", "B", "C", "D"].entries()) {
    for (const mode of ["AUTO", "MANUAL"]) {
      const card = WingEnergyCard({code, wing:{...wing, wing:code, consumption_meter:{...wing.consumption_meter, meter_id:`M${index + 2}`}}, comparison, mode, allocation, excessEnabled:false});
      assert.equal(byTestId(card, `energy-wing-generation-${code}`).props.children, "11.00 kWh");
      assert.equal(byTestId(card, `energy-wing-balance-${code}`).props.children, "-9.00 kWh");
      if (mode === "MANUAL") byTestId(card, `bills-open-${code}`);
      else assert.throws(() => byTestId(card, `bills-open-${code}`));
    }
  }

  const cardSrc = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "WingEnergyCard.tsx"), "utf8");
  assert.match(cardSrc, /CONSUMPTION · \{mode === "MANUAL" \? "DAILY REFERENCE" : "ACTUAL"\}/);
  assert.doesNotMatch(cardSrc, /manual-entry-|ManualGenerationEntry/);
});

test("Society comparison shows 7/30 selector, labels, and unavailable state", () => {
  let selection = 30;
  const React = { useState: () => [selection, (value) => { selection = value; }] };
  const SocietyEnergyComparison = loadTsx("frontend/src/components/ops/energy/SocietyEnergyComparison.tsx", "SocietyEnergyComparison", {
    react: React,
    "react/jsx-runtime": makeJsxRuntime(),
    "./types": { fmtKwh: (v) => (v == null ? "UNAVAILABLE" : `${Number(v).toFixed(2)} kWh`) },
    "./EnergyComparisonChart": { EnergyComparisonChart: (p) => ({ type: "div", props: { ...p, "data-testid": `chart-${p.scope}` } }), signedKwh: (v) => (v == null ? "UNAVAILABLE" : `${v > 0 ? "+" : ""}${v.toFixed(2)} kWh`) },
    "../DashboardHeader": { btn: "btn", input: "input", tone: { gray: "gray" } },
  });

  const unavailable = SocietyEnergyComparison({ data: null, excessEnabled: false });
  byTestId(unavailable, "society-comparison-unavailable");

  const data = {
    mode: "MANUAL",
    society: {
      today: { generated_kwh: 100, consumed_kwh: 130, generation_minus_consumption_kwh: -30 },
      rows: Array.from({length:30}, (_, i) => ({ date: `2026-04-${String(i + 1).padStart(2, "0")}`, generated_kwh: 100, consumed_kwh: 130, generation_minus_consumption_kwh: -30 })),
    },
  };
  const tree = SocietyEnergyComparison({ data, excessEnabled: false });
  const select = byTestId(tree, "society-comparison-days");
  assert.equal(select.props.value, 30);
  const options = select.props.children;
  assert.equal(options[0].props.value, 7);
  assert.equal(options[1].props.value, 30);
  byTestId(tree, "society-comparison-consumption");
  assert.equal(byTestId(tree, "chart-society").props.rows.length, 30);
  select.props.onChange({target:{value:"7"}});
  const seven = SocietyEnergyComparison({data, excessEnabled:false});
  assert.equal(byTestId(seven, "society-comparison-days").props.value, 7);
  assert.equal(byTestId(seven, "chart-society").props.rows.length, 7);
});

test("Source contracts: no physical-toggle UI, comparison isolation, and bill-save refresh wiring", () => {
  const useEnergySrc = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "useEnergy.ts"), "utf8");
  assert.match(useEnergySrc, /\/api\/energy\/graph\/comparison\?\$\{q\}&days=30/);
  assert.match(useEnergySrc, /c\.value\.data\.mode === s\.value\.data\.calculation\?\.mode/);
  assert.match(useEnergySrc, /c\.value\.data\.version === s\.value\.data\.calculation\?\.version/);
  assert.match(useEnergySrc, /c\.value\.data\.operating_date === s\.value\.data\.as_of_operating_date/);
  assert.match(useEnergySrc, /Comparison unavailable for the current mode\/day; refresh energy data/);

  const slotCardSrc = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "SlotCard.tsx"), "utf8");
  assert.match(slotCardSrc, /CONTACTOR/);
  assert.match(slotCardSrc, /ACTIVATE/);
  assert.match(slotCardSrc, /DEACTIVATE/);
  assert.doesNotMatch(slotCardSrc, /PHYSICAL TOGGLE|TOGGLE INPUT|\bTG\b/);

  const billButtonSrc = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "BillHistoryButton.tsx"), "utf8");
  assert.match(billButtonSrc, /ADD BILLS \/ CONSUMPTION/);
  assert.match(billButtonSrc, /VIEW BILLS \/ CONSUMPTION/);

  const dashboardSrc = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "OperationalDashboard.tsx"), "utf8");
  assert.match(dashboardSrc, /comparison=\{energy\.comparison/);
  assert.doesNotMatch(dashboardSrc, /ManualGenerationEntry|manualEntry=/);

  const gridRefSrc = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "GridReferencePanel.tsx"), "utf8");
  assert.match(gridRefSrc, /calculated reference/);
  assert.match(gridRefSrc, /not live dispatch/);
  assert.doesNotMatch(gridRefSrc, /measured export/i);

  const billEditorSrc = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "BillMonthEditor.tsx"), "utf8");
  assert.match(billEditorSrc, /Number\(entered\) !== m\.consumption_kwh/);
  assert.match(billEditorSrc, /if \(alive\.current\) onSaved\(data\)/);
});

test("useEnergy runtime rejects mismatched/delayed comparisons and refreshes bill-derived values", async () => {
  const slots = []; let index = 0, effects = [], selected = "one", mismatch = {}, release;
  const gate = new Promise(resolve => { release = resolve; });
  let deferOne = false;
  const equal = (a,b) => a && b && a.length === b.length && a.every((v,i)=>v===b[i]);
  const hooks = {
    useState(initial) { const i=index++; slots[i] ??= {value:typeof initial === "function" ? initial() : initial}; return [slots[i].value, value=>{slots[i].value=typeof value==="function"?value(slots[i].value):value;}]; },
    useRef(initial) { const i=index++; slots[i] ??= {current:initial}; return slots[i]; },
    useCallback(fn,deps) { const i=index++; if(!slots[i] || !equal(slots[i].deps,deps)) slots[i]={deps,value:fn}; return slots[i].value; },
    useEffect(fn,deps) { const i=index++; if(!slots[i] || !equal(slots[i].deps,deps)) { const previous=slots[i]; slots[i]={deps}; effects.push(()=>{previous?.cleanup?.(); slots[i].cleanup=fn();}); } },
  };
  const server = {one:{mode:"MANUAL",version:1,bill:10},two:{mode:"MANUAL",version:2,bill:20}};
  const api = { get(url) {
    const did=new URLSearchParams(url.split("?")[1]).get("device_id"), value=server[did];
    const day="2026-09-30";
    const data=url.includes("/summary?") ? {device_id:did,as_of_operating_date:day,calculation:{mode:value.mode,version:value.version}}
      : url.includes("/graph/comparison?") ? {device_id:did,mode:value.mode,version:value.version,operating_date:day,society:{today:{consumed_kwh:value.bill}},...mismatch}
      : {device_id:did,rows:[],events:[],allocation:{}};
    return deferOne && did==="one" ? gate.then(()=>({data})) : Promise.resolve({data});
  }};
  const useEnergy=loadTsx("frontend/src/components/ops/energy/useEnergy.ts","useEnergy",{
    react:hooks,"@/lib/api":api,"./types":{DEFAULT_MONTHS:6,isAllocationEvent:()=>false},"../types":{errorText:e=>({detail:String(e)})},
  });
  const render=()=>{index=0;const value=useEnergy("1",selected);const work=effects;effects=[];work.forEach(fn=>fn());return value;};
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  render();await flush();let result=render();
  assert.equal(result.comparison.society.today.consumed_kwh,10);
  for(const bad of [{mode:"AUTO"},{version:999},{operating_date:"2026-09-29"},{device_id:"foreign"}]) {
    mismatch=bad;await result.refresh();result=render();
    assert.equal(result.comparison,null);
    assert.match(result.error,/Comparison unavailable/);
  }
  mismatch={};await result.refresh();result=render();
  server.one.bill=31;await result.refresh();result=render();
  assert.equal(result.comparison.society.today.consumed_kwh,31);
  deferOne=true;const oldRefresh=result.refresh();await flush();
  selected="two";result=render();assert.equal(result.comparison,null,"old device masked immediately");await flush();result=render();
  assert.equal(result.comparison.device_id,"two");
  release();await oldRefresh;await flush();result=render();
  assert.equal(result.comparison.device_id,"two","late device one cannot overwrite device two");
  assert.equal(result.comparison.society.today.consumed_kwh,20);
  for(const slot of slots) slot?.cleanup?.();
});
