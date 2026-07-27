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
  final_test_metrics: {
    locked_winner: "profile_3",
    profiles: {
      profile_0: { mape: 12.3, mae: 45000.0, coverage: 0.75 },
      profile_1: { mape: 10.1, mae: 52000.0, coverage: 0.80 },
      profile_2: { mape: 9.8, mae: 48000.0, coverage: 0.82 },
      profile_3: { mape: 8.5, mae: 45000.0, coverage: 0.85 },
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

// trainingRecordState tests
assert.equal(admin.trainingRecordState({ schema_version: 2 }).legacy, true);
assert.match(admin.trainingRecordState({ schema_version: 2 }).notice, /舊版未保存調參快照/);
assert.equal(admin.trainingRecordState({ schema_version: 3 }).legacy, false);
assert.equal(admin.trainingRecordState({ schema_version: 3 }).notice, null);

process.stdout.write("model admin contract passed\n");
