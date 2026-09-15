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

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

function createHarness(_ignored, api) {
  let BillMonthEditor = null;
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
    useRef(initial) {
      if (!active) throw new Error("Hook called without active instance");
      const i = active.hook_i++;
      if (!(i in active.refs)) active.refs[i] = { current: initial };
      return active.refs[i];
    },
    useEffect(effect, deps) {
      if (!active) throw new Error("Hook called without active instance");
      const i = active.hook_i++;
      const prev = active.deps[i];
      const changed = !prev || !deps || deps.length !== prev.length || deps.some((v, idx) => v !== prev[idx]);
      if (changed) {
        active.effects[i] = effect;
        active.deps[i] = deps;
      }
    },
  };

  function mount(props) {
    const inst = {
      hook_i: 0,
      state: [],
      refs: [],
      deps: [],
      effects: [],
      cleanups: [],
      tree: null,
      render() {
        if (!BillMonthEditor) throw new Error("Component not loaded");
        this.hook_i = 0;
        active = this;
        this.tree = BillMonthEditor(props);
        active = null;
        return this.tree;
      },
      async flushEffects() {
        for (let i = 0; i < this.effects.length; i++) {
          const fx = this.effects[i];
          if (!fx) continue;
          this.effects[i] = null;
          active = this;
          const cleanup = fx();
          active = null;
          if (typeof cleanup === "function") this.cleanups[i] = cleanup;
        }
      },
      destroy() {
        this.cleanups.forEach((fn) => fn && fn());
      },
    };
    return inst;
  }

  function loadComponent() {
    const tsxPath = path.join(__dirname, "..", "frontend", "src", "components", "ops", "energy", "BillMonthEditor.tsx");
    const source = fs.readFileSync(tsxPath, "utf8");
    const out = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
      fileName: "BillMonthEditor.tsx",
    }).outputText;

    const module = { exports: {} };
    const runtime = makeJsxRuntime();
    const mocks = {
      react: React,
      "react/jsx-runtime": runtime,
      "@/lib/api": { __esModule: true, default: api },
      "../DashboardHeader": { btn: "btn", input: "input", tone: { cyan: "cyan" } },
      "../types": { errorText: (e) => ({ detail: (e && e.message) || "err" }) },
      "./types": {},
      "./referenceTypes": { dailyRate: (v) => String(v ?? "UNAVAILABLE") },
    };
    const req = (id) => {
      if (id in mocks) return mocks[id];
      throw new Error(`Unexpected import: ${id}`);
    };

    vm.runInNewContext(out, { module, exports: module.exports, require: req, URLSearchParams, AbortController, console, setTimeout, clearTimeout }, { filename: "BillMonthEditor.compiled.cjs" });
    BillMonthEditor = module.exports.BillMonthEditor;
    return BillMonthEditor;
  }

  return { mount, loadComponent };
}

function makeStore(initialByMonth) {
  return new Map(Object.entries(initialByMonth));
}

function historyFromStore(store) {
  const entries = Array.from(store.entries()).sort(([a], [b]) => a.localeCompare(b));
  return {
    device_id: "dev-1",
    wing: "A",
    end_month: "2026-08",
    reference_daily_kwh: 5,
    valid_months: entries.filter(([, v]) => v !== null).length,
    months: entries.map(([month, consumption_kwh]) => ({
      month,
      month_name: month,
      days: month.endsWith("-02") ? 28 : 31,
      consumption_kwh,
      daily_kwh: null,
      source: consumption_kwh == null ? "UNAVAILABLE" : "HISTORICAL",
      note: null,
      created_by: null,
      updated_by: null,
      updated_at: null,
    })),
  };
}

test("BillMonthEditor stale editor submits only changed month; preserves newer sibling updates", async () => {
  const store = makeStore({ "2026-01": 100, "2026-02": 200 });
  const puts = [];
  const api = {
    get: async () => ({ data: historyFromStore(store) }),
    put: async (_url, body) => {
      puts.push(body);
      for (const m of body.months) store.set(m.month, m.consumption_kwh);
      return { data: historyFromStore(store) };
    },
  };

  const harness = createHarness(null, api);
  harness.loadComponent();

  const common = { societyId: "soc-1", deviceId: "dev-1", wing: "A", endMonth: "2026-08", readOnly: false, onSaved: () => {} };
  const editorA = harness.mount(common);
  const editorB = harness.mount(common);

  editorA.render();
  editorB.render();
  await editorA.flushEffects();
  await editorB.flushEffects();
  await tick();
  let treeA = editorA.render();
  let treeB = editorB.render();

  byTestId(treeB, "bills-input-2026-02").props.onChange({ target: { value: "300" } });
  treeB = editorB.render();
  await byTestId(treeB, "bills-month-form").props.onSubmit({ preventDefault() {} });

  byTestId(treeA, "bills-input-2026-01").props.onChange({ target: { value: "150" } });
  treeA = editorA.render();
  await byTestId(treeA, "bills-month-form").props.onSubmit({ preventDefault() {} });

  assert.equal(puts.length, 2);
  assert.deepEqual(JSON.parse(JSON.stringify(puts[0].months)), [{ month: "2026-02", consumption_kwh: 300 }]);
  assert.deepEqual(JSON.parse(JSON.stringify(puts[1].months)), [{ month: "2026-01", consumption_kwh: 150 }]);
  assert.equal(puts[1].society_id, "soc-1");
  assert.equal(puts[1].device_id, "dev-1");
  assert.equal(puts[1].wing, "A");
  assert.equal(puts[1].end_month, "2026-08");
  assert.deepEqual(Object.fromEntries(store), { "2026-01": 150, "2026-02": 300 });

  editorA.destroy();
  editorB.destroy();
});

test("BillMonthEditor month filtering semantics and submit guard", async () => {
  const store = makeStore({ "2026-01": 1, "2026-02": 0, "2026-03": 5, "2026-04": null, "2026-05": 2, "2026-06": null, "2026-07": null });
  const puts = [];
  let releasePut;
  const gate = new Promise((resolve) => {
    releasePut = resolve;
  });
  const api = {
    get: async () => ({ data: historyFromStore(store) }),
    put: async (_url, body) => {
      puts.push(body);
      await gate;
      for (const m of body.months) store.set(m.month, m.consumption_kwh);
      return { data: historyFromStore(store) };
    },
  };

  const harness = createHarness(null, api);
  harness.loadComponent();
  const editor = harness.mount({ societyId: "soc-1", deviceId: "dev-1", wing: "A", endMonth: "2026-08", readOnly: false, onSaved: () => {} });

  editor.render();
  await editor.flushEffects();
  await tick();
  let tree = editor.render();

  byTestId(tree, "bills-input-2026-01").props.onChange({ target: { value: "9" } });
  tree = editor.render();
  byTestId(tree, "bills-input-2026-01").props.onChange({ target: { value: "1.0" } });
  byTestId(tree, "bills-input-2026-02").props.onChange({ target: { value: "0" } });
  byTestId(tree, "bills-input-2026-03").props.onChange({ target: { value: "" } });
  byTestId(tree, "bills-input-2026-04").props.onChange({ target: { value: "0" } });
  byTestId(tree, "bills-input-2026-05").props.onChange({ target: { value: "0" } });
  byTestId(tree, "bills-input-2026-07").props.onChange({ target: { value: "12" } });
  tree = editor.render();

  const form = byTestId(tree, "bills-month-form");
  const firstSubmit = form.props.onSubmit({ preventDefault() {} });
  const secondSubmit = form.props.onSubmit({ preventDefault() {} });
  await secondSubmit;

  assert.equal(puts.length, 1, "submitting guard should block duplicate call before first resolves");
  assert.deepEqual(JSON.parse(JSON.stringify(puts[0].months)), [
    { month: "2026-04", consumption_kwh: 0 },
    { month: "2026-05", consumption_kwh: 0 },
    { month: "2026-07", consumption_kwh: 12 },
  ]);

  releasePut();
  await firstSubmit;
  assert.deepEqual(Object.fromEntries(store), {
    "2026-01": 1,
    "2026-02": 0,
    "2026-03": 5,
    "2026-04": 0,
    "2026-05": 0,
    "2026-06": null,
    "2026-07": 12,
  });

  editor.destroy();
});
