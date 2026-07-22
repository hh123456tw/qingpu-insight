# 青埔智價 M4.1 資料完整性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 為所有刊登建立可稽核的定位方法、可信度、原因與 cache，使新建案能安全納入或明確排除青埔指標。

**Architecture:** 先擴充正規化契約與 repository schema，再以 `ListingGeocoder` protocol 將地址解析與站點距離判定分離。來源座標、結構化地址、人工確認與未知狀態皆產生同一 `LocationEvidence`，CLI build 保存品質摘要。

**Tech Stack:** Python 3.11、dataclasses、pandas、PyArrow、PyMySQL、requests、pytest

## Global Constraints

- 只有可信 WGS84 座標且最近站點距離不超過 2,000 公尺的資料可設為 `location_eligible=True`。
- 不以模糊標題、區域中心點或預設站點填補未知座標。
- Geocoder 不可用時保留 `unknown` 與原因，不得使整批發布失敗。
- 地址 cache 只保存正規化地址、座標、provider、版本與時間，不保存聯絡資料。
- M4 runtime 只讀寫 MySQL；現有 Parquet 僅保留為可重建匯出，舊 MySQL 列原地升級後預設為 `unknown`。

## File Map

| File | Responsibility |
|---|---|
| `location_evidence.py` | 定義定位方法、可信度與原因契約 |
| `listing_detail_enrichment.py` | 以正常可見瀏覽流程保存新建案詳細頁並擷取公開地址 |
| `listing_geocoding.py` | 官方門牌 exact match、MySQL cache 與 geocoder adapter |
| `listing_normalization.py` | 將來源座標轉成初始 evidence |
| `listing_location.py` | 計算最近站、距離、eligibility 與最終 reason |
| `listing_repository.py` | MySQL location 欄位 migration 與正式保存 |
| `cli.py` | 組裝 geocoding/build 與品質輸出，不實作定位規則 |

---

### Task 1: 定位證據契約與正規化欄位

**Files:**
- Create: `src/qingpu_insight/location_evidence.py`
- Modify: `src/qingpu_insight/listing_normalization.py`
- Test: `tests/test_location_evidence.py`
- Test: `tests/test_listing_normalization.py`

**Interfaces:**
- Consumes: `NormalizedListing`, `SourceListing`。
- Produces: `LocationEvidence`, `LocationMethod`, `LocationConfidence`, `unknown_location(reason)`；`NormalizedListing` 新增 `structured_address`、`address_source_url`、`address_observed_at`、`location_method`、`location_confidence`、`location_reason`、`geocoded_at`、`geocoder_version`。

- [ ] **Step 1: 寫入失敗測試**

```python
from qingpu_insight.location_evidence import LocationEvidence, unknown_location


def test_unknown_location_has_explicit_reason() -> None:
    value = unknown_location("missing_coordinates_and_address")
    assert value == LocationEvidence(
        latitude=None,
        longitude=None,
        method="unknown",
        confidence="unknown",
        reason="missing_coordinates_and_address",
        geocoded_at=None,
        geocoder_version=None,
    )
```

在 `tests/test_listing_normalization.py` 增加 assertion：有來源座標時 method 為
`source_coordinates`、confidence 為 `high`；無座標時為 `unknown`；payload 的
`structured_address`、`address_source_url`、`address_observed_at` 只有型別合法時才保留。

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_location_evidence.py tests/test_listing_normalization.py -q`
Expected: FAIL，原因為 `qingpu_insight.location_evidence` 不存在或欄位缺失。

- [ ] **Step 3: 實作最小契約**

```python
# src/qingpu_insight/location_evidence.py
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

LocationMethod = Literal["source_coordinates", "structured_address", "manual", "unknown"]
LocationConfidence = Literal["high", "medium", "low", "unknown"]


@dataclass(frozen=True)
class LocationEvidence:
    latitude: float | None
    longitude: float | None
    method: LocationMethod
    confidence: LocationConfidence
    reason: str
    geocoded_at: datetime | None
    geocoder_version: str | None


def unknown_location(reason: str) -> LocationEvidence:
    return LocationEvidence(None, None, "unknown", "unknown", reason, None, None)
```

在 `normalize_listing()` 以 `_valid_taiwan_coordinate()` 判斷來源座標；合法座標建立 high
evidence，不合法或缺少時建立 `unknown_location("missing_or_invalid_source_coordinates")`，並將
定位與地址欄位寫入 `NormalizedListing` 與 `_stable_dict()`。`address_observed_at` 正規化為 UTC
datetime；地址不完整時三個 address metadata 一律設為 `None`，避免保存半套 provenance。

- [ ] **Step 4: 執行聚焦測試與既有正規化測試**

Run: `python -m pytest tests/test_location_evidence.py tests/test_listing_normalization.py -q`
Expected: PASS，既有 `raw_hash` deterministic 測試同步更新 expected fixture。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/location_evidence.py src/qingpu_insight/listing_normalization.py tests/test_location_evidence.py tests/test_listing_normalization.py
git commit -m "feat(m4): add listing location evidence contract"
```

### Task 2: 新建案詳細頁地址 enrichment

**Files:**
- Create: `src/qingpu_insight/listing_detail_enrichment.py`
- Create: `tests/test_listing_detail_enrichment.py`
- Create: `tests/fixtures/listings/591_newhouse_detail.html`
- Modify: `src/qingpu_insight/listing_capture.py`
- Test: `tests/test_listing_capture.py`

**Interfaces:**
- Consumes: `SourceListing`、既有可見 Chrome factory、batch directory 與 human-facing `source_url`。
- Produces: `DetailAddress`, `extract_detail_address(html, source_url, observed_at)`, `ListingDetailEnricher.enrich(listing, batch_dir) -> SourceListing`、公開 `is_verification_page(html)`。

- [ ] **Step 1: 寫入 parser、provenance、raw evidence 與安全停止測試**

```python
def test_extracts_jsonld_postal_address_with_provenance(detail_html) -> None:
    observed_at = datetime(2026, 7, 22, tzinfo=timezone.utc)
    result = extract_detail_address(detail_html, "https://newhouse.591.com.tw/home/housing/detail?hid=123", observed_at)
    assert result.address == "桃園市中壢區高鐵南路一段1號"
    assert result.representation == "jsonld_postal_address"
    assert result.source_url.startswith("https://newhouse.591.com.tw/")
    assert result.observed_at == observed_at


def test_enricher_rejects_verification_page_without_accepted_raw_evidence(tmp_path) -> None:
    enricher = ListingDetailEnricher(FakeDetailBrowser(verification_html()), fixed_clock())
    with pytest.raises(DetailEnrichmentBlocked, match="verification_required"):
        enricher.enrich(newhouse_listing(), tmp_path)
    assert not list((tmp_path / "details").glob("*.html"))
```

另測試：非 newhouse 原樣回傳且不導航；非 591 HTTPS URL 在導航前拒絕；沒有地址的正常詳細頁
保留 raw diagnostic 並回傳原 listing；成功時只複製 payload 並增加三個 address provenance 欄位。

- [ ] **Step 2: 驗證測試先失敗**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_listing_detail_enrichment.py tests/test_listing_capture.py -q`
Expected: FAIL，`listing_detail_enrichment` 與公開 verification helper 尚不存在。

- [ ] **Step 3: 實作保守的詳細頁 enrichment**

```python
@dataclass(frozen=True)
class DetailAddress:
    address: str
    source_url: str
    observed_at: datetime
    representation: Literal["jsonld_postal_address", "dom_street_address"]


class ListingDetailEnricher:
    def enrich(self, listing: SourceListing, batch_dir: Path) -> SourceListing:
        if listing.listing_type != "newhouse":
            return listing
        # validate allowlisted HTTPS URL, navigate with existing visible browser,
        # reject verification, parse, atomically persist accepted evidence,
        # then return a copied SourceListing payload with address provenance.
```

Parser 優先讀取 JSON-LD `PostalAddress` 的 `addressRegion`、`addressLocality`、`streetAddress`；
DOM fallback 只接受 `[itemprop="streetAddress"]`、`.detail-address`、`.house-address` 等明確地址節點。
結果必須正規化後以 `桃園市中壢區` 或 `桃園市大園區` 開頭且包含路／街與門牌號；只有區名的
描述不得接受。成功 HTML 寫入 `batch_dir/details/<source_listing_id>.html`，以 `.tmp` + replace
原子保存；驗證頁不得成為 accepted evidence。將 `listing_capture._likely_verification()` 改名為公開
`is_verification_page()` 並更新既有 call sites，不複製驗證規則。

- [ ] **Step 4: 執行聚焦與 capture 回歸測試**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_listing_detail_enrichment.py tests/test_listing_capture.py -q`
Expected: PASS，無真實網路或 Chrome。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/listing_detail_enrichment.py src/qingpu_insight/listing_capture.py tests/test_listing_detail_enrichment.py tests/test_listing_capture.py tests/fixtures/listings/591_newhouse_detail.html
git commit -m "feat(m4): enrich newhouse detail addresses"
```

### Task 3: 結構化地址 geocoder 與持久 cache

**Files:**
- Create: `src/qingpu_insight/listing_geocoding.py`
- Create: `tests/test_listing_geocoding.py`

**Interfaces:**
- Consumes: `str address`、桃園官方門牌座標 DataFrame、可選的外部 geocoder adapter。
- Produces: `ListingGeocoder.resolve(address: str) -> LocationEvidence`、`DoorplateListingGeocoder`、`MySQLGeocodeCache.get/put`、`GeocodingService.enrich(record: dict) -> LocationEvidence`。

- [ ] **Step 1: 寫入 cache hit 與 provider failure 測試**

```python
def test_service_reuses_normalized_address_cache(mysql_cache, fake_geocoder) -> None:
    service = GeocodingService(fake_geocoder, mysql_cache)
    first = service.resolve("桃園市 中壢區 高鐵南路一段 1 號")
    second = service.resolve("桃園市中壢區高鐵南路一段1號")
    assert first == second
    assert fake_geocoder.calls == 1


def test_provider_error_returns_unknown_without_cache_poison(mysql_cache, failing_geocoder) -> None:
    result = GeocodingService(failing_geocoder, mysql_cache).resolve(
        "桃園市中壢區高鐵南路一段1號"
    )
    assert result.method == "unknown"
    assert result.reason == "geocoder_unavailable"
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_listing_geocoding.py -q`
Expected: FAIL，`GeocodingService` 尚未定義。

- [ ] **Step 3: 實作 protocol、MySQL cache 與 service**

```python
class ListingGeocoder(Protocol):
    @property
    def version(self) -> str: ...

    def resolve(self, normalized_address: str) -> tuple[float, float] | None: ...


class GeocodingService:
    def resolve(self, address: str) -> LocationEvidence:
        key = normalize_address(address)
        if not key:
            return unknown_location("missing_structured_address")
        if cached := self.cache.get(key):
            return cached
        try:
            coordinates = self.geocoder.resolve(key)
        except (requests.RequestException, TimeoutError):
            return unknown_location("geocoder_unavailable")
        if coordinates is None:
            return unknown_location("address_not_resolved")
        value = LocationEvidence(
            coordinates[0], coordinates[1], "structured_address", "medium",
            "address_resolved", datetime.now(timezone.utc), self.geocoder.version,
        )
        self.cache.put(key, value)
        return value
```

`MySQLGeocodeCache` 建立 `geocode_cache(normalized_address PRIMARY KEY, latitude, longitude,
method, confidence, reason, geocoded_at, geocoder_version, updated_at)`，`put()` 使用
`INSERT ... ON DUPLICATE KEY UPDATE` 並在單一 transaction 完成；時間使用 UTC `DATETIME(6)`。
`DoorplateListingGeocoder` 重用 `addresses.normalize_address()` 與桃園官方門牌座標資料，只接受
完整正規化地址的 exact match；nearest-number 或模糊比對回傳 `None`，不得假裝精確。未來若採外部
geocoder，endpoint 與 user agent 只由環境變數設定，不將第三方免費服務硬編碼為必要依賴。

- [ ] **Step 4: 執行測試與 lint**

Run: `python -m pytest tests/test_listing_geocoding.py -q && python -m ruff check src/qingpu_insight/listing_geocoding.py tests/test_listing_geocoding.py`
Expected: PASS；cache 測試只呼叫 fake provider 一次。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/listing_geocoding.py tests/test_listing_geocoding.py
git commit -m "feat(m4): add cached listing geocoding service"
```

### Task 4: 站點判定、repository migration 與 build 整合

**Files:**
- Modify: `src/qingpu_insight/listing_location.py`
- Modify: `src/qingpu_insight/listing_repository.py`
- Modify: `src/qingpu_insight/cli.py`
- Test: `tests/test_listing_location.py`
- Test: `tests/test_listing_repository.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `LocationEvidence` 欄位與現有 `assign_listing_life_circle()`。
- Produces: `location_reason` 最終原因、MySQL schema migration、`listing-build --geocoder-enabled`。

- [ ] **Step 1: 寫入地理與 migration 失敗測試**

```python
def test_outside_radius_keeps_nearest_distance_but_is_ineligible(stations) -> None:
    row = pd.DataFrame([{"latitude": 25.20, "longitude": 121.20,
                         "location_method": "structured_address",
                         "location_confidence": "medium"}])
    result = assign_listing_life_circle(row, stations, 2_000)
    assert bool(result.loc[0, "location_eligible"]) is False
    assert result.loc[0, "location_reason"] == "outside_service_radius"
    assert result.loc[0, "station_distance_m"] > 2_000
```

Repository 測試先建立舊 schema，再呼叫初始化並斷言六個新欄位存在且舊列為 `unknown`。
CLI 測試注入 fake geocoder，斷言只有無來源座標且有 structured address 的列被解析。

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_listing_location.py tests/test_listing_repository.py tests/test_cli.py -q`
Expected: FAIL，原因為新欄位未保存或 outside row 丟失最近距離。

- [ ] **Step 3: 實作判定與 schema upgrade**

在 `assign_listing_life_circle()` 對所有合法座標保存最近站與距離，再依 radius 設定 eligibility；
原因依序為 `missing_coordinates`、`outside_service_radius`、`eligible_source_coordinates`、
`eligible_structured_address`、`eligible_manual`。在 MySQL `CREATE TABLE`、row serialization 與
`_upgrade_location_schema()` 加入欄位。既有 Parquet writer 若仍輸出資料，僅同步欄位作為匯出相容，
不得由 Web 或 M4 job 讀回作正式狀態。

在 591 update/build 流程中，先對缺少座標的新建案呼叫 `ListingDetailEnricher`，再 normalize；
normalize 後、life-circle 前，只對 method=`unknown` 且 `structured_address` 非空資料呼叫
`GeocodingService`。Detail enrichment blocked 時批次標為不完整且不發布；正常頁無地址只記錄
`detail_address_missing` 並繼續。新增品質 JSON：

```python
quality["location"] = {
    "eligible": int(rows["location_eligible"].sum()),
    "unknown": int(rows["location_method"].eq("unknown").sum()),
    "by_method": rows["location_method"].value_counts(dropna=False).to_dict(),
    "by_reason": rows["location_reason"].value_counts(dropna=False).to_dict(),
}
```

- [ ] **Step 4: 執行聚焦與完整測試**

Run: `python -m pytest tests/test_listing_location.py tests/test_listing_repository.py tests/test_cli.py -q`
Expected: PASS。

Run: `python -m pytest -q && python -m ruff check .`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/listing_location.py src/qingpu_insight/listing_repository.py src/qingpu_insight/cli.py tests/test_listing_location.py tests/test_listing_repository.py tests/test_cli.py
git commit -m "feat(m4): publish auditable listing locations"
```

### Task 5: M4.1 方法文件與 release evidence

**Files:**
- Create: `docs/m4-location-methodology.md`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: M4.1 CLI 與品質 JSON。
- Produces: 可重現的最小 smoke 指令與 acceptance artifact 路徑。

- [ ] **Step 1: 加入 CLI help contract test**

```python
def test_listing_build_help_documents_geocoding_controls(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["listing-build", "--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "--geocoder-enabled" in output
```

- [ ] **Step 2: 執行測試確認目前 help 不完整**

Run: `python -m pytest tests/test_cli.py::test_listing_build_help_documents_geocoding_controls -q`
Expected: FAIL，help 尚未包含 flag 或說明。

- [ ] **Step 3: 補齊文件與 help**

文件明列搜尋頁限制、詳細頁 enrichment、方法優先序、信心等級、兩公里規則、cache、隱私、
失敗原因、品質 JSON 欄位，以及：

```powershell
qingpu-data listing-build --input data/raw/listings/591/<date>/<batch_id> `
  --geocoder-enabled
python -m pytest tests/test_listing_location.py tests/test_listing_repository.py -q
```

- [ ] **Step 4: 驗證 M4.1 gate**

Run: `python -m pytest -q && python -m ruff check . && git diff --check`
Expected: 全部 PASS 且無 whitespace error。

- [ ] **Step 5: 提交**

```bash
git add README.md docs/m4-location-methodology.md tests/test_cli.py src/qingpu_insight/cli.py
git commit -m "docs(m4): document location quality controls"
```
