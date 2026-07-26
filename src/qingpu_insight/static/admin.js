(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof document !== "undefined") root.QingpuAdmin = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var SECTIONS = [
    "overview", "data", "listings", "models",
    "llm", "backups", "jobs", "diagnostics",
  ];

  function normalizeSection(hash) {
    var section = hash.replace(/^#/, "");
    return SECTIONS.indexOf(section) !== -1 ? section : "overview";
  }

  function overviewView(data) {
    var ready = data.mutation_ready;
    var blockedCodes = data.readiness
      .filter(function (r) { return r.status === "blocked"; })
      .map(function (r) { return r.code; });
    return {
      ready: ready,
      headline: ready ? "運維功能正常" : "運維功能尚未就緒",
      blockedCodes: blockedCodes,
      actionCount: data.action_items.length,
    };
  }

  function jobStatusLabel(status) {
    var labels = {
      succeeded: "成功",
      failed: "失敗",
      interrupted: "已中斷",
    };
    return labels[status] || status;
  }

  function buildOfficialUpdatePayload(startSeason, endSeason, startAt) {
    if (startSeason && endSeason) {
      var sk = startSeason.toUpperCase().split("S");
      var ek = endSeason.toUpperCase().split("S");
      if (parseInt(sk[0], 10) > parseInt(ek[0], 10) ||
          (parseInt(sk[0], 10) === parseInt(ek[0], 10) &&
           parseInt(sk[1], 10) > parseInt(ek[1], 10))) {
        throw new Error("起始季度不得晚於結束季度");
      }
    }
    return {
      start_season: startSeason,
      end_season: endSeason,
      start_at: startAt || "acquire",
    };
  }

  var STAGE_LABELS = {
    acquiring: "正在取得官方資料",
    analysing: "正在分析地理編碼",
    building_market: "正在建立市場資料集",
    publishing_mysql: "正在發布正式市場資料",
    verifying: "正在驗證",
  };

  function stageLabel(stage) {
    return STAGE_LABELS[stage] || stage;
  }

  function renderOverview(data) {
    var view = overviewView(data);
    var container = document.querySelector("#admin-overview .admin-content");
    if (!container) return;

    var headline = document.createElement("p");
    headline.className = "admin-headline";
    headline.textContent = view.headline;
    container.appendChild(headline);

    if (data.readiness && data.readiness.length > 0) {
      var list = document.createElement("div");
      list.className = "admin-readiness-list";
      for (var i = 0; i < data.readiness.length; i++) {
        var r = data.readiness[i];
        var item = document.createElement("div");
        item.className = "readiness-item readiness-" + r.status;
        var code = document.createElement("span");
        code.className = "readiness-code";
        code.textContent = r.code;
        var msg = document.createElement("span");
        msg.className = "readiness-message";
        msg.textContent = r.message;
        item.appendChild(code);
        item.appendChild(msg);
        list.appendChild(item);
      }
      container.appendChild(list);
    }

    if (!data.mutation_ready) {
      var blocker = document.createElement("div");
      blocker.className = "admin-blocked";
      blocker.textContent = "系統尚無法進行資料異動操作。";
      container.appendChild(blocker);
    }

    if (data.action_items && data.action_items.length > 0) {
      var actions = document.createElement("div");
      actions.className = "admin-action-items";
      var ah = document.createElement("h3");
      ah.textContent = "待辦事項";
      actions.appendChild(ah);
      for (var j = 0; j < data.action_items.length; j++) {
        var a = data.action_items[j];
        var al = document.createElement("div");
        al.className = "admin-action-item";
        al.textContent = a.message + (a.section ? "（" + a.section + "）" : "");
        actions.appendChild(al);
      }
      container.appendChild(actions);
    }

    var details = document.createElement("details");
    var summary = document.createElement("summary");
    summary.textContent = "技術詳情";
    details.appendChild(summary);
    var raw = document.createElement("pre");
    raw.className = "admin-raw-readiness";
    raw.textContent = JSON.stringify(data.readiness, null, 2);
    details.appendChild(raw);
    container.appendChild(details);
  }

  function renderJobs(data) {
    var container = document.querySelector("#admin-jobs .admin-content");
    if (!container || !data.items || data.items.length === 0) return;

    var table = document.createElement("table");
    table.className = "admin-job-table";
    var thead = document.createElement("thead");
    var headerRow = document.createElement("tr");
    var cols = ["類型", "狀態", "開始時間", "輸出版本"];
    for (var i = 0; i < cols.length; i++) {
      var th = document.createElement("th");
      th.textContent = cols[i];
      headerRow.appendChild(th);
    }
    thead.appendChild(headerRow);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    for (var j = 0; j < data.items.length; j++) {
      var job = data.items[j];
      var row = document.createElement("tr");
      var cells = [
        job.job_type,
        jobStatusLabel(job.display_status),
        job.started_at || "",
        job.output_version || "",
      ];
      for (var k = 0; k < cells.length; k++) {
        var td = document.createElement("td");
        td.textContent = cells[k];
        row.appendChild(td);
      }
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
    container.appendChild(table);
  }

  function disableMutationButtons() {
    var buttons = document.querySelectorAll(".mutation-btn");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.add("disabled");
      buttons[i].disabled = true;
    }
  }

  function getCSRFToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function setupOfficialDataForm() {
    var form = document.getElementById("official-data-form");
    if (!form) return;
    var statusEl = document.getElementById("od-status");
    var toggleBtn = document.getElementById("od-toggle-advanced");
    var advancedPanel = document.querySelector(".admin-advanced-panel");

    if (toggleBtn && advancedPanel) {
      toggleBtn.addEventListener("click", function () {
        var hidden = advancedPanel.style.display === "none";
        advancedPanel.style.display = hidden ? "block" : "none";
        toggleBtn.textContent = hidden ? "隱藏進階選項" : "進階選項";
      });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var startSeason = document.getElementById("od-start-season").value.trim();
      var endSeason = document.getElementById("od-end-season").value.trim();
      var startAt = document.getElementById("od-start-at").value;
      var submitBtn = document.getElementById("od-submit-btn");

      var payload;
      try {
        payload = buildOfficialUpdatePayload(startSeason, endSeason, startAt);
      } catch (err) {
        statusEl.textContent = err.message;
        statusEl.className = "admin-od-status admin-od-error";
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = "更新中…";
      statusEl.textContent = "";
      statusEl.className = "admin-od-status";

      fetch("/api/admin/official-data-updates", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Qingpu-CSRF": getCSRFToken(),
        },
        body: JSON.stringify(payload),
      })
        .then(function (r) { return r.json().then(function (d) { return { status: r.status, body: d }; }); })
        .then(function (result) {
          submitBtn.disabled = false;
          submitBtn.textContent = "一鍵更新官方資料";
          if (result.body.error) {
            var msg = result.body.error.message || "更新啟動失敗";
            statusEl.textContent = msg;
            statusEl.className = "admin-od-status admin-od-error";
          } else {
            statusEl.textContent = "更新工作已啟動 (run_id: " + result.body.run_id + ")";
            statusEl.className = "admin-od-status admin-od-ok";
          }
        })
        .catch(function () {
          submitBtn.disabled = false;
          submitBtn.textContent = "一鍵更新官方資料";
          statusEl.textContent = "網路錯誤，請稍後再試。";
          statusEl.className = "admin-od-status admin-od-error";
        });
    });
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
      setupOfficialDataForm();
      var sidebarLinks = document.querySelectorAll(".admin-sidebar a[href^='#']");
      for (var i = 0; i < sidebarLinks.length; i++) {
        sidebarLinks[i].addEventListener("click", function (e) {
          var targetId = "admin-" + normalizeSection(e.currentTarget.getAttribute("href"));
          var target = document.getElementById(targetId);
          if (target) target.scrollIntoView({ behavior: "smooth" });
        });
      }

      fetch("/api/admin/overview")
        .then(function (r) { if (r.ok) return r.json(); })
        .then(function (data) {
          if (data) {
            renderOverview(data);
            if (!data.mutation_ready) disableMutationButtons();
          }
        });

      fetch("/api/admin/jobs")
        .then(function (r) { if (r.ok) return r.json(); })
        .then(function (data) {
          if (data) renderJobs(data);
        });
    });
  }

  return {
    SECTIONS: SECTIONS,
    normalizeSection: normalizeSection,
    overviewView: overviewView,
    jobStatusLabel: jobStatusLabel,
    buildOfficialUpdatePayload: buildOfficialUpdatePayload,
    stageLabel: stageLabel,
  };
});
