(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.QingpuMarketResults = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function filterSummary(state) {
    var chips = [];
    chips.push(state.transactionTypeLabel || "");
    if (state.stations && state.stations.length > 0) {
      var sorted = state.stations.slice().sort();
      chips.push(sorted[0] + "～" + sorted[sorted.length - 1]);
    } else {
      chips.push("全部生活圈");
    }
    var min = state.areaMin;
    var max = state.areaMax;
    if ((!min || min === "") && (!max || max === "")) {
      chips.push("全部坪數");
    } else if (min && min !== "" && (!max || max === "")) {
      chips.push(min + " 坪以上");
    } else if ((!min || min === "") && max && max !== "") {
      chips.push(max + " 坪以下");
    } else {
      chips.push(min + " ～ " + max + " 坪");
    }
    return chips;
  }

  function visibleRecent(items, expanded, collapsedLimit) {
    if (collapsedLimit === undefined) collapsedLimit = 8;
    if (expanded) return items;
    return items.slice(0, collapsedLimit);
  }

  function recentToggleLabel(total, expanded) {
    if (expanded) return "收合近期成交";
    return "顯示更多成交（" + total + "）";
  }

  function loadSection(url, fetchImpl, onSuccess, onError) {
    return fetchImpl(url).then(function (response) {
      if (!response.ok) {
        onError(new Error("request " + response.status));
        return;
      }
      return response.json().then(onSuccess, function (parseError) {
        onError(parseError);
      });
    }, function (networkError) {
      onError(networkError);
    });
  }

  return {
    filterSummary: filterSummary,
    visibleRecent: visibleRecent,
    recentToggleLabel: recentToggleLabel,
    loadSection: loadSection,
  };
});
