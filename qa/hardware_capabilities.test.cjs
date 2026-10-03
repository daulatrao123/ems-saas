"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("path");
const vm = require("node:vm");
const ts = require(path.join(__dirname, "..", "frontend", "node_modules", "typescript"));

function load(rel) {
  const full = path.join(__dirname, "..", rel);
  const out = ts.transpileModule(fs.readFileSync(full, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    fileName: path.basename(full),
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(out, {
    module, exports: module.exports, console, React: {},
    require(id) {
      if (id === "react" || id === "react/jsx-runtime") return { useEffect() {}, useState(v) { return [v, () => {}]; }, jsx: () => null, jsxs: () => null, Fragment: Symbol("f") };
      if (id === "@/lib/api") return { default: { get() { return Promise.reject(new Error("no network")); }, put() { return Promise.reject(new Error("no network")); } } };
      if (id.includes("DashboardHeader")) return { btn: "", tone: { cyan: "", gray: "" } };
      throw new Error(`Unexpected import: ${id}`);
    },
  }, { filename: "hardware.compiled.cjs" });
  return module.exports;
}

const view = load("frontend/src/components/provisioning/HardwareCapabilities.tsx");

test("hardware expectation labels distinguish installed, disabled, and absent", () => {
  assert.equal(view.expectationLabel(false), "NOT INSTALLED");
  assert.equal(view.expectationLabel(true, false), "INSTALLED / DISABLED");
  assert.equal(view.expectationLabel(true, true), "INSTALLED");
  assert.equal(view.expectationLabel(null), "NOT CONFIGURED");
  assert.equal(view.runtimeLabel("NOT_APPLICABLE"), "NOT AVAILABLE");
  assert.equal(view.runtimeLabel("FAULT"), "FAULT");
  assert.equal(view.runtimeLabel("OFFLINE"), "OFFLINE");
  assert.notEqual(view.runtimeLabel("NOT_APPLICABLE"), "FAULT");
});

test("hardware configuration UI does not expose GPIO pin editors", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "provisioning", "HardwareCapabilities.tsx"), "utf8");
  assert.doesNotMatch(src, /relay_gpio|detect_gpio|BCM|GPIO\s*1[0-9]/);
  assert.match(src, /Contactor Feedback/);
  assert.match(src, /Consumption Meter/);
  assert.match(src, /SAVE HARDWARE CONFIGURATION/);
});

test("provisioning has one hardware editor", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "provisioning", "ProvisioningCenter.tsx"), "utf8");
  assert.doesNotMatch(src, /prov-feedback-toggle/);
  assert.doesNotMatch(src, /feedback-hardware/);
  assert.match(src, /HardwareCapabilities/);
  assert.match(src, /Read-only/);
});

test("meter commissioning tells the operator to configure hardware first", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "frontend", "src", "components", "commissioning", "MeterForm.tsx"), "utf8");
  assert.match(src, /Meter is not marked as installed\. Super Admin must configure hardware capabilities first\./);
});
