import assert from "node:assert/strict";
import {
  createMapLoader,
  mapStatusText,
  markerRadius,
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

let requestedUrl = "";
let rendered = null;
const loader = createMapLoader({
  fetchImpl: async (url) => {
    requestedUrl = url;
    return {
      ok: true,
      json: async () => ({
        total_records: 2,
        located_records: 2,
        unlocated_records: 0,
        group_count: 1,
        items: [],
      }),
    };
  },
  render: (payload) => {
    rendered = payload;
  },
  showError: () => assert.fail("successful request must not show an error"),
});
await loader(base, {
  zoom: 14,
  south: 24.9,
  west: 121.0,
  north: 25.1,
  east: 121.3,
});
assert.match(requestedUrl, /^\/api\/market\/map-points\?/);
assert.equal(rendered.group_count, 1);

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
