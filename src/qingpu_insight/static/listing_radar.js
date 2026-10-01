(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.QingpuListingRadar = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var STATIONS = ["A17", "A18", "A19"];
  var SORTS = ["score", "gap", "asking"];
  var VERDICTS = ["clear_below", "below_estimate", "needs_check"];
  var VERDICT_LABELS = {
    clear_below: "明顯低於區間",
    below_estimate: "低於估值",
    needs_check: "需人工確認",
  };
  var STATION_NAMES = { A17: "A17 領航站", A18: "A18 高鐵桃園站", A19: "A19 桃園體育園區站" };
  var ANCHOR_LABELS = {
    same_building: "同棟成交錨定",
    nearby_sales: "鄰近成交錨定",
    station_baseline: "站點基準",
  };
  var COMMON_AREA_LABELS = {
    "591_areas": "主建物／附屬／共用換算",
    "591_listed": "591 標示公設比",
  };
  var FLAG_LABELS = {
    new_project: "新成屋（屋齡未滿 2 年）",
    presale_anchor: "依同棟預售／完工前成交推估",
    fallback_valuation: "降級估價",
    common_area_unused: "未使用公設比",
    parking_unverified: "車位坪數無法確認",
    wide_interval: "估價區間偏寬",
    price_cut: "近期降價",
    implausible_gap: "比估值低 35% 以上（常見於坪數含車位、持分或資料錯誤）",
    community_mismatch: "座標與同社區其他刊登相距過遠",
    long_on_market: "刊登超過 180 天",
    multiple_agents: "多家仲介同時刊登",
  };
  var CONFIDENCE_LABELS = { high: "高", medium: "中", low: "低" };

  function buildRadarQuery(options) {
    var params = [];
    var station = options && options.station;
    if (station && STATIONS.indexOf(station) !== -1) params.push("station=" + station);
    var sort = options && options.sort;
    params.push("sort=" + (SORTS.indexOf(sort) !== -1 ? sort : "score"));
    var verdict = options && options.verdict;
    if (verdict && VERDICTS.indexOf(verdict) !== -1) params.push("verdict=" + verdict);
    var limit = Number(options && options.limit);
    params.push("limit=" + (Number.isInteger(limit) && limit >= 1 && limit <= 100 ? limit : 50));
    return "/api/listing-radar?" + params.join("&");
  }

  function safeListingUrl(url) {
    if (typeof url !== "string") return null;
    return /^https:\/\/sale\.591\.com\.tw\/home\/house\/detail\/\d+\/[0-9A-Za-z_-]+\.html$/.test(url)
      ? url
      : null;
  }

  function formatGap(gap) {
    var number = Number(gap);
    if (gap === null || gap === undefined || !Number.isFinite(number)) return "—";
    var percent = (number * 100).toFixed(1);
    return (number > 0 ? "+" : number < 0 ? "−" : "") + percent.replace("-", "") + "%";
  }

  function wan(value) {
    var number = Number(value);
    if (value === null || value === undefined || !Number.isFinite(number) || number <= 0) {
      return "—";
    }
    return new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 }).format(number / 10000) +
      " 萬";
  }

  function number(value, suffix) {
    var parsed = Number(value);
    if (value === null || value === undefined || !Number.isFinite(parsed)) return "—";
    return String(Math.round(parsed * 10) / 10) + suffix;
  }

  function flagLabels(flags) {
    if (!Array.isArray(flags)) return [];
    return flags
      .map(function (flag) { return FLAG_LABELS[flag]; })
      .filter(function (label) { return Boolean(label); });
  }

  function priceCut(item) {
    var original = Number(item.original_price_twd);
    var percent = Number(item.down_price_percent);
    if (!item.original_price_twd || !Number.isFinite(original) || !Number.isFinite(percent) ||
        item.down_price_percent === null || item.down_price_percent === undefined) {
      return "—";
    }
    return "原 " + wan(original) + "（−" + String(Math.round(percent * 10) / 10) + "%）";
  }

  function agentsText(item) {
    if (item.duplicate_listings === null || item.duplicate_listings === undefined) return "—";
    var duplicates = Number(item.duplicate_listings);
    if (!Number.isFinite(duplicates) || duplicates <= 0) return "1 家";
    var low = Number(item.property_min_price_twd);
    var high = Number(item.property_max_price_twd);
    var range = Number.isFinite(low) && Number.isFinite(high) && high > low
      ? "，開價 " + wan(low) + "–" + wan(high)
      : "";
    return String(duplicates + 1) + " 家" + range;
  }

  function cardModel(item) {
    return {
      verdict: VERDICT_LABELS[item.verdict] ? item.verdict : null,
      verdictLabel: VERDICT_LABELS[item.verdict] || "",
      rank: "#" + (item.rank || "—"),
      station: STATION_NAMES[item.station_code] || "—",
      gap: formatGap(item.gap_pct),
      below: item.below_interval === true,
      title: typeof item.title === "string" && item.title ? item.title : "591 物件",
      url: safeListingUrl(item.url),
      facts: [
        ["社區", typeof item.community_name === "string" && item.community_name
          ? item.community_name : "—"],
        ["開價", wan(item.asking_price_twd)],
        ["降價", priceCut(item)],
        ["模型估值", wan(item.estimate_twd)],
        ["90% 區間", wan(item.interval_low_twd) + "–" + wan(item.interval_high_twd)],
        ["坪數", number(item.area_ping, " 坪") +
          (item.net_area_ping ? "（扣車位 " + number(item.net_area_ping, " 坪") + "）" : "")],
        ["屋齡／樓層", number(item.age_years, " 年") + "／" + (item.floor || "—")],
        ["格局", item.layout || "—"],
        ["估價錨點", ANCHOR_LABELS[item.price_anchor] || "無錨點資訊"],
        ["公設比", COMMON_AREA_LABELS[item.common_area_source] || "未使用"],
        ["信心度", CONFIDENCE_LABELS[item.confidence] || "—"],
        ["刊登天數", number(item.days_on_market, " 天")],
        ["刊登仲介", agentsText(item)],
      ],
      reason: typeof item.reason === "string" ? item.reason : "",
      flags: flagLabels(item.flags),
    };
  }

  function el(doc, tag, className, text) {
    var node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderCard(doc, item) {
    var model = cardModel(item);
    var card = el(doc, "article", "radar-card");
    var head = el(doc, "div", "radar-card-head");
    head.appendChild(el(doc, "span", "radar-rank", model.rank));
    head.appendChild(el(doc, "span", "radar-station", model.station));
    if (model.verdict) {
      head.appendChild(el(doc, "span", "radar-verdict " + model.verdict, model.verdictLabel));
    }
    head.appendChild(el(doc, "span", "radar-gap" + (model.below ? " below" : ""), model.gap));
    card.appendChild(head);

    var title;
    if (model.url) {
      title = el(doc, "a", "radar-title", model.title);
      title.href = model.url;
      title.target = "_blank";
      title.rel = "noopener noreferrer";
    } else {
      title = el(doc, "span", "radar-title", model.title);
    }
    card.appendChild(title);

    var facts = el(doc, "dl", "radar-facts");
    model.facts.forEach(function (pair) {
      facts.appendChild(el(doc, "dt", "", pair[0]));
      facts.appendChild(el(doc, "dd", "", pair[1]));
    });
    card.appendChild(facts);
    card.appendChild(el(doc, "p", "radar-reason", model.reason));
    if (model.flags.length) {
      var flags = el(doc, "ul", "radar-flags");
      model.flags.forEach(function (label) { flags.appendChild(el(doc, "li", "", label)); });
      card.appendChild(flags);
    }
    return card;
  }

  function formatTaipeiTime(value) {
    var date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("zh-TW", {
      timeZone: "Asia/Taipei",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date).replace(/s+/g, " "); // ICU builds differ in the space they emit
  }

  function statusText(body) {
    if (!body || !body.batch) {
      return "尚未有雷達結果。管理者可在管理中心「刊登」執行低估物件雷達。";
    }
    var batch = body.batch;
    var counts = batch.counts || {};
    var stopped = batch.status === "stopped_verification"
      ? "（遇到 591 驗證頁而提早停止，結果不完整）"
      : batch.status === "stopped_failures" ? "（多頁擷取失敗而提早停止，結果不完整）" : "";
    var items = Array.isArray(body.items) ? body.items.length : 0;
    var prescreen = counts.prescreened
      ? "初篩 " + counts.prescreened + " 戶" +
        (counts.duplicate ? "（另有 " + counts.duplicate + " 筆是同物件其他仲介的刊登）" : "") +
        "後，"
      : "";
    return "更新於 " + formatTaipeiTime(batch.finished_at) + stopped + "：" + prescreen +
      "開詳細頁精算 " +
      (counts.valued || 0) + " 筆，符合條件且開價低於估值 " + (counts.ranked || 0) +
      " 筆，其中明顯低於區間 " + (counts.below_interval || 0) + " 筆" +
      (counts.needs_check ? "、低很多但需人工確認 " + counts.needs_check + " 筆" : "") +
      "；目前顯示 " + items + " 筆。";
  }

  function percent(value) {
    var parsed = Number(value);
    if (value === null || value === undefined || !Number.isFinite(parsed)) return "—";
    return String(Math.round(parsed * 1000) / 10) + "%";
  }

  function marketFacts(market) {
    if (!market) return [];
    var facts = [];
    var heat = market.market_heat;
    if (heat) {
      facts.push(["在售戶數", (heat.properties || 0) + " 戶（" + (heat.listings || 0) +
        " 筆刊登）"]);
      facts.push(["刊登天數中位", number(heat.median_days_on_market, " 天")]);
      facts.push(["有降價紀錄", percent(heat.price_cut_share)]);
      facts.push(["已累積", (heat.days_observed || 0) + " 天資料"]);
    }
    var index = Array.isArray(market.asking_index)
      ? market.asking_index.filter(function (row) { return row.station === "all"; })[0]
      : null;
    if (index) {
      facts.push(["開價／模型估值", "中位 " + Number(index.median_ratio).toFixed(3) +
        "（" + index.properties + " 戶）"]);
    }
    var drift = market.asking_drift;
    if (drift) {
      facts.push(["開價變化", (drift.pct_change >= 0 ? "+" : "−") +
        percent(Math.abs(drift.pct_change)) + "（" + drift.days + " 天，同一模型）"]);
    }
    var negotiation = market.negotiation;
    if (negotiation) {
      facts.push(["議價空間", negotiation.usable
        ? "成交約為最後開價的 " + percent(negotiation.median_deal_to_asking) +
          "（" + negotiation.matched + " 戶）"
        : "資料累積中（已對到 " + (negotiation.matched || 0) + "／" +
          (negotiation.min_matches || 30) + " 戶）"]);
    }
    return facts;
  }

  function renderMarket(doc, container, market) {
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);
    var facts = marketFacts(market);
    container.hidden = !facts.length;
    if (!facts.length) return;
    container.appendChild(el(doc, "h2", "", "市場概況"));
    var list = el(doc, "dl", "radar-facts");
    facts.forEach(function (pair) {
      list.appendChild(el(doc, "dt", "", pair[0]));
      list.appendChild(el(doc, "dd", "", pair[1]));
    });
    container.appendChild(list);
  }

  function renderRadar(doc, container, statusNode, body) {
    while (container.firstChild) container.removeChild(container.firstChild);
    statusNode.textContent = statusText(body);
    statusNode.className = "radar-status";
    var items = body && Array.isArray(body.items) ? body.items : [];
    items.forEach(function (item) { container.appendChild(renderCard(doc, item)); });
    if (body && body.batch && !items.length) {
      container.appendChild(el(doc, "p", "radar-empty", "這個條件下沒有符合品質門檻的候選物件。"));
    }
  }

  function init(doc, fetchImpl) {
    var app = doc.getElementById("radar-app");
    if (!app) return;
    var station = doc.getElementById("radar-station");
    var sort = doc.getElementById("radar-sort");
    var verdict = doc.getElementById("radar-verdict");
    var market = doc.getElementById("radar-market");
    var list = doc.getElementById("radar-list");
    var status = doc.getElementById("radar-status");

    function load() {
      status.textContent = "載入中…";
      return fetchImpl(buildRadarQuery({
        station: station.value,
        sort: sort.value,
        verdict: verdict ? verdict.value : "",
        limit: 50,
      }))
        .then(function (response) {
          return response.json().then(function (body) {
            if (!response.ok) {
              var message = body && body.error && body.error.message;
              throw new Error(message || "雷達資料暫時無法取得");
            }
            return body;
          });
        })
        .then(function (body) {
          renderRadar(doc, list, status, body);
          renderMarket(doc, market, body && body.market);
        })
        .catch(function (error) {
          status.textContent = error.message || "雷達資料暫時無法取得";
          status.className = "radar-status error";
        });
    }

    station.addEventListener("change", load);
    sort.addEventListener("change", load);
    if (verdict) verdict.addEventListener("change", load);
    return load();
  }

  if (typeof document !== "undefined" && typeof fetch === "function") {
    document.addEventListener("DOMContentLoaded", function () {
      init(document, function (url) { return fetch(url, { credentials: "same-origin" }); });
    });
  }

  return {
    ANCHOR_LABELS: ANCHOR_LABELS,
    FLAG_LABELS: FLAG_LABELS,
    VERDICT_LABELS: VERDICT_LABELS,
    marketFacts: marketFacts,
    renderMarket: renderMarket,
    buildRadarQuery: buildRadarQuery,
    safeListingUrl: safeListingUrl,
    formatGap: formatGap,
    cardModel: cardModel,
    renderCard: renderCard,
    renderRadar: renderRadar,
    statusText: statusText,
    priceCut: priceCut,
    formatTaipeiTime: formatTaipeiTime,
    init: init,
  };
});
