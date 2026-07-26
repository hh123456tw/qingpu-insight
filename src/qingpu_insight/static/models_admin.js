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

  function submitTraining(marketValue) {
    var payload = buildTrainingPayload(marketValue);
    var token = csrfToken();
    return fetch("/api/admin/model-training-runs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": token,
      },
      body: JSON.stringify(payload),
    });
  }

  return {
    MARKET_PAYLOADS: MARKET_PAYLOADS,
    STAGE_LABELS: STAGE_LABELS,
    buildTrainingPayload: buildTrainingPayload,
    derivePageState: derivePageState,
    submitTraining: submitTraining,
    csrfToken: csrfToken,
  };
});
