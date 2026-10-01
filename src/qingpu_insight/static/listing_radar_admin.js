(function (root, factory) {
  "use strict";
  var api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.QingpuListingRadarAdmin = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  var RUN_URL = "/api/admin/listing-radar-runs";
  var STOP_MESSAGES = {
    verification_required: "591 顯示驗證頁，雷達已停止；已完成的物件已保存。請手動完成驗證後再執行。",
    capture_failed: "連續多頁無法載入，雷達已停止；已完成的物件已保存。",
  };

  function buildRunPayload(maxListings, refreshHours) {
    var max = Number(maxListings);
    var hours = Number(refreshHours);
    if (!Number.isInteger(max) || max < 1 || max > 200) {
      return { error: "最多物件數需為 1–200 的整數。" };
    }
    if (!Number.isFinite(hours) || hours < 0 || hours > 336) {
      return { error: "沿用時數需介於 0–336。" };
    }
    return { payload: { max_listings: max, refresh_hours: hours } };
  }

  function progressText(job) {
    if (!job) return "";
    var summary = job.summary || {};
    if (job.status === "succeeded") {
      return "完成：估價 " + (summary.valued || 0) + " 筆，候選 " + (summary.ranked || 0) +
        " 筆（明顯低於區間 " + (summary.below_interval || 0) + " 筆）。";
    }
    if (job.status === "failed") {
      return STOP_MESSAGES[job.error_code] || "雷達執行失敗：" + (job.error_message || "未知原因");
    }
    if (summary.stage === "capturing") {
      return "處理中 " + (summary.processed || 0) + "/" + (summary.total || 0) + "：已估價 " +
        (summary.valued || 0) + "，即時擷取 " + (summary.captured_live || 0) + "，沿用快取 " +
        (summary.from_cache || 0) + "。";
    }
    if (summary.stage === "selected") return "已選出 " + (summary.total || 0) + " 筆候選，開始擷取…";
    return "已排入佇列…";
  }

  function init(doc, fetchImpl, polling, schedule) {
    var button = doc.getElementById("lr-run-btn");
    if (!button) return null;
    var status = doc.getElementById("lr-status");
    var controller = polling.createPollController({
      fetchJob: function (runId) { return fetchImpl("/api/jobs/" + runId); },
      schedule: schedule,
      minDelay: 3000,
      maxDelay: 15000,
      maxAttempts: 1200,
      maxFailures: 5,
      onUpdate: function (job) { status.textContent = progressText(job); },
      onStop: function (_reason, job) {
        button.disabled = false;
        if (job && job.status) status.textContent = progressText(job);
      },
    });

    button.addEventListener("click", function () {
      var built = buildRunPayload(
        doc.getElementById("lr-max-listings").value,
        doc.getElementById("lr-refresh-hours").value
      );
      if (built.error) {
        status.textContent = built.error;
        return;
      }
      var meta = doc.querySelector('meta[name="csrf-token"]');
      button.disabled = true;
      status.textContent = "送出中…";
      fetchImpl(RUN_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Qingpu-CSRF": meta ? meta.getAttribute("content") : "",
        },
        body: JSON.stringify(built.payload),
      })
        .then(polling.parseApiResponse)
        .then(function (job) {
          status.textContent = job.created === false ? "已有雷達執行中，改為追蹤該工作…" : "已開始…";
          controller.start(job.run_id);
        })
        .catch(function (error) {
          button.disabled = false;
          status.textContent = error.message || "雷達無法啟動。";
        });
    });
    return controller;
  }

  if (typeof document !== "undefined" && root.QingpuJobPolling && typeof fetch === "function") {
    document.addEventListener("DOMContentLoaded", function () {
      init(
        document,
        function (url, options) { return fetch(url, options); },
        root.QingpuJobPolling,
        function (callback, delay) { setTimeout(callback, delay); }
      );
    });
  }

  return {
    RUN_URL: RUN_URL,
    buildRunPayload: buildRunPayload,
    progressText: progressText,
    init: init,
  };
});
