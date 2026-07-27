"use strict";

const assert = require("node:assert/strict");

// Minimal DOM shim for testing
function collectText(root) {
  var parts = [];
  function walk(n) {
    if (n.nodeType === 3) { parts.push(n.textContent); return; }
    if (n._children) n._children.forEach(walk);
    if (n.textContent && n.nodeType === 1) parts.push(n.textContent);
  }
  walk(root);
  return parts.join(" ");
}
if (typeof document === "undefined") {
  const elProto = {
    setAttribute: function (k, v) { this._attrs = this._attrs || {}; this._attrs[k] = v; if (k === "class") this.className = v; if (k === "id") this.id = v; },
    getAttribute: function (k) { return (this._attrs && this._attrs[k]) || null; },
    appendChild: function (c) { c.parentNode = this; (this._children = this._children || []).push(c); return c; },
    addEventListener: function () {},
    removeEventListener: function () {},
    replaceChildren: function () { this._children.length = 0; },
  };
  let counter = 0;
  globalThis.document = {
    createElement: function (tag) {
      var attrs = {};
      var children = [];
      var el = Object.assign(Object.create(elProto), {
        tagName: tag.toUpperCase(), nodeType: 1, id: "el-" + (++counter),
        className: "", textContent: "", hidden: false, disabled: false, value: "",
        style: {}, parentNode: null, _children: children, _attrs: attrs,
        scrollTop: 0, scrollHeight: 0,
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
    createDocumentFragment: function () {
      var frag = { nodeType: 11, _children: [], appendChild: function (c) { this._children.push(c); return c; } };
      return frag;
    },
    readyState: "complete",
    addEventListener: function () {},
  };
}

const asst = require("../../src/qingpu_insight/static/assistant.js");

// --- stageLabel ---

assert.equal(asst.stageLabel("validating_url"), "驗證網址…");
assert.equal(asst.stageLabel("opening_browser"), "開啟瀏覽器…");
assert.equal(asst.stageLabel("capturing_listing"), "擷取物件資料…");
assert.equal(asst.stageLabel("building_evidence"), "建立證據資料…");
assert.equal(asst.stageLabel("ready"), "準備就緒");
assert.equal(asst.stageLabel("unknown_stage"), "unknown_stage");

// --- buildReplyPayload ---

assert.deepEqual(asst.buildReplyPayload("這是問題", "ollama", "gemma3:4b", 1), {
  content: "這是問題",
  provider: "ollama",
  model: "gemma3:4b",
  evidence_revision: 1,
});

assert.deepEqual(asst.buildReplyPayload("問題", "", "", null), {
  content: "問題",
});

assert.deepEqual(asst.buildReplyPayload("test", "rule", "", undefined), {
  content: "test",
  provider: "rule",
});

// --- formatMoney ---

assert.equal(asst.formatMoney(null), "—");
assert.equal(asst.formatMoney(undefined), "—");

// --- el() creates correct DOM structure ---

var div = asst.el("div", { "class": "test-div" }, [
  asst.el("span", {}, ["hello"]),
  "world",
]);
assert.equal(div.tagName, "DIV");
assert.equal(div.className, "test-div");
assert.equal(div._children.length, 2);
assert.equal(div._children[0].tagName, "SPAN");

// textContent used (no innerHTML from user content)
assert.equal(typeof div._children[0].textContent, "string");

// --- renderMessage without innerHTML ---

var msg = {
  id: "msg-1",
  role: "assistant",
  content: "這是分析結果",
  evidence_revision: 2,
};
var msgEl = asst.renderMessage(msg, 2);
assert.equal(msgEl.tagName, "DIV");
assert.ok(msgEl.className.indexOf("message-assistant") !== -1);
// Check that content is set via textContent, not innerHTML
var contentEl = msgEl._children[1];
assert.equal(contentEl.textContent, "這是分析結果");
// Ensure no XSS via innerHTML
assert.equal(typeof contentEl.textContent, "string");

// User message
var userMsg = asst.renderMessage({ role: "user", content: "<script>alert(1)</script>" }, null);
assert.equal(userMsg.className.indexOf("message-user") !== -1, true);
assert.equal(userMsg._children[1].textContent, "<script>alert(1)</script>");

// --- renderEvidencePanel ---

var panel = document.createElement("div");
panel.id = "evidence-panel";
var panelGetId = 0;
document.getElementById = function (id) { return id === "evidence-panel" ? panel : null; };

// No data
asst.renderEvidencePanel(null);
assert.ok(panel._children.length >= 1);
assert.ok(panel._children[0].className.indexOf("empty-evidence") !== -1);

// With facts
asst.renderEvidencePanel({
  revision: 1,
  generated_at: "2025-06-15T10:00:00",
  facts: {
    title: "青埔美宅",
    total_price_twd: 15000000,
    unit_price_twd_per_ping: 350000,
    area_ping: "42.8",
    layout: "3房2廳2衛",
    address: "桃園市中壢區",
    community_name: "青埔花園",
    building_type: "住宅大樓",
    floor: "8",
    total_floors: 15,
    age_years: "3",
    parking_type: "坡道平面",
    listing_type: "sale",
  },
  valuation: null,
  comparables: [],
  limitations: ["此為 AI 輔助分析，不構成購屋建議"],
});

// Find text content across all children
var allText = collectText(panel);
assert.ok(allText.indexOf("青埔美宅") !== -1);
assert.ok(allText.indexOf("物件基本資料") !== -1);
assert.ok(allText.indexOf("注意事項") !== -1);

// With valuation
asst.renderEvidencePanel({
  revision: 2,
  generated_at: "2025-06-15T10:00:00",
  facts: { title: "測試", total_price_twd: 10000000 },
  valuation: { estimated_total_price_twd: 9800000, interval_total_price_twd: [9000000, 10500000] },
  comparables: [
    { address: "青埔一路", total_price_twd: 12000000, area_ping: "40", unit_price_twd_per_ping: 300000 },
  ],
  limitations: [],
});

var valText = collectText(panel);
assert.ok(valText.indexOf("估價") !== -1);
assert.ok(valText.indexOf("近期成交") !== -1);

// --- getCsrfToken ---

assert.equal(asst.getCsrfToken(), ""); // no meta set in mock

// --- sendReply payload shape ---

var payload = asst.buildReplyPayload("HELLO", "gemini", "gemini-2.0-flash", 3);
assert.equal(payload.content, "HELLO");
assert.equal(payload.provider, "gemini");
assert.equal(payload.model, "gemini-2.0-flash");
assert.equal(payload.evidence_revision, 3);

process.stdout.write("assistant contract passed\n");
