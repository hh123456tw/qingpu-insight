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

assert.deepEqual(ui.parkingState("", 8), {
  disabled: true, normalizedArea: 0, valid: true, message: "",
});
assert.equal(ui.parkingState("坡道平面", 0).valid, false);
assert.equal(ui.parkingState("坡道平面", 8).valid, true);

// Community filter contract
var communities = [
  { community_id: "c1", canonical_name: "青埔A18社區", station_code: "A18" },
  { community_id: "c2", canonical_name: "青埔A17社區", station_code: "A17" },
  { community_id: "c3", canonical_name: "另一A18社區", station_code: "A18" },
];
assert.deepEqual(ui.communitiesForStation(communities, "A18"), [
  { community_id: "c1", canonical_name: "青埔A18社區", station_code: "A18" },
  { community_id: "c3", canonical_name: "另一A18社區", station_code: "A18" },
]);
assert.deepEqual(ui.communitiesForStation(communities, "A19"), []);
assert.deepEqual(ui.communitiesForStation(null, "A18"), []);
assert.deepEqual(ui.communitiesForStation([], "A18"), []);

process.stdout.write("valuation form contract passed\n");
