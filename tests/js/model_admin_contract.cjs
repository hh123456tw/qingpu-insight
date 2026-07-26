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

process.stdout.write("model admin contract passed\n");
