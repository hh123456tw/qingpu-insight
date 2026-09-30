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

// 公設比 (net of parking): blank means "not provided" and sends nothing.
const blank = ui.commonAreaState({ main: "", auxiliary: "", balcony: "", ratioPercent: "", buildingArea: "30" });
assert.equal(blank.valid, true);
assert.equal(blank.ratio, null);
assert.deepEqual(blank.payload, {});
assert.match(blank.summary, /中位數/);

// Deed areas are converted with the training definition against 房屋坪數（不含車位）.
const areas = ui.commonAreaState({ main: "18.5", auxiliary: "0.5", balcony: "1", ratioPercent: "", buildingArea: "30" });
assert.equal(areas.valid, true);
assert.ok(Math.abs(areas.ratio - (1 - 20 / 30)) < 1e-9);
assert.deepEqual(areas.payload, {
  main_building_area_ping: 18.5, auxiliary_building_area_ping: 0.5, balcony_area_ping: 1,
});
assert.equal(areas.summary, "換算公設比（不含車位）：33.3%");

// Auxiliary and balcony may stay blank; they are simply not sent.
const mainOnly = ui.commonAreaState({ main: "20", auxiliary: "", balcony: "", ratioPercent: "", buildingArea: "30" });
assert.deepEqual(mainOnly.payload, { main_building_area_ping: 20 });

// Without the net building area the ratio cannot be shown yet, but the areas are still valid.
const noArea = ui.commonAreaState({ main: "20", auxiliary: "", balcony: "", ratioPercent: "", buildingArea: "" });
assert.equal(noArea.valid, true);
assert.equal(noArea.ratio, null);
assert.match(noArea.summary, /房屋坪數/);

// A direct percentage becomes a fraction.
const direct = ui.commonAreaState({ main: "", auxiliary: "", balcony: "", ratioPercent: "33", buildingArea: "30" });
assert.equal(direct.valid, true);
assert.deepEqual(direct.payload, { common_area_ratio: 0.33 });
assert.equal(direct.summary, "將採用公設比（不含車位）33.0%");

// Invalid combinations name the control to fix.
const missingMain = ui.commonAreaState({ main: "", auxiliary: "", balcony: "2", ratioPercent: "", buildingArea: "30" });
assert.equal(missingMain.valid, false);
assert.equal(missingMain.field, "main");
const tooLarge = ui.commonAreaState({ main: "31", auxiliary: "", balcony: "", ratioPercent: "", buildingArea: "30" });
assert.equal(tooLarge.valid, false);
assert.equal(tooLarge.field, "main");
const tooCommon = ui.commonAreaState({ main: "5", auxiliary: "", balcony: "", ratioPercent: "", buildingArea: "30" });
assert.equal(tooCommon.valid, false);
const badPercent = ui.commonAreaState({ main: "", auxiliary: "", balcony: "", ratioPercent: "75", buildingArea: "30" });
assert.equal(badPercent.valid, false);
assert.equal(badPercent.field, "ratio");
const negative = ui.commonAreaState({ main: "20", auxiliary: "-1", balcony: "", ratioPercent: "", buildingArea: "30" });
assert.equal(negative.field, "auxiliary");
const conflict = ui.commonAreaState({ main: "20", auxiliary: "", balcony: "", ratioPercent: "40", buildingArea: "30" });
assert.equal(conflict.valid, false);
assert.equal(conflict.field, "ratio");
const agree = ui.commonAreaState({ main: "20", auxiliary: "", balcony: "", ratioPercent: "33.5", buildingArea: "30" });
assert.equal(agree.valid, true);
assert.deepEqual(agree.payload, { main_building_area_ping: 20, common_area_ratio: 0.335 });

// Result page line about whether the 公設比 entered the valuation.
assert.equal(
  ui.commonAreaSummaryLine({ provided: true, source: "areas", ratio: 0.3333 }, false),
  "公設比（不含車位）約 33.3%，由權狀面積換算並納入估價"
);
assert.equal(
  ui.commonAreaSummaryLine({ provided: true, source: "ratio", ratio: 0.3 }, false),
  "公設比（不含車位）30.0%，依填寫數字納入估價"
);
assert.equal(
  ui.commonAreaSummaryLine({ provided: false, source: null, ratio: null }, false),
  "未提供公設比，模型以訓練資料中位數代入，估價區間較寬"
);
assert.equal(
  ui.commonAreaSummaryLine({ provided: true, source: "ratio", ratio: 0.3 }, true),
  "本次使用降級模型，未使用公設比"
);
// Older records without the block render nothing.
assert.equal(ui.commonAreaSummaryLine(undefined, false), null);

process.stdout.write("valuation form contract passed\n");
