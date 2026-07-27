# Market Map Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓首頁成交地圖代表全部篩選資料、以最多 500 個自適應群組呈現，並將近期成交表格固定為最新 100 筆。

**Architecture:** 在 `market_metrics.py` 新增不依賴 Flask 的網格聚合函式，`web.py` 只負責驗證地圖視窗參數與公開 JSON。前端新增可由 Node 直接測試的 ES module，`app.js` 動態載入它，將地圖請求和近期成交請求分離。

**Tech Stack:** Python 3.11、pandas、Flask、原生 JavaScript ES modules、Leaflet 1.9.4、pytest、Node.js contract tests。

## Global Constraints

- 依照 `docs/superpowers/specs/2026-07-27-market-map-aggregation-design.md` 實作。
- 每個 production behavior 必須先有會因缺少該行為而失敗的測試，確認 RED 後才寫 implementation。
- 不導入 PostGIS、向量圖磚、Leaflet.markercluster 或新的 npm/Python 套件。
- 地圖群組最多 500 個；近期成交請求明確使用 `limit=100`。
- 地圖使用完整篩選結果；地圖邊界只影響 `items`，不改變完整資料計數。
- 不回傳地址、門牌、內部座標比對欄位或其他隱私欄位。
- 保留現有未提交的 `.gitignore`、`src/qingpu_insight/static/app.css`、`src/qingpu_insight/templates/index.html` 與 `candidates/`；不得重設、覆蓋或納入本計畫提交。
- 使用 `apply_patch` 修改檔案；每次提交前執行 `git diff --cached --check` 並確認 staged diff 只包含該任務。

## File Structure

- Modify: `src/qingpu_insight/market_metrics.py`
  - 擁有地圖邊界型別、座標有效性判斷及自適應網格聚合。
- Modify: `src/qingpu_insight/web.py`
  - 解析/驗證 `zoom` 與 bbox，公開 `/api/market/map-points`。
- Create: `src/qingpu_insight/static/market_map.mjs`
  - 擁有地圖/近期成交 query builder、群組 marker 表示及地圖資料 loader。
- Modify: `src/qingpu_insight/static/app.js`
  - 將既有 Leaflet 畫面接到新的地圖 loader，近期成交改為 100 筆。
- Modify: `tests/test_market_metrics.py`
  - 驗證完整計數、缺失座標、縮放拆群與 500 群組上限。
- Modify: `tests/test_web.py`
  - 驗證地圖 API 契約、篩選與輸入錯誤。
- Create: `tests/js/market_map_contract.mjs`
  - 驗證 query、狀態文字、marker 半徑與 request coordinator。
- Modify: `README.md`
  - 說明成交地圖與近期成交的資料範圍不同。

---

### Task 1: Domain-level adaptive grid aggregation

**Files:**
- Modify: `src/qingpu_insight/market_metrics.py:1-135`
- Modify: `tests/test_market_metrics.py:1-100`

**Interfaces:**
- Consumes: `filter_market(frame: pd.DataFrame, filters: MarketFilters) -> pd.DataFrame`
- Produces: `MapBounds(south: float, west: float, north: float, east: float)`
- Produces: `market_map_points(frame: pd.DataFrame, filters: MarketFilters, zoom: int, bounds: MapBounds | None = None, max_groups: int = 500) -> dict[str, Any]`
- Output keys: `total_records`, `located_records`, `unlocated_records`, `group_count`, `items`
- Item keys: `latitude`, `longitude`, `record_count`, `median_unit_price_per_ping_twd`, `latest_transaction_date`

- [ ] **Step 1: Add failing tests for complete counts, missing coordinates, and bounds**

Add imports for `MapBounds` and `market_map_points`, then add a literal fixture and assertions:

```python
def _map_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "transaction_type": ["resale", "resale", "resale", "presale"],
            "station_code": ["A18", "A18", "A18", "A18"],
            "transaction_date": pd.to_datetime(
                ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]
            ),
            "unit_price_per_ping_twd": [400_000, 500_000, 600_000, 700_000],
            "latitude": [25.0001, 25.0002, None, 25.0003],
            "longitude": [121.0001, 121.0002, 121.0003, 121.0004],
        }
    )


def test_market_map_counts_all_filtered_rows_but_groups_only_located_rows() -> None:
    result = market_map_points(
        _map_frame(),
        MarketFilters(transaction_type="resale"),
        zoom=12,
    )

    assert result["total_records"] == 3
    assert result["located_records"] == 2
    assert result["unlocated_records"] == 1
    assert result["group_count"] == 1
    assert result["items"] == [
        {
            "latitude": 25.0002,
            "longitude": 121.0002,
            "record_count": 2,
            "median_unit_price_per_ping_twd": 450_000.0,
            "latest_transaction_date": "2026-02-01",
        }
    ]


def test_market_map_bounds_limit_groups_not_complete_counts() -> None:
    result = market_map_points(
        _map_frame(),
        MarketFilters(transaction_type="resale"),
        zoom=16,
        bounds=MapBounds(24.999, 120.999, 25.00015, 121.00015),
    )

    assert result["total_records"] == 3
    assert result["located_records"] == 2
    assert result["unlocated_records"] == 1
    assert sum(item["record_count"] for item in result["items"]) == 1


def test_market_map_empty_filter_returns_zero_counts_and_no_groups() -> None:
    result = market_map_points(
        _map_frame(),
        MarketFilters(
            transaction_type="resale",
            date_from=pd.Timestamp("2099-01-01"),
        ),
        zoom=14,
    )

    assert result == {
        "total_records": 0,
        "located_records": 0,
        "unlocated_records": 0,
        "group_count": 0,
        "items": [],
    }
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_market_metrics.py::test_market_map_counts_all_filtered_rows_but_groups_only_located_rows tests\test_market_metrics.py::test_market_map_bounds_limit_groups_not_complete_counts tests\test_market_metrics.py::test_market_map_empty_filter_returns_zero_counts_and_no_groups -q
```

Expected: collection fails because `MapBounds` and `market_map_points` do not exist. Do not proceed if the failure is a fixture typo.

- [ ] **Step 3: Implement the minimal aggregation contract**

Add to `market_metrics.py`:

```python
import math


@dataclass(frozen=True)
class MapBounds:
    south: float
    west: float
    north: float
    east: float

    def __post_init__(self) -> None:
        values = (self.south, self.west, self.north, self.east)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("map bounds must be finite")
        if self.south >= self.north or self.west >= self.east:
            raise ValueError("map bounds must be ordered")


def _aggregate_map_rows(rows: pd.DataFrame, grid_size: float) -> list[dict[str, Any]]:
    if rows.empty:
        return []
    working = rows.copy()
    working["_lat_cell"] = (working["latitude"] / grid_size).apply(math.floor)
    working["_lon_cell"] = (working["longitude"] / grid_size).apply(math.floor)
    grouped = (
        working.groupby(["_lat_cell", "_lon_cell"], sort=True)
        .agg(
            latitude=("latitude", "mean"),
            longitude=("longitude", "mean"),
            record_count=("transaction_type", "size"),
            median_unit_price_per_ping_twd=("unit_price_per_ping_twd", "median"),
            latest_transaction_date=("transaction_date", "max"),
        )
        .reset_index(drop=True)
    )
    items: list[dict[str, Any]] = []
    for row in grouped.to_dict(orient="records"):
        items.append(
            {
                "latitude": round(float(row["latitude"]), 4),
                "longitude": round(float(row["longitude"]), 4),
                "record_count": int(row["record_count"]),
                "median_unit_price_per_ping_twd": float(
                    row["median_unit_price_per_ping_twd"]
                ),
                "latest_transaction_date": str(
                    pd.Timestamp(row["latest_transaction_date"]).date()
                ),
            }
        )
    return items


def market_map_points(
    frame: pd.DataFrame,
    filters: MarketFilters,
    zoom: int,
    bounds: MapBounds | None = None,
    max_groups: int = 500,
) -> dict[str, Any]:
    filtered = filter_market(frame, filters)
    latitude = pd.to_numeric(filtered["latitude"], errors="coerce")
    longitude = pd.to_numeric(filtered["longitude"], errors="coerce")
    valid = (
        latitude.between(-90, 90)
        & longitude.between(-180, 180)
        & latitude.map(math.isfinite)
        & longitude.map(math.isfinite)
    )
    located = filtered.loc[valid].copy()
    located["latitude"] = latitude.loc[valid]
    located["longitude"] = longitude.loc[valid]
    total_records = len(filtered)
    located_records = len(located)
    if bounds is not None:
        located = located.loc[
            located["latitude"].between(bounds.south, bounds.north)
            & located["longitude"].between(bounds.west, bounds.east)
        ]
    items = _aggregate_map_rows(located, 0.01)
    return {
        "total_records": int(total_records),
        "located_records": int(located_records),
        "unlocated_records": int(total_records - located_records),
        "group_count": len(items),
        "items": items,
    }
```

If pandas rounds the literal centroid to `25.0001` rather than `25.0002`, use coordinates whose arithmetic mean has an unambiguous fourth decimal; do not weaken the assertion to “is not null.”

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Step 2 command again.

Expected: both tests pass.

- [ ] **Step 5: Add failing tests for zoom detail and adaptive cap**

```python
def test_market_map_higher_zoom_splits_close_coordinates() -> None:
    frame = _map_frame().iloc[:2].copy()
    frame.loc[frame.index[1], ["latitude", "longitude"]] = [25.0011, 121.0011]

    coarse = market_map_points(
        frame, MarketFilters(transaction_type="resale"), zoom=12
    )
    detailed = market_map_points(
        frame, MarketFilters(transaction_type="resale"), zoom=16
    )

    assert coarse["group_count"] == 1
    assert detailed["group_count"] == 2


def test_market_map_adapts_grid_to_group_limit() -> None:
    count = 620
    frame = pd.DataFrame(
        {
            "transaction_type": ["resale"] * count,
            "station_code": ["A18"] * count,
            "transaction_date": pd.to_datetime(["2026-01-01"] * count),
            "unit_price_per_ping_twd": [500_000] * count,
            "latitude": [24.8 + index * 0.02 for index in range(count)],
            "longitude": [120.8 + index * 0.021 for index in range(count)],
        }
    )

    result = market_map_points(
        frame,
        MarketFilters(transaction_type="resale"),
        zoom=19,
        max_groups=500,
    )

    assert result["total_records"] == 620
    assert result["group_count"] <= 500
    assert sum(item["record_count"] for item in result["items"]) == 620
```

- [ ] **Step 6: Verify RED, then make only the smallest correction required**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_market_metrics.py -q
```

Expected RED: the higher zoom still returns one group and the 620-row case exceeds
500 groups. Replace the fixed aggregation line with:

```python
def _grid_size(zoom: int) -> float:
    return 0.01 / (2 ** max(zoom - 12, 0))


# inside market_map_points
grid_size = _grid_size(zoom)
items = _aggregate_map_rows(located, grid_size)
while len(items) > max_groups:
    grid_size *= 2
    items = _aggregate_map_rows(located, grid_size)
```

Then rerun and expect all `test_market_metrics.py` tests to pass.

- [ ] **Step 7: Commit Task 1**

```powershell
git add -- src/qingpu_insight/market_metrics.py tests/test_market_metrics.py
git diff --cached --check
git diff --cached --stat
git commit -m "feat(market): aggregate transaction map points"
```

---

### Task 2: Validated map-points API

**Files:**
- Modify: `src/qingpu_insight/web.py:35-55,364-420,800-825`
- Modify: `tests/test_web.py:385-490`

**Interfaces:**
- Consumes: `MapBounds` and `market_map_points` from Task 1.
- Produces: `_parse_map_view(args: MultiDict[str, str]) -> tuple[int, MapBounds | None]`
- Produces: `GET /api/market/map-points`
- Error fields: `zoom: integer_10_to_19`, `bounds: all_or_none`, or `bounds: ordered_finite_numbers`

- [ ] **Step 1: Write failing API success tests**

Add:

```python
def test_map_points_reports_complete_counts_and_public_groups(
    client: FlaskClient,
) -> None:
    response = client.get(
        "/api/market/map-points?transaction_type=resale&station=A18&zoom=14"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_records"] == 2
    assert payload["located_records"] <= payload["total_records"]
    assert payload["unlocated_records"] == (
        payload["total_records"] - payload["located_records"]
    )
    assert payload["group_count"] == len(payload["items"])
    assert set(payload["items"][0]) == {
        "latitude",
        "longitude",
        "record_count",
        "median_unit_price_per_ping_twd",
        "latest_transaction_date",
    }


def test_map_points_bounds_do_not_change_complete_filtered_count(
    client: FlaskClient,
) -> None:
    base = client.get(
        "/api/market/map-points?transaction_type=resale&station=A18&zoom=14"
    ).get_json()
    bounded = client.get(
        "/api/market/map-points"
        "?transaction_type=resale&station=A18&zoom=14"
        "&south=24&west=120&north=24.1&east=120.1"
    ).get_json()

    assert bounded["total_records"] == base["total_records"]
    assert bounded["items"] == []
```

- [ ] **Step 2: Run API success tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py::TestMarketApi::test_map_points_reports_complete_counts_and_public_groups tests\test_web.py::TestMarketApi::test_map_points_bounds_do_not_change_complete_filtered_count -q
```

Expected: 404 because `/api/market/map-points` is not registered.

- [ ] **Step 3: Implement the success path and route**

Import `MapBounds` and `market_map_points`. Add only the happy-path parser next to
`parse_filters`; validation is deliberately deferred until its RED test:

```python
def _parse_map_view(args: MultiDict[str, str]) -> tuple[int, MapBounds | None]:
    zoom = int(args.get("zoom", "14"))
    names = ("south", "west", "north", "east")
    values = [args.get(name) for name in names]
    if not any(value not in (None, "") for value in values):
        return zoom, None
    return zoom, MapBounds(*(float(value) for value in values if value is not None))
```

Register before the existing `/api/transactions` route:

```python
@app.get("/api/market/map-points")
def map_points_api():
    filters = parse_filters(request.args)
    zoom, bounds = _parse_map_view(request.args)
    return jsonify(
        market_map_points(
            ds.load(filters),
            filters,
            zoom=zoom,
            bounds=bounds,
        )
    )
```

- [ ] **Step 4: Run API success tests and verify GREEN**

Run the Step 2 command again. Expected: both pass.

- [ ] **Step 5: Write failing validation tests**

```python
@pytest.mark.parametrize(
    ("query", "fields"),
    [
        ("zoom=9", {"zoom": "integer_10_to_19"}),
        ("zoom=14.5", {"zoom": "integer_10_to_19"}),
        ("zoom=14&south=24", {"bounds": "all_or_none"}),
        (
            "zoom=14&south=25&west=121&north=24&east=122",
            {"bounds": "ordered_finite_numbers"},
        ),
        (
            "zoom=14&south=nan&west=121&north=25&east=122",
            {"bounds": "ordered_finite_numbers"},
        ),
    ],
)
def test_map_points_rejects_invalid_view_parameters(
    client: FlaskClient, query: str, fields: dict[str, str]
) -> None:
    response = client.get(
        f"/api/market/map-points?transaction_type=resale&{query}"
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["fields"] == fields
```

- [ ] **Step 6: Verify RED then GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py::TestMarketApi::test_map_points_rejects_invalid_view_parameters -q
```

Expected RED: malformed cases currently escape as 500 responses or return the wrong
field code. Replace the minimal parser with:

```python
def _parse_map_view(args: MultiDict[str, str]) -> tuple[int, MapBounds | None]:
    raw_zoom = args.get("zoom", "14")
    try:
        zoom = int(raw_zoom)
    except (TypeError, ValueError):
        raise ApiInputError(
            "地圖縮放層級格式不正確。", {"zoom": "integer_10_to_19"}
        ) from None
    if str(zoom) != raw_zoom or not 10 <= zoom <= 19:
        raise ApiInputError(
            "地圖縮放層級無效。", {"zoom": "integer_10_to_19"}
        )

    names = ("south", "west", "north", "east")
    values = [args.get(name) for name in names]
    if not any(value not in (None, "") for value in values):
        return zoom, None
    if any(value in (None, "") for value in values):
        raise ApiInputError("地圖邊界不完整。", {"bounds": "all_or_none"})
    try:
        bounds = MapBounds(*(float(value) for value in values if value is not None))
    except (TypeError, ValueError):
        raise ApiInputError(
            "地圖邊界無效。", {"bounds": "ordered_finite_numbers"}
        ) from None
    return zoom, bounds
```

Then rerun and expect every invalid parameter case to pass.

- [ ] **Step 7: Run relevant backend regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_market_metrics.py tests\test_web.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/market_metrics.py src/qingpu_insight/web.py tests/test_market_metrics.py tests/test_web.py
```

Expected: all pass and Ruff reports `All checks passed!`.

- [ ] **Step 8: Commit Task 2**

```powershell
git add -- src/qingpu_insight/web.py tests/test_web.py
git diff --cached --check
git diff --cached --stat
git commit -m "feat(web): expose aggregated market map API"
```

---

### Task 3: Independent map loading and 100-row recent table

**Files:**
- Create: `src/qingpu_insight/static/market_map.mjs`
- Modify: `src/qingpu_insight/static/app.js:1-105,185-250`
- Create: `tests/js/market_map_contract.mjs`
- Modify: `README.md`

**Interfaces:**
- Consumes: `GET /api/market/map-points` from Task 2.
- Produces: `withRecentLimit(baseParams: URLSearchParams) -> URLSearchParams`
- Produces: `withMapView(baseParams: URLSearchParams, view: object) -> URLSearchParams`
- Produces: `mapStatusText(payload: object) -> string`
- Produces: `markerRadius(recordCount: number) -> number`
- Produces: `createMapLoader({fetchImpl, render, showError}) -> async load(baseParams, view)`
- Browser global is not used; `app.js` loads the module with `await import("/static/market_map.mjs")`.

- [ ] **Step 1: Write the failing Node contract**

Create `tests/js/market_map_contract.mjs`:

```javascript
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
```

- [ ] **Step 2: Run the Node contract and verify RED**

```powershell
node tests\js\market_map_contract.mjs
```

Expected: `ERR_MODULE_NOT_FOUND` for `market_map.mjs`.

- [ ] **Step 3: Implement the tested ES module**

Create `src/qingpu_insight/static/market_map.mjs`:

```javascript
export function withRecentLimit(baseParams) {
  const params = new URLSearchParams(baseParams);
  params.set("limit", "100");
  return params;
}

export function withMapView(baseParams, view) {
  const params = new URLSearchParams(baseParams);
  params.set("zoom", String(view.zoom));
  params.set("south", String(view.south));
  params.set("west", String(view.west));
  params.set("north", String(view.north));
  params.set("east", String(view.east));
  return params;
}

export function mapStatusText(payload) {
  const number = new Intl.NumberFormat("zh-TW");
  return [
    "符合 " + number.format(payload.total_records || 0) + " 筆",
    "有座標 " + number.format(payload.located_records || 0) + " 筆",
    "未定位 " + number.format(payload.unlocated_records || 0) + " 筆",
    "目前顯示 " + number.format(payload.group_count || 0) + " 個群組",
  ].join("｜");
}

export function markerRadius(recordCount) {
  return Math.min(16, Math.max(5, 4 + Math.log2(Math.max(1, recordCount))));
}

export function createMapLoader({ fetchImpl, render, showError }) {
  let controller = null;
  return async function load(baseParams, view) {
    if (controller !== null) controller.abort();
    controller = new AbortController();
    const params = withMapView(baseParams, view);
    try {
      const response = await fetchImpl(
        "/api/market/map-points?" + params.toString(),
        { signal: controller.signal }
      );
      if (!response.ok) throw new Error("map " + response.status);
      const payload = await response.json();
      render(payload);
      return payload;
    } catch (error) {
      if (error.name === "AbortError") return null;
      showError("地圖資料載入失敗：" + error.message);
      return null;
    }
  };
}
```

- [ ] **Step 4: Run the Node contract and verify GREEN**

Run the Step 2 command. Expected: `market map contract passed`, including the failed-request case where `renderCount` remains zero.

- [ ] **Step 5: Wire the tested module into `app.js`**

In the first `DOMContentLoaded` callback:

1. Make the callback `async`.
2. Load the module:

```javascript
const marketMapUi = await import("/static/market_map.mjs");
```

3. Dynamically create a status paragraph before `#market-map` so no dirty template or stylesheet must be changed:

```javascript
const mapStatus = document.createElement("p");
mapStatus.id = "map-data-status";
mapStatus.setAttribute("role", "status");
mapStatus.setAttribute("aria-live", "polite");
mapDiv.parentNode.insertBefore(mapStatus, mapDiv);
```

4. Replace `updateMap(items)` with `renderMap(payload)`. Only call
   `markerLayer.clearLayers()` after a successful payload arrives. For each group, use:

```javascript
L.circleMarker([item.latitude, item.longitude], {
  radius: marketMapUi.markerRadius(item.record_count),
  color: "#0b5f55",
  weight: 2,
  fillColor: "#22a896",
  fillOpacity: 0.82,
})
  .bindPopup(
    "成交 " + item.record_count + " 筆<br>" +
    "中位單價 " + formatWan(item.median_unit_price_per_ping_twd) + "<br>" +
    "最近成交 " + (item.latest_transaction_date || "—")
  )
  .addTo(markerLayer);
```

Then set:

```javascript
mapStatus.textContent = marketMapUi.mapStatusText(payload);
```

5. Instantiate the loader once:

```javascript
const loadMap = marketMapUi.createMapLoader({
  fetchImpl: fetch,
  render: renderMap,
  showError: function (message) {
    mapStatus.textContent = message;
  },
});
```

6. Add:

```javascript
function currentMapView() {
  var bounds = map.getBounds();
  return {
    zoom: map.getZoom(),
    south: Number(bounds.getSouth().toFixed(6)),
    west: Number(bounds.getWest().toFixed(6)),
    north: Number(bounds.getNorth().toFixed(6)),
    east: Number(bounds.getEast().toFixed(6)),
  };
}
```

7. In `fetchData()`, keep summary/trends/recent transactions together, but use:

```javascript
var recentParams = marketMapUi.withRecentLimit(params);
fetch("/api/transactions?" + recentParams.toString(), { signal })
```

Remove `updateMap(transactions.items || [])` and call:

```javascript
loadMap(params, currentMapView());
```

8. Register a 200 ms debounced `moveend` handler that calls only `loadMap(buildParams(), currentMapView())`. Do not call `fetchData()` on map movement.

- [ ] **Step 6: Update README with the public behavior**

In the homepage/market dashboard section, state:

```markdown
- 成交地圖依目前篩選條件涵蓋全部有效座標資料，並依縮放層級聚合為最多
  500 個群組；地圖狀態會揭露總筆數、有座標筆數與未定位筆數。
- 「近期成交」獨立顯示最新 100 筆，不代表地圖或市場摘要的資料上限。
```

- [ ] **Step 7: Run frontend and focused integration verification**

```powershell
node tests\js\market_map_contract.mjs
node tests\js\model_admin_contract.cjs
node tests\js\admin_contract.cjs
node tests\js\job_polling_contract.cjs
.\.venv\Scripts\python.exe -m pytest tests\test_market_metrics.py tests\test_web.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: four Node contracts pass, focused pytest passes, Ruff reports `All checks passed!`.

- [ ] **Step 8: Browser acceptance**

Start/restart the local app, then verify at `http://127.0.0.1:5000/`:

1. Default resale summary `record_count` equals map `total_records`.
2. Map status contains total, located, unlocated, and group count.
3. Network request to `/api/transactions` contains `limit=100`.
4. The recent table contains 100 data rows when at least 100 records match.
5. Zooming in triggers `/api/market/map-points` only and updates group count.
6. Changing station/date/area filters updates summary, map status, and recent rows consistently.
7. Simulate/observe a map API failure: existing markers remain and the map status shows the failure without blanking trends or recent transactions.
8. No uncaught console errors appear.

Capture the initial status text, row count, group count before zoom, and group count after zoom in the implementation handoff.

- [ ] **Step 9: Commit Task 3**

```powershell
git add -- src/qingpu_insight/static/market_map.mjs src/qingpu_insight/static/app.js tests/js/market_map_contract.mjs README.md
git diff --cached --check
git diff --cached --stat
git commit -m "feat(web): visualize complete transaction map"
```

## Final Verification

- [ ] Run the complete Python suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

- [ ] Run every JavaScript contract:

```powershell
Get-ChildItem tests\js\*.cjs,tests\js\*.mjs | ForEach-Object { node $_.FullName }
```

- [ ] Run the full lint gate:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
```

- [ ] Confirm only intended files are committed and pre-existing user changes remain untouched:

```powershell
git status --short
git log -4 --oneline
```

Expected remaining unrelated state may include `.gitignore`, `src/qingpu_insight/static/app.css`, `src/qingpu_insight/templates/index.html`, and `candidates/`.
