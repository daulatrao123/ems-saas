"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");
let ts;
try { ts = require("typescript"); } catch { ts = require(path.join(__dirname, "..", "frontend", "node_modules", "typescript")); }

function walk(node, visitor) {
  if (Array.isArray(node)) return node.forEach((child) => walk(child, visitor));
  if (!node || typeof node !== "object") return;
  visitor(node);
  const children = node.props?.children;
  if (Array.isArray(children)) children.forEach((c) => walk(c, visitor));
  else walk(children, visitor);
}

function byTestId(tree, id) {
  let found;
  walk(tree, (node) => { if (!found && node?.props?.["data-testid"] === id) found = node; });
  if (!found) throw new Error(`Missing data-testid=${id}`);
  return found;
}

function firstByPrefix(tree, prefix) {
  let found;
  walk(tree, (node) => {
    const id = node?.props?.["data-testid"];
    if (!found && typeof id === "string" && id.startsWith(prefix)) found = node;
  });
  if (!found) throw new Error(`Missing data-testid prefix=${prefix}`);
  return found;
}

function text(node) {
  if (node == null) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join(" ");
  if (typeof node === "object") return text(node.props?.children);
  return "";
}

const norm = (v) => String(v || "").replace(/\s+/g, " ").trim();

function jsxRuntime() {
  const pack = (type, props) => (typeof type === "function" ? type(props || {}) : { type, props: props || {} });
  return { jsx: pack, jsxs: pack, Fragment: Symbol.for("react.fragment") };
}

function transpile(relPath, exportName, mocks = {}, extra = {}) {
  const full = path.join(__dirname, "..", relPath);
  const src = fs.readFileSync(full, "utf8");
  const out = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    fileName: path.basename(full),
  }).outputText;
  const module = { exports: {} };
  const req = (id) => {
    if (id in mocks) return mocks[id];
    throw new Error(`Unexpected import: ${id}`);
  };
  vm.runInNewContext(out, { module, exports: module.exports, require: req, console, URLSearchParams, AbortController, ...extra }, { filename: `${exportName}.compiled.cjs` });
  return module.exports[exportName];
}

function hookHarness() {
  const slots = []; let idx = 0;
  return {
    React: {
      useState(initial) {
        const i = idx++;
        if (!slots[i]) slots[i] = { value: typeof initial === "function" ? initial() : initial };
        return [slots[i].value, (next) => { slots[i].value = typeof next === "function" ? next(slots[i].value) : next; }];
      },
    },
    renderStart() { idx = 0; },
  };
}

test("dashboard presentation source contracts remain truthful and navigable", () => {
  const sections = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "DashboardSections.tsx"), "utf8");
  assert.match(sections, /\[\"overview\", \"Overview\"\]/);
  assert.match(sections, /\[\"energy\", \"Energy & wings\"\]/);
  assert.match(sections, /\[\"references\", \"References\"\]/);
  assert.match(sections, /\[\"history\", \"History\"\]/);
  assert.match(sections, /hasDevice \|\| key === "overview"/);

  const gaps = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "ConfigurationGaps.tsx"), "utf8");
  assert.match(gaps, /Pending controller report/);
  assert.match(gaps, /Stale report/);
  assert.match(gaps, /UNKNOWN/);
  assert.match(gaps, /feedback_hardware_installed === false \? "Not installed" : "UNKNOWN"/);
  assert.doesNotMatch(gaps, /426\.10/);

  const dashboard = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "OperationalDashboard.tsx"), "utf8");
  assert.match(dashboard, /data-testid="hardware-fault-banner"/);
  assert.match(dashboard, /data-testid="controller-storage-summary"/);
  assert.match(dashboard, /<details data-testid="controller-diagnostics"/);

  const slot = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "SlotCard.tsx"), "utf8");
  assert.match(slot, /data-testid=\{`slot-disabled-data-\$\{code\}`\}/);
  assert.match(slot, /!readOnly && slot && !slot\.disabled/);

  const last = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "LastResponse.tsx"), "utf8");
  assert.match(last, /Historical command result/);
  assert.match(last, /GPIO CONFIRMED/);
  assert.match(last, /CONTACTOR VERIFIED/);
});

test("OperationalLogs filters severity, resets limit, and show-more remains bounded", () => {
  const h = hookHarness();
  const OperationalLogs = transpile("frontend/src/components/ops/OperationalLogs.tsx", "OperationalLogs", {
    react: h.React,
    "react/jsx-runtime": jsxRuntime(),
    "./types": { COMMAND_LABEL: {}, fmtDateTime: (v) => String(v || "—"), statusTone: () => "tone" },
    "./DashboardHeader": { btn: "btn", input: "input", label: "label", tone: { gray: "gray" } },
  });

  const commands = Array.from({ length: 25 }, (_, i) => ({
    id: `c${i}`,
    command: "set_active_slot",
    slot: i % 2 ? "A" : "",
    status: i % 5 === 0 ? "failed" : i % 3 === 0 ? "queued" : "completed",
    sequence_no: i + 1,
    result: i % 5 === 0 ? "NOT_AVAILABLE" : i % 3 === 0 ? null : "VERIFIED_ON",
    error: i % 5 === 0 ? "DISPLAY_UNAVAILABLE" : null,
    created_at: `2026-09-${String((i % 28) + 1).padStart(2, "0")}T10:00:00Z`,
    delivered_at: null,
    executing_at: null,
    completed_at: i % 3 === 0 ? null : `2026-09-${String((i % 28) + 1).padStart(2, "0")}T10:01:00Z`,
  }));
  const events = Array.from({ length: 20 }, (_, i) => ({
    id: `e${i}`,
    ts: `2026-09-${String((i % 28) + 1).padStart(2, "0")}T11:00:00Z`,
    level: i % 4 === 0 ? "ERROR_CLOCK" : i % 3 === 0 ? "WARN_DRIFT" : "INFO_REFRESH",
    msg: `event-${i}`,
  }));

  const render = () => { h.renderStart(); return OperationalLogs({ commands, events }); };
  let tree = render();
  assert.match(norm(text(byTestId(tree, "operational-logs-count"))), /20 \/ \d+ matching · 45 loaded/);
  assert.ok(byTestId(tree, "logs-more"));
  byTestId(tree, "logs-more").props.onClick();
  tree = render();
  assert.match(norm(text(byTestId(tree, "operational-logs-count"))), /40 \/ \d+ matching/);
  byTestId(tree, "logs-more").props.onClick();
  tree = render();
  assert.match(norm(text(byTestId(tree, "operational-logs-count"))), /45 \/ 45 matching/);
  assert.throws(() => byTestId(tree,"logs-more"), /Missing/);

  byTestId(tree, "logs-level-filter").props.onChange({ target: { value: "ERROR" } });
  tree = render();
  assert.match(norm(text(byTestId(tree, "operational-logs-count"))), /^\d+ \/ \d+ matching/);

  byTestId(tree, "logs-level-filter").props.onChange({ target: { value: "INFO" } });
  tree = render();
  const firstRowLevel = text(firstByPrefix(tree, "log-level-"));
  assert.equal(firstRowLevel, "INFO");
  for(const level of ["ERROR","WARN","INFO"]){
    byTestId(tree,"logs-level-filter").props.onChange({target:{value:level}}); tree=render();
    const levels=[]; walk(tree,node=>{if(node.props?.["data-testid"]?.startsWith("log-level-"))levels.push(text(node));});
    assert.ok(levels.length>0); assert.ok(levels.every(v=>v===level));
  }
  byTestId(tree,"logs-level-filter").props.onChange({target:{value:"ALL"}}); tree=render();
  assert.match(norm(text(byTestId(tree,"operational-logs-count"))),/20 \/ 45 matching/);
});

test("offline dashboard fixture keeps disclosures, member restrictions, and references truthful", () => {
  execFileSync("node", ["/app/test_reports/dashboard_layout.cjs"], { stdio: "pipe" });
  for (const f of ["/tmp/dashboard-layout-MANUAL-false.html", "/tmp/dashboard-layout-MANUAL-true.html", "/tmp/dashboard-layout-AUTO-false.html", "/tmp/dashboard-layout-AUTO-true.html"]) {
    fs.copyFileSync(f, f.replace(/\.html$/, "-normal.html"));
  }
  execFileSync("node", ["/app/test_reports/dashboard_layout.cjs", "--unavailable"], { stdio: "pipe" });
  for (const f of ["/tmp/dashboard-layout-MANUAL-false.html", "/tmp/dashboard-layout-MANUAL-true.html", "/tmp/dashboard-layout-AUTO-false.html", "/tmp/dashboard-layout-AUTO-true.html"]) {
    fs.copyFileSync(f, f.replace(/\.html$/, "-unavailable.html"));
  }

  const opNormal = fs.readFileSync("/tmp/dashboard-layout-MANUAL-false-normal.html", "utf8");
  assert.match(opNormal, /data-testid="dashboard-nav-overview"/);
  assert.match(opNormal, /href="#ops-overview"/);
  assert.match(opNormal, /data-testid="dashboard-nav-energy"/);
  assert.match(opNormal, /data-testid="dashboard-nav-references"/);
  assert.match(opNormal, /data-testid="dashboard-nav-controls"/);
  assert.match(opNormal, /data-testid="dashboard-nav-history"/);
  assert.match(opNormal, /data-testid="controller-diagnostics"/);
  assert.match(opNormal, /data-testid="controller-storage-summary"[^>]*>Storage: WARNING · SMART UNAVAILABLE/);
  assert.match(opNormal, /data-testid="slot-disabled-data-toggle-D"/);
  assert.match(opNormal, /data-testid="slot-enable-D"/);
  assert.doesNotMatch(opNormal, /cmd-set_active_slot-offline-controller-D/);
  assert.doesNotMatch(opNormal, /cmd-off_slot-offline-controller-D/);

  const member = fs.readFileSync("/tmp/dashboard-layout-MANUAL-true-normal.html", "utf8");
  assert.doesNotMatch(member, /data-testid="lcd-panel"/);
  assert.doesNotMatch(member, /data-testid="grid-save"/);

  const unavailable = fs.readFileSync("/tmp/dashboard-layout-MANUAL-false-unavailable.html", "utf8");
  assert.match(unavailable, /data-testid="configuration-gap-generation-value"[^>]*>Meter disabled/);
  assert.match(unavailable, /data-testid="configuration-gap-targets-value"[^>]*>UNAVAILABLE · A, B, C/);
  assert.match(unavailable, /data-testid="configuration-gap-allocation-value"[^>]*>Disabled in configuration/);
  assert.match(unavailable, /data-testid="configuration-gap-feedback-value"[^>]*>Not installed/);
  assert.match(unavailable, /data-testid="energy-target-delivery-status"[^>]*>Awaiting controller report/);
  assert.doesNotMatch(unavailable, /bill reference 426\.10 physical/i);
});
