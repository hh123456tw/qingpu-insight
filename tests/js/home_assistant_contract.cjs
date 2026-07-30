"use strict";

const assert = require("node:assert/strict");

// Track document-level event listeners
var docListeners = {};
var elementIdCounter = 0;

// Minimal DOM shim for testing
if (typeof document === "undefined") {
  var elProto = {
    setAttribute: function (k, v) { this._attrs = this._attrs || {}; this._attrs[k] = v; if (k === "class") this.className = v; if (k === "id") this.id = v; },
    getAttribute: function (k) { return (this._attrs && this._attrs[k]) || null; },
    appendChild: function (c) { c.parentNode = this; (this._children = this._children || []).push(c); return c; },
    removeChild: function (c) { var idx = (this._children || []).indexOf(c); if (idx >= 0) this._children.splice(idx, 1); return c; },
    addEventListener: function (type, handler) { this._listeners = this._listeners || {}; this._listeners[type] = this._listeners[type] || []; this._listeners[type].push(handler); },
    removeEventListener: function (type, handler) { if (this._listeners && this._listeners[type]) { this._listeners[type] = this._listeners[type].filter(function (h) { return h !== handler; }); } },
    dispatchEvent: function (event) { var handlers = (this._listeners && this._listeners[event.type]) || []; handlers.forEach(function (h) { h(event); }); },
    focus: function () { this._focused = true; },
    contains: function (child) {
      if (child === this) return true;
      var children = this._children || [];
      for (var i = 0; i < children.length; i++) {
        if (children[i] === child) return true;
        if (children[i].contains && children[i].contains(child)) return true;
      }
      return false;
    },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    replaceChildren: function () { if (this._children) this._children.length = 0; },
  };
  globalThis.document = {
    createElement: function (tag) {
      var children = [];
      var el = Object.assign(Object.create(elProto), {
        tagName: tag.toUpperCase(), nodeType: 1, id: "el-" + (++elementIdCounter),
        className: "", hidden: false, disabled: false,
        style: {}, parentNode: null, _children: children, _attrs: {},
      });
      Object.defineProperty(el, "innerHTML", {
        get: function () { return ""; },
        set: function (v) { children.length = 0; },
      });
      Object.defineProperty(el, "textContent", {
        get: function () { return this._textContent || ""; },
        set: function (v) {
          this._textContent = v;
          children.length = 0;
        },
        configurable: true,
      });
      return el;
    },
    createTextNode: function (text) { return { nodeType: 3, textContent: text, nodeValue: text, parentNode: null }; },
    getElementById: function () { return null; },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    documentElement: Object.assign(Object.create(elProto), { tagName: "HTML" }),
    body: Object.assign(Object.create(elProto), { tagName: "BODY" }),
    readyState: "complete",
    addEventListener: function (type, handler) { docListeners[type] = docListeners[type] || []; docListeners[type].push(handler); },
    removeEventListener: function (type, handler) { if (docListeners[type]) { docListeners[type] = docListeners[type].filter(function (h) { return h !== handler; }); } },
  };
}

var ha = require("../../src/qingpu_insight/static/home_assistant.js");

// --- truncateConversationTitle ---

assert.equal(ha.truncateConversationTitle("short", 28), "short");
assert.equal(ha.truncateConversationTitle("", 28), "");
assert.equal(ha.truncateConversationTitle("hello world this is a test", 10), "hello worl\u2026");
assert.equal(ha.truncateConversationTitle("hello world this is a test", 10).length, 11);
assert.equal(ha.truncateConversationTitle("1234567890123456789012345678", 28), "1234567890123456789012345678");
assert.equal(ha.truncateConversationTitle("12345678901234567890123456789", 28), "1234567890123456789012345678\u2026");

// --- URL validation ---

assert.equal(ha.validateUrl(null), false);
assert.equal(ha.validateUrl(""), false);
assert.equal(ha.validateUrl("not-a-url"), false);
assert.equal(ha.validateUrl("https://rent.591.com.tw/home/house/detail/123"), false);
assert.equal(ha.validateUrl("https://sale.591.com.tw/home/house/detail/2/123.html"), true);
assert.equal(ha.validateUrl("https://newhouse.591.com.tw/456/detail"), false);
assert.equal(ha.validateUrl("https://591.to/abc123"), true);
assert.equal(
  ha.validateUrl("https://sale.591.com.tw/home/house/detail/abc?from=search"),
  false
);

// --- Payload construction ---

assert.deepEqual(ha.buildCreatePayload("gemini-3.5-flash-lite"), {
  model: "gemini-3.5-flash-lite",
});

assert.deepEqual(ha.buildCreatePayload(""), { model: "" });

assert.equal(
  ha.modelStatusText(
    { provider: "gemini", cloud: true },
    { geminiConfigured: false, ollamaReady: true }
  ),
  "尚未設定 Gemini API Key；送出後將自動使用本機模型"
);
assert.equal(
  ha.modelStatusText(
    { provider: "ollama", cloud: false },
    { geminiConfigured: true, ollamaReady: false }
  ),
  "本機 Gemma 4 尚未安裝；送出後可能改用 Rule 摘要"
);
assert.equal(
  ha.modelStatusText(
    { provider: "ollama", cloud: false },
    { geminiConfigured: true, ollamaReady: true }
  ),
  "本機模式，不使用 Google API"
);

assert.deepEqual(ha.buildListingPayload("https://sale.591.com.tw/abc"), {
  url: "https://sale.591.com.tw/abc",
});

// --- CSRF token (no meta tag = empty) ---

assert.equal(ha.getCsrfToken(), "");

// With meta tag
var meta = document.createElement("meta");
meta.setAttribute("name", "csrf-token");
meta.setAttribute("content", "test-token-123");
document.querySelector = function (sel) {
  return sel === 'meta[name="csrf-token"]' ? meta : null;
};
assert.equal(ha.getCsrfToken(), "test-token-123");

// --- Status rendering ---

var statusEl = document.createElement("div");
statusEl.id = "assistant-status";
document.getElementById = function (id) {
  return id === "assistant-status" ? statusEl : null;
};

ha.renderStatus("正在載入...", false);
assert.equal(statusEl.hidden, false);
assert.equal(statusEl.textContent, "正在載入...");
assert.equal(statusEl.className, "status");

ha.renderStatus("錯誤訊息", true);
assert.equal(statusEl.textContent, "錯誤訊息");
assert.equal(statusEl.className, "status error");

// --- Recent conversations dropdown ---

var recentContainer = document.createElement("div");
recentContainer.id = "recent-conversations";
document.getElementById = function (id) {
  if (id === "assistant-status") return statusEl;
  if (id === "recent-conversations") return recentContainer;
  return null;
};

// Empty items → render nothing
ha.renderRecentConversations(null);
assert.equal(recentContainer._children.length, 0);

ha.renderRecentConversations([]);
assert.equal(recentContainer._children.length, 0);

// Non-empty items → pill + hidden menu
var testItems = [
  { id: "conv-1", title: "測試物件", created_at: "2026-07-28T10:30:00Z" },
  { id: "conv-2", title: "這是一個非常長的對話標題，肯定超過二十八個字元你確定這真的很長", created_at: "2026-07-27T14:00:00Z" },
  { id: "conv-3", created_at: "2026-07-26T08:00:00Z" },
];

// Reset doc listeners before rendering
docListeners = {};
ha.renderRecentConversations(testItems);

// Container should have button + menu
assert.equal(recentContainer._children.length, 2);
var button = recentContainer._children[0];
var menu = recentContainer._children[1];

// Pill button: text "最近對話 3", aria-expanded="false", aria-controls
assert.equal(button.tagName, "BUTTON");
assert.equal(button.textContent, "最近對話 3");
assert.equal(button.getAttribute("aria-expanded"), "false");
assert.ok(button.getAttribute("aria-controls"));

// Menu: hidden, linked by aria-controls
assert.equal(button.getAttribute("aria-controls"), menu.id);
assert.equal(menu.hidden, true);
assert.equal(menu.tagName, "DIV");

// Menu contains links
assert.ok(menu._children.length <= 5);
assert.equal(menu._children.length, 3);

// First link: no truncation needed
var link0 = menu._children[0];
assert.equal(link0.tagName, "A");
assert.equal(link0.href, "/assistant/conv-1");
var title0 = link0._children[0];
assert.equal(title0.textContent, "測試物件");
assert.equal(title0.className, "recent-item-title");

// Second link: truncated title (> 28 chars ends with …)
var link1 = menu._children[1];
assert.equal(link1.href, "/assistant/conv-2");
var title1 = link1._children[0];
assert.equal(title1.className, "recent-item-title");
assert.ok(title1.textContent.length > 28);
assert.equal(title1.textContent.slice(-1), "\u2026");

// Third link: no title, uses id.slice(0, 8)
var link2 = menu._children[2];
assert.equal(link2.href, "/assistant/conv-3");
var title2 = link2._children[0];
assert.equal(title2.className, "recent-item-title");
assert.equal(title2.textContent, "conv-3");

// Timestamps formatted as Taipei time
var time0 = link0._children[1];
assert.ok(time0);
assert.equal(time0.className, "recent-item-time");
assert.ok(time0.textContent.length > 0);

var expectedTime0 = new Date("2026-07-28T10:30:00Z").toLocaleString("zh-TW", {timeZone: "Asia/Taipei"});
assert.equal(time0.textContent, expectedTime0);

var time2 = link2._children[1];
assert.ok(time2);
assert.equal(time2.className, "recent-item-time");
var expectedTime2 = new Date("2026-07-26T08:00:00Z").toLocaleString("zh-TW", {timeZone: "Asia/Taipei"});
assert.equal(time2.textContent, expectedTime2);

// --- Click toggles menu ---

// Reset doc listeners to get fresh handlers
docListeners = {};
recentContainer._children.length = 0;
recentContainer._textContent = "";
ha.renderRecentConversations(testItems);
var pill = recentContainer._children[0];
var dropMenu = recentContainer._children[1];

assert.equal(pill.getAttribute("aria-expanded"), "false");
assert.equal(dropMenu.hidden, true);

// Simulate button click
var clickEvent = { type: "click", stopPropagation: function () {} };
pill.dispatchEvent(clickEvent);

assert.equal(pill.getAttribute("aria-expanded"), "true");
assert.equal(dropMenu.hidden, false);

// Click again to close
pill.dispatchEvent(clickEvent);

assert.equal(pill.getAttribute("aria-expanded"), "false");
assert.equal(dropMenu.hidden, true);

// --- Escape closes menu, focus returns to button ---

pill.dispatchEvent(clickEvent);
assert.equal(pill.getAttribute("aria-expanded"), "true");

var escEvent = { key: "Escape", type: "keydown" };
(docListeners["keydown"] || []).forEach(function (h) { h(escEvent); });

assert.equal(pill.getAttribute("aria-expanded"), "false");
assert.equal(dropMenu.hidden, true);
assert.equal(pill._focused, true);

// --- Outside click closes menu ---

pill.dispatchEvent(clickEvent);
assert.equal(pill.getAttribute("aria-expanded"), "true");

var outsideEvent = { type: "click", target: document.body };
(docListeners["click"] || []).forEach(function (h) { h(outsideEvent); });

assert.equal(pill.getAttribute("aria-expanded"), "false");
assert.equal(dropMenu.hidden, true);

process.stdout.write("home assistant contract passed\n");
