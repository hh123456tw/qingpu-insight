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

  var PROFILE_LABELS = {
    quick: "快速",
    balanced: "平衡",
    thorough: "精細",
    custom: "自訂",
  };

  var BUILD_LABELS = {
    quick: "快速探索（5 分鐘）",
    standard: "標準探索（15 分鐘）",
    deep: "深度探索（30 分鐘）",
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
        var customPayload = {
          hgb_learning_rate: Number(custom.hgb_learning_rate),
          hgb_max_iter: Number(custom.hgb_max_iter),
          rf_n_estimators: Number(custom.rf_n_estimators),
        };
        if (value !== "presale") {
          customPayload.recency_half_life_months = Number(
            custom.recency_half_life_months
          );
        }
        tuning.custom = customPayload;
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
    overall_mae_not_improved: "整體 MAE 未改善至少 2%",
    station_regression: "A17–A19 有生活圈退步超過 10%",
    a18_not_improved: "A18 MAPE 未優於基準模型",
    backtest_insufficient: "三期回測中優於基準模型的期數不足",
    backtest_station_regression: "三期回測有兩期以上的生活圈退步超過 10%",
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
      skipped: "已停止",
      needs_attention: "需要處理",
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
    var lr = Number(custom.hgb_learning_rate);
    if (!Number.isFinite(lr) || lr < 0.01 || lr > 0.20) {
      errors.hgb_learning_rate = true;
    }
    var maxIter = Number(custom.hgb_max_iter);
    if (!Number.isInteger(maxIter) || maxIter < 100 || maxIter > 1000) {
      errors.hgb_max_iter = true;
    }
    var nEst = Number(custom.rf_n_estimators);
    if (!Number.isInteger(nEst) || nEst < 100 || nEst > 1000) {
      errors.rf_n_estimators = true;
    }
    if (market === "presale") {
      if (
        custom.recency_half_life_months != null
        && custom.recency_half_life_months !== ""
      ) {
        errors.recency_half_life_months = "not_applicable";
      }
    } else {
      var halfLife = Number(custom.recency_half_life_months);
      if (!Number.isInteger(halfLife) || halfLife < 12 || halfLife > 84) {
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
    var fmetrics = (result && result.final_test_metrics) || {};
    var profile = fmetrics[result && result.selected_model];
    var overall = profile && (profile.overall || profile);
    return [
      {
        key: "mape",
        label: "MAPE",
        value: overall && overall.mape != null ? overall.mape + "%" : "—",
      },
      {
        key: "mae",
        label: "MAE",
        value: overall && overall.mae != null
          ? (overall.mae / 10000).toFixed(2) + " 萬／坪"
          : "—",
      },
      {
        key: "coverage",
        label: "測試覆蓋率",
        value: result && result.test_coverage != null
          ? (result.test_coverage * 100).toFixed(1) + "%"
          : "—",
      },
    ];
  }

  function profileComparisonRows(result) {
    var profiles = (result && result.profile_results) || [];
    var rows = [];
    if (Array.isArray(profiles)) {
      if (profiles.length === 0 || !profiles[0].profile_name) return profiles;
      profiles.forEach(function (profile) {
        var metrics = profile.selection_metrics || {};
        Object.keys(metrics).forEach(function (modelName) {
          var overall = metrics[modelName] && metrics[modelName].overall;
          rows.push({
            name: profile.profile_name + ":" + modelName,
            profile: profile.profile_name,
            model: modelName,
            params: profile.parameters || {},
            mae: overall && overall.mae,
            mape: overall && overall.mape,
            metrics: overall || {},
            selected:
              profile.profile_name === result.selected_profile
              && modelName === result.selected_model,
          });
        });
      });
      return rows;
    }
    Object.keys(profiles).forEach(function (profileName) {
      var profile = profiles[profileName] || {};
      var metrics = profile.selection_metrics || {};
      Object.keys(metrics).forEach(function (modelName) {
        var overall = metrics[modelName] && metrics[modelName].overall;
        rows.push({
          name: profileName + ":" + modelName,
          profile: profileName,
          model: modelName,
          params: profile.parameters || {},
          mae: overall && overall.mae,
          mape: overall && overall.mape,
          metrics: overall || {},
          selected:
            profileName === result.selected_profile
            && modelName === result.selected_model,
        });
      });
    });
    return rows;
  }

  function trainingRecordState(manifest) {
    if (manifest.schema_version < 3) {
      return {
        legacy: true,
        notice:
          "舊版未保存調參快照；部分舊版模型未包含特徵實驗與時間回測。",
      };
    }
    return { legacy: false, notice: null };
  }

  function trainingOverview(result) {
    var fm = result && result.final_test_metrics;
    var winnerName = result && result.selected_model;
    var winnerLabel = winnerName;
    if (winnerName === "ridge") winnerLabel = "Ridge";
    else if (winnerName === "random_forest") winnerLabel = "Random Forest";
    else if (winnerName === "hist_gradient_boosting") winnerLabel = "HistGradientBoosting";

    var mape = fm && fm[winnerName] && fm[winnerName].overall && fm[winnerName].overall.mape;
    var mae = fm && fm[winnerName] && fm[winnerName].overall && fm[winnerName].overall.mae;
    var baselineMae = fm && fm.baseline && fm.baseline.overall && fm.baseline.overall.mae;

    var coverage = result && result.test_coverage;
    var publishable = result && result.recommended;

    return {
      publishable: publishable,
      selectedProfileLabel: PROFILE_LABELS[result && result.selected_profile] || "—",
      selectedModelLabel: winnerLabel,
      mape: mape != null ? mape.toFixed(1) + "%" : "—",
      mae: mae != null ? (mae / 10000).toFixed(2) + " 萬／坪" : "—",
      coverage: coverage != null ? (coverage * 100).toFixed(1) + "%" : "—",
      baselineMaeDelta: baselineMae != null && mae != null ? mae - baselineMae : null,
      stationWarnings: stationRows(result).filter(function (row) {
        return row.comparison === "regressed";
      }).map(function (row) {
        return row.station;
      }),
      readingOrder: [
        "先看是否通過發布門檻",
        "再看 MAPE 與 MAE",
        "最後確認各站與年度回測沒有明顯退步",
      ],
    };
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
        selected:
          names[i] === result.selected_model
          || names[i] === result.selected_profile + ":" + result.selected_model,
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

  function candidateDecisionSummary(run, market) {
    var results = (run && run.manifest && run.manifest.results) || [];
    var result = null;
    for (var i = 0; i < results.length; i++) {
      if (results[i].market === market) {
        result = results[i];
        break;
      }
    }
    if (!result) return null;

    var overview = trainingOverview(result);
    var finalMetrics = result.final_test_metrics || {};
    var selectedOverall =
      finalMetrics[result.selected_model]
      && finalMetrics[result.selected_model].overall;
    var baselineOverall = finalMetrics.baseline && finalMetrics.baseline.overall;
    var baselineComparison = "無 baseline 資料";
    if (
      selectedOverall
      && selectedOverall.mae != null
      && baselineOverall
      && baselineOverall.mae > 0
    ) {
      var changePercent =
        (selectedOverall.mae - baselineOverall.mae)
        / baselineOverall.mae
        * 100;
      baselineComparison =
        (changePercent <= 0 ? "改善 " : "退步 ")
        + Math.abs(changePercent).toFixed(1)
        + "%";
    }
    var backtests = backtestRows(result);
    var passedBacktests = backtests.filter(function (row) {
      return row.passed;
    }).length;

    return {
      model: overview.selectedModelLabel,
      profile:
        result.selected_profile
          ? overview.selectedProfileLabel
          : "舊版未記錄",
      mae: overview.mae,
      mape: overview.mape,
      coverage: overview.coverage,
      baselineComparison: baselineComparison,
      isCurrentOfficial: Boolean(
        run.markets
        && run.markets[market]
        && run.markets[market].is_current_official
      ),
      backtests:
        backtests.length > 0
          ? passedBacktests + " / " + backtests.length + " 通過"
          : "不適用或未記錄",
      stationWarnings: overview.stationWarnings,
      recommended: result.recommended === true,
      result: result,
    };
  }

  function buildAutoMLPayload(market, budget) {
    if (!MARKET_PAYLOADS.hasOwnProperty(market)) {
      throw new Error("unknown market selection: " + market);
    }
    if (!BUILD_LABELS.hasOwnProperty(budget)) {
      throw new Error("unknown budget: " + budget);
    }
    return {
      markets: MARKET_PAYLOADS[market],
      tuning: { mode: "automl", budget: budget },
    };
  }

  function canStopAutoML(run) {
    return Boolean(
      run
      && run.status === "running"
      && run.summary
      && run.summary.mode === "automl"
    );
  }

  function automlProgressView(summary) {
    if (!summary) summary = {};
    var totalSecs = summary.elapsed_seconds || 0;
    var mins = Math.floor(totalSecs / 60);
    var secs = Math.floor(totalSecs % 60);
    var elapsed = (mins < 10 ? "0" : "") + mins + ":" + (secs < 10 ? "0" : "") + secs;
    return {
      stage: summary.stage || null,
      modelName: summary.model_name || null,
      completedTrials: summary.completed_trials || 0,
      failedTrials: summary.failed_trials || 0,
      elapsedSeconds: elapsed,
      bestMae: summary.best_mae != null ? summary.best_mae : null,
      lastParameters: summary.last_parameters || null,
    };
  }

  function automlLeaderboardRows(detail) {
    var manifest = (detail && detail.manifest) || {};
    var automl = manifest.automl || (detail && detail.automl) || {};
    var markets = automl.markets || {};
    if (automl.top_trials) {
      markets = { unknown: { top_trials: automl.top_trials } };
    }
    var rows = [];
    Object.keys(markets).forEach(function (market) {
      var marketResult = markets[market] || {};
      var trials = marketResult.top_trials || marketResult.ranked_trials || [];
      trials.slice(0, 10).forEach(function (t) {
        rows.push({
          market: market,
          trialNumber: t.trial_number,
          state: t.state,
          modelName: (t.fit_spec && t.fit_spec.model_name) || t.model_name,
          mae: t.overall_mae != null ? t.overall_mae : t.mae,
          mape: t.overall_mape != null ? t.overall_mape : t.mape,
          calibrationPassed: t.calibration_passed === true,
          durationSeconds: t.duration_seconds,
        });
      });
    });
    return rows;
  }

  function parkingPolicySummary(policy) {
    if (!policy) return "無車位估值政策";
    var lines = ["車位估值政策 v" + policy.version];
    for (var type in policy.by_type) {
      var stat = policy.by_type[type];
      lines.push(type + "：" + (stat.price_twd / 10000).toFixed(0) + " 萬（" + stat.sample_size + " 筆）");
    }
    if (policy.market_fallback) {
      lines.push("市場中位數：" + (policy.market_fallback.price_twd / 10000).toFixed(0) + " 萬（" + policy.market_fallback.sample_size + " 筆）");
    }
    return lines.join(" | ");
  }

  return {
    MARKET_PAYLOADS: MARKET_PAYLOADS,
    PROFILE_LABELS: PROFILE_LABELS,
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
    trainingOverview: trainingOverview,
    modelComparisonRows: modelComparisonRows,
    stationRows: stationRows,
    ablationRows: ablationRows,
    backtestRows: backtestRows,
    releaseCheckRows: releaseCheckRows,
    candidateDecisionSummary: candidateDecisionSummary,
    parkingPolicySummary: parkingPolicySummary,
    BUILD_LABELS: BUILD_LABELS,
    buildAutoMLPayload: buildAutoMLPayload,
    canStopAutoML: canStopAutoML,
    automlProgressView: automlProgressView,
    automlLeaderboardRows: automlLeaderboardRows,
  };
});
