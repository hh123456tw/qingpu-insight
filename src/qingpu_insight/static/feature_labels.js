(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.QingpuFeatureLabels = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // Traditional Chinese names for the model's feature columns
  // (qingpu_insight.model_features.FEATURE_COLUMNS and PARKING_FEATURE_COLUMNS).
  // Shared by the valuation result (主要影響因素) and the admin model report.
  var FEATURE_LABELS = {
    station_code: "生活圈",
    station_distance_m: "距離捷運站",
    building_area_ping: "建物坪數",
    building_type: "建物類型",
    bedrooms: "房數",
    living_rooms: "廳數",
    bathrooms: "衛浴數",
    building_age_years: "屋齡",
    floor: "樓層",
    total_floors: "總樓層",
    floor_ratio: "樓層高低（樓層／總樓層）",
    transaction_year: "交易年份",
    transaction_month: "交易月別",
    transaction_month_index: "交易月份",
    station_building_type: "生活圈 × 建物類型",
    building_age_band: "屋齡區間",
    area_band: "坪數區間",
    floor_band: "樓層區間",
    twd97_x: "位置（東西向）",
    twd97_y: "位置（南北向）",
    location_known: "是否有精確位置",
    common_area_ratio: "公設比",
    parking_type: "車位類型",
    parking_area_ping: "車位面積",
  };

  function featureLabel(name) {
    if (typeof name !== "string" || name === "") return "—";
    return Object.prototype.hasOwnProperty.call(FEATURE_LABELS, name)
      ? FEATURE_LABELS[name]
      : name;
  }

  return {
    FEATURE_LABELS: FEATURE_LABELS,
    featureLabel: featureLabel,
  };
});
