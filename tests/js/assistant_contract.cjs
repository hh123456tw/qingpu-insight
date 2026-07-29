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

const display = require("../../src/qingpu_insight/static/display_format.js");
const asst = require("../../src/qingpu_insight/static/assistant.js");

// --- stageLabel ---

assert.equal(asst.stageLabel("validating_url"), "驗證網址…");
assert.equal(asst.stageLabel("opening_browser"), "開啟瀏覽器…");
assert.equal(asst.stageLabel("capturing_listing"), "擷取物件資料…");
assert.equal(asst.stageLabel("building_evidence"), "建立證據資料…");
assert.equal(asst.stageLabel("ready"), "準備就緒");
assert.equal(asst.stageLabel("unknown_stage"), "unknown_stage");
assert.match(
  asst.formatTaipeiDatetime("2026-07-28T00:00:00Z"),
  /2026\/7\/28.*8:00:00/
);
assert.equal(asst.formatTaipeiDatetime(null), "—");

// --- buildReplyPayload ---

assert.deepEqual(asst.buildReplyPayload("這是問題", 1), {
  content: "這是問題",
  evidence_revision: 1,
});

assert.deepEqual(asst.buildReplyPayload("問題", null), {
  content: "問題",
});

assert.deepEqual(asst.buildReplyPayload("test", undefined), {
  content: "test",
});

assert.equal(asst.fallbackLabel({
  provider: "ollama",
  model: "gemma4:e2b",
  fallback_reason: "cloud_timeout",
}), "Gemini 連線逾時，已改用本機 Gemma 4");
assert.equal(
  asst.actualModelLabel({ provider: "rule", model: "rule" }),
  "Rule／離線摘要"
);
assert.equal(
  asst.fallbackLabel({ fallback_reason: "unknown" }),
  "已自動切換備援模型"
);

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
  requested_model: "gemini-3.5-flash-lite",
  provider: "ollama",
  model: "gemma4:e2b",
  fallback_reason: "cloud_timeout",
};
var msgEl = asst.renderMessage(msg, 2);
assert.equal(msgEl.tagName, "DIV");
assert.ok(msgEl.className.indexOf("message-assistant") !== -1);
// Check that content is set via textContent, not innerHTML
var contentEl = msgEl._children[1];
assert.equal(contentEl.textContent, "這是分析結果");
// Ensure no XSS via innerHTML
assert.equal(typeof contentEl.textContent, "string");
var messageText = collectText(msgEl);
assert.ok(messageText.indexOf("本機 Gemma 4") !== -1);
assert.ok(messageText.indexOf("Gemini 連線逾時，已改用本機 Gemma 4") !== -1);

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
  valuation: { estimated_total_price_twd: 9800000, interval_total_price_twd: [9000000, 10500000], confidence: "high" },
  comparables: [
    { address: "青埔一路", total_price_twd: 12000000, area_ping: "40", unit_price_twd_per_ping: 300000 },
  ],
  limitations: [],
});

var valText = collectText(panel);
assert.ok(valText.indexOf("估價") !== -1);
assert.ok(valText.indexOf("信心度") !== -1);
assert.ok(valText.indexOf("高") !== -1);
assert.ok(valText.indexOf("近期成交") !== -1);

// --- getCsrfToken ---

assert.equal(asst.getCsrfToken(), ""); // no meta set in mock

// --- sendReply payload shape ---

var payload = asst.buildReplyPayload("HELLO", 3);
assert.equal(payload.content, "HELLO");
assert.equal(payload.provider, undefined);
assert.equal(payload.model, undefined);
assert.equal(payload.evidence_revision, 3);

// --- stripLegacyInlineCitations ---

assert.equal(
  asst.stripLegacyInlineCitations("開價 2,298 萬（依據：listing.price）"),
  "開價 2,298 萬"
);
assert.equal(
  asst.stripLegacyInlineCitations("（依據：listing.price、listing.area）"),
  ""
);
assert.equal(
  asst.stripLegacyInlineCitations("沒有標註"),
  "沒有標註"
);
assert.equal(asst.stripLegacyInlineCitations(null), "");
assert.equal(asst.stripLegacyInlineCitations(undefined), "");

// --- renderCitationDetails ---

var details = asst.renderCitationDetails(["listing.price", "listing.area"]);
assert.equal(details.tagName, "DETAILS");
assert.equal(details.className, "message-citations");
assert.equal(details._children[0].tagName, "SUMMARY");
// summary has a text child node
assert.equal(details._children[0]._children[0].textContent, "查看資料依據（2）");
assert.equal(details._children[1].tagName, "UL");
assert.equal(details._children[1]._children.length, 2);
assert.equal(details._children[1]._children[0].textContent, "listing.price");
assert.equal(details._children[1]._children[1].textContent, "listing.area");

assert.equal(asst.renderCitationDetails([]), null);
assert.equal(asst.renderCitationDetails(null), null);

var localizedDetails = asst.renderCitationDetails(
  ["listing.price"],
  [{
    id: "listing.price",
    label: "591 開價",
    value: "1,350 萬",
    source: "591",
  }]
);
assert.ok(collectText(localizedDetails).indexOf("591 開價") !== -1);
assert.ok(collectText(localizedDetails).indexOf("1,350 萬") !== -1);
assert.ok(collectText(localizedDetails).indexOf("listing.price") === -1);
assert.equal(localizedDetails.getAttribute("open"), null);

// --- renderMessage with citations accordion ---

var msgWithCitations = {
  role: "assistant",
  content: "開價 22,980,000 元（依據：listing.price）",
  citations: ["listing.price", "listing.area"],
};
var msgEl2 = asst.renderMessage(msgWithCitations, null);
var contentDiv2 = msgEl2._children[1];
assert.equal(contentDiv2.className, "message-content");
assert.ok(contentDiv2.textContent.indexOf("listing.price") === -1,
  "message-content should not contain inline citation");
assert.ok(contentDiv2.textContent.indexOf("2,298 萬") !== -1,
  "message-content should show 萬-formatted price");
var detailsEl = msgEl2._children[2];
assert.equal(detailsEl.tagName, "DETAILS");
assert.equal(detailsEl.className, "message-citations");
assert.equal(detailsEl._children[0]._children[0].textContent, "查看資料依據（2）");
assert.equal(detailsEl.getAttribute("open"), null,
  "details should not be open by default");

// Message without citations should not add details
var msgNoCitations = { role: "user", content: "hello" };
var msgEl3 = asst.renderMessage(msgNoCitations, null);
assert.equal(msgEl3._children.length, 2); // header + content only
var userVerbatim = asst.renderMessage({
  role: "user",
  content: "我的預算是 1 元（依據：我自己）",
}, null);
assert.equal(userVerbatim._children[1].textContent, "我的預算是 1 元（依據：我自己）");

// --- renderMessage with price-position ---

var msgWithPrice = {
  role: "assistant",
  content: "此物件在合理價格區間內",
  price_low: 15380000,
  price_point: 19890000,
  price_high: 24410000,
  price_asking: 22980000,
};
var msgEl4 = asst.renderMessage(msgWithPrice, null);
var ppDiv = msgEl4._children[2];
assert.equal(ppDiv.className, "price-position");
assert.ok(ppDiv.getAttribute("role") === "img");
// Should have price-range-track child
var track = ppDiv._children[0];
assert.equal(track.className, "price-range-track");
// Should have price-marker children
assert.ok(track._children.length >= 2);

// --- renderMessage with deterministic price summary ---

var msgWithSummary = {
  role: "assistant",
  content: "請核對同社區近期成交案例。",
  citations: ["listing.price"],
  citation_details: [{
    id: "listing.price",
    label: "591 開價",
    value: "1,350 萬",
    source: "591",
  }],
  price_summary: {
    asking_twd: 13500000,
    low_twd: 14034000,
    point_twd: 17546000,
    high_twd: 21058000,
    position: "below",
    gap_twd: 534000,
    gap_percent: 3.8,
    confidence: "low",
    confidence_reason: "估價區間較寬",
  },
};
var summaryMessage = asst.renderMessage(msgWithSummary, null);
assert.equal(summaryMessage._children[1].className,
  "reply-price-summary price-summary-below");
var summaryText = collectText(summaryMessage._children[1]);
assert.ok(summaryText.indexOf("低於估價下限 53.4 萬（3.8%）") !== -1);
assert.ok(summaryText.indexOf("591 顯示的是開價，不代表最後成交價。") !== -1);
assert.equal(summaryMessage._children[3].tagName, "DETAILS");
assert.equal(summaryMessage._children[3].getAttribute("open"), null);

// Historical evidence keeps precise TWD strings and low/high estimate fields.
asst.renderEvidencePanel({
  revision: 1,
  facts: [
    { id: "listing.price", label: "開價總價", value: "22,000,000 元", source: "591" },
    { id: "listing.unit_price", label: "單價", value: "519,100 元/坪", source: "591" },
  ],
  valuation: {
    point_estimate_twd: 24714994,
    low_estimate_twd: 19190114,
    high_estimate_twd: 30239875,
    confidence: "low",
  },
  comparables: [],
  limitations: [],
});
var historicalText = collectText(panel);
assert.ok(historicalText.indexOf("2,200 萬") !== -1);
assert.ok(historicalText.indexOf("51.9 萬／坪") !== -1);
assert.ok(historicalText.indexOf("信心度：低") !== -1);
assert.ok(panel._children.some(function (section) {
  return section._children && section._children.some(function (child) {
    return child.className === "price-position";
  });
}));

process.stdout.write("assistant contract passed\n");
