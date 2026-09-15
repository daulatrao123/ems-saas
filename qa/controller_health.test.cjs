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

function collectTestIds(tree) {
  const ids = [];
  walk(tree, (n) => {
    const id = n?.props?.["data-testid"];
    if (typeof id === "string") ids.push(id);
  });
  return ids;
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
  vm.runInNewContext(out, { module, exports: module.exports, require: req, console, Date }, { filename: `${exportName}.compiled.cjs` });
  return module.exports[exportName];
}

const fixedNowMs = Date.parse("2026-02-01T00:00:00Z");

function withFixedNow(fn) {
  const realNow = Date.now;
  Date.now = () => fixedNowMs;
  try {
    return fn();
  } finally {
    Date.now = realNow;
  }
}

const typeMocks = {
  SLOT_CODES: ["A", "B", "C", "D"],
  ago: () => "5s ago",
  fmtDateTime: (iso) => (iso ? `FMT:${iso}` : "—"),
  fmtUptime: (sec) => (sec == null || sec <= 0 ? null : `${sec}s`),
  nextResetDate: () => "15 Feb 2026",
};

const WatchdogHealth = loadTsx("frontend/src/components/ops/WatchdogHealth.tsx", "WatchdogHealth", {
  "react/jsx-runtime": makeJsxRuntime(),
  "./types": { fmtDateTime: typeMocks.fmtDateTime },
  "./DashboardHeader": { label: "label" },
});

const healthFreshness = loadTsx("frontend/src/components/ops/WatchdogHealth.tsx", "healthFreshness", {
  "react/jsx-runtime": makeJsxRuntime(),
  "./types": { fmtDateTime: typeMocks.fmtDateTime },
  "./DashboardHeader": { label: "label" },
});

const StatusStrip = loadTsx("frontend/src/components/ops/StatusStrip.tsx", "StatusStrip", {
  "react/jsx-runtime": makeJsxRuntime(),
  "./types": typeMocks,
  "./DashboardHeader": { label: "label", panel: "panel" },
  "./WatchdogHealth": { WatchdogHealth, healthFreshness },
});

function mkDevice(health) {
  return {
    id: "d-1",
    name: "Pi",
    connected: true,
    active_slot: "A",
    last_sync: "2026-02-01T00:00:00Z",
    config_state: "APPLIED",
    telemetry: {
      cpu_temp: 99,
      uptime_seconds: 10,
      boot_count: 123,
      health,
    },
    slots: {
      A: { display_name: "Wing A", disabled: false },
      B: { display_name: "Wing B", disabled: false },
      C: { display_name: "Wing C", disabled: true },
      D: { display_name: "Wing D", disabled: true },
    },
  };
}

test("health freshness handles current, stale, unknown, and future timestamps", () => {
  withFixedNow(() => {
    const current = healthFreshness({ sampled_at: "2026-01-31T23:59:50Z", status: "CURRENT", max_age_seconds: 120 }, true);
    assert.equal(current, "CURRENT");

    const stale = healthFreshness({ sampled_at: "2026-01-31T23:57:00Z", status: "STALE", max_age_seconds: 120 }, true);
    assert.equal(stale, "STALE");

    const unknown = healthFreshness({ sampled_at: null, status: "UNKNOWN", max_age_seconds: 120 }, true);
    assert.equal(unknown, "UNKNOWN");

    const future = healthFreshness({ sampled_at: "2026-02-01T00:10:00Z", status: "CURRENT", max_age_seconds: 120 }, true);
    assert.equal(future, "UNKNOWN");
  });
});

test("WatchdogHealth shows configured/service evidence and keeps recovery NOT VERIFIED", () => {
  withFixedNow(() => {
    const health = {
      sampled_at: "2026-01-31T23:59:50Z",
      status: "CURRENT",
      max_age_seconds: 120,
      watchdog: {
        service_timeout_us: 1000000,
        service_state: "ACTIVE",
        hardware_state: "INACTIVE",
        hardware_timeout_seconds: 15,
        recovery: "PASSED",
      },
    };
    const tree = WatchdogHealth({ health, connected: true });
    assert.equal(byTestId(tree, "watchdog-service-status").props.children, "CONFIGURED");
    assert.equal(byTestId(tree, "watchdog-hardware-status").props.children, "INACTIVE");
    assert.equal(byTestId(tree, "watchdog-recovery-status").props.children, "NOT VERIFIED");
    assert.match(byTestId(tree, "watchdog-service-evidence").props.children, /Service: ACTIVE/);
  });
});

test("StatusStrip uses measured health values, including valid zero, not legacy numbers", () => {
  withFixedNow(() => {
    const health = {
      sampled_at: "2026-01-31T23:59:50Z",
      status: "CURRENT",
      max_age_seconds: 120,
      cpu: { celsius: 0, source: "LINUX_THERMAL" },
      boot: { count: 3, tracking_since: "2026-01-01T00:00:00Z", status: "OBSERVED" },
      watchdog: { service_timeout_us: 0, service_state: "INACTIVE", hardware_state: "INACTIVE", hardware_timeout_seconds: 0, recovery: "NOT_VERIFIED" },
    };
    const tree = StatusStrip({ device: mkDevice(health), resetDay: 15 });
    assert.equal(byTestId(tree, "stat-cpu-value").props.children, "0.0°C");
    assert.equal(byTestId(tree, "stat-boots-value").props.children, "3");
    assert.match(byTestId(tree, "stat-boots-detail").props.children, /Since/);

    // Unknown health must not fall back to legacy telemetry.cpu_temp/boot_count
    const unknown = { ...health, sampled_at: null, status: "UNKNOWN", cpu: { celsius: null, source: "UNKNOWN" }, boot: { count: null, tracking_since: null, status: "UNKNOWN" } };
    const treeUnknown = StatusStrip({ device: mkDevice(unknown), resetDay: 15 });
    assert.equal(byTestId(treeUnknown, "stat-cpu-value").props.children, "UNKNOWN");
    assert.equal(byTestId(treeUnknown, "stat-boots-value").props.children, "UNKNOWN");
    assert.equal(byTestId(treeUnknown, "stat-boots-detail").props.children, "Observed OS boots · unknown");
  });
});

test("StatusStrip shows stale/disconnected semantics and does not leak prior device data", () => {
  withFixedNow(() => {
    const stale = {
      sampled_at: "2026-01-31T23:55:00Z",
      status: "STALE",
      max_age_seconds: 120,
      cpu: { celsius: 48.2, source: "LINUX_THERMAL" },
      boot: { count: 8, tracking_since: "2026-01-01T00:00:00Z", status: "OBSERVED" },
      watchdog: { service_timeout_us: 1000000, service_state: "ACTIVE", hardware_state: "ACTIVE", hardware_timeout_seconds: 15, recovery: "NOT_VERIFIED" },
    };
    const d1 = mkDevice(stale);
    d1.id = "d-1";
    d1.connected = false;
    const tree1 = StatusStrip({ device: d1, resetDay: 15 });
    assert.equal(byTestId(tree1, "stat-cpu-value").props.children, "STALE");
    assert.equal(byTestId(tree1, "watchdog-service-status").props.children, "STALE");

    const current = { ...stale, sampled_at: "2026-01-31T23:59:55Z", status: "CURRENT", cpu: { celsius: 22.5, source: "LINUX_THERMAL" }, boot: { count: 2, tracking_since: "2026-01-20T00:00:00Z", status: "OBSERVED" } };
    const d2 = mkDevice(current);
    d2.id = "d-2";
    d2.name = "Other";
    const tree2 = StatusStrip({ device: d2, resetDay: 15 });
    assert.equal(byTestId(tree2, "stat-cpu-value").props.children, "22.5°C");
    assert.equal(byTestId(tree2, "stat-boots-value").props.children, "2");
  });
});

test("StatusStrip and WatchdogHealth testids stay unique and expose no health-control buttons", () => {
  const health = {
    sampled_at: "2026-01-31T23:59:50Z",
    status: "CURRENT",
    max_age_seconds: 120,
    cpu: { celsius: 42.1, source: "LINUX_THERMAL" },
    boot: { count: 1, tracking_since: "2026-01-01T00:00:00Z", status: "OBSERVED" },
    watchdog: { service_timeout_us: 0, service_state: "INACTIVE", hardware_state: "INACTIVE", hardware_timeout_seconds: 0, recovery: "NOT_VERIFIED" },
  };
  const tree = StatusStrip({ device: mkDevice(health), resetDay: 15 });
  const ids = collectTestIds(tree);
  const dupes = ids.filter((id, i) => ids.indexOf(id) !== i);
  assert.equal(dupes.length, 0, `duplicate data-testid entries: ${dupes.join(",")}`);

  let buttons = 0;
  walk(tree, (n) => {
    if (n?.type === "button") buttons += 1;
  });
  assert.equal(buttons, 0, "health UI must remain read-only");
});
