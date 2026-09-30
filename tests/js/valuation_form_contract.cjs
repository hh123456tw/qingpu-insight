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

// Distance to the MRT is only needed when no address is given.
assert.deepEqual(ui.locationFieldState(""), { distanceRequired: true });
assert.deepEqual(ui.locationFieldState("   "), { distanceRequired: true });
assert.deepEqual(ui.locationFieldState("中壢區青埔路二段289號"), { distanceRequired: false });

// The request carries a trimmed address and never an empty one.
assert.equal(ui.addressForPayload("  中壢區青埔路二段289號 "), "中壢區青埔路二段289號");
assert.equal(ui.addressForPayload("   "), null);
assert.equal(ui.addressForPayload(undefined), null);

const sameBuilding = ui.locationSummaryLines(
  { source: "address", precise: true, match_quality: "exact", station_code: "A18", station_distance_m: 332.4 },
  { price_anchor: "same_building" }
);
assert.deepEqual(sameBuilding, [
  "已依門牌地址精確定位：A18 生活圈，距捷運站約 332 公尺",
  "模型找到同棟建物過去的成交紀錄，並以此作為估價基準",
]);

const nearby = ui.locationSummaryLines(
  { source: "address", precise: true, match_quality: "nearest_number", station_code: "A17", station_distance_m: 800 },
  { price_anchor: "nearby_sales" }
);
assert.equal(nearby[0], "已依門牌地址定位（採用同路段號碼最接近的門牌）：A17 生活圈，距捷運站約 800 公尺");
assert.equal(nearby[1], "未找到足夠的同棟成交紀錄，以附近成交行情作為估價基準");

const coords = ui.locationSummaryLines(
  { source: "coordinates", precise: true, station_code: "A19", station_distance_m: 10 },
  { price_anchor: "station_baseline" }
);
assert.equal(coords[0], "已依座標定位：A19 生活圈，距捷運站約 10 公尺");
assert.equal(coords[1], "附近成交不足，以生活圈整體行情作為估價基準");

assert.deepEqual(ui.locationSummaryLines({ source: "form", precise: false }, {}), [
  "未提供門牌地址，僅依生活圈與距捷運距離估價，無法比對同棟成交紀錄",
]);
assert.deepEqual(
  ui.locationSummaryLines({ source: "form", precise: false, note: "門牌定位資料暫時無法使用" }, {}),
  ["門牌定位資料暫時無法使用"]
);
// Older records without a location block render nothing.
assert.deepEqual(ui.locationSummaryLines(undefined, undefined), []);

process.stdout.write("valuation form contract passed\n");
