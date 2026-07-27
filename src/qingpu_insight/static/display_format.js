(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.QingpuDisplayFormat = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function formatWanNumber(value) {
    var numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric <= 0) return null;
    return new Intl.NumberFormat("zh-TW", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 1,
    }).format(numeric / 10000);
  }

  function formatTotalWan(value) {
    var formatted = formatWanNumber(value);
    return formatted !== null ? formatted + " 萬" : "—";
  }

  function formatUnitWan(value) {
    var formatted = formatWanNumber(value);
    return formatted !== null ? formatted + " 萬／坪" : "—";
  }

  function localizeConfidence(value) {
    var map = { high: "高", medium: "中", low: "低" };
    return map[value] || "—";
  }

  function normalizeLegacyMoneyText(text) {
    return text.replace(
      /([\d,]+(?:\.\d+)?)\s*元(?:\s*[/／]\s*坪)?/g,
      function (match, amount) {
        var clean = amount.replace(/,/g, "");
        var numeric = Number(clean);
        if (!Number.isFinite(numeric) || numeric <= 0) return match;
        var wan = numeric / 10000;
        var formatted = new Intl.NumberFormat("zh-TW", {
          minimumFractionDigits: 0,
          maximumFractionDigits: 1,
        }).format(wan);
        if (/[/／]\s*坪/.test(match)) {
          return formatted + " 萬／坪";
        }
        return formatted + " 萬";
      }
    );
  }

  function pricePositionState(low, point, high, asking) {
    var pLow = Number(low);
    var pPoint = Number(point);
    var pHigh = Number(high);
    var pAsking = Number(asking);

    if (!Number.isFinite(pPoint) || pPoint <= 0) {
      return { pointPercent: 50, askingPercent: 50, askingPosition: "missing" };
    }

    var validLow = Number.isFinite(pLow) && pLow > 0;
    var validHigh = Number.isFinite(pHigh) && pHigh > 0;
    var validAsking = Number.isFinite(pAsking) && pAsking > 0;

    var interval = (validHigh ? pHigh : pPoint * 2) - (validLow ? pLow : 0);
    if (interval <= 0) {
      return { pointPercent: 50, askingPercent: 50, askingPosition: "missing" };
    }

    var pointPercent = ((pPoint - (validLow ? pLow : 0)) / interval) * 100;
    pointPercent = Math.round(pointPercent);

    var askingState;
    var askingPercent;

    if (!validAsking) {
      askingState = "missing";
      askingPercent = pointPercent;
    } else if (pAsking < (validLow ? pLow : 0)) {
      askingState = "below";
      askingPercent = 0;
    } else if (pAsking > (validHigh ? pHigh : pPoint * 2)) {
      askingState = "above";
      askingPercent = 100;
    } else {
      askingState = "inside";
      askingPercent = ((pAsking - (validLow ? pLow : 0)) / interval) * 100;
      askingPercent = Math.round(askingPercent * 100) / 100;
    }

    return {
      pointPercent: pointPercent,
      askingPercent: askingPercent,
      askingPosition: askingState,
    };
  }

  return {
    formatTotalWan: formatTotalWan,
    formatUnitWan: formatUnitWan,
    localizeConfidence: localizeConfidence,
    normalizeLegacyMoneyText: normalizeLegacyMoneyText,
    pricePositionState: pricePositionState,
  };
});
