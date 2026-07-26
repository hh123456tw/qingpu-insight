(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.QingpuModelAdmin = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var MARKET_PAYLOADS = {
    resale: ["resale"],
    presale: ["presale"],
    all: ["resale", "presale"],
  };

  var STAGE_LABELS = {
    validating_data: "驗證訓練資料",
    training_resale: "正在訓練中古屋模型",
    evaluating_resale: "正在評估中古屋模型",
    training_presale: "正在訓練預售屋模型",
    evaluating_presale: "正在評估預售屋模型",
    writing_artifacts: "正在寫入模型成品",
  };

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function buildTrainingPayload(value) {
    if (!MARKET_PAYLOADS.hasOwnProperty(value)) {
      throw new Error("unknown market selection: " + value);
    }
    return { markets: MARKET_PAYLOADS[value] };
  }

  function derivePageState(statusPayload, activeRun) {
    var officialModels = (statusPayload && statusPayload.official_models) || {};
    var modelNames = Object.keys(officialModels);
    var hasOfficial = modelNames.length > 0;

    var officialLabel;
    if (!hasOfficial) {
      officialLabel = "無官方模型";
    } else {
      officialLabel = modelNames
        .map(function (key) {
          var m = officialModels[key];
          var name = m.name || key;
          var rec = "";
          if (m.recommended === true) rec = "（建議）";
          else if (m.recommended === false) rec = "（不建議）";
          return name + "（官方）" + rec;
        })
        .join("；");
    }

    var stageLabel = null;
    var canSubmit = true;
    var candidateNotice = "";
    if (activeRun && activeRun.status === "running") {
      canSubmit = false;
      candidateNotice = "訓練進行中，無法提交新任務。";
      var summary = activeRun.summary;
      if (summary && summary.stage) {
        stageLabel = STAGE_LABELS[summary.stage] || "正在處理模型工作";
      } else {
        stageLabel = "正在處理模型工作";
      }
    }

    return {
      canSubmit: canSubmit,
      stageLabel: stageLabel,
      officialLabel: officialLabel,
      candidateNotice: candidateNotice,
    };
  }

  var REASON_LABELS = {
    data_quality: "資料品質不足",
    feature_missing: "特徵遺失",
    convergence_failure: "收斂失敗",
    validation_failed: "驗證未通過",
    candidate_rejected: "候選模型被拒絕",
    not_recommended: "未通過發布門檻",
    baseline_selected: "基準模型表現最佳，未產生可發布的新模型",
    overall_mae_not_improved: "整體 MAE 未改善至少 2%",
    station_regression: "A17–A19 有生活圈退步超過 10%",
    a18_not_improved: "A18 MAPE 未優於基準模型",
    backtest_insufficient: "三期回測中優於基準模型的期數不足",
    backtest_station_regression: "回測生活圈退步超過 10%",
    candidate_stale: "候選模型資料落後最新官方資料超過 180 天",
    artifact_missing: "模型檔案遺失",
    sha256_mismatch: "模型檔案驗證失敗",
    corrupt_artifact: "模型檔案無法讀取",
    market_mismatch: "模型市場類型不符",
    other: "其他原因",
  };

  function reasonLabel(code) {
    return REASON_LABELS[code] || code;
  }

  function formatDatetime(iso) {
    if (!iso) return "—";
    try {
      var d = new Date(iso);
      return d.toLocaleString("zh-TW", { timeZone: "Asia/Taipei" });
    } catch (_) {
      return iso;
    }
  }

  function marketLabel(market) {
    var labels = {
      resale: "中古屋",
      presale: "預售屋",
    };
    return labels[market] || market || "—";
  }

  function statusLabel(status) {
    var labels = {
      pending: "排隊中",
      running: "執行中",
      succeeded: "成功",
      failed: "失敗",
      interrupted: "已中斷",
      retry_wait: "等待重試",
    };
    return labels[status] || status || "—";
  }

  function buildReleasePreviewPayload(action, market, id) {
    if (action === "publish") {
      return { action: action, market: market, run_id: id };
    }
    return { action: action, market: market, version_id: id };
  }

  function canConfirmDangerousAction(typed, expected) {
    return typed === expected;
  }

  function submitTraining(marketValue) {
    var payload = buildTrainingPayload(marketValue);
    var token = csrfToken();
    return fetch("/api/admin/model-training-runs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Qingpu-CSRF": token,
      },
      body: JSON.stringify(payload),
    });
  }

  function modelComparisonRows(result) {
    var out = [];
    var metrics = (result && result.selection_metrics) || {};
    var names = Object.keys(metrics);
    for (var i = 0; i < names.length; i++) {
      var overall = metrics[names[i]] && metrics[names[i]].overall;
      out.push({
        name: names[i],
        mae: overall && overall.mae != null ? overall.mae : null,
        mape: overall && overall.mape != null ? overall.mape : null,
        selected: names[i] === result.selected_model,
      });
    }
    return out;
  }

  function stationRows(result) {
    var out = [];
    if (!result) return out;
    var finalMetrics = result.final_test_metrics || {};
    var baseline = finalMetrics.baseline || {};
    var candidate = finalMetrics[result.selected_model] || {};
    var stations = ["A17", "A18", "A19"];
    for (var i = 0; i < stations.length; i++) {
      var key = "station:" + stations[i];
      var candidateMape = candidate[key] && candidate[key].mape;
      var baselineMape = baseline[key] && baseline[key].mape;
      var comparison = null;
      if (candidateMape != null && baselineMape != null) {
        comparison = candidateMape < baselineMape
          ? "improved"
          : candidateMape > baselineMape ? "regressed" : "same";
      }
      out.push({
        station: stations[i],
        mape: candidateMape != null ? candidateMape : null,
        baselineMape: baselineMape != null ? baselineMape : null,
        comparison: comparison,
      });
    }
    return out;
  }

  function ablationRows(result) {
    return (result && result.feature_experiments) || [];
  }

  function backtestRows(result) {
    var rows = (result && result.backtests) || [];
    return rows.map(function (row) {
      var candidate = row.candidate_metrics || {};
      var baseline = row.baseline_metrics || {};
      return {
        cutoff_date: row.cutoff_date,
        passed: row.passed === true,
        stations_within_limit: row.stations_within_limit === true,
        candidate_mae: candidate.overall && candidate.overall.mae,
        baseline_mae: baseline.overall && baseline.overall.mae,
        a17_mape: candidate["station:A17"] && candidate["station:A17"].mape,
        a18_mape: candidate["station:A18"] && candidate["station:A18"].mape,
        a19_mape: candidate["station:A19"] && candidate["station:A19"].mape,
      };
    });
  }

  function releaseCheckRows(result) {
    var checks = (result && result.release_checks) || {};
    var names = Object.keys(checks);
    var out = [];
    for (var i = 0; i < names.length; i++) {
      out.push({ code: names[i], passed: checks[names[i]] });
    }
    return out;
  }

  return {
    MARKET_PAYLOADS: MARKET_PAYLOADS,
    STAGE_LABELS: STAGE_LABELS,
    REASON_LABELS: REASON_LABELS,
    reasonLabel: reasonLabel,
    buildTrainingPayload: buildTrainingPayload,
    derivePageState: derivePageState,
    formatDatetime: formatDatetime,
    marketLabel: marketLabel,
    statusLabel: statusLabel,
    buildReleasePreviewPayload: buildReleasePreviewPayload,
    canConfirmDangerousAction: canConfirmDangerousAction,
    submitTraining: submitTraining,
    csrfToken: csrfToken,
    modelComparisonRows: modelComparisonRows,
    stationRows: stationRows,
    ablationRows: ablationRows,
    backtestRows: backtestRows,
    releaseCheckRows: releaseCheckRows,
  };
});
