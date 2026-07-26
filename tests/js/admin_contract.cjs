"use strict";

const assert = require("node:assert/strict");
const admin = require("../../src/qingpu_insight/static/admin.js");

assert.deepEqual(admin.SECTIONS, [
  "overview", "data", "listings", "models",
  "llm", "backups", "jobs", "diagnostics",
]);
assert.equal(admin.normalizeSection("#models"), "models");
assert.equal(admin.normalizeSection("#unknown"), "overview");

process.stdout.write("admin contract passed\n");
