"use strict";

const assert = require("node:assert/strict");
const admin = require("../../src/qingpu_insight/static/models_admin.js");

assert.deepEqual(admin.buildTrainingPayload("all"), {
  markets: ["resale", "presale"],
});
assert.deepEqual(admin.buildTrainingPayload("resale"), {
  markets: ["resale"],
});
assert.deepEqual(admin.buildTrainingPayload("presale"), {
  markets: ["presale"],
});
assert.throws(() => admin.buildTrainingPayload("xgboost"), /unknown market/i);
assert.throws(() => admin.buildTrainingPayload(""), /unknown market/i);

assert.deepEqual(
  admin.derivePageState(
    { official_models: { resale: { role: "official", name: "ridge", recommended: true } } },
    { status: "running", summary: { stage: "training_resale" } }
  ),
  {
    canSubmit: false,
    stageLabel: "正在訓練中古屋模型",
    officialLabel: "ridge（官方）（建議）",
    candidateNotice: "訓練進行中，無法提交新任務。",
  }
);

assert.deepEqual(
  admin.derivePageState(
    { official_models: { resale: { role: "official", name: "ridge", recommended: true } } },
    null
  ),
  {
    canSubmit: true,
    stageLabel: null,
    officialLabel: "ridge（官方）（建議）",
    candidateNotice: "",
  }
);

assert.deepEqual(
  admin.derivePageState(
    { official_models: {}, candidate_count: 0 },
    null
  ),
  {
    canSubmit: true,
    stageLabel: null,
    officialLabel: "無官方模型",
    candidateNotice: "",
  }
);

assert.deepEqual(
  admin.derivePageState(
    {
      official_models: {
        resale: { role: "official", name: "ridge", recommended: true },
        presale: { role: "official", name: "lasso", recommended: false },
      },
    },
    { status: "running", summary: { stage: "unknown_stage" } }
  ),
  {
    canSubmit: false,
    stageLabel: "正在處理模型工作",
    officialLabel: "ridge（官方）（建議）；lasso（官方）（不建議）",
    candidateNotice: "訓練進行中，無法提交新任務。",
  }
);

assert.deepEqual(admin.MARKET_PAYLOADS, {
  resale: ["resale"],
  presale: ["presale"],
  all: ["resale", "presale"],
});

assert.equal(admin.REASON_LABELS.data_quality, "資料品質不足");
assert.equal(admin.reasonLabel("not_recommended"), "未通過發布門檻");
assert.match(admin.reasonLabel("baseline_selected"), /基準模型/);

assert.equal(admin.formatDatetime("2026-07-26T12:00:00Z").length > 0, true);
assert.equal(admin.formatDatetime(null), "—");
assert.equal(admin.formatDatetime(""), "—");

assert.equal(typeof admin.buildReleasePreviewPayload, "function");
assert.equal(typeof admin.canConfirmDangerousAction, "function");
assert.equal(admin.marketLabel("resale"), "中古屋");
assert.equal(admin.marketLabel("presale"), "預售屋");
assert.equal(admin.statusLabel("succeeded"), "成功");
assert.equal(admin.statusLabel("interrupted"), "已中斷");

// Custom tuning fixtures
var custom = {
  hgb_learning_rate: "0.05",
  hgb_max_iter: "420",
  rf_n_estimators: "520",
  recency_half_life_months: "36",
};

// Extended buildTrainingPayload tests
assert.deepEqual(admin.buildTrainingPayload("all", true, custom), {
  markets: ["resale", "presale"],
  tuning: {
    mode: "preset_comparison",
    include_custom: true,
    custom: {
      hgb_learning_rate: 0.05,
      hgb_max_iter: 420,
      rf_n_estimators: 520,
      recency_half_life_months: 36,
    },
  },
});

assert.deepEqual(admin.buildTrainingPayload("all", false), {
  markets: ["resale", "presale"],
  tuning: {
    mode: "preset_comparison",
    include_custom: false,
  },
});

assert.deepEqual(admin.buildTrainingPayload("resale", true, custom), {
  markets: ["resale"],
  tuning: {
    mode: "preset_comparison",
    include_custom: true,
    custom: {
      hgb_learning_rate: 0.05,
      hgb_max_iter: 420,
      rf_n_estimators: 520,
      recency_half_life_months: 36,
    },
  },
});
assert.deepEqual(admin.buildTrainingPayload("presale", true, {
  hgb_learning_rate: "0.05",
  hgb_max_iter: "420",
  rf_n_estimators: "520",
  recency_half_life_months: "",
}), {
  markets: ["presale"],
  tuning: {
    mode: "preset_comparison",
    include_custom: true,
    custom: {
      hgb_learning_rate: 0.05,
      hgb_max_iter: 420,
      rf_n_estimators: 520,
    },
  },
});

// validateCustomTuning tests
assert.equal(
  admin.validateCustomTuning("presale", custom).recency_half_life_months,
  "not_applicable"
);

var emptyCustom = admin.validateCustomTuning("resale", {});
assert.ok(emptyCustom.hgb_learning_rate);
assert.ok(emptyCustom.hgb_max_iter);
assert.ok(emptyCustom.rf_n_estimators);
assert.ok(emptyCustom.recency_half_life_months);

// Boundary tests for all 4 custom fields
assert.equal(admin.validateCustomTuning("resale", { hgb_learning_rate: "0.01", hgb_max_iter: "500", rf_n_estimators: "500", recency_half_life_months: "48" }).hgb_learning_rate, undefined);
assert.equal(admin.validateCustomTuning("resale", { hgb_learning_rate: "0.20" }).hgb_learning_rate, undefined);
assert.ok(admin.validateCustomTuning("resale", { hgb_learning_rate: "0.009" }).hgb_learning_rate);
assert.ok(admin.validateCustomTuning("resale", { hgb_learning_rate: "abc" }).hgb_learning_rate);

assert.equal(admin.validateCustomTuning("resale", { hgb_learning_rate: "0.05", hgb_max_iter: "100" }).hgb_max_iter, undefined);
assert.equal(admin.validateCustomTuning("resale", { hgb_learning_rate: "0.05", hgb_max_iter: "1000" }).hgb_max_iter, undefined);
assert.ok(admin.validateCustomTuning("resale", { hgb_max_iter: "99" }).hgb_max_iter);
assert.ok(admin.validateCustomTuning("resale", { hgb_max_iter: "abc" }).hgb_max_iter);

assert.equal(admin.validateCustomTuning("resale", { hgb_learning_rate: "0.05", hgb_max_iter: "500", rf_n_estimators: "100" }).rf_n_estimators, undefined);
assert.equal(admin.validateCustomTuning("resale", { hgb_learning_rate: "0.05", hgb_max_iter: "500", rf_n_estimators: "1000" }).rf_n_estimators, undefined);
assert.ok(admin.validateCustomTuning("resale", { rf_n_estimators: "99" }).rf_n_estimators);

assert.equal(admin.validateCustomTuning("resale", { hgb_learning_rate: "0.05", hgb_max_iter: "500", rf_n_estimators: "500", recency_half_life_months: "12" }).recency_half_life_months, undefined);
assert.equal(admin.validateCustomTuning("resale", { hgb_learning_rate: "0.05", hgb_max_iter: "500", rf_n_estimators: "500", recency_half_life_months: "84" }).recency_half_life_months, undefined);
assert.ok(admin.validateCustomTuning("resale", { recency_half_life_months: "11" }).recency_half_life_months);
assert.ok(admin.validateCustomTuning("resale", { recency_half_life_months: "85" }).recency_half_life_months);

// trainingSubmitSummary tests
assert.equal(admin.trainingSubmitSummary("all", true), "2 個市場 × 4 組設定");
assert.equal(admin.trainingSubmitSummary("all", false), "2 個市場 × 3 組設定");
assert.equal(admin.trainingSubmitSummary("resale", true), "1 個市場 × 4 組設定");
assert.equal(admin.trainingSubmitSummary("presale", false), "1 個市場 × 3 組設定");

// v3Result fixture
var v3Result = {
  selected_model: "hist_gradient_boosting",
  final_test_metrics: {
    hist_gradient_boosting: {
      overall: { mape: 8.5, mae: 45000.0, coverage: 0.85 },
    },
  },
  profile_results: [
    { name: "baseline", params: {}, metrics: { mape: 12.3, mae: 55000.0, coverage: 0.70 } },
    { name: "profile_1", params: { hgb_learning_rate: 0.05, hgb_max_iter: 200 }, metrics: { mape: 10.1, mae: 52000.0, coverage: 0.80 } },
    { name: "profile_2", params: { hgb_learning_rate: 0.08, hgb_max_iter: 400 }, metrics: { mape: 9.8, mae: 48000.0, coverage: 0.82 } },
    { name: "profile_3", params: { hgb_learning_rate: 0.05, hgb_max_iter: 600 }, metrics: { mape: 8.5, mae: 45000.0, coverage: 0.85 } },
  ],
  test_coverage: 0.85,
  schema_version: 3,
};

// metricCards tests
var cards = admin.metricCards(v3Result);
assert.deepEqual(
  cards.map(function (x) { return x.key; }),
  ["mape", "mae", "coverage"]
);
assert.equal(cards[0].label, "MAPE");
assert.equal(cards[0].value, "8.5%");
assert.equal(cards[1].label, "MAE");
assert.equal(cards[1].value, "4.50 萬元／坪");
assert.equal(cards[2].label, "測試覆蓋率");
assert.equal(cards[2].value, "85.0%");

// profileComparisonRows tests
var rows = admin.profileComparisonRows(v3Result);
assert.equal(rows.length, 4);
assert.equal(rows[0].name, "baseline");
assert.deepEqual(rows[0].metrics, { mape: 12.3, mae: 55000.0, coverage: 0.70 });
assert.equal(rows[3].name, "profile_3");

var persistedProfileRows = admin.profileComparisonRows({
  selected_profile: "balanced",
  selected_model: "ridge",
  profile_results: [{
    profile_name: "balanced",
    parameters: { hgb_learning_rate: 0.06 },
    selection_metrics: {
      ridge: { overall: { mae: 48000, mape: 9.8 } },
    },
  }],
});
assert.equal(persistedProfileRows[0].name, "balanced:ridge");
assert.equal(persistedProfileRows[0].mae, 48000);
assert.equal(persistedProfileRows[0].selected, true);

// trainingRecordState tests
assert.equal(admin.trainingRecordState({ schema_version: 2 }).legacy, true);
assert.match(admin.trainingRecordState({ schema_version: 2 }).notice, /舊版未保存調參快照/);
assert.match(
  admin.trainingRecordState({ schema_version: 2 }).notice,
  /未包含特徵實驗與時間回測/
);
assert.equal(admin.trainingRecordState({ schema_version: 3 }).legacy, false);
assert.equal(admin.trainingRecordState({ schema_version: 3 }).notice, null);

// v3 market result fixture for trainingOverview
var v3MarketResult = {
  market: "resale",
  selected_model: "hist_gradient_boosting",
  selected_profile: "quick",
  recommended: true,
  test_coverage: 0.85,
  final_test_metrics: {
    hist_gradient_boosting: {
      overall: { mape: 8.5, mae: 45000.0, rmse: 61000.0, r2: 0.72, count: 320 },
    },
    ridge: {
      overall: { mape: 10.2, mae: 52000.0, rmse: 68000.0, r2: 0.65, count: 320 },
    },
    random_forest: {
      overall: { mape: 9.1, mae: 48000.0, rmse: 64000.0, r2: 0.69, count: 320 },
    },
    baseline: {
      overall: { mape: 12.3, mae: 55000.0, rmse: 72000.0, r2: 0.58, count: 320 },
    },
  },
  profile_results: [
    { name: "quick", params: { hgb_learning_rate: 0.08, hgb_max_iter: 180 }, metrics: { mape: 8.5, mae: 45000.0, coverage: 0.85 } },
    { name: "balanced", params: { hgb_learning_rate: 0.06, hgb_max_iter: 350 }, metrics: { mape: 9.1, mae: 48000.0, coverage: 0.82 } },
    { name: "thorough", params: { hgb_learning_rate: 0.04, hgb_max_iter: 600 }, metrics: { mape: 9.8, mae: 50000.0, coverage: 0.80 } },
  ],
  schema_version: 3,
};

// trainingOverview tests
var overview = admin.trainingOverview(v3MarketResult);
assert.equal(overview.publishable, true);
assert.equal(overview.selectedProfileLabel, "快速");
assert.equal(overview.selectedModelLabel, "HistGradientBoosting");
assert.deepEqual(overview.readingOrder, [
  "先看是否通過發布門檻",
  "再看 MAPE 與 MAE",
  "最後確認各站與年度回測沒有明顯退步",
]);
assert.ok(overview.baselineMaeDelta < 0);
assert.equal(overview.mape, "8.5%");
assert.equal(overview.mae, "4.50 萬元／坪");
assert.equal(overview.coverage, "85.0%");

// Regression fixture: A18 candidate exceeds baseline
var regressionResult = {
  market: "resale",
  selected_model: "random_forest",
  selected_profile: "balanced",
  recommended: false,
  test_coverage: 0.80,
  final_test_metrics: {
    random_forest: {
      overall: { mape: 11.5, mae: 56000.0 },
      "station:A17": { mape: 10.2, mae: 50000.0 },
      "station:A18": { mape: 13.8, mae: 62000.0 },
      "station:A19": { mape: 10.5, mae: 51000.0 },
    },
    ridge: {
      overall: { mape: 12.0, mae: 57000.0 },
    },
    baseline: {
      overall: { mape: 11.0, mae: 53000.0 },
      "station:A17": { mape: 10.5, mae: 50000.0 },
      "station:A18": { mape: 11.2, mae: 54000.0 },
      "station:A19": { mape: 11.3, mae: 55000.0 },
    },
  },
  schema_version: 3,
};
var regOverview = admin.trainingOverview(regressionResult);
assert.equal(regOverview.publishable, false);
assert.equal(regOverview.baselineMaeDelta, 3000);
assert.equal(regOverview.selectedModelLabel, "Random Forest");
assert.deepEqual(regOverview.stationWarnings, ["A18"]);

assert.equal(admin.PROFILE_LABELS.quick, "快速");
assert.equal(admin.PROFILE_LABELS.balanced, "平衡");
assert.equal(admin.PROFILE_LABELS.thorough, "精細");
assert.equal(admin.PROFILE_LABELS.custom, "自訂");

var result = {
  selected_model: "hist_gradient_boosting",
  selection_metrics: {
    baseline: { overall: { mae: 84000, mape: 18.0 } },
    ridge: { overall: { mae: 82000, mape: 17.5 } },
    random_forest: { overall: { mae: 70000, mape: 15.0 } },
    hist_gradient_boosting: { overall: { mae: 67000, mape: 14.2 } },
  },
  final_test_metrics: {
    baseline: {
      "station:A17": { mape: 16.0 },
      "station:A18": { mape: 18.0 },
      "station:A19": { mape: 15.0 },
    },
    hist_gradient_boosting: {
      "station:A17": { mape: 14.0 },
      "station:A18": { mape: 16.2 },
      "station:A19": { mape: 14.5 },
    },
  },
  feature_experiments: [
    { name: "enhanced", selected_model: "hist_gradient_boosting",
      metrics: { overall: { mae: 67000 }, "station:A18": { mape: 16.2 } } }
  ],
  backtests: [{
    cutoff_date: "2026-06-12",
    passed: true,
    stations_within_limit: true,
    candidate_metrics: {
      overall: { mae: 67000 },
      "station:A17": { mape: 14.0 },
      "station:A18": { mape: 16.2 },
      "station:A19": { mape: 14.5 },
    },
    baseline_metrics: { overall: { mae: 84000 } },
  }],
  release_checks: { overall_mae_improved: true, a18_improved: true, recommended: true }
};
assert.equal(admin.ablationRows(result)[0].name, "enhanced");
assert.equal(admin.modelComparisonRows(result).length, 4);
assert.equal(admin.modelComparisonRows(result).at(-1).selected, true);
assert.equal(admin.stationRows(result)[1].station, "A18");
assert.equal(admin.stationRows(result)[1].comparison, "improved");
assert.equal(admin.backtestRows(result)[0].passed, true);
assert.equal(admin.backtestRows(result)[0].a18_mape, 16.2);
assert.equal(admin.releaseCheckRows(result).at(-1).code, "recommended");
assert.deepEqual(admin.ablationRows({}), []);

// Candidate cards must expose the evidence needed before publishing.
var candidateRun = {
  run_id: "f61915ba-a08a-4f18-a284-64e2d4efa6eb",
  manifest: {
    schema_version: 2,
    results: [{
      market: "resale",
      selected_model: "hist_gradient_boosting",
      selected_profile: null,
      recommended: true,
      test_coverage: null,
      final_test_metrics: {
        hist_gradient_boosting: {
          overall: { mae: 62798.38, mape: 18.304861243 },
          "station:A18": { mape: 22.91 },
        },
        baseline: {
          overall: { mae: 84120.00, mape: 22.01 },
          "station:A18": { mape: 18.30 },
        },
      },
      backtests: [
        { passed: true },
        { passed: false },
        { passed: true },
      ],
    }],
  },
};
var candidateSummary = admin.candidateDecisionSummary(candidateRun, "resale");
assert.equal(candidateSummary.model, "HistGradientBoosting");
assert.equal(candidateSummary.mae, "6.28 萬元／坪");
assert.equal(candidateSummary.mape, "18.3%");
assert.equal(candidateSummary.coverage, "—");
assert.equal(candidateSummary.backtests, "2 / 3 通過");
assert.equal(candidateSummary.baselineComparison, "改善 25.3%");
assert.deepEqual(candidateSummary.stationWarnings, ["A18"]);

process.stdout.write("model admin contract passed\n");
