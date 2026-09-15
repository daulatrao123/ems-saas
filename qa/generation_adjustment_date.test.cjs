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
      render() {
        if (!ManualGenerationEntry) throw new Error("Component not loaded");
        this.hook_i = 0;
        active = this;
        this.tree = ManualGenerationEntry(props);
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
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
      fileName: "ManualGenerationEntry.tsx",
    }).outputText;

    const module = { exports: {} };
    const mocks = {
      react: React,
      "react/jsx-runtime": makeJsxRuntime(),
      "../DashboardHeader": { btn: "btn", input: "input", label: "label", tone: { amber: "amber" } },
      "./types": {},
    };
    const req = (id) => {
      if (id in mocks) return mocks[id];
      throw new Error(`Unexpected import: ${id}`);
    };
    class FrozenUTCDate extends Date {
      constructor(...args) { args.length ? super(...args) : super("2026-12-01T00:01:00Z"); }
      static now() { return Date.parse("2026-12-01T00:01:00Z"); }
    }
    vm.runInNewContext(out, { module, exports: module.exports, require: req, console, Date: FrozenUTCDate }, { filename: "ManualGenerationEntry.compiled.cjs" });
    ManualGenerationEntry = module.exports.ManualGenerationEntry;
    return ManualGenerationEntry;
  }

  return { mount, loadComponent };
}

test("ManualGenerationEntry keeps explicit operating date and submits additive payload", async () => {
  const calls = [];
  const onAdd = async (entry) => {
    calls.push(entry);
    return true;
  };
  const harness = createManualGenerationHarness();
  harness.loadComponent();
  const inst = harness.mount({ wing: "A", operatingDate: "2026-11-30", disabled: false, onAdd });

  let tree = inst.render();
  assert.equal(byTestId(tree, "manual-entry-date-A").props.value, "2026-11-30", "Pi operating date, not frozen UTC December1, must initialize the form");
  byTestId(tree, "manual-entry-value-A").props.onChange({ target: { value: "3" } });
  byTestId(tree, "manual-entry-reason-A").props.onChange({ target: { value: "month boundary" } });
  tree = inst.render();
  await byTestId(tree, "manual-entry-form-A").props.onSubmit({ preventDefault() {} });

  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])), {
    wing: "A",
    operating_date: "2026-11-30",
    kind: "MANUAL_GENERATION",
    value_kwh: 3,
    reason: "month boundary",
  });
});

test("useEnergy forwards manual entry unchanged to adjustments API payload", () => {
  const useEnergyPath = path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "useEnergy.ts");
  const source = fs.readFileSync(useEnergyPath, "utf8");
  assert.match(source, /api\.post\("\/api\/energy\/adjustments",\s*\{\s*\.\.\.identity,\s*\.\.\.entry\s*\}\)/m);
});
