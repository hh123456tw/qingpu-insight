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

  function buildTrainingPayload(value, includeCustom, custom) {
    if (!MARKET_PAYLOADS.hasOwnProperty(value)) {
      throw new Error("unknown market selection: " + value);
    }
    var payload = { markets: MARKET_PAYLOADS[value] };
    if (typeof includeCustom === "boolean") {
      var tuning = {
        mode: "preset_comparison",
        include_custom: includeCustom,
      };
      if (includeCustom && custom) {
        tuning.custom = {
          hgb_learning_rate: parseFloat(custom.hgb_learning_rate),
          hgb_max_iter: parseInt(custom.hgb_max_iter, 10),
          rf_n_estimators: parseInt(custom.rf_n_estimators, 10),
          recency_half_life_months: parseInt(custom.recency_half_life_months, 10),
        };
      }
      payload.tuning = tuning;
    }
    return payload;
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

  function validateCustomTuning(market, custom) {
    var errors = {};
    var lr = parseFloat(custom.hgb_learning_rate);
    if (isNaN(lr) || lr < 0.01 || lr > 0.20) {
      errors.hgb_learning_rate = true;
    }
    var maxIter = parseInt(custom.hgb_max_iter, 10);
    if (isNaN(maxIter) || maxIter < 100 || maxIter > 1000) {
      errors.hgb_max_iter = true;
    }
    var nEst = parseInt(custom.rf_n_estimators, 10);
    if (isNaN(nEst) || nEst < 100 || nEst > 1000) {
      errors.rf_n_estimators = true;
    }
    if (market === "presale") {
      errors.recency_half_life_months = "not_applicable";
    } else {
      var halfLife = parseInt(custom.recency_half_life_months, 10);
      if (isNaN(halfLife) || halfLife < 12 || halfLife > 84) {
        errors.recency_half_life_months = true;
      }
    }
    return errors;
  }

  function trainingSubmitSummary(market, includeCustom) {
    var marketCount = market === "all" ? 2 : 1;
    var configCount = includeCustom ? 4 : 3;
    return marketCount + " 個市場 × " + configCount + " 組設定";
  }

  function metricCards(result) {
    var fmetrics = result.final_test_metrics;
    var locked = fmetrics.locked_winner;
    var profile = fmetrics.profiles[locked];
    return [
      { key: "mape", label: "MAPE", value: profile.mape + "%" },
      { key: "mae", label: "MAE", value: (profile.mae / 10000).toFixed(2) + " 萬元／坪" },
      { key: "coverage", label: "測試覆蓋率", value: (result.test_coverage * 100).toFixed(1) + "%" },
    ];
  }

  function profileComparisonRows(result) {
    return result.profile_results;
  }

  function trainingRecordState(manifest) {
    if (manifest.schema_version < 3) {
      return { legacy: true, notice: "舊版未保存調參快照" };
    }
    return { legacy: false, notice: null };
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
    validateCustomTuning: validateCustomTuning,
    trainingSubmitSummary: trainingSubmitSummary,
    metricCards: metricCards,
    profileComparisonRows: profileComparisonRows,
    trainingRecordState: trainingRecordState,
  };
});
