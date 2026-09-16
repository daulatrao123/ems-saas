"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
let ts;
try { ts = require("typescript"); } catch { ts = require(path.join(__dirname, "..", "frontend", "node_modules", "typescript")); }

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
  const slots = []; let index = 0; let effects = [];
  const eq = (a, b) => a && b && a.length === b.length && a.every((v, i) => v === b[i]);
  const React = {
    useState(initial) { const i = index++; slots[i] ??= { value: typeof initial === "function" ? initial() : initial }; return [slots[i].value, (v) => { slots[i].value = typeof v === "function" ? v(slots[i].value) : v; }]; },
    useRef(initial) { const i = index++; slots[i] ??= { current: initial }; return slots[i]; },
    useCallback(fn, deps) { const i = index++; if (!slots[i] || !eq(slots[i].deps, deps)) slots[i] = { deps, value: fn }; return slots[i].value; },
    useEffect(fn, deps) { const i = index++; if (!slots[i] || !eq(slots[i].deps, deps)) { const prev = slots[i]; slots[i] = { deps }; effects.push(() => { prev?.cleanup?.(); slots[i].cleanup = fn(); }); } },
  };
  const runEffects = () => { const work = effects; effects = []; work.forEach((fn) => fn()); };
  const cleanup = () => { slots.forEach((s) => s?.cleanup?.()); };
  return { React, renderStart: () => { index = 0; }, runEffects, cleanup };
}

// Execute the real dependency graph. Only React's scheduler, HTTP and clock
// are boundaries; validators/readRequest/history hooks are NOT substituted.
function runtime(api, React, extra = {}) {
  const cache = new Map(), root = path.join(__dirname, "..", "frontend", "src");
  function load(file) {
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} }; cache.set(file, module);
    const code = ts.transpileModule(fs.readFileSync(file, "utf8"), { fileName: file,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
    const req = (id) => {
      if (id === "react") return React;
      if (id === "react/jsx-runtime") return { jsx: (t,p) => typeof t === "function" ? t(p || {}) : { type:t, props:p || {} }, jsxs: (t,p) => typeof t === "function" ? t(p || {}) : { type:t, props:p || {} }, Fragment: "fragment" };
      if (id === "./DashboardHeader") return { btn:"btn",label:"label",panel:"panel",tone:{gray:"gray",cyan:"cyan"} };
      if (id === "@/lib/api") return api;
      if (id.startsWith(".")) { const prefix = path.resolve(path.dirname(file), id); return load([".ts", ".tsx"].map((ext) => prefix + ext).find(fs.existsSync)); }
      throw Error(`Offline boundary: unexpected module ${id}`);
    };
    vm.runInNewContext(code, { module, exports: module.exports, require: req, console, URLSearchParams, AbortController,
      setTimeout, clearTimeout, Date, ...extra }, { filename: file });
    return module.exports;
  }
  return (rel) => load(path.join(root, rel));
}
const flush = () => new Promise((r) => setImmediate(r));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
function eventWindow() {
  const listeners = new Map(), sent = [];
  return { sent, listeners,
    addEventListener(n, fn) { if (!listeners.has(n)) listeners.set(n, new Set()); listeners.get(n).add(fn); },
    removeEventListener(n, fn) { listeners.get(n)?.delete(fn); },
    dispatchEvent(e) { sent.push(e); listeners.get(e.type)?.forEach((fn) => fn(e)); return true; } };
}
const CustomEventMock = class { constructor(type, init) { this.type = type; this.detail = init?.detail; } };

test("clock and delivery notices render actual data safely without implying hardware verification", () => {
  const h = hookHarness();
  const graph = runtime({ get() { throw Error("No network in notice rendering"); } }, h.React);
  const Clock = graph("components/ops/energy/ClockQualificationNotice.tsx").ClockQualificationNotice;
  const Delivery = graph("components/ops/energy/TargetDeliveryStatus.tsx").TargetDeliveryStatus;
  for (const value of [null, undefined, [], "bad", 7, { status: {} }]) {
    assert.doesNotThrow(() => Clock({ report: value }));
    assert.doesNotThrow(() => Delivery({ delivery: value }));
  }
  const clock = Clock({ report: { status: "WARNING", sampled_at: "2026-09-16T01:00:00Z", open_days: 2,
    rejected_rows: [null, { operating_date: "2099-01-01", reason: "FUTURE_OPERATING_DATE" }, { reason: {}, operating_date: [] }] } });
  assert.ok(nodeById(clock, "energy-clock-warning"));
  assert.ok(nodeById(clock, "energy-clock-open-backlog"));
  assert.ok(nodeById(clock, "energy-clock-rejected-0"));
  const delivery = Delivery({ delivery: { desired_version: 5, reported_version: 4, status: "PENDING", reported_at: null } });
  assert.equal(nodeById(delivery, "energy-target-delivery-status").props.children, "Pending controller version");
  assert.equal(nodeById(delivery, "energy-target-delivery-time"), undefined);
  const reported = Delivery({ delivery: { desired_version: 5, reported_version: 5, status: "REPORTED_CURRENT" } });
  assert.equal(nodeById(reported, "energy-target-delivery-status").props.children, "Controller reports current version");
  assert.doesNotMatch(JSON.stringify(reported), /hardware.verified|HW VERIFIED/i);
});
function nodeById(tree, id) {
  if (Array.isArray(tree)) { for (const child of tree) { const found = nodeById(child,id); if(found) return found; } }
  if (!tree || typeof tree !== "object") return undefined;
  if (tree.props?.['data-testid'] === id) return tree;
  return nodeById(tree.props?.children,id);
}

test("retained 20x4 LCD reads validate, send/deactivate refresh, members never read or write", async () => {
  const h=hookHarness(), calls=[]; let active=null, malformed=false;
  const api={get:async()=>({data:malformed?{active:null,messages:{bad:true}}:{active,messages:active?[active]:[]}}),post:async(url,data)=>{
    calls.push({url,data});
    active=url.endsWith('/deactivate')?null:{id:1,device_id:'one',message:data.message,active:true,created_at:'2026-09-30T10:00:00Z',expires_at:null,delivered_at:null};
    return {data:{success:true}};
  }};
  const Panel=runtime(api,h.React)("components/ops/LcdMessagePanel.tsx").LcdMessagePanel;
  const render=()=>{h.renderStart();const v=Panel({societyId:'1',device:{id:'one',name:'Pi'},readOnly:false});h.runEffects();return v;};
  render();await flush();let tree=render();
  assert.ok(nodeById(tree,'lcd-panel'));assert.equal(nodeById(tree,'lcd-error'),undefined);
  nodeById(tree,'lcd-input').props.onChange({target:{value:'Offline test message'}});tree=render();
  await nodeById(tree,'lcd-send').props.onClick();tree=render();
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/admin/lcd-messages');assert.equal(calls[0].data.device_id,'one');
  assert.equal(nodeById(tree,'lcd-input').props.value,'');assert.ok(nodeById(tree,'lcd-deactivate'));
  malformed=true;await nodeById(tree,'lcd-deactivate').props.onClick();tree=render();
  assert.match(nodeById(tree,'lcd-error').props.children,/LCD history unavailable/);
  assert.equal(nodeById(tree,'lcd-deactivate'),undefined);h.cleanup();
  const member=hookHarness();let accesses=0;
  const View=runtime({get(){accesses++;throw Error('member must not request LCD');},post(){accesses++;throw Error('member must not write LCD');}},member.React)("components/ops/LcdMessagePanel.tsx").LcdMessagePanel;
  member.renderStart();assert.equal(View({societyId:'1',device:{id:'one'},readOnly:true}),null);member.runEffects();await flush();assert.equal(accesses,0);member.cleanup();
});
const dashboardFixture = (sid, last_sync = "2026-09-30T10:00:00Z") => ({ society_id: Number(sid), society: { name: `Society ${sid}` }, reset_day: 15,
  devices: [{ id: `d${sid}`, name: "Pi", connected: true, last_sync, feedback_hardware_installed: false, slots: {
    A: { display_name: "Wing A", disabled: false, physical_toggle: "UNKNOWN", used_days: 1, target_days: 3, feedback_enabled: false } } }] });

test("actual operations + provisioning converge on same snapshot without a refresh loop", async () => {
  const ops = hookHarness(), prov = hookHarness(), win = eventWindow(), calls = [];
  let sync = "2026-09-30T10:00:00Z";
  const api = { get: async (url) => { calls.push(url);
    if (url.includes("/dashboard?")) return { data: dashboardFixture("1", sync) };
    if (url.includes("/devices?")) return { data: { society_id: 1, devices: [{ id: "d1", name: "Pi", online: false, last_sync: null }] } };
    if (url.includes("/pi-commands?")) return { data: { commands: [] } };
    return { data: { events: [{ id: 1, ts: "now", level: 42, msg: "bad" }] } };
  } };
  const extras = { window: win, CustomEvent: CustomEventMock };
  const opHook = runtime(api, ops.React, extras)("components/ops/useOperationsRead.ts").useOperationsRead;
  const prHook = runtime(api, prov.React, extras)("components/provisioning/useProvisioningDevices.ts").useProvisioningDevices;
  const renderOps = () => { ops.renderStart(); const v = opHook("1"); ops.runEffects(); return v; };
  const renderProv = () => { prov.renderStart(); const v = prHook("1"); prov.runEffects(); return v; };
  renderOps(); renderProv(); await flush();
  let o = renderOps(), p = renderProv();
  assert.equal(o.loading, false); assert.equal(p.devices[0].last_sync, o.dash.devices[0].last_sync);
  assert.equal(p.devices[0].online, o.dash.devices[0].connected);
  assert.equal(o.panelErrors.length, 1, "numeric event level rejected safely by actual validator");
  const count = calls.length; await flush(); assert.equal(calls.length, count, "no self-triggered event loop or polling");
  sync = "2026-09-30T10:01:00Z"; await o.refreshAll("1"); await flush(); o = renderOps(); p = renderProv();
  assert.equal(p.devices[0].last_sync, sync); assert.equal(o.dash.devices[0].last_sync, sync);
  ops.cleanup(); prov.cleanup(); assert.equal([...win.listeners.values()].reduce((n, s) => n + s.size, 0), 0);
});

test("actual dashboard guard rejects malformed data, retries and isolates late society replies", async () => {
  const h = hookHarness(), win = eventWindow(), late = deferred(); let sid = "1", broken = false;
  const api = { get: async (url) => {
    const selected = new URLSearchParams(url.split("?")[1]).get("society_id");
    if (url.includes("/dashboard?")) return selected === "1" ? late.promise : { data: broken ? { ...dashboardFixture(selected), devices: {} } : dashboardFixture(selected) };
    return { data: url.includes("pi-commands") ? { commands: [] } : { events: [] } };
  } };
  const hook = runtime(api, h.React, { window: win, CustomEvent: CustomEventMock })("components/ops/useOperationsRead.ts").useOperationsRead;
  const render = () => { h.renderStart(); const v = hook(sid); h.runEffects(); return v; };
  render(); await flush(); sid = "2"; assert.equal(render().dash, null); await flush(); let out = render();
  assert.equal(out.dash.society_id, 2); late.resolve({ data: dashboardFixture("1") }); await flush();
  assert.equal(render().dash.society_id, 2);
  broken = true; await out.refreshAll("2"); out = render(); assert.equal(out.loading, false); assert.equal(out.dash, null); assert.match(out.error, /unavailable/);
  broken = false; await out.refreshAll("2"); out = render(); assert.equal(out.error, ""); assert.equal(out.dash.society_id, 2);
  const dispatches = win.sent.length; h.cleanup(); await out.loadDashboard("2"); assert.equal(win.sent.length, dispatches, "no late targeted publish after unmount");
});

function energyFixture(did) {
  const day = "2026-09-30", wings = ["A", "B", "C", "D"];
  const metric = Object.fromEntries(["today", "yesterday", "this_month", "previous_month", "this_year", "lifetime", "reset_period"].map((k) => [k, { kwh: 10, status: "PHYSICAL" }]));
  const point = { date: day, generated_kwh: 10, consumed_kwh: 0, generation_minus_consumption_kwh: 10, generation_source: "PHYSICAL", consumption_source: "HISTORICAL" };
  return {
    summary: { device_id: did, as_of_operating_date: day, calculation: { mode: "MANUAL", version: 1, operating_date: day },
      generation_meter: { generation: metric, unattributed: null }, wings: Object.fromEntries(wings.map((w) => [w, { wing: w, consumption_meter: { comm_status: "ONLINE" },
        generation: metric, consumption: metric, required_generation: { target_kwh_per_day: 10, achievement_percent: 100 } }])) },
    comparison: { device_id: did, mode: "MANUAL", version: 1, operating_date: day, society: { today: point, rows: [point] },
      wings: Object.fromEntries(wings.map((w) => [w, { wing: w, today: point, rows: [point] }])) },
    monthly: { meter_id: "M1", rows: [{ month: "2026-09", generation_kwh: 0, source: "PHYSICAL" }] },
    allocation: { allocation: { enabled: true, sequence: wings, wings: Object.fromEntries(wings.map((w) => [w, { generation_attribution_enabled: true }])) } },
  };
}

test("real energy dependency graph: slow comparison/history cannot block summary or other panels", async () => {
  const h = hookHarness(), slowComparison = deferred(), slowHistory = deferred(); let phase = "slow", selected = "one";
  const timers = new Map(); let id = 0;
  const api = { get: async (url, options) => {
    assert.equal(options.timeout, 12000); assert.ok(options.signal);
    const did = new URLSearchParams(url.split("?")[1]).get("device_id"), f = energyFixture(did);
    if (url.includes("/summary?")) return { data: phase === "bad-summary" ? { ...f.summary, calculation: null } : f.summary };
    if (url.includes("/comparison?")) return phase === "slow" ? slowComparison.promise : { data: phase === "bad-comparison" ? { ...f.comparison, wings: { A: null } } : f.comparison };
    if (url.includes("/monthly?")) return { data: f.monthly };
    if (url.includes("/allocation?")) return { data: f.allocation };
    if (url.includes("/pi-events?")) return phase === "slow" ? slowHistory.promise : { data: { events: [] } };
    return { data: { rows: [] } };
  } };
  const hook = runtime(api, h.React, { setTimeout: (fn, ms) => { assert.equal(ms, 12000); timers.set(++id, fn); return id; }, clearTimeout: (n) => timers.delete(n) })("components/ops/energy/useEnergy.ts").useEnergy;
  const render = () => { h.renderStart(); const v = hook("1", selected); h.runEffects(); return v; };
  render(); await flush(); let out = render();
  assert.equal(out.loading, false); assert.equal(out.summary.device_id, "one"); assert.equal(out.comparison, null);
  assert.equal(out.monthly.rows[0].generation_kwh, 0); assert.equal(out.allocation.enabled, true); assert.equal(out.panelErrors.length, 0);
  [...timers.values()].forEach((fn) => fn()); await flush(); out = render();
  assert.match(out.error, /Comparison unavailable/); assert.match(out.panelErrors.join(" "), /Event history unavailable/); assert.equal(out.loading, false);
  phase = "good"; await out.refresh(); out = render(); assert.equal(out.error, ""); assert.equal(out.panelErrors.length, 0); assert.equal(out.comparison.society.today.consumed_kwh, 0);
  for (const p of ["bad-summary", "bad-comparison"]) { phase = p; await out.refresh(); out = render(); assert.equal(out.loading, false); assert.equal(out.comparison, null); assert.ok(out.error); }
  phase = "good"; selected = "two"; assert.equal(render().summary, null); await flush(); out = render(); assert.equal(out.summary.device_id, "two");
  slowComparison.resolve({ data: energyFixture("one").comparison }); slowHistory.resolve({ data: { events: [{ id: 1, ts: "now", level: "ENERGY_ALLOCATION_BLOCKED", msg: "old" }] } });
  await flush(); out = render(); assert.equal(out.comparison.device_id, "two"); assert.equal(out.events.length, 0);
  h.cleanup(); assert.equal(timers.size, 0, "all timers cancelled");
});

test("readRequest enforces 12s overall timeout and supports external cancellation", async () => {
  const calls = { timeout: null, cleared: 0, aborted: 0 };
  const timers = [];
  const api = { get: () => new Promise(() => {}) };
  const readRequest = transpile("frontend/src/components/ops/readRequest.ts", "readRequest", { "@/lib/api": api }, {
    setTimeout: (fn, ms) => { calls.timeout = ms; timers.push(fn); return timers.length; },
    clearTimeout: () => { calls.cleared += 1; },
  });
  const promise = readRequest("/api/admin/dashboard?society_id=1");
  assert.equal(calls.timeout, 12000);
  timers[0]();
  await assert.rejects(promise, (e) => e && e.code === "ECONNABORTED");
  const ctrl = new AbortController();
  const cancelled = readRequest("/api/admin/dashboard?society_id=1", ctrl.signal);
  ctrl.signal.addEventListener("abort", () => { calls.aborted += 1; });
  ctrl.abort();
  await assert.rejects(cancelled, (e) => e && e.code === "ERR_CANCELED");
});

test("useOperationsRead publishes dashboard and ends loading without waiting panel reads", async () => {
  const h = hookHarness();
  const listeners = new Map();
  const win = {
    dispatches: [],
    addEventListener: (n, fn) => listeners.set(n, fn),
    removeEventListener: (n) => listeners.delete(n),
    dispatchEvent: (e) => { win.dispatches.push(e.type); const fn = listeners.get(e.type); if (fn) fn(e); return true; },
  };
  let malformed = true;
  const readRequest = async (url) => {
    if (url.includes("/dashboard")) return { data: { society_id: "1", society: { name: "S" }, reset_day: 15, devices: [{ id: "d1", name: "Pi", connected: true, slots: { A: { display_name: "A", disabled: false, used_days: 1, target_days: 2, physical_toggle: "UNKNOWN" } } }] } };
    if (url.includes("/pi-commands")) return malformed ? { data: { commands: [{ id: "1", command: "x", slot: "A", status: 1, sequence_no: 1, result: null, error: null, params: {} }] } } : { data: { commands: [{ id: "1", command: "x", slot: "A", status: "queued", sequence_no: 1, result: null, error: null, params: {} }] } };
    return new Promise(() => {}); // hanging events endpoint must not block dashboard publish/loading
  };
  const useOperationsRead = transpile("frontend/src/components/ops/useOperationsRead.ts", "useOperationsRead", {
    react: h.React,
    "./types": { errorText: (e) => ({ detail: String(e) }) },
    "./readRequest": { readRequest, record: (v) => !!v && typeof v === "object" && !Array.isArray(v) },
    "./operationsValidation": {
      dashboardValid: () => true,
      commandsValid: (rows) => Array.isArray(rows) && rows.every((r) => typeof r.status === "string"),
      eventsValid: () => true,
    },
  }, { window: win, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init?.detail; } } });
  const flush = () => new Promise((r) => setImmediate(r));
  const render = () => { h.renderStart(); const out = useOperationsRead("1"); h.runEffects(); return out; };
  render(); await flush(); let out = render();
  assert.equal(out.loading, false, "dashboard load must complete without panel endpoints resolving");
  assert.equal(out.dash?.devices?.[0]?.id, "d1");
  assert.ok(win.dispatches.includes("ems-dashboard-refreshed"));
  await out.loadCommands("1", "d1"); out = render();
  assert.match(out.panelErrors.join(" | "), /Command history unavailable/);
  malformed = false;
  await out.loadCommands("1", "d1"); out = render();
  assert.equal(out.panelErrors.length, 0, "retry clears its own panel error");
  h.cleanup();
});

test("useProvisioningDevices dispatches metadata-changed and consumes ops refresh snapshot", async () => {
  const h = hookHarness();
  const listeners = new Map();
  const win = {
    sent: [],
    addEventListener: (n, fn) => listeners.set(n, fn),
    removeEventListener: (n) => listeners.delete(n),
    dispatchEvent: (e) => { win.sent.push({ type: e.type, detail: e.detail }); const fn = listeners.get(e.type); if (fn) fn(e); return true; },
  };
  const readRequest = async () => ({ data: { society_id: "1", devices: [{ id: "d1", name: "Pi", online: false }] } });
  const useProvisioningDevices = transpile("frontend/src/components/provisioning/useProvisioningDevices.ts", "useProvisioningDevices", {
    react: h.React,
    "./AdminDevices": {},
    "../ops/types": { errorText: (e) => ({ detail: String(e) }) },
    "../ops/readRequest": { readRequest, record: (v) => !!v && typeof v === "object", text: (v) => typeof v === "string" },
  }, { window: win, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init?.detail; } } });
  const flush = () => new Promise((r) => setImmediate(r));
  const render = (sid = "1") => { h.renderStart(); const out = useProvisioningDevices(sid); h.runEffects(); return out; };
  render("1"); await flush(); let out = render("1");
  assert.equal(out.loading, false);
  assert.equal(out.devices[0].online, false);
  assert.ok(win.sent.some((e) => e.type === "ems-device-metadata-changed" && e.detail?.societyId === "1"));
  win.dispatchEvent(new (class { constructor() { this.type = "ems-dashboard-refreshed"; this.detail = { society_id: "1", devices: [{ id: "d1", connected: true, last_sync: "2026-02-01T00:00:00Z", config_state: "APPLIED", feedback_hardware_installed: true }] }; } })());
  out = render("1");
  assert.equal(out.devices[0].online, true);
  assert.equal(out.devices[0].config_state, "APPLIED");
  h.cleanup();
  assert.equal(listeners.size, 0, "listeners must be cleaned on unmount");
});

test("LastResponse labels GPIO/contactor states; dashboard keeps single LCD panel", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "OperationalDashboard.tsx"), "utf8");
  assert.equal((src.match(/<LcdMessagePanel/g) || []).length, 1, "exactly one rendered LCD panel must remain");
  assert.doesNotMatch(src, /LcdControl|16x2/i);
  assert.match(src, /key=\{`\$\{societyId\}:\$\{device\.id\}`\}/);
  assert.match(src, /!readOnly && <SystemControls/);

  const scopeSrc = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "ConsumptionScopeLabel.tsx"), "utf8");
  assert.match(scopeSrc, /Current logical configuration/);
  assert.match(scopeSrc, /Disabled \/ excluded/);

  const LastResponse = transpile("frontend/src/components/ops/LastResponse.tsx", "LastResponse", {
    "react/jsx-runtime": {
      jsx: (t, p) => (typeof t === "function" ? t(p || {}) : { type: t, props: p || {} }),
      jsxs: (t, p) => (typeof t === "function" ? t(p || {}) : { type: t, props: p || {} }),
      Fragment: Symbol.for("react.fragment"),
    },
    "./types": { COMMAND_LABEL: {}, TERMINAL: new Set(["completed", "failed", "acked", "hardware_verified"]), fmtTime: (v) => String(v || "—"), statusTone: () => "tone" },
    "./DashboardHeader": { label: "label", panel: "panel" },
  });
  const text = (node) => {
    if (node == null) return "";
    if (typeof node === "string" || typeof node === "number") return String(node);
    if (Array.isArray(node)) return node.map(text).join(" ");
    if (typeof node === "object") return text(node.props?.children);
    return "";
  };

  const gpio = LastResponse({ last: null, row: { command: "off_slot", slot: "A", sequence_no: 1, status: "hardware_verified", result: "GPIO_CONFIRMED", created_at: "t", delivered_at: null, completed_at: null, hardware_verified_at: "2026-01-01", params: {} } });
  const gpioText = text(gpio);
  assert.match(gpioText, /GPIO CONFIRMED/);
  assert.doesNotMatch(gpioText, /HW VERIFIED|CONTACTOR VERIFIED/);

  const contactor = LastResponse({ last: null, row: { command: "set_active_slot", slot: "A", sequence_no: 1, status: "hardware_verified", result: "VERIFIED_ON", created_at: "t", delivered_at: null, completed_at: null, hardware_verified_at: "2026-01-01", params: {} } });
  assert.match(text(contactor), /CONTACTOR VERIFIED/);

  const unknown = LastResponse({ last: null, row: { command: "set_active_slot", slot: "A", sequence_no: 1, status: "hardware_verified", result: "X", created_at: "t", delivered_at: null, completed_at: null, hardware_verified_at: "2026-01-01", params: {} } });
  assert.match(text(unknown), /ACKNOWLEDGED/);
});
