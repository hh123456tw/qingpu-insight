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

  // 公設比 is net of parking, matching the model: 1 − (主建物 + 附屬建物 + 陽台) ÷ 房屋坪數（不含車位）.
  var MAX_COMMON_AREA_RATIO = 0.70;
  var COMMON_AREA_RATIO_TOLERANCE = 0.01;

  function optionalNumber(value) {
    var text = value === undefined || value === null ? "" : String(value).trim();
    if (!text) return null;
    var number = Number(text);
    return isFinite(number) ? number : NaN;
  }

  function percentText(ratio) {
    return (ratio * 100).toFixed(1) + "%";
  }

  function invalidCommonArea(field, message) {
    return { valid: false, field: field, message: message, ratio: null, payload: {}, summary: message };
  }

  function commonAreaState(values) {
    var main = optionalNumber(values.main);
    var auxiliary = optionalNumber(values.auxiliary);
    var balcony = optionalNumber(values.balcony);
    var percent = optionalNumber(values.ratioPercent);
    var buildingArea = optionalNumber(values.buildingArea);
    var payload = {};
    var ratio = null;
    var summary = "未填寫時，模型以訓練資料的中位數代入，估價區間會較寬。";

    var parts = [["main", main], ["auxiliary", auxiliary], ["balcony", balcony]];
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i][1];
      if (part !== null && !(part >= 0 && part <= 200)) {
        return invalidCommonArea(parts[i][0], "權狀面積必須是 0～200 之間的坪數");
      }
    }
    if (percent !== null && !(percent >= 0 && percent <= MAX_COMMON_AREA_RATIO * 100)) {
      return invalidCommonArea("ratio", "公設比（不含車位）必須介於 0%～70%");
    }

    var anyArea = main !== null || auxiliary !== null || balcony !== null;
    if (anyArea) {
      if (main === null || main <= 0) {
        return invalidCommonArea("main", "填寫附屬建物或陽台坪數時，請一併填寫主建物坪數");
      }
      payload.main_building_area_ping = main;
      if (auxiliary !== null) payload.auxiliary_building_area_ping = auxiliary;
      if (balcony !== null) payload.balcony_area_ping = balcony;
      if (buildingArea !== null && buildingArea > 0) {
        ratio = 1 - (main + (auxiliary || 0) + (balcony || 0)) / buildingArea;
        if (!(ratio >= 0 && ratio <= MAX_COMMON_AREA_RATIO)) {
          return invalidCommonArea(
            "main",
            "主建物＋附屬建物＋陽台換算出的公設比不在 0%～70% 之間，請確認權狀面積與房屋坪數（不含車位）"
          );
        }
        summary = "換算公設比（不含車位）：" + percentText(ratio);
      } else {
        summary = "請先填寫房屋坪數（不含車位），才能換算公設比";
      }
    }
    if (percent !== null) {
      var stated = percent / 100;
      if (ratio !== null && Math.abs(stated - ratio) > COMMON_AREA_RATIO_TOLERANCE) {
        return invalidCommonArea(
          "ratio",
          "填寫的公設比與權狀面積換算結果（約 " + percentText(ratio) + "）不一致，請擇一填寫或確認數字"
        );
      }
      payload.common_area_ratio = stated;
      if (!anyArea) {
        ratio = stated;
        summary = "將採用公設比（不含車位）" + percentText(stated);
      }
    }
    return { valid: true, field: null, message: "", ratio: ratio, payload: payload, summary: summary };
  }

  function commonAreaSummaryLine(commonArea, degraded) {
    if (!commonArea) return null;
    if (!commonArea.provided) return "未提供公設比，模型以訓練資料中位數代入，估價區間較寬";
    if (degraded) return "本次使用降級模型，未使用公設比";
    if (commonArea.source === "areas") {
      return "公設比（不含車位）約 " + percentText(commonArea.ratio) + "，由權狀面積換算並納入估價";
    }
    return "公設比（不含車位）" + percentText(commonArea.ratio) + "，依填寫數字納入估價";
  }

  return {
    addressForPayload: addressForPayload,
    commonAreaState: commonAreaState,
    commonAreaSummaryLine: commonAreaSummaryLine,
    firstErrorControlId: firstErrorControlId,
    locationFieldState: locationFieldState,
    locationSummaryLines: locationSummaryLines,
    parkingState: parkingState,
  };
});
