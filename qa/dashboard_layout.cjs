"use strict";

// Renders the current OperationalDashboard to HTML for dashboard_presentation.test.cjs.
// This is the test harness. It does not invent markup for assertions.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..");
const SRC = path.join(ROOT, "frontend", "src");
let ts;
try { ts = require("typescript"); }
catch { ts = require(path.join(ROOT, "frontend", "node_modules", "typescript")); }

const unavailable = process.argv.includes("--unavailable");
const outDir = process.env.DASHBOARD_LAYOUT_OUT || fs.mkdtempSync(path.join(os.tmpdir(), "ems-dashboard-layout-"));
fs.mkdirSync(outDir, { recursive: true });

function reactModule() {
  const slots = [];
  let idx = 0;
  let dirty = false;
  const contexts = [];
  const api = {
    useState(initial) {
      const i = idx++;
      if (slots[i] === undefined) slots[i] = { value: typeof initial === "function" ? initial() : initial };
      return [slots[i].value, (next) => {
        const value = typeof next === "function" ? next(slots[i].value) : next;
        if (!Object.is(slots[i].value, value)) dirty = true;
        slots[i].value = value;
      }];
    },
    useRef(initial) {
      const i = idx++;
      if (slots[i] === undefined) slots[i] = { current: initial };
      return slots[i];
    },
    useCallback(fn) { idx++; return fn; },
    useMemo(fn) {
      const i = idx++;
      if (slots[i] === undefined) slots[i] = fn();
      return slots[i];
    },
    useEffect() { idx++; },
    useLayoutEffect() { idx++; },
    useContext(ctx) {
      for (let i = contexts.length - 1; i >= 0; i -= 1) if (contexts[i][0] === ctx) return contexts[i][1];
      return ctx._currentValue;
    },
    createContext(defaultValue) {
      const ctx = { _currentValue: defaultValue, Provider: null };
      ctx.Provider = function Provider({ value, children }) {
        contexts.push([ctx, value]);
        try { return children; }
        finally { contexts.pop(); }
      };
      return ctx;
    },
    Fragment: Symbol.for("react.fragment"),
    reset() { idx = 0; dirty = false; slots.length = 0; },
    consumeDirty() { const was = dirty; dirty = false; return was; },
  };
  return api;
}

const react = reactModule();

function jsxRuntime() {
  const pack = (type, props) => {
    const next = props || {};
    if (typeof type === "function") return type(next);
    return { type, props: next };
  };
  return { jsx: pack, jsxs: pack, Fragment: react.Fragment };
}

const cache = new Map();

function resolveImport(fromFile, spec) {
  if (spec === "react") return "react";
  if (spec === "react/jsx-runtime" || spec === "react/jsx-dev-runtime") return "react/jsx-runtime";
  if (spec === "@/lib/api") return "@/lib/api";
  if (spec === "next/link") return "next/link";
  if (spec.startsWith("@/")) return resolveFile(path.join(SRC, spec.slice(2)));
  if (spec.startsWith(".")) return resolveFile(path.resolve(path.dirname(fromFile), spec));
  throw new Error(`Unexpected import ${spec} from ${path.relative(ROOT, fromFile)}`);
}

function resolveFile(base) {
  const candidates = [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts"), path.join(base, "index.tsx")];
  for (const candidate of candidates) if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  throw new Error(`Cannot resolve ${base}`);
}

function load(abs) {
  if (abs === "react") return react;
  if (abs === "react/jsx-runtime") return jsxRuntime();
  if (abs === "next/link") {
    return { __esModule: true, default(props) { return { type: "a", props }; } };
  }
  if (abs === "@/lib/api") {
    const fail = () => { throw new Error("dashboard layout render must not call the API"); };
    return { __esModule: true, default: { get: fail, post: fail, put: fail, patch: fail, delete: fail } };
  }
  if (cache.has(abs)) return cache.get(abs).exports;
  const mod = { exports: {} };
  cache.set(abs, mod);
  const output = ts.transpileModule(fs.readFileSync(abs, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
    fileName: path.basename(abs),
  }).outputText;
  const req = (spec) => load(resolveImport(abs, spec));
  vm.runInNewContext(output, {
    module: mod,
    exports: mod.exports,
    require: req,
    console,
    AbortController,
    URLSearchParams,
    Date,
    Number,
    String,
    Math,
    Object,
    Array,
    JSON,
    Symbol,
    Error,
    Set,
    Map,
    Promise,
  }, { filename: abs });
  return mod.exports;
}

function wing(code, disabled, target) {
  return {
    wing: code,
    consumption_meter: { meter_id: { A: "M2", B: "M3", C: "M4", D: "M5" }[code], enabled: true, comm_status: "OFFLINE", serial: null, last_seen: null, power_kw: null },
    consumption: { status: "UNAVAILABLE", reason: "NO_READING" },
    generation: { status: "UNAVAILABLE", reason: "NO_READING" },
    required_generation: { target_kwh_per_day: target, adjustment_percent: null, base_daily_average_kwh: null, effective_from: null, generated_today_kwh: null, achievement_percent: null, status: "UNAVAILABLE" },
    source: "PHYSICAL",
  };
}

function summary(mode) {
  const wings = { A: wing("A", false, unavailable ? null : 10), B: wing("B", false, unavailable ? null : 10), C: wing("C", false, unavailable ? null : 10), D: wing("D", true, 10) };
  const referenceWing = { history: { reference_daily_kwh: null, valid_months: 0 }, effective: { daily_kwh: null, source: "UNAVAILABLE", operating_date: null } };
  return {
    device_id: "offline-controller",
    as_of_operating_date: "2026-11-30",
    reset_day: 15,
    reset_period: "2026-11",
    generation_meter: {
      meter_id: "M1",
      status: unavailable ? "DISABLED" : "ONLINE",
      serial: null,
      model: null,
      current_power_kw: null,
      generation: { status: "UNAVAILABLE", reason: "NO_READING" },
      unattributed: null,
      active_generation_wing: null,
      attribution: null,
      source: "PHYSICAL",
    },
    wings,
    calculation: { mode, version: 1, operating_date: "2026-11-30", wings: {} },
    references: {
      wings: { A: referenceWing, B: referenceWing, C: referenceWing, D: referenceWing },
      society_historical_daily_kwh: null,
      society_reference_daily_kwh: null,
      society_reference_source: "UNAVAILABLE",
      grid: { enabled: false, limit_kwh_day: null, version: 1 },
      allocation: {
        generation_kwh: null, generation_source: "UNAVAILABLE", required_kwh: null, excess_kwh: null, unmet_kwh: null,
        grid_allocation_kwh: 0, unassigned_excess_kwh: null, all_quotas_known: false, all_quotas_satisfied: false,
        status: "UNAVAILABLE", reason: "NO_PHYSICAL_M1", wings: [],
      },
    },
    target_delivery: unavailable ? { status: "UNKNOWN", desired_version: null, reported_version: null } : { status: "REPORTED_CURRENT", desired_version: 1, reported_version: 1 },
    allocation_mode: "AUTO",
    allocation_mode_version: 1,
  };
}

function device() {
  const slot = (disabled) => ({ display_name: "", target_days: 5, used_days: 0, physical_toggle: "OFF", disabled, feedback_enabled: false });
  return {
    id: "offline-controller",
    name: "Pi",
    connected: true,
    active_slot: null,
    hardware_fault: null,
    feedback_hardware_installed: false,
    storage_health: { health: "WARNING", smart: "UNAVAILABLE" },
    lcd: { available: true },
    slots: { A: slot(false), B: slot(false), C: slot(false), D: slot(true) },
  };
}

function energy(mode) {
  return {
    summary: summary(mode),
    comparison: null,
    monthly: null,
    events: [],
    loading: false,
    saving: false,
    error: "",
    saveError: "",
    notice: "",
    panelErrors: [],
    refresh() {},
    setMode() {},
    setAllocationMode() {},
    // ConfigurationGaps reads this allocation object, not summary.references.
    allocation: {
      enabled: !unavailable,
      sequence: ["A", "B", "C", "D"],
      tolerance_kwh: 0,
      persistence_s: 0,
      wings: {},
    },
  };
}

function escapeHtml(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function toHtml(node) {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return escapeHtml(node);
  if (Array.isArray(node)) return node.map(toHtml).join("");
  if (typeof node !== "object") return "";
  if (typeof node.type === "function") return toHtml(node.type(node.props || {}));
  if (typeof node.type === "symbol") return toHtml(node.props?.children);
  if (typeof node.type !== "string") return toHtml(node.props?.children);
  const attrs = Object.entries(node.props || {}).filter(([key, value]) => value != null && typeof value !== "function" && key !== "children")
    .map(([key, value]) => {
      const name = key === "className" ? "class" : key;
      if (value === true) return ` ${name}`;
      if (value === false) return "";
      return ` ${name}="${escapeHtml(value)}"`;
    }).join("");
  return `<${node.type}${attrs}>${toHtml(node.props?.children)}</${node.type}>`;
}

const dashboardModule = load(path.join(SRC, "components", "ops", "OperationalDashboard.tsx"));
const OperationalDashboard = dashboardModule.OperationalDashboard || dashboardModule.default;

function renderOne(mode, readOnly) {
  const ops = {
    loading: false,
    error: "",
    panelErrors: [],
    dash: { society: { name: "QA Society" }, reset_day: 15, devices: [device()] },
    commands: {},
    events: [],
    last: null,
    queue() { return false; },
    setSlotConfig() { return false; },
    isPending() { return false; },
    refresh() {},
  };
  const hooked = load(path.join(SRC, "components", "ops", "useOperations.ts"));
  const energyHook = load(path.join(SRC, "components", "ops", "energy", "useEnergy.ts"));
  hooked.useOperations = () => ops;
  energyHook.useEnergy = () => energy(mode);
  // One pass. A second pass shifts hook indexes after the in-render allocation
  // update, and these assertions do not depend on that second allocation value.
  react.reset();
  const tree = OperationalDashboard({ societyId: "1", readOnly, backHref: "/society/1" });
  const file = path.join(outDir, `dashboard-layout-${mode}-${readOnly}.html`);
  fs.writeFileSync(file, `<!DOCTYPE html><html><body>${toHtml(tree)}</body></html>`);
}

for (const mode of ["MANUAL", "AUTO"]) {
  for (const readOnly of [false, true]) renderOne(mode, readOnly);
}

process.stdout.write(`${outDir}\n`);
