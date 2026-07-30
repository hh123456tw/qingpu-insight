(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof document !== "undefined") root.QingpuHomeAssistant = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var _clickHandler = null;
  var _keydownHandler = null;

  function isNewhouseUrl(url) {
    if (!url || typeof url !== "string") return false;
    try {
      return new URL(url).hostname === "newhouse.591.com.tw";
    } catch (e) {
      return false;
    }
  }

  function validateUrl(url) {
    if (!url || typeof url !== "string") return false;
    try {
      var parsed = new URL(url);
      if (parsed.protocol !== "https:" || parsed.username || parsed.password) return false;
      if (parsed.port && parsed.port !== "443") return false;
      if (parsed.hash) return false;
      if (parsed.hostname === "591.to") {
        return /^\/[A-Za-z0-9_-]{2,64}$/.test(parsed.pathname);
      }
      if (parsed.hostname === "sale.591.com.tw") {
        return /^\/home\/house\/detail\/[1-9][0-9]*\/[1-9][0-9]*\.html$/.test(parsed.pathname);
      }
      return false;
    } catch (e) {
      return false;
    }
  }

  function urlValidationMessage(url) {
    if (isNewhouseUrl(url)) {
      return "591 預售屋分析已停用，目前只支援中古屋";
    }
    if (!validateUrl(url)) {
      return "不支援的網址，僅接受 591 中古屋詳細頁或 591.to 短網址";
    }
    return "";
  }

  function commandKey() {
    if (
      typeof crypto !== "undefined"
      && typeof crypto.randomUUID === "function"
    ) {
      return crypto.randomUUID();
    }
    return "cmd-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }

  function buildCreatePayload(model) {
    return { model: model };
  }

  function modelStatusText(item, readiness) {
    if (!item) return "";
    var state = readiness || {};
    if (item.cloud && !state.geminiConfigured) {
      return "尚未設定 Gemini API Key；送出後將自動使用本機模型";
    }
    if (item.provider === "ollama" && !state.ollamaReady) {
      return "本機 Gemma 4 尚未安裝；送出後可能改用 Rule 摘要";
    }
    if (item.provider === "ollama") {
      return "本機模式，不使用 Google API";
    }
    if (item.provider === "rule") {
      return "離線摘要，不使用 LLM";
    }
    return "雲端模型；失敗時會自動切換本機模型";
  }

  function renderModelCatalog(catalog) {
    var select = document.getElementById("assistant-model");
    var status = document.getElementById("assistant-model-help");
    var startButton = document.getElementById("assistant-start");
    if (!select) return;
    select.innerHTML = "";
    (catalog.items || []).forEach(function (item) {
      var option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label;
      select.appendChild(option);
    });
    select.value = catalog.default_model || "";
    select.disabled = false;
    if (startButton) startButton.disabled = false;

    function updateStatus() {
      if (!status) return;
      var selected = (catalog.items || []).find(function (item) {
        return item.id === select.value;
      });
      status.textContent = modelStatusText(
        selected,
        {
          geminiConfigured: Boolean(catalog.gemini_configured),
          ollamaReady: Boolean(catalog.ollama_ready),
        }
      );
    }
    select.addEventListener("change", updateStatus);
    updateStatus();
  }

  function loadModelCatalog() {
    return fetch("/api/conversation-models")
      .then(function (response) {
        if (!response.ok) throw new Error("模型清單載入失敗");
        return response.json();
      })
      .then(renderModelCatalog)
      .catch(function () {
        renderStatus("模型目錄暫時無法載入，請重新整理頁面", true);
      });
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

  function truncateConversationTitle(value, maxLength) {
    maxLength = maxLength || 28;
    if (value.length > maxLength) {
      return value.slice(0, maxLength) + "\u2026";
    }
    return value;
  }

  function renderRecentConversations(items) {
    if (typeof document === "undefined") return;
    if (_clickHandler) document.removeEventListener("click", _clickHandler, true);
    if (_keydownHandler) document.removeEventListener("keydown", _keydownHandler);
    var container = document.getElementById("recent-conversations");
    if (!container) return;
    container.textContent = "";
    if (!items || items.length === 0) return;
    var menuId = "recent-menu-" + Date.now();
    var button = document.createElement("button");
    button.className = "recent-pill";
    button.textContent = "\u6700\u8FD1\u5C0D\u8A71 " + items.length;
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", menuId);
    button.setAttribute("aria-haspopup", "true");
    container.appendChild(button);
    var menu = document.createElement("div");
    menu.id = menuId;
    menu.className = "recent-menu";
    menu.hidden = true;
    container.appendChild(menu);
    items.forEach(function (item) {
      var link = document.createElement("a");
      link.href = "/assistant/" + item.id;
      menu.appendChild(link);
      var titleSpan = document.createElement("span");
      titleSpan.className = "recent-item-title";
      titleSpan.textContent = truncateConversationTitle(item.title || item.id.slice(0, 8));
      link.appendChild(titleSpan);
      if (item.created_at) {
        var timeSpan = document.createElement("span");
        timeSpan.className = "recent-item-time";
        var date = new Date(item.created_at);
        timeSpan.textContent = date.toLocaleString("zh-TW", {timeZone: "Asia/Taipei"});
        link.appendChild(timeSpan);
      }
    });
    function toggle() {
      var expanded = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!expanded));
      menu.hidden = expanded;
    }
    button.addEventListener("click", function (e) {
      e.stopPropagation();
      toggle();
    });
    function onDocumentClick(e) {
      if (e.target !== button && !menu.contains(e.target)) {
        button.setAttribute("aria-expanded", "false");
        menu.hidden = true;
      }
    }
    function onDocumentKeydown(e) {
      if (e.key === "Escape" && button.getAttribute("aria-expanded") === "true") {
        button.setAttribute("aria-expanded", "false");
        menu.hidden = true;
        button.focus();
      }
    }
    _clickHandler = onDocumentClick;
    _keydownHandler = onDocumentKeydown;
    document.addEventListener("click", _clickHandler, true);
    document.addEventListener("keydown", _keydownHandler, true);
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
        .catch(function() {
          pollCount++;
          if (pollCount < maxPolls) setTimeout(check, 1000);
          else onError("匯入超時");
        });
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
      var modelSelect = document.getElementById("assistant-model");
      var url = urlInput.value.trim();
      var model = modelSelect.value;

      var validationMessage = urlValidationMessage(url);
      if (validationMessage) {
        renderStatus(validationMessage, true);
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
        body: JSON.stringify(buildCreatePayload(model)),
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
              "Idempotency-Key": commandKey(),
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
        loadModelCatalog();
        loadRecentConversations();
      });
    } else {
      setupForm();
      loadModelCatalog();
      loadRecentConversations();
    }
  }

  return {
    isNewhouseUrl: isNewhouseUrl,
    validateUrl: validateUrl,
    urlValidationMessage: urlValidationMessage,
    buildCreatePayload: buildCreatePayload,
    modelStatusText: modelStatusText,
    renderModelCatalog: renderModelCatalog,
    loadModelCatalog: loadModelCatalog,
    buildListingPayload: buildListingPayload,
    getCsrfToken: getCsrfToken,
    renderStatus: renderStatus,
    truncateConversationTitle: truncateConversationTitle,
    renderRecentConversations: renderRecentConversations,
  };
});
