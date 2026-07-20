# 青埔智價 M1 市場分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將已通過 M0 的 A17～A19 官方成交資料，做成中古屋與預售屋嚴格分流、可由 MySQL 載入、可透過 API 查詢，並能在瀏覽器展示地圖、趨勢與近期成交的市場分析產品。

**Architecture:** 保留 M0 的 raw acquisition 與座標流程，在其後新增可版本化的 market clean 資料集。市場指標集中在純 Pandas service，資料來源透過 `MarketDataSource` 介面切換 Parquet 與 MySQL；Flask API 只處理輸入驗證與序列化，首頁以原生 HTML/CSS/JavaScript、Leaflet、Chart.js 消費相同 API。

**Tech Stack:** Python 3.11+、Pandas、PyArrow、PyProj、Flask 3、PyMySQL、MySQL 8、pytest、HTML5、CSS、JavaScript、Leaflet 1.9、Chart.js 4。

## Global Constraints

- 地理範圍固定為桃園機場捷運 A17、A18、A19，生活圈半徑固定為 2,000 公尺，重疊時歸最近車站。
- 中古屋 `resale` 與預售屋 `presale` 在篩選、彙總、圖表與 API 回應中必須可明確分開，不提供混合價格指標。
- 成交核心只使用內政部官方實價登錄；M1 不爬售屋網站、不訓練估價模型、不讓 LLM 產生價格或成交紀錄。
- `data/raw/`、完整 Parquet、資料庫內容、密鑰與本機環境檔不可提交 Git；只提交程式、schema、少量 fixture、測試與文件。
- 無可靠座標、非 A17～A19 兩公里範圍、非住宅、日期或價格無效的紀錄可保留稽核標記，但不得進入正式市場指標。
- API 日期一律使用 ISO `YYYY-MM-DD`，金額一律以新台幣數值表示，面積顯示坪但保留原始平方公尺欄位。
- M1 本機無 MySQL 時必須可從 `data/processed/market_transactions.parquet` 啟動；設定 `QINGPU_DATABASE_URL` 後使用 MySQL 8。
- 所有清理規則、資料期間、資料更新時間與限制必須可由 UI 或 API 看見。

---

## File Structure

```text
src/qingpu_insight/
├─ moi.py                    擴充官方欄位解析
├─ market_cleaning.py        住宅規則、衍生欄位與品質摘要
├─ market_metrics.py         篩選、摘要、趨勢與近期成交純函式
├─ market_repository.py      Parquet/MySQL 資料來源介面
├─ mysql_loader.py           冪等載入 market_transactions
├─ web.py                    Flask app factory 與市場 API
├─ templates/index.html      混合首頁骨架
└─ static/{app.css,app.js}   地圖、圖表、篩選與成交表格
database/
└─ 001_market_schema.sql     MySQL 8 schema 與索引
tests/
├─ fixtures/market_transactions.csv
├─ test_market_cleaning.py
├─ test_market_metrics.py
├─ test_market_repository.py
├─ test_mysql_loader.py
└─ test_web.py
```

## Acceptance Gates

1. `qingpu-data market-build` 可從 M0 `transactions.parquet` 產出正式 market Parquet 與 JSON 品質報告。
2. 中古屋與預售屋的摘要、月趨勢與近期成交可個別查詢，任一回應不得混入另一類型。
3. MySQL schema 可重複套用，載入器對相同 `transaction_key` 為 upsert，不產生重複資料。
4. `GET /api/market/summary`、`GET /api/market/trends`、`GET /api/transactions` 具一致錯誤格式與輸入範圍限制。
5. 首頁可切換交易類型與站點，更新 KPI、價格趨勢、交易量、地圖點與近期成交。
6. `pytest`、Ruff、market build、Flask test-client smoke test 全部通過。

---

### Task 1: Expand the official MOI contract for residential analysis

**Files:**
- Modify: `src/qingpu_insight/moi.py`
- Modify: `tests/fixtures/moi_resale.csv`
- Modify: `tests/fixtures/moi_presale.csv`
- Modify: `tests/test_moi.py`

**Interfaces:**
- Consumes: `read_moi_csv(path: Path, transaction_type: Literal["resale", "presale"])` from M0.
- Produces: canonical fields `transaction_subject`, `main_use`, `completion_date`, `bedrooms`, `living_rooms`, `bathrooms`, `has_management`, and `remarks` in addition to the existing M0 columns.

- [ ] **Step 1: Extend both fixture headers and rows with actual MOI column names**

Use these exact source-to-canonical mappings in the fixture:

```python
{
    "交易標的": "transaction_subject",
    "主要用途": "main_use",
    "建築完成年月": "completion_date",
    "建物現況格局-房": "bedrooms",
    "建物現況格局-廳": "living_rooms",
    "建物現況格局-衛": "bathrooms",
    "有無管理組織": "has_management",
    "備註": "remarks",
}
```

The resale fixture must describe a completed residential building; the presale fixture must leave `completion_date` empty and identify a presale residential building.

- [ ] **Step 2: Write failing parser assertions**

```python
def test_resale_parser_exposes_residential_analysis_fields() -> None:
    frame = read_moi_csv(FIXTURES / "moi_resale.csv", "resale")
    row = frame.iloc[0]
    assert row["transaction_subject"] == "房地(土地+建物)+車位"
    assert row["main_use"] == "住家用"
    assert row["completion_date"] == pd.Timestamp("2020-01-15")
    assert row[["bedrooms", "living_rooms", "bathrooms"]].tolist() == [3, 2, 2]
    assert row["has_management"] == "有"


def test_presale_parser_allows_missing_completion_date() -> None:
    frame = read_moi_csv(FIXTURES / "moi_presale.csv", "presale")
    assert pd.isna(frame.loc[0, "completion_date"])
```

- [ ] **Step 3: Run tests and verify the new contract fails**

Run: `\.\.venv\Scripts\python -m pytest tests/test_moi.py -v`

Expected: FAIL because the new canonical fields do not exist.

- [ ] **Step 4: Implement the expanded parser contract**

Add the mappings above, add the eight names to `CANONICAL_COLUMNS`, parse the three room columns with `pd.to_numeric(errors="coerce").astype("Int64")`, and add:

```python
def roc_month_or_date_to_timestamp(value: object) -> pd.Timestamp:
    text = str(value).strip().split(".")[0]
    if not text or text.lower() == "nan" or not text.isdigit():
        return pd.NaT
    if len(text) in (5, 6):
        text = text.zfill(6) + "01"
    elif len(text) == 7:
        text = text.zfill(7)
    else:
        return pd.NaT
    return roc_date_to_timestamp(text)
```

Missing optional fields must be created as `pd.NA` before selecting `CANONICAL_COLUMNS`; parse `completion_date` with `roc_month_or_date_to_timestamp`.

- [ ] **Step 5: Verify and commit**

Run: `\.\.venv\Scripts\python -m pytest tests/test_moi.py -v`

Expected: all parser tests PASS.

```powershell
git add src/qingpu_insight/moi.py tests/fixtures/moi_resale.csv tests/fixtures/moi_presale.csv tests/test_moi.py
git commit -m "feat: expand residential MOI contract"
```

---

### Task 2: Build a versioned market-clean dataset and quality report

**Files:**
- Create: `src/qingpu_insight/market_cleaning.py`
- Create: `tests/test_market_cleaning.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: M0 assigned transaction frame plus Task 1 residential fields.
- Produces: `build_market_dataset(frame: pd.DataFrame) -> tuple[pd.DataFrame, MarketQuality]`, `MarketQuality.to_dict() -> dict[str, object]`, and CLI command `market-build`.

- [ ] **Step 1: Write the cleaning contract tests**

```python
import pandas as pd

from qingpu_insight.market_cleaning import build_market_dataset


def sample_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "transaction_type": ["resale", "presale", "resale", "resale"],
            "record_id": ["R1", "P1", "R2", "R3"],
            "transaction_subject": ["房地(土地+建物)+車位"] * 4,
            "main_use": ["住家用", "住家用", "店鋪", "住家用"],
            "transaction_date": pd.to_datetime(
                ["2026-01-10", "2026-02-10", "2026-03-10", "2026-04-10"]
            ),
            "completion_date": pd.to_datetime(["2020-01-01", None, "2020-01-01", "2020-01-01"]),
            "building_area_sqm": [99.17355, 66.1157, 99.17355, 99.17355],
            "unit_price_sqm_twd": [181500, 211750, 181500, 181500],
            "total_price_twd": [18_000_000, 14_000_000, 18_000_000, 18_000_000],
            "building_type": ["住宅大樓"] * 4,
            "bedrooms": [3, 2, 3, 3],
            "living_rooms": [2, 1, 2, 2],
            "bathrooms": [2, 1, 2, 2],
            "station_code": ["A18", "A17", "A18", None],
            "station_distance_m": [500.0, 800.0, 500.0, None],
            "coordinate_eligible": [True, True, True, False],
            "match_quality": ["exact", "nearest_number", "exact", "unmatched"],
            "longitude": [121.21, 121.22, 121.21, None],
            "latitude": [25.01, 25.02, 25.01, None],
            "source_file": ["a.csv", "b.csv", "a.csv", "a.csv"],
        }
    )


def test_build_market_dataset_keeps_only_eligible_residential_rows() -> None:
    clean, quality = build_market_dataset(sample_rows())
    assert clean["record_id"].tolist() == ["R1", "P1"]
    assert clean["analysis_eligible"].all()
    assert quality.input_records == 4
    assert quality.output_records == 2
    assert quality.exclusion_reasons == {"non_residential": 1, "outside_life_circle": 1}


def test_build_market_dataset_derives_ping_price_age_and_stable_key() -> None:
    clean, _ = build_market_dataset(sample_rows())
    resale = clean.loc[clean["record_id"] == "R1"].iloc[0]
    assert resale["building_area_ping"] == pytest.approx(30.0, rel=1e-4)
    assert resale["unit_price_per_ping_twd"] == pytest.approx(600_000, rel=1e-4)
    assert resale["building_age_years"] == pytest.approx(6.0, abs=0.1)
    assert len(resale["transaction_key"]) == 64
```

- [ ] **Step 2: Run the tests to establish RED**

Run: `\.\.venv\Scripts\python -m pytest tests/test_market_cleaning.py -v`

Expected: collection fails because `market_cleaning` does not exist.

- [ ] **Step 3: Implement explicit eligibility and derivation rules**

```python
from dataclasses import asdict, dataclass
import hashlib

import pandas as pd

SQM_PER_PING = 3.305785
PRICE_PER_PING_MIN = 100_000
PRICE_PER_PING_MAX = 2_000_000


@dataclass(frozen=True)
class MarketQuality:
    input_records: int
    output_records: int
    output_by_type: dict[str, int]
    exclusion_reasons: dict[str, int]
    minimum_date: str | None
    maximum_date: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _key(row: pd.Series) -> str:
    payload = "|".join(
        str(row.get(name, ""))
        for name in ("transaction_type", "record_id", "transaction_date", "source_file")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_market_dataset(frame: pd.DataFrame) -> tuple[pd.DataFrame, MarketQuality]:
    output = frame.copy()
    output["building_area_ping"] = output["building_area_sqm"] / SQM_PER_PING
    output["unit_price_per_ping_twd"] = output["unit_price_sqm_twd"] * SQM_PER_PING
    output["building_age_years"] = (
        output["transaction_date"] - output["completion_date"]
    ).dt.days / 365.2425
    output.loc[output["transaction_type"].eq("presale"), "building_age_years"] = pd.NA
    output["transaction_key"] = output.apply(_key, axis=1)

    residential = output["main_use"].fillna("").str.contains("住家")
    in_circle = output["coordinate_eligible"].fillna(False) & output["station_code"].isin(
        ("A17", "A18", "A19")
    )
    valid_price = output["unit_price_per_ping_twd"].between(
        PRICE_PER_PING_MIN, PRICE_PER_PING_MAX, inclusive="both"
    )
    valid_area = output["building_area_ping"].between(5, 200, inclusive="both")
    valid_date = output["transaction_date"].notna()
    output["analysis_eligible"] = residential & in_circle & valid_price & valid_area & valid_date

    reasons = {
        "non_residential": int((~residential).sum()),
        "outside_life_circle": int((residential & ~in_circle).sum()),
        "invalid_price": int((residential & in_circle & ~valid_price).sum()),
        "invalid_area": int((residential & in_circle & valid_price & ~valid_area).sum()),
        "invalid_date": int((residential & in_circle & valid_price & valid_area & ~valid_date).sum()),
    }
    reasons = {name: count for name, count in reasons.items() if count}
    clean = output.loc[output["analysis_eligible"]].drop_duplicates("transaction_key").copy()
    quality = MarketQuality(
        input_records=len(output),
        output_records=len(clean),
        output_by_type={
            str(kind): int(count)
            for kind, count in clean["transaction_type"].value_counts().items()
        },
        exclusion_reasons=reasons,
        minimum_date=clean["transaction_date"].min().date().isoformat() if len(clean) else None,
        maximum_date=clean["transaction_date"].max().date().isoformat() if len(clean) else None,
    )
    return clean.reset_index(drop=True), quality
```

The implementation must raise `ValueError` when required columns are absent and must validate `transaction_type` against `{"resale", "presale"}` before applying rules.

- [ ] **Step 4: Add the `market-build` CLI path and test it**

Add a CLI subcommand with `--input` defaulting to `data/processed/transactions.parquet`, `--output` defaulting to `data/processed/market_transactions.parquet`, and `--quality-output` defaulting to `outputs/reports/m1-market-quality.json`. Its implementation must call `build_market_dataset`, write Parquet without an index, and serialize quality with UTF-8 and `indent=2`.

Test with a temporary M0 Parquet and assert all three outcomes:

```python
assert exit_code == 0
assert (root / "data/processed/market_transactions.parquet").exists()
payload = json.loads((root / "outputs/reports/m1-market-quality.json").read_text("utf-8"))
assert payload["output_records"] == 2
```

- [ ] **Step 5: Verify and commit**

Run: `\.\.venv\Scripts\python -m pytest tests/test_market_cleaning.py tests/test_cli.py -v`

Expected: all tests PASS.

```powershell
git add src/qingpu_insight/market_cleaning.py src/qingpu_insight/cli.py tests/test_market_cleaning.py tests/test_cli.py
git commit -m "feat: build M1 market dataset"
```

---

### Task 3: Implement deterministic market filters and metrics

**Files:**
- Create: `src/qingpu_insight/market_metrics.py`
- Create: `tests/fixtures/market_transactions.csv`
- Create: `tests/test_market_metrics.py`

**Interfaces:**
- Consumes: Task 2 market dataset.
- Produces: `MarketFilters`, `filter_market`, `market_summary`, `market_trends`, and `recent_transactions`.

- [ ] **Step 1: Create a twelve-row fixture**

The fixture must contain one resale and one presale record per A17/A18/A19 in each of two months. Use distinct prices, coordinates, building types, areas, and room counts so every filter can prove it excludes the correct rows.

- [ ] **Step 2: Write filter and aggregation tests**

```python
from qingpu_insight.market_metrics import (
    MarketFilters,
    filter_market,
    market_summary,
    market_trends,
    recent_transactions,
)


def test_filter_market_never_mixes_transaction_types(market_frame) -> None:
    result = filter_market(market_frame, MarketFilters(transaction_type="presale"))
    assert set(result["transaction_type"]) == {"presale"}


def test_summary_returns_station_kpis_and_data_date(market_frame) -> None:
    result = market_summary(
        market_frame,
        MarketFilters(transaction_type="resale", station_codes=("A18",)),
    )
    assert result["transaction_type"] == "resale"
    assert result["station_codes"] == ["A18"]
    assert result["record_count"] == 2
    assert result["median_unit_price_per_ping_twd"] > 0
    assert result["latest_transaction_date"] == "2026-02-15"


def test_trends_group_by_calendar_month(market_frame) -> None:
    result = market_trends(market_frame, MarketFilters(transaction_type="resale"))
    assert [item["month"] for item in result] == ["2026-01", "2026-02"]
    assert all("median_unit_price_per_ping_twd" in item for item in result)


def test_recent_transactions_limit_and_round_coordinates(market_frame) -> None:
    result = recent_transactions(
        market_frame, MarketFilters(transaction_type="presale"), limit=3
    )
    assert len(result) == 3
    assert result[0]["transaction_date"] >= result[1]["transaction_date"]
    assert len(str(result[0]["latitude"]).split(".")[-1]) <= 4
```

- [ ] **Step 3: Run the tests to establish RED**

Run: `\.\.venv\Scripts\python -m pytest tests/test_market_metrics.py -v`

Expected: collection fails because `market_metrics` does not exist.

- [ ] **Step 4: Implement the typed filter contract**

```python
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MarketFilters:
    transaction_type: str
    station_codes: tuple[str, ...] = ("A17", "A18", "A19")
    date_from: pd.Timestamp | None = None
    date_to: pd.Timestamp | None = None
    area_ping_min: float | None = None
    area_ping_max: float | None = None
    building_types: tuple[str, ...] = ()
    bedrooms: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.transaction_type not in {"resale", "presale"}:
            raise ValueError("transaction_type must be resale or presale")
        if not self.station_codes or not set(self.station_codes) <= {"A17", "A18", "A19"}:
            raise ValueError("station_codes must contain A17, A18, or A19")
        if self.area_ping_min is not None and self.area_ping_min < 0:
            raise ValueError("area_ping_min must be non-negative")
        if (
            self.area_ping_min is not None
            and self.area_ping_max is not None
            and self.area_ping_min > self.area_ping_max
        ):
            raise ValueError("area_ping_min must not exceed area_ping_max")
```

`filter_market` applies every non-empty filter with boolean masks. `market_summary` returns medians and count using Python `int`/`float` values, `market_trends` groups by `transaction_date.dt.to_period("M")`, and `recent_transactions` sorts newest first, caps `limit` at 100, removes exact address, and rounds latitude/longitude to four decimals.

- [ ] **Step 5: Verify and commit**

Run: `\.\.venv\Scripts\python -m pytest tests/test_market_metrics.py -v`

Expected: all tests PASS.

```powershell
git add src/qingpu_insight/market_metrics.py tests/fixtures/market_transactions.csv tests/test_market_metrics.py
git commit -m "feat: add market metrics service"
```

---

### Task 4: Add the MySQL 8 schema, repository boundary, and idempotent loader

**Files:**
- Create: `database/001_market_schema.sql`
- Create: `src/qingpu_insight/market_repository.py`
- Create: `src/qingpu_insight/mysql_loader.py`
- Create: `tests/test_market_repository.py`
- Create: `tests/test_mysql_loader.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Task 2 market Parquet and Task 3 `MarketFilters`.
- Produces: `MarketDataSource.load(filters) -> pd.DataFrame`, `ParquetMarketDataSource`, `MySQLMarketDataSource`, `repository_from_env(root)`, and `load_market_rows(connection, frame, batch_size=1000) -> int`.

- [ ] **Step 1: Add runtime dependencies**

Add exact compatible ranges:

```toml
"flask>=3.1,<4",
"pymysql>=1.1,<2",
```

Run: `\.\.venv\Scripts\python -m pip install -e ".[dev]"`

- [ ] **Step 2: Write the MySQL schema**

```sql
CREATE DATABASE IF NOT EXISTS qingpu_insight
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE qingpu_insight;

CREATE TABLE IF NOT EXISTS data_refreshes (
  refresh_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  dataset_version VARCHAR(32) NOT NULL,
  source_max_date DATE NOT NULL,
  row_count INT UNSIGNED NOT NULL,
  quality_report JSON NOT NULL,
  loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_data_refresh_version (dataset_version)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS market_transactions (
  transaction_key CHAR(64) PRIMARY KEY,
  transaction_type ENUM('resale','presale') NOT NULL,
  record_id VARCHAR(64) NULL,
  station_code ENUM('A17','A18','A19') NOT NULL,
  transaction_date DATE NOT NULL,
  building_area_sqm DECIMAL(12,4) NOT NULL,
  building_area_ping DECIMAL(12,4) NOT NULL,
  unit_price_sqm_twd DECIMAL(14,2) NOT NULL,
  unit_price_per_ping_twd DECIMAL(14,2) NOT NULL,
  total_price_twd BIGINT UNSIGNED NOT NULL,
  building_type VARCHAR(80) NULL,
  bedrooms TINYINT UNSIGNED NULL,
  living_rooms TINYINT UNSIGNED NULL,
  bathrooms TINYINT UNSIGNED NULL,
  building_age_years DECIMAL(8,2) NULL,
  station_distance_m DECIMAL(10,2) NOT NULL,
  longitude DECIMAL(10,7) NOT NULL,
  latitude DECIMAL(10,7) NOT NULL,
  match_quality ENUM('exact','nearest_number') NOT NULL,
  source_file VARCHAR(160) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY ix_market_type_station_date (transaction_type, station_code, transaction_date),
  KEY ix_market_type_date (transaction_type, transaction_date),
  KEY ix_market_filters (transaction_type, building_type, bedrooms, building_area_ping)
) ENGINE=InnoDB;
```

- [ ] **Step 3: Write repository selection tests**

```python
def test_repository_defaults_to_parquet(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("QINGPU_DATABASE_URL", raising=False)
    repository = repository_from_env(tmp_path)
    assert isinstance(repository, ParquetMarketDataSource)


def test_repository_uses_mysql_when_url_is_set(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "QINGPU_DATABASE_URL", "mysql+pymysql://user:pass@localhost/qingpu_insight"
    )
    repository = repository_from_env(tmp_path)
    assert isinstance(repository, MySQLMarketDataSource)
```

The Parquet source must select only rows matching `MarketFilters`. The MySQL source must use parameter placeholders for all values and an allowlisted column list; test its generated SQL and parameters with a fake cursor rather than requiring a live database.

- [ ] **Step 4: Write the loader idempotency test**

Use a fake connection recording `executemany` calls:

```python
def test_loader_uses_upsert_and_returns_loaded_count(market_frame, fake_connection) -> None:
    count = load_market_rows(fake_connection, market_frame, batch_size=5)
    sql, rows = fake_connection.cursor_value.executemany_calls[0]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert count == len(market_frame)
    assert len(rows) == 5
```

- [ ] **Step 5: Implement repository and loader**

Parse `QINGPU_DATABASE_URL` with `urllib.parse.urlparse`; reject schemes other than `mysql` and `mysql+pymysql`. `MySQLMarketDataSource.load` must execute a parameterized `SELECT` and return only the columns needed by Task 3. `load_market_rows` must convert `NaN`/`NaT` to `None`, use batches, and execute:

```sql
INSERT INTO market_transactions (
  transaction_key, transaction_type, record_id, station_code,
  transaction_date, building_area_sqm, building_area_ping,
  unit_price_sqm_twd, unit_price_per_ping_twd, total_price_twd,
  building_type, bedrooms, living_rooms, bathrooms, building_age_years,
  station_distance_m, longitude, latitude, match_quality, source_file
)
VALUES (
  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON DUPLICATE KEY UPDATE
  station_code=VALUES(station_code),
  transaction_date=VALUES(transaction_date),
  unit_price_per_ping_twd=VALUES(unit_price_per_ping_twd),
  total_price_twd=VALUES(total_price_twd),
  updated_at=CURRENT_TIMESTAMP
```

Commit only after rollback-on-error and commit-on-success tests pass.

- [ ] **Step 6: Verify and commit**

Run: `\.\.venv\Scripts\python -m pytest tests/test_market_repository.py tests/test_mysql_loader.py -v`

Expected: all tests PASS without a live MySQL server.

```powershell
git add pyproject.toml database/001_market_schema.sql src/qingpu_insight/market_repository.py src/qingpu_insight/mysql_loader.py tests/test_market_repository.py tests/test_mysql_loader.py
git commit -m "feat: add MySQL market storage"
```

---

### Task 5: Expose the market analysis through a validated Flask API

**Files:**
- Create: `src/qingpu_insight/web.py`
- Create: `tests/test_web.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Task 3 metrics and Task 4 `MarketDataSource`.
- Produces: `create_app(data_source: MarketDataSource | None = None, root: Path | None = None) -> Flask` and executable `qingpu-web`.

- [ ] **Step 1: Write endpoint contract tests with an injected in-memory source**

```python
def test_summary_requires_transaction_type(client) -> None:
    response = client.get("/api/market/summary")
    assert response.status_code == 400
    assert response.get_json() == {
        "error": {
            "code": "invalid_request",
            "message": "請選擇中古屋或預售屋。",
            "fields": {"transaction_type": "required"},
        }
    }


def test_summary_keeps_transaction_type_isolated(client) -> None:
    response = client.get("/api/market/summary?transaction_type=resale&station=A18")
    assert response.status_code == 200
    assert response.get_json()["transaction_type"] == "resale"


def test_trends_and_transactions_share_filters(client) -> None:
    query = "transaction_type=presale&station=A17&date_from=2026-01-01"
    assert client.get(f"/api/market/trends?{query}").status_code == 200
    payload = client.get(f"/api/transactions?{query}&limit=10").get_json()
    assert all(row["station_code"] == "A17" for row in payload["items"])
    assert all(row["transaction_type"] == "presale" for row in payload["items"])


def test_unhandled_exception_uses_safe_error_shape(client, failing_source) -> None:
    response = client.get("/api/market/summary?transaction_type=resale")
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "market_data_unavailable"
    assert "Traceback" not in response.get_data(as_text=True)
```

- [ ] **Step 2: Run endpoint tests to establish RED**

Run: `\.\.venv\Scripts\python -m pytest tests/test_web.py -v`

Expected: collection fails because `web` does not exist.

- [ ] **Step 3: Implement one parser and three routes**

```python
def parse_filters(args: MultiDict[str, str]) -> MarketFilters:
    transaction_type = args.get("transaction_type", "")
    if not transaction_type:
        raise ApiInputError(
            "請選擇中古屋或預售屋。", {"transaction_type": "required"}
        )
    stations = tuple(args.getlist("station") or ("A17", "A18", "A19"))
    return MarketFilters(
        transaction_type=transaction_type,
        station_codes=stations,
        date_from=pd.to_datetime(args.get("date_from"), errors="raise")
        if args.get("date_from")
        else None,
        date_to=pd.to_datetime(args.get("date_to"), errors="raise")
        if args.get("date_to")
        else None,
        area_ping_min=float(args["area_ping_min"]) if args.get("area_ping_min") else None,
        area_ping_max=float(args["area_ping_max"]) if args.get("area_ping_max") else None,
        building_types=tuple(args.getlist("building_type")),
        bedrooms=tuple(int(value) for value in args.getlist("bedrooms")),
    )
```

Routes call the same injected source and filter object:

```python
@app.get("/api/market/summary")
def summary_api():
    filters = parse_filters(request.args)
    return jsonify(market_summary(data_source.load(filters), filters))


@app.get("/api/market/trends")
def trends_api():
    filters = parse_filters(request.args)
    return jsonify({"items": market_trends(data_source.load(filters), filters)})


@app.get("/api/transactions")
def transactions_api():
    filters = parse_filters(request.args)
    limit = min(max(int(request.args.get("limit", "20")), 1), 100)
    return jsonify(
        {
            "items": recent_transactions(data_source.load(filters), filters, limit),
            "limit": limit,
        }
    )
```

All `ValueError`, date, float, and integer parsing failures return HTTP 400 with `invalid_request`; repository failures return HTTP 503 with `market_data_unavailable` and are logged server-side.

- [ ] **Step 4: Register the console command**

```toml
[project.scripts]
qingpu-data = "qingpu_insight.cli:main"
qingpu-web = "qingpu_insight.web:main"
```

`main()` must bind `127.0.0.1`, read `QINGPU_PORT` default `5000`, and enable debug only when `QINGPU_DEBUG=1`.

- [ ] **Step 5: Verify and commit**

Run: `\.\.venv\Scripts\python -m pytest tests/test_web.py -v`

Expected: all API tests PASS.

```powershell
git add pyproject.toml src/qingpu_insight/web.py tests/test_web.py
git commit -m "feat: expose market analysis API"
```

---

### Task 6: Build the portfolio-ready market dashboard

**Files:**
- Create: `src/qingpu_insight/templates/index.html`
- Create: `src/qingpu_insight/static/app.css`
- Create: `src/qingpu_insight/static/app.js`
- Modify: `src/qingpu_insight/web.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: Task 5 endpoints.
- Produces: `GET /` market dashboard with synchronized filters, KPI cards, Leaflet map, Chart.js trend/volume chart, recent transaction table, update date, and limitation copy.

- [ ] **Step 1: Write the page smoke test**

```python
def test_homepage_contains_market_dashboard_contract(client) -> None:
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="transaction-type"' in html
    assert 'id="station-filter"' in html
    assert 'id="market-map"' in html
    assert 'id="price-trend"' in html
    assert 'id="recent-transactions"' in html
    assert "資料更新至" in html
    assert "僅供市場研究" in html
```

- [ ] **Step 2: Run the smoke test to establish RED**

Run: `\.\.venv\Scripts\python -m pytest tests/test_web.py::test_homepage_contains_market_dashboard_contract -v`

Expected: FAIL with HTTP 404.

- [ ] **Step 3: Implement the accessible page skeleton**

`index.html` must contain:

```html
<main>
  <header class="hero">
    <p class="eyebrow">桃園機場捷運 A17–A19</p>
    <h1>青埔智價 <span>Qingpu Insight</span></h1>
    <p>用官方成交資料，看懂青埔三個生活圈的價格、交易量與近期個案。</p>
  </header>
  <section class="controls" aria-label="市場篩選">
    <label>市場
      <select id="transaction-type">
        <option value="resale">中古屋</option>
        <option value="presale">預售屋</option>
      </select>
    </label>
    <fieldset id="station-filter">
      <legend>生活圈</legend>
      <label><input type="checkbox" value="A17" checked>A17</label>
      <label><input type="checkbox" value="A18" checked>A18</label>
      <label><input type="checkbox" value="A19" checked>A19</label>
    </fieldset>
  </section>
  <section id="status" role="status" aria-live="polite"></section>
  <section class="kpis" aria-label="市場摘要">
    <article><span>中位單價</span><strong id="median-price">—</strong></article>
    <article><span>成交筆數</span><strong id="record-count">—</strong></article>
    <article><span>中位總價</span><strong id="median-total">—</strong></article>
    <article><span>資料更新至</span><strong id="latest-date">—</strong></article>
  </section>
  <section class="dashboard-grid">
    <article><h2>成交地圖</h2><div id="market-map" aria-label="成交地圖"></div></article>
    <article><h2>價格與交易量趨勢</h2><canvas id="price-trend"></canvas></article>
  </section>
  <section><h2>近期成交</h2><div id="recent-transactions"></div></section>
  <aside class="method-note">成交來自官方實價登錄；座標為門牌比對結果。僅供市場研究，不構成估價、投資或購屋建議。</aside>
</main>
```

Use these version-pinned assets, then load `app.css` and `app.js` through `url_for('static', filename='app.css')` and `url_for('static', filename='app.js')`:

```html
<link rel="stylesheet"
      href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIINfQ3yn3h/ttZB+QOFqVaGPH92n5r1B4="
      crossorigin="anonymous">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
        crossorigin="anonymous"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
```

- [ ] **Step 4: Implement synchronized JavaScript rendering**

`app.js` must expose no globals except one `DOMContentLoaded` handler. Build one `URLSearchParams` instance from the selected transaction type and checked stations, call all three endpoints with `Promise.all`, and use:

```javascript
const money = new Intl.NumberFormat("zh-TW", {
  style: "currency",
  currency: "TWD",
  maximumFractionDigits: 0,
});

function formatWan(value) {
  return `${new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 1 }).format(value / 10000)} 萬`;
}
```

Abort the previous request with `AbortController` when filters change. Replace map markers and chart data rather than recreating map/chart instances. Render API failures into `#status`, keep the last successful view visible, and never insert API strings with `innerHTML`; build table cells with `textContent`.

- [ ] **Step 5: Implement responsive visual styling**

Use CSS custom properties for a restrained portfolio palette, minimum 44px controls, visible focus states, a two-column layout above 960px, and one column below. Required sizes:

```css
:root {
  --ink: #18302b;
  --muted: #64736f;
  --paper: #f4f1e9;
  --card: #fffdf7;
  --accent: #147d6f;
  --accent-warm: #dd7a45;
  --line: #d8d5cc;
}
#market-map { min-height: 440px; }
.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(320px, .85fr); gap: 1.25rem; }
@media (max-width: 960px) { .dashboard-grid { grid-template-columns: 1fr; } }
```

- [ ] **Step 6: Verify and commit**

Run: `\.\.venv\Scripts\python -m pytest tests/test_web.py -v`

Expected: all API and homepage tests PASS.

```powershell
git add src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.css src/qingpu_insight/static/app.js src/qingpu_insight/web.py tests/test_web.py
git commit -m "feat: build M1 market dashboard"
```

---

### Task 7: Wire the reproducible M1 workflow and document the demo

**Files:**
- Modify: `src/qingpu_insight/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`
- Create: `docs/m1-market-methodology.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: `qingpu-data market-build`, `qingpu-data mysql-load`, documented Parquet/MySQL startup paths, and a five-minute portfolio demo script.

- [ ] **Step 1: Add a fake-connection CLI test for `mysql-load`**

```python
def test_mysql_load_requires_database_url(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("QINGPU_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="QINGPU_DATABASE_URL is required"):
        main(["mysql-load", "--input", str(tmp_path / "market.parquet")])
```

Add a success test that patches the connection factory and asserts `load_market_rows` receives the Parquet frame.

- [ ] **Step 2: Implement `mysql-load`**

The command reads the market Parquet, requires `QINGPU_DATABASE_URL`, opens a PyMySQL connection through the shared Task 4 parser, calls `load_market_rows`, closes the connection in `finally`, and prints exactly:

```text
Loaded <N> market rows into MySQL.
```

- [ ] **Step 3: Document exact local workflows**

README must include these runnable paths:

```powershell
# Build the market-ready Parquet and quality report
.\.venv\Scripts\qingpu-data market-build

# Portfolio demo without MySQL
.\.venv\Scripts\qingpu-web

# Optional MySQL 8 path
$env:QINGPU_DATABASE_URL = "mysql+pymysql://qingpu:password@127.0.0.1:3306/qingpu_insight"
mysql -u root -p < database/001_market_schema.sql
.\.venv\Scripts\qingpu-data mysql-load
.\.venv\Scripts\qingpu-web
```

Document every environment variable, generated file, official source, update date behavior, and the separation between `resale` and `presale`.

- [ ] **Step 4: Write the methodology and five-minute demo**

`docs/m1-market-methodology.md` must state:

1. Data range and the M0 GO evidence (20 seasons, 62.6% eligible coordinate coverage at the M0 checkpoint).
2. Residential eligibility and price/area bounds with the exact constants from Task 2.
3. Doorplate matching qualities and the 2 km nearest-station rule.
4. Why resale and presale are never aggregated into one price KPI.
5. Known limitation: presale exact coordinates are substantially less complete than resale coordinates.
6. Demo order: source traceability → market switch → station comparison → trend → map → recent cases → architecture and limitations.

- [ ] **Step 5: Run the full acceptance suite**

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\qingpu-data analyse --allow-no-go
.\.venv\Scripts\qingpu-data market-build
```

Expected:

- pytest reports zero failures.
- Ruff reports `All checks passed!`.
- M0 prints `M0 decision: GO`.
- market build writes non-empty `data/processed/market_transactions.parquet` and `outputs/reports/m1-market-quality.json`.
- Quality JSON `output_by_type` contains positive `resale` and `presale` counts and its `maximum_date` equals the latest eligible M1 transaction date.

- [ ] **Step 6: Perform a browser smoke test**

Start `qingpu-web`, open `http://127.0.0.1:5000`, and verify:

1. Initial resale KPIs, chart, map, and table load without console errors.
2. Switching to presale changes every visible component and never leaves resale rows in the table.
3. Selecting only A17 updates the summary, trend, map, and table.
4. Mobile width 390px has no horizontal page overflow and controls remain operable.
5. Disconnecting the data source produces a visible Chinese error while the last successful chart remains.

- [ ] **Step 7: Commit the M1 documentation and workflow**

```powershell
git add src/qingpu_insight/cli.py tests/test_cli.py README.md docs/m1-market-methodology.md
git commit -m "docs: complete M1 market workflow"
```

---

## Plan Self-Review

- **Spec coverage:** M1 residential cleaning, A17～A19 life-circle enforcement, MySQL schema, transaction-type isolation, map, trend, volume, recent transactions, source/update disclosure, tests, and portfolio demo are covered. Estimation models, valuation endpoints, similar cases, LLM reports, Docker deployment, scheduled scraping, and alerting remain explicitly in M2～M4.
- **Data safety:** Exact raw addresses never leave the backend response; map coordinates are rounded to four decimals. SQL values are parameterized and schema fields are allowlisted.
- **Type consistency:** Task 3 defines `MarketFilters`; Tasks 4 and 5 consume the same type. Task 4 defines `MarketDataSource.load`; Task 5 injects that same interface. Task 2 is the only producer of the market Parquet consumed by Tasks 3, 4, and 7.
- **Test independence:** MySQL behavior uses fake DB-API connections; the default demo and CI need no live database. Browser smoke testing is the only manual acceptance gate.
- **No silent threshold changes:** The 2 km radius and M0 60% coordinate threshold remain unchanged. M1 adds explicit residential, price, and area rules and reports every exclusion count.
