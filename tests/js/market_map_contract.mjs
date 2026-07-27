import assert from "node:assert/strict";
import {
  createMapLoader,
  mapStatusText,
  markerRadius,
  transactionItemsToMapPayload,
  withMapView,
  withRecentLimit,
} from "../../src/qingpu_insight/static/market_map.mjs";

const base = new URLSearchParams();
base.set("transaction_type", "resale");
base.append("station", "A17");
base.append("station", "A18");

const recent = withRecentLimit(base);
assert.equal(recent.get("limit"), "100");
assert.deepEqual(recent.getAll("station"), ["A17", "A18"]);
assert.equal(base.has("limit"), false);

const mapParams = withMapView(base, {
  zoom: 14,
  south: 24.9,
  west: 121.0,
  north: 25.1,
  east: 121.3,
});
assert.equal(
  mapParams.toString(),
  "transaction_type=resale&station=A17&station=A18&zoom=14" +
    "&south=24.9&west=121&north=25.1&east=121.3"
);

assert.equal(
  mapStatusText({
    total_records: 16293,
    located_records: 16120,
    unlocated_records: 173,
    group_count: 84,
  }),
  "符合 16,293 筆｜有座標 16,120 筆｜未定位 173 筆｜目前顯示 84 個群組"
);
assert.equal(markerRadius(1), 5);
assert.equal(markerRadius(10000), 16);

// --- transactionItemsToMapPayload conversion ---

const converted = transactionItemsToMapPayload([
  {
    id: "valid",
    latitude: 25.01,
    longitude: 121.21,
    total_price: 1800,
    transaction_date: "2026-07-01",
  },
  {id: "nan", latitude: "not-a-number", longitude: 121.22},
  {id: "missing", latitude: null, longitude: null},
]);
assert.equal(converted.mode, "compatibility");
assert.equal(converted.total_records, 3);
assert.equal(converted.located_records, 1);
assert.equal(converted.unlocated_records, 2);
assert.equal(converted.group_count, 1);
assert.equal(converted.items[0].record_count, 1);

// --- 404 fallback compatibility path ---

const calls = [];
let compatibilityPayload = null;
const fallbackLoader = createMapLoader({
  fetchImpl: async function (url) {
    calls.push(url);
    if (calls.length === 1) {
      return {ok: false, status: 404};
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        items: [{
          id: "tx-1",
          latitude: 25.01,
          longitude: 121.21,
          total_price: 1800,
          transaction_date: "2026-07-01",
        }],
      }),
    };
  },
  render: (payload) => { compatibilityPayload = payload; },
  showError: () => assert.fail("404 compatibility path must render"),
});
await fallbackLoader(base, {
  zoom: 14, south: 24.9, west: 121.0, north: 25.1, east: 121.3,
});
assert.match(calls[0], /^\/api\/market\/map-points\?/);
assert.equal(
  calls[1],
  "/api/transactions?transaction_type=resale&station=A17&station=A18&limit=100"
);
assert.equal(compatibilityPayload.mode, "compatibility");
assert.match(mapStatusText(compatibilityPayload), /^相容模式：/);
assert.match(mapStatusText(compatibilityPayload), /最近 100 筆/);
assert.match(mapStatusText(compatibilityPayload), /重新啟動 Web/);

// --- Non-fallback error paths ---

for (const response of [
  {ok: false, status: 500},
  {ok: true, status: 200, json: async () => ({items: "invalid"})},
]) {
  let fetchCount = 0;
  let error = "";
  const loaderUnderTest = createMapLoader({
    fetchImpl: async () => { fetchCount += 1; return response; },
    render: () => assert.fail("invalid primary response must not render"),
    showError: (message) => { error = message; },
  });
  assert.equal(await loaderUnderTest(base, {
    zoom: 14, south: 24.9, west: 121.0, north: 25.1, east: 121.3,
  }), null);
  assert.equal(fetchCount, 1);
  assert.match(error, /^地圖資料載入失敗：/);
}

// --- AbortError ---

let abortCalled = false;
const abortLoader = createMapLoader({
  fetchImpl: async () => {
    abortCalled = true;
    const err = new Error("simulated abort");
    err.name = "AbortError";
    throw err;
  },
  render: () => assert.fail("abort must not render"),
  showError: () => assert.fail("abort must not show error"),
});
assert.equal(await abortLoader(base, {
  zoom: 14, south: 24.9, west: 121.0, north: 25.1, east: 121.3,
}), null);
assert.equal(abortCalled, true);

let errorMessage = "";
let renderCount = 0;
const failingLoader = createMapLoader({
  fetchImpl: async () => ({ ok: false, status: 503 }),
  render: () => {
    renderCount += 1;
  },
  showError: (message) => {
    errorMessage = message;
  },
});
assert.equal(
  await failingLoader(base, {
    zoom: 14,
    south: 24.9,
    west: 121.0,
    north: 25.1,
    east: 121.3,
  }),
  null
);
assert.equal(renderCount, 0);
assert.equal(errorMessage, "地圖資料載入失敗：map 503");

process.stdout.write("market map contract passed\n");
