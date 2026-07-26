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

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
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
  };
});
