"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const radar = require("../../src/qingpu_insight/static/listing_radar.js");
const radarAdmin = require("../../src/qingpu_insight/static/listing_radar_admin.js");

function fakeDocument(elements) {
  function node(tag) {
    return {
      tagName: tag.toUpperCase(),
      className: "",
      textContent: "",
      children: [],
      listeners: {},
      appendChild(child) { this.children.push(child); return child; },
      removeChild(child) { this.children.splice(this.children.indexOf(child), 1); },
      get firstChild() { return this.children[0] || null; },
      addEventListener(type, fn) { this.listeners[type] = fn; },
      set innerHTML(_value) { throw new Error("innerHTML is not allowed"); },
    };
  }
  return {
    createElement: node,
    getElementById(id) { return (elements || {})[id] || null; },
    querySelector() { return { getAttribute: () => "csrf-1" }; },
    node,
  };
}

function texts(element) {
  return [element.textContent].concat(...element.children.map(texts)).filter(Boolean);
}

// --- query building and URL safety -------------------------------------------------------
assert.equal(radar.buildRadarQuery({}), "/api/listing-radar?sort=score&limit=50");
assert.equal(
  radar.buildRadarQuery({ station: "A18", sort: "gap", limit: 10 }),
  "/api/listing-radar?station=A18&sort=gap&limit=10"
);
assert.equal(
  radar.buildRadarQuery({ station: "A20&x=1", sort: "drop", limit: 999 }),
  "/api/listing-radar?sort=score&limit=50"
);
assert.equal(
  radar.safeListingUrl("https://sale.591.com.tw/home/house/detail/2/20516622.html"),
  "https://sale.591.com.tw/home/house/detail/2/20516622.html"
);
assert.equal(radar.safeListingUrl("javascript:alert(1)"), null);
assert.equal(radar.safeListingUrl("https://evil.example/home/house/detail/2/1.html"), null);

assert.equal(radar.formatGap(-0.2222), "−22.2%");
assert.equal(radar.formatGap(0.05), "+5.0%");
assert.equal(radar.formatGap(null), "—");

// --- card rendering uses textContent only ---------------------------------------------
const item = {
  rank: 1,
  source_listing_id: "1",
  url: "https://sale.591.com.tw/home/house/detail/2/1.html",
  title: "<img src=x onerror=alert(1)>青埔三房",
  station_code: "A18",
  area_ping: 40.32,
  net_area_ping: 30,
  layout: "3房2廳2衛",
  floor: "12F/15F",
  age_years: 1.5,
  asking_price_twd: 12800000,
  estimate_twd: 18000000,
  interval_low_twd: 16000000,
  interval_high_twd: 20000000,
  gap_pct: -0.2889,
  below_interval: true,
  price_anchor: "same_building",
  common_area_source: "591_areas",
  confidence: "medium",
  flags: ["new_project", "presale_anchor", "unknown_flag"],
  reason: "明顯低於區間：開價比 90% 區間下限低 20.0%",
};
const model = radar.cardModel(item);
assert.equal(model.station, "A18 高鐵桃園站");
assert.equal(model.gap, "−28.9%");
assert.equal(model.below, true);
assert.deepEqual(model.flags, ["新成屋（屋齡未滿 2 年）", "依同棟預售／完工前成交推估"]);
assert.deepEqual(model.facts[0], ["開價", "1,280 萬"]);
assert.deepEqual(model.facts[2], ["90% 區間", "1,600 萬–2,000 萬"]);
assert.deepEqual(model.facts[6], ["估價錨點", "同棟成交錨定"]);

const doc = fakeDocument();
const card = radar.renderCard(doc, item);
const link = card.children[1];
assert.equal(link.tagName, "A");
assert.equal(link.textContent, "<img src=x onerror=alert(1)>青埔三房");
assert.equal(link.rel, "noopener noreferrer");
assert.equal(link.target, "_blank");
assert.ok(texts(card).includes("明顯低於區間：開價比 90% 區間下限低 20.0%"));

const unsafe = radar.renderCard(doc, Object.assign({}, item, { url: "javascript:alert(1)" }));
assert.equal(unsafe.children[1].tagName, "SPAN");

// --- status and list rendering ---------------------------------------------------------
assert.match(radar.statusText({ batch: null }), /尚未有雷達結果/);
const status = doc.node("section");
const list = doc.node("section");
radar.renderRadar(doc, list, status, {
  batch: {
    status: "stopped_verification",
    finished_at: "2026-10-01T04:00:00+00:00",
    counts: { valued: 12, ranked: 3, below_interval: 1 },
  },
  items: [item],
});
assert.match(status.textContent, /驗證頁/);
assert.match(status.textContent, /2026\/10\/01 12:00/);
assert.equal(radar.formatTaipeiTime(null), "—");
assert.match(status.textContent, /共估價 12 筆/);
assert.equal(list.children.length, 1);

// --- admin trigger -----------------------------------------------------------------
assert.deepEqual(radarAdmin.buildRunPayload("60", "24"), {
  payload: { max_listings: 60, refresh_hours: 24 },
});
assert.ok(radarAdmin.buildRunPayload("0", "24").error);
assert.ok(radarAdmin.buildRunPayload("1.5", "24").error);
assert.ok(radarAdmin.buildRunPayload("10", "-1").error);
assert.match(
  radarAdmin.progressText({
    status: "running",
    summary: { stage: "capturing", processed: 3, total: 60, valued: 2, captured_live: 3 },
  }),
  /處理中 3\/60/
);
assert.match(
  radarAdmin.progressText({ status: "failed", error_code: "verification_required" }),
  /驗證頁/
);
assert.match(
  radarAdmin.progressText({
    status: "succeeded",
    summary: { valued: 40, ranked: 5, below_interval: 2 },
  }),
  /估價 40 筆，候選 5 筆/
);

const adminDoc = fakeDocument();
const button = adminDoc.node("button");
const adminStatus = adminDoc.node("div");
const elements = {
  "lr-run-btn": button,
  "lr-status": adminStatus,
  "lr-max-listings": { value: "30" },
  "lr-refresh-hours": { value: "12" },
};
const docWithElements = Object.assign(adminDoc, {
  getElementById: (id) => elements[id] || null,
});
const requests = [];
let started = null;
const polling = {
  parseApiResponse: (response) => response.json(),
  createPollController: () => ({ start: (runId) => { started = runId; } }),
};
radarAdmin.init(
  docWithElements,
  (url, options) => {
    requests.push({ url, options });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ run_id: "r-1", created: true }) });
  },
  polling,
  () => {}
);
button.listeners.click();
setImmediate(() => {
  assert.equal(requests[0].url, "/api/admin/listing-radar-runs");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Qingpu-CSRF"], "csrf-1");
  assert.deepEqual(JSON.parse(requests[0].options.body), { max_listings: 30, refresh_hours: 12 });
  assert.equal(button.disabled, true);
  assert.equal(started, "r-1");

  for (const file of ["listing_radar.js", "listing_radar_admin.js"]) {
    const source = fs.readFileSync(`src/qingpu_insight/static/${file}`, "utf8");
    assert.doesNotMatch(source, /innerHTML|insertAdjacentHTML|document\.write/);
  }
  process.stdout.write("listing radar contract passed\n");
});
