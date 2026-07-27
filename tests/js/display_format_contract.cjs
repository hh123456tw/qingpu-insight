"use strict";

const assert = require("node:assert/strict");
const display = require("../../src/qingpu_insight/static/display_format.js");

assert.equal(display.formatTotalWan(22980000), "2,298 萬");
assert.equal(display.formatTotalWan(15377250), "1,537.7 萬");
assert.equal(display.formatUnitWan(586700), "58.7 萬／坪");
assert.equal(display.formatTotalWan(null), "—");
assert.equal(display.localizeConfidence("low"), "低");
assert.equal(
  display.normalizeLegacyMoneyText("開價 22,980,000 元，單價 586,700 元/坪"),
  "開價 2,298 萬，單價 58.7 萬／坪"
);
assert.deepEqual(
  display.pricePositionState(15380000, 19890000, 24410000, 22980000),
  { pointPercent: 50, askingPercent: 84.16, askingPosition: "inside" }
);

process.stdout.write("display format contract passed\n");
