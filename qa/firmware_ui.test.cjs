"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const read = (rel) => fs.readFileSync(path.join(__dirname, "..", rel), "utf8");

test("firmware update is scheduled inside the maintenance window and has no immediate install action", () => {
  const src = read("frontend/src/components/ops/FirmwareUpdate.tsx");
  assert.match(src, /Firmware update available/);
  assert.match(src, /Latest firmware installed/);
  assert.match(src, /firmware-view/);
  assert.match(src, /firmware-notes/);
  assert.match(src, /min="02:00"/);
  assert.match(src, /max="03:59"/);
  assert.match(src, /\/firmware\/schedule/);
  assert.doesNotMatch(src, /Update Now/);
  assert.doesNotMatch(src, /Install Firmware/);
  assert.doesNotMatch(src, /firmware\/install/);
  assert.doesNotMatch(src, /Improved OFF ALL/);
});

test("dashboard mounts one firmware panel through the shared confirmation state", () => {
  const src = read("frontend/src/components/ops/OperationalDashboard.tsx");
  assert.match(src, /!readOnly && <FirmwareUpdate/);
  assert.match(src, /ask=\{setConfirm\}/);
  assert.equal((src.match(/<LcdMessagePanel/g) || []).length, 1);
});
