(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof document !== "undefined") root.QingpuAssistant = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function el(tag, attrs, children) {
    if (typeof document === "undefined") return null;
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    }
    if (children) {
      (Array.isArray(children) ? children : [children]).forEach(function (c) {
        if (typeof c === "string") {
          node.appendChild(document.createTextNode(c));
        } else if (c) {
          node.appendChild(c);
        }
      });
    }
    return node;
  }

  function getCsrfToken() {
    if (typeof document === "undefined") return "";
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
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

  function formatMoney(value) {
    if (value == null) return "\u2014";
    return new Intl.NumberFormat("zh-TW", {
      style: "currency", currency: "TWD", maximumFractionDigits: 0,
    }).format(value);
  }

  var STAGE_LABELS = {
    validating_url: "\u9a57\u8b49\u7db2\u5740\u2026",
    opening_browser: "\u958b\u555f\u700f\u89bd\u5668\u2026",
    capturing_listing: "\u64f7\u53d6\u7269\u4ef6\u8cc7\u6599\u2026",
    building_evidence: "\u5efa\u7acb\u8b49\u64da\u8cc7\u6599\u2026",
    ready: "\u6e96\u5099\u5c31\u7dd2",
    preparing_evidence: "\u6e96\u5099\u8b49\u64da\u8cc7\u6599\u2026",
    asking_provider: "\u8a62\u554f AI \u6a21\u578b\u2026",
    validating_citations: "\u9a57\u8b49\u5f15\u7528\u2026",
  };

  function stageLabel(stage) {
    return STAGE_LABELS[stage] || stage;
  }

  function buildReplyPayload(content, evidenceRevision) {
    var payload = { content: content };
    if (evidenceRevision != null) payload.evidence_revision = evidenceRevision;
    return payload;
  }

  var FALLBACK_REASON_LABELS = {
    cloud_timeout: "Gemini 連線逾時",
    cloud_rate_limited: "Gemini 暫時達到使用限制",
    cloud_unavailable: "Gemini 暫時無法使用",
    cloud_auth_failed: "Gemini API Key 無法使用",
    cloud_invalid_response: "Gemini 回覆未通過資料驗證",
    local_unavailable: "本機模型不可用，已切換 Rule",
  };

  function actualModelLabel(message) {
    if (message.provider === "rule") return "Rule／離線摘要";
    if (message.provider === "ollama") return "本機 Gemma 4";
    return message.model || message.provider || "未知模型";
  }

  function fallbackLabel(message) {
    var reason = message.fallback_reason;
    var base = FALLBACK_REASON_LABELS[reason];
    if (!base) return "已自動切換備援模型";
    if (reason === "local_unavailable") {
      return "本機 Ollama 無法使用，已改用離線摘要";
    }
    if (message.provider === "ollama") {
      return base + "，已改用本機 Gemma 4";
    }
    if (message.provider === "rule") {
      return base + "，已改用離線摘要";
    }
    return base;
  }

  function renderEvidencePanel(data) {
    if (typeof document === "undefined") return;
    var panel = document.getElementById("evidence-panel");
    if (!panel) return;
    panel.innerHTML = "";

    if (!data || !data.facts) {
      panel.appendChild(el("p", { "class": "empty-evidence" }, ["\u5c1a\u7121\u7269\u4ef6\u8cc7\u6599"]));
      return;
    }

    var facts = data.facts;
    var valuation = data.valuation;
    var comparables = data.comparables || [];
    var limitations = data.limitations || [];

    if (data.generated_at) {
      panel.appendChild(el("div", { "class": "evidence-meta" }, [
        el("span", { "class": "evidence-revision" }, ["\u8b49\u64da\u7248\u672c rev" + (data.revision || "?")]),
        el("span", { "class": "evidence-timestamp" }, [
          "\u7522\u751f\u6642\u9593 " + new Date(data.generated_at).toLocaleString("zh-TW"),
        ]),
      ]));
    }

    var factItems = [];
    if (Array.isArray(facts)) {
      facts.filter(function (fact) {
        return fact && typeof fact.id === "string" && fact.id.indexOf("listing.") === 0;
      }).forEach(function (fact) {
        factItems.push({
          k: fact.label || fact.id,
          v: fact.value == null ? "\u2014" : String(fact.value),
          source: fact.source || "",
        });
      });
    } else {
      if (facts.title) factItems.push({ k: "\u6a19\u984c", v: facts.title });
      if (facts.total_price_twd) factItems.push({ k: "\u7e3d\u50f9", v: formatMoney(facts.total_price_twd) });
      if (facts.unit_price_twd_per_ping) factItems.push({ k: "\u55ae\u50f9", v: formatMoney(facts.unit_price_twd_per_ping) + "/\u576a" });
      if (facts.area_ping) factItems.push({ k: "\u576a\u6578", v: facts.area_ping + " \u576a" });
      if (facts.layout) factItems.push({ k: "\u683c\u5c40", v: facts.layout });
      if (facts.address) factItems.push({ k: "\u5730\u5740", v: facts.address });
    }

    if (factItems.length) {
      var factEls = factItems.map(function (f) {
        var children = [el("strong", {}, [f.k + "\uff1a"]), " " + f.v];
        if (f.source) children.push(el("small", { "class": "fact-source" }, [" " + f.source]));
        return el("p", {}, children);
      });
      panel.appendChild(el("section", { "class": "evidence-facts" }, [
        el("h3", {}, ["\u7269\u4ef6\u57fa\u672c\u8cc7\u6599"]),
        el("div", { "class": "facts-grid" }, factEls),
      ]));
    }

    if (valuation) {
      var valChildren = [el("h3", {}, ["\u4f30\u50f9"])];
      var pointEstimate = valuation.point_estimate_twd || valuation.estimated_total_price_twd;
      if (pointEstimate) {
        valChildren.push(el("p", { "class": "val-price" }, [formatMoney(pointEstimate)]));
      }
      if (valuation.interval_total_price_twd) {
        valChildren.push(el("p", { "class": "val-range" }, [
          "\u5408\u7406\u5340\u9593\uff1a" + formatMoney(valuation.interval_total_price_twd[0]) +
          " ~ " + formatMoney(valuation.interval_total_price_twd[1]),
        ]));
      }
      panel.appendChild(el("section", { "class": "evidence-valuation" }, valChildren));
    }

    if (comparables.length) {
      var table = el("table", { "class": "comparable-table" }, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", {}, ["\u4ea4\u6613\u65e5\u671f"]),
            el("th", {}, ["\u7e3d\u50f9"]),
            el("th", {}, ["\u8ddd\u96e2"]),
            el("th", {}, ["\u55ae\u50f9"]),
          ]),
        ]),
        el("tbody", {}, comparables.slice(0, 5).map(function (c) {
          return el("tr", {}, [
            el("td", {}, [c.transaction_date || "\u2014"]),
            el("td", {}, [formatMoney(c.price_twd || c.total_price_twd)]),
            el("td", {}, [c.distance_m != null ? c.distance_m + "m" : "\u2014"]),
            el("td", {}, [formatMoney(c.unit_price_per_ping_twd || c.unit_price_twd_per_ping)]),
          ]);
        })),
      ]);
      panel.appendChild(el("section", { "class": "evidence-comparables" }, [
        el("h3", {}, ["\u8fd1\u671f\u6210\u4ea4"]),
        table,
      ]));
    }

    if (limitations.length) {
      panel.appendChild(el("section", { "class": "evidence-limitations" }, [
        el("h3", {}, ["\u6ce8\u610f\u4e8b\u9805"]),
        el("ul", {}, limitations.map(function (l) {
          return el("li", {}, [l]);
        })),
      ]));
    }
  }

  function renderMessage(msg, currentEvidenceRevision) {
    var messageClass = "message message-" + msg.role;
    if (msg.role === "assistant" && msg.provider === "rule") {
      messageClass += " offline-summary";
    }
    var div = el("div", { "class": messageClass });
    var header = el("div", { "class": "message-header" });
    var roleLabel = msg.role === "user" ? "\u4f60" : "\u52a9\u7406";
    header.appendChild(el("span", { "class": "message-role" }, [roleLabel]));
    if (msg.role === "assistant" && msg.provider) {
      header.appendChild(el("span", { "class": "message-provider" }, [
        "實際：" + actualModelLabel(msg),
      ]));
    }
    if (msg.role === "assistant" && msg.evidence_revision != null) {
      var badge = "\u8b49\u64da\u7248\u672c rev" + msg.evidence_revision;
      if (msg.evidence_revision === currentEvidenceRevision) {
        badge += " (\u76ee\u524d)";
      }
      header.appendChild(el("span", { "class": "revision-badge" }, [badge]));
    }
    div.appendChild(header);
    var content = el("div", { "class": "message-content" });
    content.textContent = msg.content;
    div.appendChild(content);
    if (msg.role === "assistant" && msg.fallback_reason) {
      div.appendChild(el("p", {
        "class": "fallback-notice",
        "role": "status",
      }, [fallbackLabel(msg)]));
    }
    return div;
  }

  function renderSuggestedQuestions(questions, onClick) {
    if (typeof document === "undefined") return;
    var container = document.getElementById("suggested-questions");
    if (!container) return;
    container.innerHTML = "";
    if (!questions || !questions.length) return;
    questions.forEach(function (q) {
      var btn = el("button", { "class": "suggested-q-btn", "type": "button" }, [q]);
      btn.addEventListener("click", function () { onClick(q); });
      container.appendChild(btn);
    });
  }

  function updateJobStages(runId, stageContainer) {
    if (!runId || !stageContainer) return null;
    if (typeof fetch === "undefined") return null;

    var poller = null;
    if (root.QingpuJobPolling) {
      poller = root.QingpuJobPolling.createPollController({
        fetchJob: function (id) {
          return fetch("/api/jobs/" + encodeURIComponent(id));
        },
        schedule: function (cb, delay) { setTimeout(cb, delay); },
        onUpdate: function (data) {
          var stage = data.summary && data.summary.stage;
          if (stage) {
            stageContainer.textContent = stageLabel(stage);
            stageContainer.className = "stage-indicator";
          }
          if (data.status === "succeeded") {
            stageContainer.textContent = "\u5b8c\u6210";
            stageContainer.className = "stage-indicator stage-done";
          } else if (data.status === "failed") {
            stageContainer.textContent = "\u5931\u6557\uff1a" + (data.error_code || "unknown");
            stageContainer.className = "stage-indicator stage-error";
          } else if (data.status === "needs_attention") {
            stageContainer.textContent = "\u9700\u8981\u9a57\u8b49";
            stageContainer.className = "stage-indicator stage-attention";
          }
        },
        onStop: function () {},
        minDelay: 1000,
        maxDelay: 5000,
        maxAttempts: 120,
        maxFailures: 5,
      });
      poller.start(runId);
    }
    return poller;
  }

  function loadConversation(id) {
    return fetch("/api/conversations/" + encodeURIComponent(id))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error.message || "\u7121\u6cd5\u8f09\u5165\u5c0d\u8a71");
        return data;
      });
  }

  function loadMessages(id, before) {
    var url = "/api/conversations/" + encodeURIComponent(id) + "/messages?limit=50";
    if (before != null) url += "&before=" + before;
    return fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error.message || "\u7121\u6cd5\u8f09\u5165\u8a0a\u606f");
        return data;
      });
  }

  function loadEvidence(id) {
    return fetch("/api/conversations/" + encodeURIComponent(id) + "/evidence")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) return null;
        return data;
      });
  }

  function sendReply(id, content, evidenceRevision) {
    var payload = buildReplyPayload(content, evidenceRevision);
    return fetch("/api/conversations/" + encodeURIComponent(id) + "/replies", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Qingpu-CSRF": getCsrfToken(),
        "Idempotency-Key": commandKey(),
      },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
      })
      .then(function (resp) {
        if (!resp.ok) {
          var errMsg = resp.data && resp.data.error && resp.data.error.message;
          if (resp.status === 409) throw new Error(errMsg || "\u5df2\u6709\u56de\u8986\u6b63\u5728\u751f\u6210");
          throw new Error(errMsg || "\u50b3\u9001\u5931\u6557");
        }
        return resp.data;
      });
  }

  function refreshListing(id) {
    return fetch("/api/conversations/" + encodeURIComponent(id) + "/refresh", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Qingpu-CSRF": getCsrfToken(),
        "Idempotency-Key": commandKey(),
      },
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error.message || "\u91cd\u65b0\u64f7\u53d6\u5931\u6557");
        return data;
      });
  }

  function deleteConversation(id) {
    return fetch("/api/conversations/" + encodeURIComponent(id), {
      method: "DELETE",
      headers: {
        "X-Qingpu-CSRF": getCsrfToken(),
        "X-Qingpu-Confirm": "delete:" + id,
      },
    }).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (d) {
          throw new Error((d.error && d.error.message) || "\u522a\u9664\u5931\u6557");
        });
      }
      return true;
    });
  }

  function pollForJob(runId, stageContainer) {
    return new Promise(function (resolve, reject) {
      var attempts = 0;
      function check() {
        fetch("/api/jobs/" + encodeURIComponent(runId))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            attempts++;
            var stage = data.summary && data.summary.stage;
            if (stageContainer && stage) {
              stageContainer.textContent = stageLabel(stage);
              stageContainer.className = "stage-indicator";
            }
            if (data.status === "succeeded") {
              resolve(data);
            } else if (data.status === "failed" || data.status === "interrupted") {
              reject(new Error(data.error_code || "\u4f5c\u696d\u5931\u6557"));
            } else if (data.status === "needs_attention") {
              reject(new Error("needs_attention"));
            } else if (attempts > 120) {
              reject(new Error("\u904e\u6642"));
            } else {
              setTimeout(check, 1000);
            }
          })
          .catch(function () {
            attempts++;
            if (attempts > 5) reject(new Error("\u7121\u6cd5\u9023\u7dda"));
            else setTimeout(check, 2000);
          });
      }
      check();
    });
  }

  function setupWorkbench() {
    var appEl = document.getElementById("app");
    if (!appEl) return;
    var conversationId = appEl.getAttribute("data-conversation-id");
    if (!conversationId) return;

    var messagesContainer = document.getElementById("messages-container");
    var evidencePanel = document.getElementById("evidence-panel");
    var questionInput = document.getElementById("question-input");
    var sendBtn = document.getElementById("send-btn");
    var refreshBtn = document.getElementById("refresh-btn");
    var deleteBtn = document.getElementById("delete-btn");
    var loadOlderBtn = document.getElementById("load-older-btn");
    var titleEl = document.querySelector(".conversation-title");
    var providerBadge = document.querySelector(".provider-badge");
    var stageContainer = document.getElementById("stage-indicator");

    var conversation = null;
    var sending = false;
    var pendingText = "";
    var oldestSequence = null;

    function loadAndRender() {
      loadConversation(conversationId).then(function (data) {
        conversation = data;
        if (titleEl) titleEl.textContent = data.title || "\u65b0\u7684\u7269\u4ef6\u5206\u6790";
        if (providerBadge) {
          providerBadge.textContent = data.default_model
            ? "回答模型：" + data.default_model
            : "";
        }
        if (sendBtn) {
          sendBtn.disabled = (
            data.status !== "ready"
            || data.default_provider === "rule"
          );
        }
        if (questionInput && data.default_provider === "rule") {
          questionInput.disabled = true;
          questionInput.placeholder = "Rule 模式只顯示離線證據摘要";
        }
        return loadEvidence(conversationId);
      }).then(function (evData) {
        if (evidencePanel) {
          renderEvidencePanel(evData);
        }
        if (conversation && conversation.suggested_questions && conversation.suggested_questions.length) {
          renderSuggestedQuestions(conversation.suggested_questions, function (q) {
            if (questionInput) {
              questionInput.value = q;
              handleSend();
            }
          });
        }
        return loadMessages(conversationId);
      }).then(function (msgData) {
        if (!messagesContainer) return;
        messagesContainer.innerHTML = "";
        var msgs = msgData.items || [];
        oldestSequence = msgs.length
          ? Math.min.apply(null, msgs.map(function (msg) {
            return msg.sequence_no;
          }))
          : null;
        if (loadOlderBtn) {
          loadOlderBtn.hidden = msgs.length < 50;
        }
        var frag = document.createDocumentFragment();
        msgs.slice().reverse().forEach(function (msg) {
          frag.appendChild(renderMessage(msg, conversation ? conversation.active_evidence_revision : null));
        });
        messagesContainer.appendChild(frag);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
      }).catch(function (err) {
        if (evidencePanel) {
          evidencePanel.innerHTML = "";
          evidencePanel.appendChild(
            el("p", { "class": "error" }, ["\u8f09\u5165\u5931\u6557\uff1a" + err.message])
          );
        }
      });
    }

    if (loadOlderBtn) {
      loadOlderBtn.addEventListener("click", function () {
        if (oldestSequence == null || !messagesContainer) return;
        loadOlderBtn.disabled = true;
        loadMessages(conversationId, oldestSequence)
          .then(function (data) {
            var older = data.items || [];
            var fragment = document.createDocumentFragment();
            older.slice().reverse().forEach(function (msg) {
              fragment.appendChild(renderMessage(
                msg,
                conversation
                  ? conversation.active_evidence_revision
                  : null
              ));
            });
            messagesContainer.insertBefore(
              fragment,
              messagesContainer.firstChild
            );
            if (older.length) {
              oldestSequence = Math.min.apply(
                null,
                older.map(function (msg) {
                  return msg.sequence_no;
                })
              );
            }
            loadOlderBtn.hidden = older.length < 50;
          })
          .catch(function (error) {
            alert("\u8f09\u5165\u8f03\u65e9\u8a0a\u606f\u5931\u6557\uff1a" + error.message);
          })
          .finally(function () {
            loadOlderBtn.disabled = false;
          });
      });
    }

    function handleSend() {
      if (!questionInput || !sendBtn || sending) return;
      var content = questionInput.value.trim() || pendingText;
      if (!content) return;

      sending = true;
      sendBtn.disabled = true;
      pendingText = content;
      questionInput.value = "";

      if (stageContainer) {
        stageContainer.textContent = "\u6e96\u5099\u8b49\u64da\u8cc7\u6599\u2026";
        stageContainer.className = "stage-indicator";
      }

      sendReply(
        conversationId,
        content,
        conversation ? conversation.active_evidence_revision : undefined
      )
        .then(function (result) {
          if (result && result.run_id) {
          }
          return result.run_id
            ? pollForJob(result.run_id, stageContainer)
            : Promise.resolve();
        })
        .then(function () {
          pendingText = "";
          return loadAndRender();
        })
        .then(function () {
          sending = false;
        })
        .catch(function (err) {
          if (err.message === "needs_attention") {
            if (stageContainer) {
              stageContainer.innerHTML = "";
              stageContainer.appendChild(
                el("span", {}, ["\u9700\u8981\u9a57\u8b49"])
              );
              var retryBtn = el("button", { "class": "retry-btn" }, ["\u91cd\u8a66"]);
              retryBtn.addEventListener("click", function () {
                handleSend();
              });
              stageContainer.appendChild(retryBtn);
            }
            if (sendBtn) sendBtn.disabled = false;
            sending = false;
          } else {
            pendingText = "";
            if (stageContainer) {
              stageContainer.textContent = "\u932f\u8aa4\uff1a" + err.message;
              stageContainer.className = "stage-indicator stage-error";
            }
            if (sendBtn) sendBtn.disabled = false;
            sending = false;
          }
        });
    }

    if (refreshBtn) {
      refreshBtn.addEventListener("click", function () {
        refreshBtn.disabled = true;
        if (stageContainer) {
          stageContainer.textContent = "\u6b63\u5728\u91cd\u65b0\u64f7\u53d6\u2026";
          stageContainer.className = "stage-indicator";
        }
        refreshListing(conversationId)
          .then(function (result) {
            if (result && result.run_id) {
            }
            return result.run_id
              ? pollForJob(result.run_id, stageContainer)
              : Promise.resolve();
          })
          .then(function () {
            loadAndRender();
            if (refreshBtn) refreshBtn.disabled = false;
          })
          .catch(function (err) {
            if (stageContainer) {
              if (err.message === "needs_attention") {
                stageContainer.innerHTML = "";
                stageContainer.appendChild(el("span", {}, ["\u9700\u8981\u9a57\u8b49"]));
              } else {
                stageContainer.textContent = "\u91cd\u65b0\u64f7\u53d6\u5931\u6557\uff1a" + err.message;
                stageContainer.className = "stage-indicator stage-error";
              }
            }
            if (refreshBtn) refreshBtn.disabled = false;
          });
      });
    }

    if (deleteBtn) {
      deleteBtn.addEventListener("click", function () {
        if (!confirm("\u78ba\u5b9a\u8981\u522a\u9664\u6b64\u5c0d\u8a71\uff1f")) return;
        deleteBtn.disabled = true;
        deleteConversation(conversationId)
          .then(function () { window.location.href = "/"; })
          .catch(function (err) {
            alert("\u522a\u9664\u5931\u6557\uff1a" + err.message);
            deleteBtn.disabled = false;
          });
      });
    }

    if (questionInput) {
      questionInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          handleSend();
        }
      });
    }

    if (sendBtn) {
      sendBtn.addEventListener("click", handleSend);
    }

    loadAndRender();
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", setupWorkbench);
    } else {
      setupWorkbench();
    }
  }

  return {
    el: el,
    getCsrfToken: getCsrfToken,
    formatMoney: formatMoney,
    stageLabel: stageLabel,
    buildReplyPayload: buildReplyPayload,
    actualModelLabel: actualModelLabel,
    fallbackLabel: fallbackLabel,
    renderEvidencePanel: renderEvidencePanel,
    renderMessage: renderMessage,
    renderSuggestedQuestions: renderSuggestedQuestions,
    updateJobStages: updateJobStages,
    loadConversation: loadConversation,
    loadMessages: loadMessages,
    loadEvidence: loadEvidence,
    sendReply: sendReply,
    refreshListing: refreshListing,
    deleteConversation: deleteConversation,
  };
});
