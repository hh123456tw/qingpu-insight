"use strict";

const assert = require("node:assert/strict");
const ui = require("../../src/qingpu_insight/static/valuation_form.js");

assert.equal(
  ui.firstErrorControlId(
    { total_floors: "must be >= floor", building_area_ping: "required" },
    {
      building_area_ping: "valuation-area",
      total_floors: "valuation-total-floors",
    }
  ),
  "valuation-total-floors"
);
assert.equal(ui.firstErrorControlId({}, {}), null);

process.stdout.write("valuation form contract passed\n");
