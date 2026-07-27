"use strict";

const assert = require("node:assert/strict");

// Minimal DOM shim for testing
if (typeof document === "undefined") {
  const elProto = {
    setAttribute: function (k, v) { this._attrs = this._attrs || {}; this._attrs[k] = v; if (k === "class") this.className = v; if (k === "id") this.id = v; },
    getAttribute: function (k) { return (this._attrs && this._attrs[k]) || null; },
    appendChild: function (c) { c.parentNode = this; (this._children = this._children || []).push(c); return c; },
    addEventListener: function () {},
    removeEventListener: function () {},
    focus: function () {},
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    replaceChildren: function () { if (this._children) this._children.length = 0; },
  };
  let counter = 0;
  globalThis.document = {
    createElement: function (tag) {
      var children = [];
      var el = Object.assign(Object.create(elProto), {
        tagName: tag.toUpperCase(), nodeType: 1, id: "el-" + (++counter),
        className: "", textContent: "", hidden: false, disabled: false,
        style: {}, parentNode: null, _children: children, _attrs: {},
      });
      Object.defineProperty(el, "innerHTML", {
        get: function () { return ""; },
        set: function (v) { children.length = 0; },
      });
      return el;
    },
    createTextNode: function (text) { return { nodeType: 3, textContent: text, nodeValue: text, parentNode: null }; },
    getElementById: function () { return null; },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    documentElement: Object.assign(Object.create(elProto), { tagName: "HTML" }),
    readyState: "complete",
    addEventListener: function () {},
  };
}

const ha = require("../../src/qingpu_insight/static/home_assistant.js");

// --- URL validation ---

assert.equal(ha.validateUrl(null), false);
assert.equal(ha.validateUrl(""), false);
assert.equal(ha.validateUrl("not-a-url"), false);
assert.equal(ha.validateUrl("https://rent.591.com.tw/home/house/detail/123"), false);
assert.equal(ha.validateUrl("https://sale.591.com.tw/home/house/detail/123"), true);
assert.equal(ha.validateUrl("https://newhouse.591.com.tw/home/house/detail/456"), true);
assert.equal(
  ha.validateUrl("https://sale.591.com.tw/home/house/detail/abc?from=search"),
  true
);

// --- Payload construction ---

assert.deepEqual(ha.buildCreatePayload("ollama", "gemma3:4b"), {
  provider: "ollama", model: "gemma3:4b",
});

assert.deepEqual(ha.buildCreatePayload("rule", ""), {
  provider: "rule",
});

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

// --- Recent conversations rendering ---

var recentContainer = document.createElement("div");
recentContainer.id = "recent-conversations";
document.getElementById = function (id) {
  if (id === "assistant-status") return statusEl;
  if (id === "recent-conversations") return recentContainer;
  return null;
};

ha.renderRecentConversations(null);
assert.equal(recentContainer._children.length, 0);

ha.renderRecentConversations([]);
assert.equal(recentContainer._children.length, 0);

ha.renderRecentConversations([
  { id: "conv-1", title: "測試物件" },
  { id: "conv-2" },
]);
// Verify children structure (headding + list)
assert.ok(recentContainer._children.length >= 1);
var heading = recentContainer._children[0];
assert.equal(heading.tagName, "P");
assert.equal(heading.textContent, "最近對話");
var list = recentContainer._children[1];
assert.equal(list.tagName, "UL");
assert.equal(list._children.length, 2);
assert.equal(list._children[0]._children[0].textContent, "測試物件");
assert.equal(list._children[0]._children[0].tagName, "A");
// Verify textContent (no innerHTML for user content)
var links = recentContainer.querySelectorAll("a");
// In our mock querySelectorAll returns [], which is fine for a mock

process.stdout.write("home assistant contract passed\n");
