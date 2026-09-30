(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.QingpuValuationForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function firstErrorControlId(fields, fieldMap) {
    var keys = Object.keys(fields);
    if (!keys.length) return null;
    var firstKey = keys[0];
    return fieldMap[firstKey] || null;
  }

  function parkingState(parkingType, parkingArea) {
    var disabled = !parkingType;
    var normalizedArea = disabled ? 0 : parkingArea;
    var valid = disabled ? true : normalizedArea > 0;
    var message = valid ? "" : "車位面積必須大於 0";
    return { disabled: disabled, normalizedArea: normalizedArea, valid: valid, message: message };
  }

  function addressForPayload(value) {
    var text = typeof value === "string" ? value.trim() : "";
    return text ? text : null;
  }

  function locationFieldState(address) {
    return { distanceRequired: addressForPayload(address) === null };
  }

  var PRICE_ANCHOR_TEXT = {
    same_building: "模型找到同棟建物過去的成交紀錄，並以此作為估價基準",
    nearby_sales: "未找到足夠的同棟成交紀錄，以附近成交行情作為估價基準",
    station_baseline: "附近成交不足，以生活圈整體行情作為估價基準",
  };

  function locationSummaryLines(location, model) {
    if (!location) return [];
    if (!location.precise) {
      return [location.note || "未提供門牌地址，僅依生活圈與距捷運距離估價，無法比對同棟成交紀錄"];
    }
    var how = location.source === "coordinates"
      ? "已依座標定位"
      : (location.match_quality === "nearest_number"
        ? "已依門牌地址定位（採用同路段號碼最接近的門牌）"
        : "已依門牌地址精確定位");
    var lines = [how + "：" + location.station_code + " 生活圈，距捷運站約 "
      + Math.round(location.station_distance_m) + " 公尺"];
    var anchor = model && PRICE_ANCHOR_TEXT[model.price_anchor];
    if (anchor) lines.push(anchor);
    return lines;
  }

  return {
    addressForPayload: addressForPayload,
    firstErrorControlId: firstErrorControlId,
    locationFieldState: locationFieldState,
    locationSummaryLines: locationSummaryLines,
    parkingState: parkingState,
  };
});
