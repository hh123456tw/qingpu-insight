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
  var DEFAULT_LISTING_TYPES = ["sale", "newhouse"];

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

  function runListingSequence(_a) {
    var types = _a.types, submit = _a.submit, waitForTerminal = _a.waitForTerminal, onTypeStart = _a.onTypeStart, onTypeDone = _a.onTypeDone;
    var ordered = DEFAULT_LISTING_TYPES.filter(function (type) {
      return types.indexOf(type) !== -1;
    });
    var results = {};
    function step(idx) {
      if (idx >= ordered.length) return Promise.resolve(results);
      var type = ordered[idx];
      onTypeStart(type);
      return Promise.resolve().then(function () {
        return submit(type);
      }).then(function (job) {
        return waitForTerminal(job.run_id);
      }).then(function (finalState) {
        results[type] = { status: finalState.status, output_version: finalState.output_version, error_code: finalState.error_code };
        onTypeDone(type, results[type]);
        return step(idx + 1);
      }, function (err) {
        results[type] = { status: "error", error_code: String(err) };
        onTypeDone(type, results[type]);
        return step(idx + 1);
      });
    }
    return step(0);
  }

  function runAllListings() {
    var maxPagesInput = document.getElementById("ls-max-pages");
    var maxPages = parseInt(maxPagesInput.value, 10);
    if (isNaN(maxPages) || maxPages < 1 || maxPages > 50) { maxPagesInput.focus(); return; }
    var runBtn = document.getElementById("ls-run-all-btn");
    runBtn.disabled = true;
    runBtn.classList.add("disabled");
    var statusItems = document.querySelectorAll(".listing-type-status");
    for (var i = 0; i < statusItems.length; i++) {
      statusItems[i].textContent = "—";
      statusItems[i].className = "listing-type-status";
    }
    var retryBtns = document.querySelectorAll(".listing-retry-btn");
    for (var j = 0; j < retryBtns.length; j++) { retryBtns[j].style.display = "none"; }
    runListingSequence({
      types: DEFAULT_LISTING_TYPES.slice(),
      maxPages: maxPages,
      submit: function (type) {
        return fetch("/api/admin/listing-updates", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Qingpu-CSRF": getCSRFToken() },
          body: JSON.stringify({ types: [type], max_pages: maxPages }),
        }).then(function (r) { return r.json(); });
      },
      waitForTerminal: function (runId) {
        return new Promise(function (resolve, reject) {
          var retries = 0;
          (function poll() {
            fetch("/api/jobs/" + runId)
              .then(function (r) { return r.json(); })
              .then(function (job) {
                if (job.status === "succeeded" || job.status === "failed" || job.status === "interrupted") {
                  resolve(job);
                } else {
                  setTimeout(poll, 2000);
                }
              })
              .catch(function () {
                if (retries < 1) {
                  retries++;
                  setTimeout(poll, 2000);
                } else {
                  reject(new Error("輪詢失敗"));
                }
              });
          })();
        });
      },
      onTypeStart: function (type) {
        var el = document.getElementById("ls-status-" + type);
        el.textContent = "執行中…";
        el.className = "listing-type-status status-running";
      },
      onTypeDone: function (type, result) {
        var el = document.getElementById("ls-status-" + type);
        if (result.status === "succeeded") {
          el.textContent = "成功 (" + (result.output_version || "?") + ")";
          el.className = "listing-type-status status-success";
        } else {
          el.textContent = "失敗" + (result.error_code ? " (" + result.error_code + ")" : "");
          el.className = "listing-type-status status-fail";
          var btn = document.querySelector('.listing-retry-btn[data-type="' + type + '"]');
          if (btn) btn.style.display = "";
        }
      },
    }).then(function () {
      runBtn.disabled = false;
      runBtn.classList.remove("disabled");
    }).catch(function () {
      runBtn.disabled = false;
      runBtn.classList.remove("disabled");
      var statusEl = document.getElementById("ls-status-sale");
      if (statusEl) { statusEl.textContent = "失敗 (系統錯誤)"; statusEl.className = "listing-type-status status-fail"; }
    });
  }

  function retrySingleType(type, maxPages) {
    var pages = parseInt(maxPages, 10);
    if (isNaN(pages) || pages < 1) pages = 1;
    if (pages > 50) pages = 50;
    maxPages = pages;
    var retryBtn = document.querySelector('.listing-retry-btn[data-type="' + type + '"]');
    if (retryBtn) { retryBtn.disabled = true; retryBtn.style.display = "none"; }
    var el = document.getElementById("ls-status-" + type);
    el.textContent = "執行中…";
    el.className = "listing-type-status status-running";
    runListingSequence({
      types: [type],
      maxPages: maxPages,
      submit: function (t) {
        return fetch("/api/admin/listing-updates", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Qingpu-CSRF": getCSRFToken() },
          body: JSON.stringify({ types: [t], max_pages: maxPages }),
        }).then(function (r) { return r.json(); });
      },
      waitForTerminal: function (runId) {
        return new Promise(function (resolve, reject) {
          var retries = 0;
          (function poll() {
            fetch("/api/jobs/" + runId)
              .then(function (r) { return r.json(); })
              .then(function (job) {
                if (job.status === "succeeded" || job.status === "failed" || job.status === "interrupted") {
                  resolve(job);
                } else {
                  setTimeout(poll, 2000);
                }
              })
              .catch(function () {
                if (retries < 1) {
                  retries++;
                  setTimeout(poll, 2000);
                } else {
                  reject(new Error("輪詢失敗"));
                }
              });
          })();
        });
      },
      onTypeStart: function () {},
      onTypeDone: function (t, result) {
        var el = document.getElementById("ls-status-" + t);
        if (result.status === "succeeded") {
          el.textContent = "成功 (" + (result.output_version || "?") + ")";
          el.className = "listing-type-status status-success";
        } else {
          el.textContent = "失敗" + (result.error_code ? " (" + result.error_code + ")" : "");
          el.className = "listing-type-status status-fail";
          var btn = document.querySelector('.listing-retry-btn[data-type="' + t + '"]');
          if (btn) { btn.disabled = false; btn.style.display = ""; }
        }
      },
    }).catch(function () {
      if (retryBtn) { retryBtn.disabled = false; retryBtn.style.display = ""; }
      el.textContent = "失敗 (系統錯誤)";
      el.className = "listing-type-status status-fail";
    });
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

  function loadBackups() {
    return fetch("/api/ops/backups?limit=20")
      .then(function (r) { if (r.ok) return r.json(); })
      .then(function (data) {
        if (!data) return;
        var tbody = document.querySelector("#bk-table tbody");
        if (!tbody) return;
        tbody.innerHTML = "";
        for (var i = 0; i < data.items.length; i++) {
          var bk = data.items[i];
          var tr = document.createElement("tr");
          var shortId = bk.backup_id.slice(0, 8);
          var shortSha = bk.sha256 ? bk.sha256.slice(0, 16) : "";
          var sizeStr = bk.size_bytes > 0 ? (bk.size_bytes / 1024).toFixed(1) + " KB" : "";
          var restoreLabel = bk.restore_status || "—";
          var drillBtn = document.createElement("button");
          drillBtn.className = "mutation-btn admin-btn admin-btn-small";
          drillBtn.textContent = "隔離還原演練";
          drillBtn.dataset.backupId = bk.backup_id;
          drillBtn.addEventListener("click", function (e) {
            startRestoreDrill(e.currentTarget.dataset.backupId);
          });
          var tdDrill = document.createElement("td");
          tdDrill.appendChild(drillBtn);
          tr.innerHTML = "<td>" + shortId + "</td><td>" + (bk.created_at || "") + "</td><td>" + sizeStr + "</td><td>" + shortSha + "</td><td>" + restoreLabel + "</td>";
          tr.appendChild(tdDrill);
          tbody.appendChild(tr);
        }
      });
  }

  function startBackupCreate() {
    var btn = document.getElementById("bk-create-btn");
    var statusEl = document.getElementById("bk-status");
    if (!btn || !statusEl) return;
    btn.disabled = true;
    btn.textContent = "建立中…";
    statusEl.textContent = "";
    statusEl.className = "admin-bk-status";

    fetch("/api/admin/backups", {
      method: "POST",
      headers: { "X-Qingpu-CSRF": getCSRFToken() },
    })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, body: d }; }); })
      .then(function (result) {
        btn.disabled = false;
        btn.textContent = "建立備份";
        if (result.body.error) {
          statusEl.textContent = result.body.error.message || "備份啟動失敗";
          statusEl.className = "admin-bk-status admin-od-error";
        } else {
          statusEl.textContent = "備份工作已啟動 (run_id: " + result.body.run_id.slice(0, 8) + ")";
          statusEl.className = "admin-bk-status admin-od-ok";
          setTimeout(loadBackups, 3000);
        }
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = "建立備份";
        statusEl.textContent = "網路錯誤，請稍後再試。";
        statusEl.className = "admin-bk-status admin-od-error";
      });
  }

  function startRestoreDrill(backupId) {
    var statusEl = document.getElementById("bk-status");
    if (!statusEl) return;
    statusEl.textContent = "還原演練啟動中…";
    statusEl.className = "admin-bk-status";

    fetch("/api/admin/backups/" + backupId + "/restore-drills", {
      method: "POST",
      headers: { "X-Qingpu-CSRF": getCSRFToken() },
    })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, body: d }; }); })
      .then(function (result) {
        if (result.body.error) {
          statusEl.textContent = result.body.error.message || "還原演練啟動失敗";
          statusEl.className = "admin-bk-status admin-od-error";
        } else {
          statusEl.textContent = "還原演練工作已啟動 (run_id: " + result.body.run_id.slice(0, 8) + ")";
          statusEl.className = "admin-bk-status admin-od-ok";
          setTimeout(loadBackups, 3000);
        }
      })
      .catch(function () {
        statusEl.textContent = "網路錯誤，請稍後再試。";
        statusEl.className = "admin-bk-status admin-od-error";
      });
  }

  var pendingRestorePreview = null;

  function startRestorePreview() {
    var backupId = document.getElementById("restore-backup-id").value.trim();
    if (!backupId) return;
    var statusEl = document.getElementById("restore-status");
    var previewArea = document.getElementById("restore-preview-area");

    statusEl.textContent = "正在取得預覽…";
    statusEl.className = "admin-bk-status";
    previewArea.style.display = "none";

    fetch("/api/ops/restore-previews", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Qingpu-CSRF": getCSRFToken() },
      body: JSON.stringify({ backup_id: backupId }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          statusEl.textContent = data.error.message || "預覽失敗";
          statusEl.className = "admin-bk-status admin-od-error";
          return;
        }
        pendingRestorePreview = data;
        previewArea.style.display = "block";
        statusEl.textContent = "";
        document.getElementById("restore-preview-detail").textContent =
          "確認文字: " + data.confirmation_text;
        document.getElementById("restore-confirm-input").value = "";
        document.getElementById("restore-confirm-btn").disabled = true;
        document.getElementById("restore-error").style.display = "none";
      })
      .catch(function () {
        statusEl.textContent = "網路錯誤";
        statusEl.className = "admin-bk-status admin-od-error";
      });
  }

  function submitRestore() {
    if (!pendingRestorePreview) return;
    var confirmBtn = document.getElementById("restore-confirm-btn");
    var errorEl = document.getElementById("restore-error");
    var statusEl = document.getElementById("restore-status");

    confirmBtn.disabled = true;
    confirmBtn.textContent = "提交中…";
    statusEl.textContent = "";
    statusEl.className = "admin-bk-status";

    fetch("/api/ops/restores", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Qingpu-CSRF": getCSRFToken() },
      body: JSON.stringify({
        preview_id: pendingRestorePreview.preview_id,
        confirmation_text: pendingRestorePreview.confirmation_text,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          errorEl.textContent = data.error.message || "提交失敗";
          errorEl.style.display = "block";
          confirmBtn.disabled = false;
          confirmBtn.textContent = "確認還原";
          return;
        }
        pendingRestorePreview = null;
        document.getElementById("restore-preview-area").style.display = "none";
        statusEl.textContent = "還原工作已啟動 (run_id: " + data.run_id.slice(0, 8) + ")";
        statusEl.className = "admin-bk-status admin-od-ok";
        confirmBtn.textContent = "確認還原";
      })
      .catch(function () {
        errorEl.textContent = "網路錯誤";
        errorEl.style.display = "block";
        confirmBtn.disabled = false;
        confirmBtn.textContent = "確認還原";
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

      var runAllBtn = document.getElementById("ls-run-all-btn");
      if (runAllBtn) runAllBtn.addEventListener("click", runAllListings);
      document.addEventListener("click", function (e) {
        if (e.target.classList.contains("listing-retry-btn")) {
          var type = e.target.getAttribute("data-type");
          var mp = parseInt(document.getElementById("ls-max-pages").value, 10) || 10;
          retrySingleType(type, mp);
        }
      });

      loadBackups();

      var bkCreateBtn = document.getElementById("bk-create-btn");
      if (bkCreateBtn) bkCreateBtn.addEventListener("click", startBackupCreate);

      var restorePreviewBtn = document.getElementById("restore-preview-btn");
      if (restorePreviewBtn) restorePreviewBtn.addEventListener("click", startRestorePreview);

      var restoreConfirmBtn = document.getElementById("restore-confirm-btn");
      if (restoreConfirmBtn) restoreConfirmBtn.addEventListener("click", submitRestore);

      var restoreCancelBtn = document.getElementById("restore-cancel-btn");
      if (restoreCancelBtn) {
        restoreCancelBtn.addEventListener("click", function () {
          pendingRestorePreview = null;
          document.getElementById("restore-preview-area").style.display = "none";
        });
      }

      loadProviderStatus();

      var gmSetBtn = document.getElementById("gm-key-set-btn");
      if (gmSetBtn) gmSetBtn.addEventListener("click", setGeminiKey);

      var gmDeleteBtn = document.getElementById("gm-key-delete-btn");
      if (gmDeleteBtn) gmDeleteBtn.addEventListener("click", deleteGeminiKey);

      var smokeBtn = document.getElementById("smoke-run-btn");
      if (smokeBtn) smokeBtn.addEventListener("click", runSmokeTest);

      var benchmarkSelect = document.getElementById("benchmark-model-select");
      if (benchmarkSelect) {
        benchmarkSelect.addEventListener("change", updateBenchmarkSelection);
      }
      var benchmarkRefresh = document.getElementById("benchmark-model-refresh");
      if (benchmarkRefresh) {
        benchmarkRefresh.addEventListener("click", loadBenchmarkModels);
      }
      var benchmarkBtn = document.getElementById("benchmark-run-btn");
      if (benchmarkBtn) benchmarkBtn.addEventListener("click", runBenchmark);
      loadBenchmarkModels();

      var restoreInput = document.getElementById("restore-confirm-input");
      if (restoreInput) {
        restoreInput.addEventListener("input", function () {
          var match = canConfirmDangerousAction(
            restoreInput.value,
            pendingRestorePreview ? pendingRestorePreview.confirmation_text : ""
          );
          document.getElementById("restore-confirm-btn").disabled = !match;
        });
      }
    });
  }

  function loadProviderStatus() {
    fetch("/api/admin/providers")
      .then(function (r) { if (r.ok) return r.json(); })
      .then(function (data) {
        if (!data || !data.providers) return;
        var keyStatus = document.getElementById("gm-key-status");
        var gemini = null;
        for (var i = 0; i < data.providers.length; i++) {
          if (data.providers[i].name === "gemini") { gemini = data.providers[i]; break; }
        }
        if (keyStatus) {
          if (gemini && gemini.ready) {
            keyStatus.textContent = "Gemini API Key 已設定";
            keyStatus.className = "admin-od-status admin-od-ok";
          } else {
            keyStatus.textContent = "Gemini API Key 尚未設定";
            keyStatus.className = "admin-od-status admin-od-error";
          }
        }
      });
  }

  function setGeminiKey() {
    var input = document.getElementById("gm-key-input");
    var statusEl = document.getElementById("gm-key-status");
    var key = input ? input.value.trim() : "";
    if (!key) { statusEl.textContent = "請輸入 API Key"; statusEl.className = "admin-od-status admin-od-error"; return; }
    var originalKey = key;
    fetch("/api/admin/providers/gemini-key", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Qingpu-CSRF": getCSRFToken() },
      body: JSON.stringify({ key: key }),
    })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, body: d }; }); })
      .then(function (result) {
        if (result.body.error) {
          statusEl.textContent = result.body.error.message || "設定失敗";
          statusEl.className = "admin-od-status admin-od-error";
        } else {
          statusEl.textContent = "Gemini API Key 已設定";
          statusEl.className = "admin-od-status admin-od-ok";
          if (input) input.value = "";
        }
      })
      .catch(function () {
        statusEl.textContent = "網路錯誤";
        statusEl.className = "admin-od-status admin-od-error";
      });
  }

  function deleteGeminiKey() {
    var statusEl = document.getElementById("gm-key-status");
    fetch("/api/admin/providers/gemini-key", {
      method: "DELETE",
      headers: { "X-Qingpu-CSRF": getCSRFToken() },
    })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, body: d }; }); })
      .then(function (result) {
        if (result.body.error) {
          statusEl.textContent = result.body.error.message || "刪除失敗";
          statusEl.className = "admin-od-status admin-od-error";
        } else {
          statusEl.textContent = "Gemini API Key 已刪除";
          statusEl.className = "admin-od-status admin-od-ok";
          var input = document.getElementById("gm-key-input");
          if (input) input.value = "";
        }
      })
      .catch(function () {
        statusEl.textContent = "網路錯誤";
        statusEl.className = "admin-od-status admin-od-error";
      });
  }

  function runSmokeTest() {
    var select = document.getElementById("smoke-provider-select");
    var statusEl = document.getElementById("smoke-result");
    var provider = select ? select.value : "rule";
    statusEl.textContent = "執行中…";
    statusEl.className = "admin-od-status";
    fetch("/api/admin/provider-smoke-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Qingpu-CSRF": getCSRFToken() },
      body: JSON.stringify({ provider: provider }),
    })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, body: d }; }); })
      .then(function (result) {
        if (result.body.error) {
          statusEl.textContent = result.body.error.message || "啟動失敗";
          statusEl.className = "admin-od-status admin-od-error";
          return;
        }
        var runId = result.body.run_id;
        statusEl.textContent = "Smoke test 已提交 (" + runId.slice(0, 8) + ")，等待結果…";
        var retries = 0;
        var polls = 0;
        (function poll() {
          fetch("/api/jobs/" + runId)
            .then(function (r) {
              if (!r.ok) throw new Error("job_status_" + r.status);
              return r.json();
            })
            .then(function (job) {
              if (job.status === "succeeded" || job.status === "failed" || job.status === "interrupted") {
                var latency = job.summary && job.summary.latency_ms != null
                  ? "，耗時 " + job.summary.latency_ms + " ms"
                  : "";
                statusEl.textContent = "Smoke test " + (job.status === "succeeded" ? "成功" + latency : "失敗 (" + (job.error_message || job.status) + ")");
                statusEl.className = job.status === "succeeded" ? "admin-od-status admin-od-ok" : "admin-od-status admin-od-error";
              } else if (polls >= 30) {
                statusEl.textContent = "Smoke test 等待逾時，請到「工作」查看狀態";
                statusEl.className = "admin-od-status admin-od-error";
              } else {
                polls++;
                setTimeout(poll, 2000);
              }
            })
            .catch(function () {
              if (retries < 2) { retries++; setTimeout(poll, 3000); }
              else { statusEl.textContent = "輪詢逾時"; statusEl.className = "admin-od-status admin-od-error"; }
            });
        })();
      })
      .catch(function () {
        statusEl.textContent = "網路錯誤";
        statusEl.className = "admin-od-status admin-od-error";
      });
  }

  function runBenchmark() {
    var selected = selectedBenchmarkModel();
    if (!canRunBenchmark(selected)) return;
    var payload = buildBenchmarkPayload(selected.id);
    var statusEl = document.getElementById("benchmark-result");
    statusEl.textContent = "執行中…";
    statusEl.className = "admin-od-status";
    fetch("/api/admin/llm-benchmark-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Qingpu-CSRF": getCSRFToken() },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, body: d }; }); })
      .then(function (result) {
        if (result.body.error) {
          statusEl.textContent = result.body.error.message || "啟動失敗";
          statusEl.className = "admin-od-status admin-od-error";
          return;
        }
        var runId = result.body.run_id;
        statusEl.textContent = "Benchmark 已提交 (" + runId.slice(0, 8) + ")，等待結果…";
        var retries = 0;
        (function poll() {
          fetch("/api/jobs/" + runId)
            .then(function (r) { return r.json(); })
            .then(function (job) {
              if (job.status === "succeeded" || job.status === "failed" || job.status === "interrupted") {
                if (job.status === "succeeded") {
                  var s = job.summary || {};
                  statusEl.textContent = "Benchmark 成功 — " +
                    "Schema: " + (s.schema_success != null ? (s.schema_success * 100).toFixed(0) + "%" : "?") +
                    ", 事實: " + (s.fact_accuracy != null ? (s.fact_accuracy * 100).toFixed(0) + "%" : "?") +
                    ", 章節: " + (s.required_section_success != null ? (s.required_section_success * 100).toFixed(0) + "%" : "?") +
                    ", P50: " + (s.p50_latency_ms != null ? s.p50_latency_ms.toFixed(0) + "ms" : "?") +
                    ", P95: " + (s.p95_latency_ms != null ? s.p95_latency_ms.toFixed(0) + "ms" : "?");
                  statusEl.className = "admin-od-status admin-od-ok";
                } else {
                  statusEl.textContent = "Benchmark " + (job.status === "failed" ? "失敗" : "已中斷") + (job.error_message ? " (" + job.error_message + ")" : "");
                  statusEl.className = "admin-od-status admin-od-error";
                }
              } else {
                setTimeout(poll, 2000);
              }
            })
            .catch(function () {
              if (retries < 2) { retries++; setTimeout(poll, 3000); }
              else { statusEl.textContent = "輪詢逾時"; statusEl.className = "admin-od-status admin-od-error"; }
            });
        })();
      })
      .catch(function () {
        statusEl.textContent = "網路錯誤";
        statusEl.className = "admin-od-status admin-od-error";
      });
  }

  function buildBenchmarkPayload(modelId) {
    return { model_id: modelId };
  }

  function benchmarkModelHelp(option) {
    return option && typeof option.note === "string" ? option.note : "";
  }

  function canRunBenchmark(option) {
    return Boolean(option && option.ready === true);
  }

  var benchmarkModels = [];

  function selectedBenchmarkModel() {
    var select = document.getElementById("benchmark-model-select");
    if (!select) return null;
    return benchmarkModels.find(function (item) {
      return item.id === select.value;
    }) || null;
  }

  function updateBenchmarkSelection() {
    var help = document.getElementById("benchmark-model-help");
    var runButton = document.getElementById("benchmark-run-btn");
    var selected = selectedBenchmarkModel();
    if (help) help.textContent = benchmarkModelHelp(selected);
    if (runButton) runButton.disabled = !canRunBenchmark(selected);
  }

  function renderBenchmarkModels(catalog) {
    var select = document.getElementById("benchmark-model-select");
    var help = document.getElementById("benchmark-model-help");
    if (!select) return;
    benchmarkModels = Array.isArray(catalog.items) ? catalog.items : [];
    select.replaceChildren();
    benchmarkModels.forEach(function (item) {
      var option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label;
      option.disabled = item.ready !== true;
      select.appendChild(option);
    });
    select.disabled = benchmarkModels.length === 0;
    var firstReady = benchmarkModels.find(function (item) {
      return item.ready === true;
    });
    select.value = firstReady ? firstReady.id : "";
    if (help && Array.isArray(catalog.warnings)
        && catalog.warnings.includes("ollama_unavailable")) {
      help.textContent = "無法連線本機 Ollama；Gemini 模型仍可使用。";
    }
    updateBenchmarkSelection();
  }

  function loadBenchmarkModels() {
    var select = document.getElementById("benchmark-model-select");
    var runButton = document.getElementById("benchmark-run-btn");
    var help = document.getElementById("benchmark-model-help");
    if (select) select.disabled = true;
    if (runButton) runButton.disabled = true;
    if (help) help.textContent = "正在載入模型清單…";
    return fetch("/api/admin/llm-models")
      .then(function (response) {
        if (!response.ok) throw new Error("catalog_load_failed");
        return response.json();
      })
      .then(renderBenchmarkModels)
      .catch(function () {
        benchmarkModels = [];
        if (select) select.replaceChildren();
        if (help) help.textContent = "模型清單暫時無法載入，請稍後重試。";
      });
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

  return {
    DEFAULT_LISTING_TYPES: DEFAULT_LISTING_TYPES,
    SECTIONS: SECTIONS,
    normalizeSection: normalizeSection,
    overviewView: overviewView,
    jobStatusLabel: jobStatusLabel,
    buildOfficialUpdatePayload: buildOfficialUpdatePayload,
    stageLabel: stageLabel,
    runListingSequence: runListingSequence,
    buildReleasePreviewPayload: buildReleasePreviewPayload,
    canConfirmDangerousAction: canConfirmDangerousAction,
    loadProviderStatus: loadProviderStatus,
    setGeminiKey: setGeminiKey,
    deleteGeminiKey: deleteGeminiKey,
    runSmokeTest: runSmokeTest,
    buildBenchmarkPayload: buildBenchmarkPayload,
    benchmarkModelHelp: benchmarkModelHelp,
    canRunBenchmark: canRunBenchmark,
  };
});
