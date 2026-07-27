(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof document !== "undefined") root.QingpuHomeAssistant = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function validateUrl(url) {
    if (!url || typeof url !== "string") return false;
    try {
      var parsed = new URL(url);
      return /^(sale|newhouse)\.591\.com\.tw$/.test(parsed.hostname);
    } catch (e) {
      return false;
    }
  }

  function buildCreatePayload(provider, model) {
    var payload = { provider: provider };
    if (model) payload.model = model;
    return payload;
  }

  function buildListingPayload(url) {
    return { url: url };
  }

  function getCsrfToken() {
    if (typeof document === "undefined") return "";
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function renderStatus(message, isError) {
    if (typeof document === "undefined") return;
    var el = document.getElementById("assistant-status");
    if (!el) return;
    el.hidden = false;
    el.textContent = message;
    el.className = "status" + (isError ? " error" : "");
  }

  function renderRecentConversations(items) {
    if (typeof document === "undefined") return;
    var container = document.getElementById("recent-conversations");
    if (!container) return;
    container.innerHTML = "";
    if (!items || items.length === 0) return;
    var heading = document.createElement("p");
    heading.className = "recent-heading";
    heading.textContent = "最近對話";
    container.appendChild(heading);
    var list = document.createElement("ul");
    list.className = "recent-list";
    items.forEach(function (item) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "/assistant/" + item.id;
      a.textContent = item.title || item.id.slice(0, 8);
      li.appendChild(a);
      list.appendChild(li);
    });
    container.appendChild(list);
  }

  function pollJob(runId, onSuccess, onError) {
    var pollCount = 0;
    var maxPolls = 120;
    function check() {
      fetch("/api/jobs/" + runId)
        .then(function(r) { return r.json(); })
        .then(function(data) {
          if (data.status === "succeeded") { onSuccess(data); return; }
          if (data.status === "needs_attention") { onError("需要驗證"); return; }
          if (data.status === "failed") { onError(data.error_code || "匯入失敗"); return; }
          pollCount++;
          if (pollCount < maxPolls) { setTimeout(check, 1000); }
          else { onError("匯入超時"); }
        })
        .catch(function() { setTimeout(check, 1000); });
    }
    check();
  }

  function setupForm() {
    var form = document.getElementById("assistant-form");
    if (!form) return;
    var startBtn = document.getElementById("assistant-start");

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var urlInput = document.getElementById("assistant-url");
      var providerSelect = document.getElementById("assistant-provider");
      var modelInput = document.getElementById("assistant-model");
      var url = urlInput.value.trim();
      var provider = providerSelect.value;
      var model = modelInput.value.trim();

      if (!validateUrl(url)) {
        renderStatus("不支援的網址，僅接受 sale.591.com.tw 與 newhouse.591.com.tw 的詳細頁", true);
        return;
      }

      if (startBtn) startBtn.disabled = true;
      renderStatus("正在建立對話...", false);

      var csrf = getCsrfToken();

      fetch("/api/conversations", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Qingpu-CSRF": csrf,
        },
        body: JSON.stringify(buildCreatePayload(provider, model)),
      })
        .then(function (r) {
          return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
        })
        .then(function (resp) {
          if (!resp.ok) {
            throw new Error(
              (resp.data && resp.data.error && resp.data.error.message) || "建立對話失敗"
            );
          }
          var convId = resp.data.id;
          renderStatus("正在擷取物件資料...", false);
          return fetch("/api/conversations/" + encodeURIComponent(convId) + "/listing", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Qingpu-CSRF": csrf,
            },
            body: JSON.stringify(buildListingPayload(url)),
          })
            .then(function (r2) {
              return r2.json().then(function (d2) {
                return { ok: r2.ok, data: d2, convId: convId };
              });
            });
        })
        .then(function (resp2) {
          if (!resp2.ok) {
            throw new Error(
              (resp2.data && resp2.data.error && resp2.data.error.message) || "擷取物件失敗"
            );
          }
          var runId = resp2.data && resp2.data.run_id;
          if (runId) {
            renderStatus("正在處理物件資料...", false);
            pollJob(runId,
              function () { window.location.href = "/assistant/" + resp2.convId; },
              function (msg) { renderStatus(msg, true); if (startBtn) startBtn.disabled = false; }
            );
          } else {
            window.location.href = "/assistant/" + resp2.convId;
          }
        })
        .catch(function (err) {
          renderStatus(err.message, true);
          if (startBtn) startBtn.disabled = false;
        });
    });
  }

  function loadRecentConversations() {
    fetch("/api/conversations?limit=5")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderRecentConversations(data.items || []);
      })
      .catch(function () {});
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        setupForm();
        loadRecentConversations();
      });
    } else {
      setupForm();
      loadRecentConversations();
    }
  }

  return {
    validateUrl: validateUrl,
    buildCreatePayload: buildCreatePayload,
    buildListingPayload: buildListingPayload,
    getCsrfToken: getCsrfToken,
    renderStatus: renderStatus,
    renderRecentConversations: renderRecentConversations,
  };
});
