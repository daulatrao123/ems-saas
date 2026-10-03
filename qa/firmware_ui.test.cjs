"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const read = (rel) => fs.readFileSync(path.join(__dirname, "..", rel), "utf8");

test("install firmware opens the existing confirmation dialog before any request", () => {
  const src = read("frontend/src/components/ops/FirmwareUpdate.tsx");
  assert.match(src, /Install firmware update\?/);
  assert.match(src, /severity: "DANGER"/);
  assert.match(src, /onConfirm: install/);
  assert.match(src, /api\.post\(`\/api\/admin\/devices\/\$\{deviceId\}\/firmware\/install`/);
  assert.doesNotMatch(src, /onClick=\{[^}]*api\.post/);
  assert.match(src, /from "\.\/ConfirmDialog"/);
  const dialog = read("frontend/src/components/ops/ConfirmDialog.tsx");
  assert.match(dialog, /cancelRef\.current\?\.focus\(\)/);
  assert.match(dialog, /requestCancel/);
});

test("dashboard mounts one firmware panel through the shared confirmation state", () => {
  const src = read("frontend/src/components/ops/OperationalDashboard.tsx");
  assert.match(src, /!readOnly && <FirmwareUpdate/);
  assert.match(src, /ask=\{setConfirm\}/);
  assert.equal((src.match(/<LcdMessagePanel/g) || []).length, 1);
});
