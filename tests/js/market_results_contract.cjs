"use strict";

const assert = require("node:assert/strict");
const ui = require("../../src/qingpu_insight/static/market_results.js");
const rows = Array.from({ length: 100 }, (_, index) => ({ id: index }));

assert.equal(ui.visibleRecent(rows, false).length, 8);
assert.equal(ui.visibleRecent(rows, true).length, 100);
assert.equal(ui.recentToggleLabel(100, false), "顯示更多成交（100）");
assert.equal(ui.recentToggleLabel(100, true), "收合近期成交");
assert.deepEqual(
  ui.filterSummary({
    transactionTypeLabel: "中古屋",
    stations: ["A17", "A18", "A19"],
    areaMin: "",
    areaMax: "",
  }),
  ["中古屋", "A17～A19", "全部坪數"]
);

async function testIndependentSections() {
  var successes = [];
  var failures = [];
  await Promise.all([
    ui.loadSection("/summary", async () => ({ ok: true, json: async () => ({ count: 1 }) }),
      (value) => successes.push(value), (error) => failures.push(error.message)),
    ui.loadSection("/trends", async () => ({ ok: false, status: 503 }),
      (value) => successes.push(value), (error) => failures.push(error.message)),
  ]);
  assert.deepEqual(successes, [{ count: 1 }]);
  assert.deepEqual(failures, ["request 503"]);
}

testIndependentSections().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
