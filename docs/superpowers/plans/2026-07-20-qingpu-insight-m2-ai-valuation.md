# 青埔智價 M2 AI 估價 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 M1 的 19,200 筆官方成交資料，分別訓練中古屋與預售屋估價模型，提供時間外評估、合理價格區間、可信度、主要影響因素、相似成交與可操作的估價頁面。

**Architecture:** `model_features.py` 建立訓練與推論共用的欄位契約；`model_training.py` 以時間序列切割比較近期中位數、Ridge、Random Forest、HistGradientBoosting，為中古屋與預售屋各自選模並保存單一 `ValuationBundle`。`valuation.py` 只載入已核准 artifact，產生區間、可信度、解釋與相似案例；Flask 負責輸入驗證與 API，前端只呈現結構化證據。模型失效時回退到同交易類型的近期中位數，不呼叫 LLM，也不使用售屋網站資料。

**Tech Stack:** Python 3.11+、Pandas 2.2、NumPy 2、scikit-learn 1.6、Joblib、Flask 3、PyArrow、pytest、HTML5、CSS、原生 JavaScript。

## Global Constraints

- 地理範圍固定為桃園機場捷運 A17、A18、A19，僅使用 M1 `analysis_eligible=True` 的官方實價登錄成交。
- 中古屋 `resale` 與預售屋 `presale` 必須使用不同的資料切割、模型、artifact、評估報告與版本；任何正式估價不可跨類型共用模型。
- 預測目標為不含可拆分車位價值的每坪單價；有有效車位價格與面積時必須先扣除，無法可靠拆分時使用官方每平方公尺單價換算值並在模型卡揭露。
- 測試集固定為各類型最新 12 個月，校準集為測試集之前 6 個月，其餘才是訓練集；不得隨機打散時間。
- 候選比較至少包含近期中位數基準、Ridge、Random Forest、HistGradientBoosting；正式模型必須在時間外 MAE 優於基準，否則發布基準模型。
- 指標至少包含 MAE、MAPE、RMSE、R²，並分別輸出 A17、A18、A19 與主要建物類型誤差；MAPE 分母下限固定為每坪 100,000 元。
- 估價區間使用校準集絕對殘差的 90 百分位，目標覆蓋率為 90%；報告必須列出時間外實際 coverage 與平均區間寬度。
- API 金額單位一律為 TWD，面積一律為坪，距離為公尺，日期為 ISO `YYYY-MM-DD`；不得回傳地址、門牌、TWD97 座標、remarks 或模型內部例外。
- 使用者輸入超出訓練範圍、相似案例不足或模型失效時必須明確降級，不得假裝高信心。
- `data/raw/`、`data/processed/`、`artifacts/`、估價紀錄、完整 Parquet 與密鑰不可提交 Git；只提交程式、少量 fixture、測試、schema-free 報告範本與文件。
- M2 不爬售屋網站、不預測未來漲跌、不使用 LLM 產生或修改價格；目前開價只用於與模型中位估值比較。

---

## File Structure

```text
src/qingpu_insight/
├─ model_features.py          特徵欄位、樓層解析、目標計算、輸入契約
├─ model_training.py          時間切割、候選模型、評估、選模與 artifact
├─ valuation.py               模型登錄、推論、區間、可信度、解釋、相似成交
├─ valuation_reporting.py     JSON 評估報告與 Markdown 模型卡
├─ cli.py                     model-train 與 model-evaluate 指令
├─ web.py                     POST/GET valuation API
├─ templates/index.html       快速估價表單與證據優先結果區
└─ static/{app.css,app.js}    估價互動與結果呈現
artifacts/                    本機生成的 resale/presale joblib，不提交 Git
outputs/reports/              本機生成的評估 JSON 與模型卡，不提交 Git
tests/
├─ fixtures/model_transactions.csv
├─ test_model_features.py
├─ test_model_training.py
├─ test_valuation.py
├─ test_valuation_reporting.py
└─ test_web.py
```

## Public Interfaces

```python
@dataclass(frozen=True)
class ValuationInput:
    transaction_type: Literal["resale", "presale"]
    station_code: Literal["A17", "A18", "A19"]
    building_area_ping: float
    station_distance_m: float
    building_type: str
    bedrooms: int
    living_rooms: int
    bathrooms: int
    building_age_years: float | None
    floor: int
    total_floors: int
    parking_type: str | None
    parking_area_ping: float
    asking_total_price_twd: int | None

train_all: Callable[[pd.DataFrame, Path, Path], dict[str, ModelEvaluation]]
valuate: Callable[[ValuationInput, ModelRegistry, pd.DataFrame], dict[str, Any]]
```

`POST /api/valuations` 成功回應固定包含：`valuation_id`、`transaction_type`、`estimated_unit_price_per_ping_twd`、`estimated_total_price_twd`、`interval_total_price_twd`、`asking_price_assessment`、`confidence`、`confidence_reasons`、`factors`、`comparables`、`model`、`data_date`、`degraded`。`GET /api/valuations/{id}` 回傳相同物件。

## Acceptance Gates

1. `qingpu-data model-train` 能從 `market_transactions.parquet` 產生兩個獨立 artifact、兩份評估 JSON 與兩份模型卡。
2. 每種類型都完成 baseline 加三種模型的相同時間外測試；報告含全體、各站及主要建物類型 MAE/MAPE/RMSE/R²。
3. 正式模型若未穩定勝過 baseline，自動發布 baseline；測試不得以修改門檻或測試期間強迫複雜模型獲勝。
4. 每次估價都有 90% 校準區間、信心等級與原因、至少 0 至 5 筆同類型相似成交、正負影響因素、模型版本與資料日期。
5. 正常、欄位錯誤、超出訓練範圍、artifact 遺失與模型例外都具測試；服務失敗時可用近期中位數回退並標示 `degraded=true`。
6. 首頁可完成一次中古屋及一次預售屋估價，且市場切換不會誤用另一類模型。
7. 完整 `pytest`、Ruff、正式資料訓練、API smoke test 與 390px/1280px 瀏覽器流程全部通過。

---

### Task 1: Lock the model dependency and canonical feature contract

**Files:**
- Modify: `pyproject.toml`
- Create: `src/qingpu_insight/model_features.py`
- Create: `tests/fixtures/model_transactions.csv`
- Create: `tests/test_model_features.py`

**Interfaces:**
- Consumes: M1 canonical columns from `market_transactions.parquet`.
- Produces: `FEATURE_COLUMNS`, `ValuationInput`, `parse_floor(value)`, `build_model_frame(frame, transaction_type)` and `input_frame(value, data_date)`.

- [ ] **Step 1: Write a six-row fixture covering both transaction types, Chinese floor text, parking adjustment, missing presale age and invalid floor order**

```csv
transaction_type,record_id,transaction_date,station_code,station_distance_m,building_area_sqm,building_area_ping,total_price_twd,unit_price_per_ping_twd,building_type,bedrooms,living_rooms,bathrooms,building_age_years,floor,total_floors,parking_type,parking_area_sqm,parking_price_twd,longitude,latitude,analysis_eligible
resale,R1,2024-01-15,A17,500,99.17355,30,18000000,600000,住宅大樓(11層含以上有電梯),3,2,2,6.0,十層,15,坡道平面,33.05785,2000000,121.21,25.01,True
resale,R2,2025-04-15,A18,900,132.2314,40,20000000,500000,華廈(10層含以下有電梯),3,2,2,12.0,五層,10,,0,0,121.22,25.02,True
resale,R3,2026-03-15,A19,1200,82.644625,25,12500000,500000,住宅大樓(11層含以上有電梯),2,1,1,3.0,三層,15,坡道機械,16.528925,1000000,121.20,25.00,True
presale,P1,2024-02-15,A17,600,115.702475,35,24500000,700000,住宅大樓(11層含以上有電梯),3,2,2,,十二層,20,坡道平面,33.05785,2200000,121.21,25.01,True
presale,P2,2025-05-15,A18,700,99.17355,30,21000000,700000,華廈(10層含以下有電梯),2,2,1,,八層,10,,0,0,121.22,25.02,True
presale,P3,2026-04-15,A19,800,132.2314,40,30000000,750000,住宅大樓(11層含以上有電梯),4,2,2,,二十五層,20,坡道平面,33.05785,2500000,121.20,25.00,True
```

- [ ] **Step 2: Write failing tests for parsing, type isolation, parking-adjusted target and online/offline feature parity**

```python
def test_parse_floor_handles_chinese_and_rejects_impossible_values():
    assert parse_floor("十層") == 10
    assert parse_floor("地下二層") == -2
    assert parse_floor("全") is None

def test_build_model_frame_isolates_type_and_adjusts_parking(fixture_frame):
    resale = build_model_frame(fixture_frame, "resale")
    assert set(resale["transaction_type"]) == {"resale"}
    assert resale.loc[resale.record_id.eq("R1"), "target_unit_price_twd"].item() == pytest.approx(800_000)

def test_input_frame_matches_training_feature_columns(valid_resale_input):
    online = input_frame(valid_resale_input, pd.Timestamp("2026-06-12"))
    assert list(online.columns) == list(FEATURE_COLUMNS)

def test_presale_input_rejects_age_and_floor_above_total(valid_presale_input):
    with pytest.raises(ValueError, match="building_age_years must be omitted"):
        replace(valid_presale_input, building_age_years=1.0)
    with pytest.raises(ValueError, match="floor must not exceed total_floors"):
        replace(valid_presale_input, floor=21, total_floors=20)
```

- [ ] **Step 3: Run the new test module and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_features.py -q
```

Expected: collection fails because `qingpu_insight.model_features` does not exist.

- [ ] **Step 4: Add scikit-learn and implement the feature contract**

```toml
"scikit-learn>=1.6,<2",
```

```python
FEATURE_COLUMNS = (
    "station_code", "station_distance_m", "building_area_ping", "building_type",
    "bedrooms", "living_rooms", "bathrooms", "building_age_years", "floor",
    "total_floors", "floor_ratio", "parking_type", "parking_area_ping",
    "transaction_year", "transaction_month",
)

def parking_adjusted_target(row: pd.Series) -> tuple[float, str]:
    area = float(row["building_area_ping"])
    parking_ping = max(float(row.get("parking_area_sqm", 0) or 0) / 3.305785, 0)
    parking_price = max(float(row.get("parking_price_twd", 0) or 0), 0)
    if parking_price > 0 and 0 < parking_ping < area:
        return (float(row["total_price_twd"]) - parking_price) / (area - parking_ping), "split"
    return float(row["unit_price_per_ping_twd"]), "official_unit_price"

def build_model_frame(frame: pd.DataFrame, transaction_type: str) -> pd.DataFrame:
    result = frame.loc[
        frame["analysis_eligible"].fillna(False)
        & frame["transaction_type"].eq(transaction_type)
    ].copy()
    result["floor"] = result["floor"].map(parse_floor)
    result["total_floors"] = pd.to_numeric(result["total_floors"], errors="coerce")
    result["floor_ratio"] = result["floor"] / result["total_floors"]
    result["parking_area_ping"] = result["parking_area_sqm"].fillna(0) / 3.305785
    result["transaction_year"] = result["transaction_date"].dt.year
    result["transaction_month"] = result["transaction_date"].dt.month
    targets = result.apply(parking_adjusted_target, axis=1)
    result[["target_unit_price_twd", "target_policy"]] = pd.DataFrame(
        targets.tolist(), index=result.index
    )
    return result
```

`ValuationInput.__post_init__` 必須逐欄驗證：坪數 5–200、距離 0–2,000、房廳衛 0–10、樓層不得高於總樓層、車位坪數 0–60、中古屋屋齡 0–100、預售屋屋齡必須為 `None`、目前開價若提供則必須大於 0。

- [ ] **Step 5: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests/test_model_features.py -q
git add pyproject.toml src/qingpu_insight/model_features.py tests/fixtures/model_transactions.csv tests/test_model_features.py
git commit -m "feat: add M2 valuation feature contract"
```

Expected: all `test_model_features.py` tests pass.

---

### Task 2: Add deterministic time splits, baseline and metric reporting

**Files:**
- Create: `src/qingpu_insight/model_training.py`
- Create: `tests/test_model_training.py`

**Interfaces:**
- Consumes: output of `build_model_frame`.
- Produces: `TimeSplit`, `RecentMedianBaseline`, `metric_rows(actual, predicted, frame)` and `evaluate_candidate(name, estimator, split)`.

- [ ] **Step 1: Write failing tests for chronological boundaries, unseen-group fallback and metrics**

```python
def test_time_split_never_trains_on_future_rows(model_frame):
    split = split_by_time(model_frame, test_months=12, calibration_months=6)
    assert split.train.transaction_date.max() < split.calibration.transaction_date.min()
    assert split.calibration.transaction_date.max() < split.test.transaction_date.min()

def test_recent_median_falls_back_from_group_to_station_then_global():
    baseline = RecentMedianBaseline(months=24).fit(train_frame)
    assert baseline.predict(unseen_building_same_station)[0] == pytest.approx(
        train_frame.loc[train_frame.station_code.eq("A17"), "target_unit_price_twd"].median()
    )

def test_metrics_include_overall_station_and_building_type_rows():
    rows = metric_rows(actual, predicted, test_frame)
    assert {"overall", "station:A17", "building_type:住宅大樓"} <= set(rows.index)
    assert {"mae", "mape", "rmse", "r2", "count"} <= set(rows.columns)
```

- [ ] **Step 2: Run the focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py -q
```

Expected: import failure for `model_training`.

- [ ] **Step 3: Implement split, baseline and metrics with fixed rules**

```python
@dataclass(frozen=True)
class TimeSplit:
    train: pd.DataFrame
    calibration: pd.DataFrame
    test: pd.DataFrame

def split_by_time(frame: pd.DataFrame, test_months: int = 12, calibration_months: int = 6) -> TimeSplit:
    maximum = frame["transaction_date"].max().normalize()
    test_start = maximum - pd.DateOffset(months=test_months) + pd.Timedelta(days=1)
    calibration_start = test_start - pd.DateOffset(months=calibration_months)
    split = TimeSplit(
        train=frame.loc[frame.transaction_date < calibration_start].copy(),
        calibration=frame.loc[frame.transaction_date.between(calibration_start, test_start, inclusive="left")].copy(),
        test=frame.loc[frame.transaction_date >= test_start].copy(),
    )
    if min(map(len, (split.train, split.calibration, split.test))) < 100:
        raise ValueError("train, calibration, and test must each contain at least 100 rows")
    return split
```

`RecentMedianBaseline` 使用訓練截止日前 24 個月，依 `(station_code, building_type)` 中位數預測；群組少於 20 筆時退到 station，中站點少於 20 筆再退到同交易類型全體中位數。`metric_rows` 對少於 30 筆的分群不發布指標，MAPE 使用 `max(abs(y), 100_000)` 當分母。

- [ ] **Step 4: Add a leakage audit for repeated road/project proxies**

```python
def leakage_audit(split: TimeSplit) -> dict[str, object]:
    train_groups = set(split.train["road_key"].dropna())
    test_groups = set(split.test["road_key"].dropna())
    return {
        "target_in_features": "target_unit_price_twd" in FEATURE_COLUMNS,
        "transaction_key_overlap": bool(
            set(split.train.transaction_key) & set(split.test.transaction_key)
        ),
        "road_group_overlap_count": len(train_groups & test_groups),
    }
```

The training command must stop if `target_in_features` or `transaction_key_overlap` is true. Road overlap is reported as a limitation because the current MOI contract lacks a stable community/project identifier; `road_key` itself is not a model feature.

- [ ] **Step 5: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py -q
git add src/qingpu_insight/model_training.py tests/test_model_training.py
git commit -m "feat: add chronological model evaluation"
```

---

### Task 3: Compare three learned candidates and enforce the release gate

**Files:**
- Modify: `src/qingpu_insight/model_training.py`
- Modify: `tests/test_model_training.py`

**Interfaces:**
- Produces: `candidate_estimators(seed=42)`, `CandidateEvaluation`, `select_release_candidate(results)`.

- [ ] **Step 1: Write failing tests for candidate inventory, deterministic fitting and baseline fallback**

```python
def test_candidate_inventory_contains_required_models():
    assert set(candidate_estimators(seed=42)) == {
        "ridge", "random_forest", "hist_gradient_boosting"
    }

def test_release_gate_requires_overall_improvement_and_station_stability():
    assert select_release_candidate(results_with_ridge_win).name == "ridge"
    assert select_release_candidate(results_with_station_regression).name == "baseline"
```

- [ ] **Step 2: Run the two tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py -q -k "candidate or release"
```

- [ ] **Step 3: Implement a shared preprocessing factory and candidates**

```python
NUMERIC_FEATURES = [
    "station_distance_m", "building_area_ping", "bedrooms", "living_rooms",
    "bathrooms", "building_age_years", "floor", "total_floors", "floor_ratio",
    "parking_area_ping", "transaction_year", "transaction_month",
]
CATEGORICAL_FEATURES = ["station_code", "building_type", "parking_type"]

def make_preprocessor() -> ColumnTransformer:
    return ColumnTransformer([
        ("numeric", Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]), NUMERIC_FEATURES),
        ("categorical", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]), CATEGORICAL_FEATURES),
    ])

def candidate_estimators(seed: int = 42) -> dict[str, Pipeline]:
    return {
        "ridge": Pipeline([("features", make_preprocessor()), ("model", Ridge(alpha=10.0))]),
        "random_forest": Pipeline([("features", make_preprocessor()), ("model", RandomForestRegressor(
            n_estimators=400, min_samples_leaf=5, max_features=0.8,
            random_state=seed, n_jobs=-1,
        ))]),
        "hist_gradient_boosting": Pipeline([("features", make_preprocessor()), ("model", HistGradientBoostingRegressor(
            learning_rate=0.06, max_iter=350, max_leaf_nodes=31,
            l2_regularization=1.0, random_state=seed,
        ))]),
    }
```

- [ ] **Step 4: Implement the deterministic release rule**

```python
def select_release_candidate(results: list[CandidateEvaluation]) -> CandidateEvaluation:
    baseline = next(result for result in results if result.name == "baseline")
    eligible = [result for result in results if result.name != "baseline"
        and result.overall_mae <= baseline.overall_mae * 0.98
        and all(result.station_mape[s] <= baseline.station_mape[s] * 1.10
                for s in ("A17", "A18", "A19"))]
    return min(eligible, key=lambda result: result.overall_mae, default=baseline)
```

All candidate hyperparameters are fixed in M2. Hyperparameter search is deliberately deferred; this makes the portfolio comparison reproducible and prevents tuning on the test set.

- [ ] **Step 5: Run the full training tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py -q
git add src/qingpu_insight/model_training.py tests/test_model_training.py
git commit -m "feat: compare M2 valuation candidates"
```

---

### Task 4: Package conformal intervals, explanations, comparables and confidence

**Files:**
- Create: `src/qingpu_insight/valuation.py`
- Create: `tests/test_valuation.py`

**Interfaces:**
- Consumes: selected candidate, calibration frame and type-isolated training reference.
- Produces: `ValuationBundle`, `ModelRegistry`, `valuate`, `similar_transactions`, `confidence_assessment`.

- [ ] **Step 1: Write failing tests for bundle isolation, interval coverage shape and private fields**

```python
def test_registry_never_serves_other_transaction_type(tmp_path):
    registry = ModelRegistry(tmp_path)
    with pytest.raises(ModelUnavailableError, match="presale"):
        registry.get("presale")

def test_valuation_has_ordered_interval_and_five_or_fewer_comparables(bundle, market):
    result = valuate(valid_resale_input, FakeRegistry(bundle), market)
    low, high = result["interval_total_price_twd"]
    assert low <= result["estimated_total_price_twd"] <= high
    assert len(result["comparables"]) <= 5

def test_comparables_are_same_type_public_and_recent(bundle, market):
    result = valuate(valid_resale_input, FakeRegistry(bundle), market)
    assert all(row["transaction_type"] == "resale" for row in result["comparables"])
    assert all("address" not in row and "transaction_key" not in row for row in result["comparables"])

def test_out_of_range_input_is_low_confidence(bundle, market):
    result = valuate(replace(valid_resale_input, building_area_ping=199), FakeRegistry(bundle), market)
    assert result["confidence"] == "low"
    assert "坪數超出主要訓練範圍" in result["confidence_reasons"]
```

- [ ] **Step 2: Run the module and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_valuation.py -q
```

- [ ] **Step 3: Define and persist the complete bundle**

```python
@dataclass
class ValuationBundle:
    transaction_type: str
    model_name: str
    model_version: str
    pipeline: Any
    interval_abs_residual_twd_per_ping: float
    feature_ranges: dict[str, tuple[float, float]]
    feature_medians: dict[str, float]
    global_importance: list[dict[str, float | str]]
    reference_rows: pd.DataFrame
    data_min_date: str
    data_max_date: str
    metrics: dict[str, Any]
```

`model_version` is `{transaction_type}-{maximum_date}-{sha256(training_contract)[:8]}`. Write to a temporary file and replace `artifacts/{transaction_type}.joblib` only after evaluation and serialization both succeed. Load must verify `bundle.transaction_type` exactly matches the requested type.

- [ ] **Step 4: Implement interval, local factors, nearest cases and confidence**

```python
def prediction_interval(bundle: ValuationBundle, unit_price: float) -> tuple[float, float]:
    radius = bundle.interval_abs_residual_twd_per_ping
    return max(0.0, unit_price - radius), unit_price + radius

def local_factors(bundle: ValuationBundle, row: pd.DataFrame) -> list[dict[str, object]]:
    base = float(bundle.pipeline.predict(row)[0])
    factors = []
    for feature, median in bundle.feature_medians.items():
        changed = row.copy()
        changed.loc[0, feature] = median
        delta = base - float(bundle.pipeline.predict(changed)[0])
        factors.append({"feature": feature, "impact_twd_per_ping": round(delta),
                        "direction": "positive" if delta >= 0 else "negative"})
    return sorted(factors, key=lambda item: abs(item["impact_twd_per_ping"]), reverse=True)[:5]
```

Comparables must be filtered to the same transaction type, station and trailing 36 months first. Rank by standardized distance across area, station distance, bedrooms, age (resale only), floor ratio and transaction recency; add 0.5 penalty for another building type. If fewer than 3 rows remain, expand to same type/all stations/trailing 36 months and return `comparable_scope="expanded_station"`. Public fields are the same whitelist as M1 plus `similarity_score`; coordinates remain rounded to four decimals.

Confidence is `high` only when all numeric values lie within training 5th–95th percentiles, interval width is at most 30% of estimated total, and at least 3 comparables score at least 0.60. One failed condition is `medium`; two or more, any hard 1st–99th percentile violation, or fallback model is `low`. Return each failed rule in `confidence_reasons`.

- [ ] **Step 5: Fit calibration residual and permutation importance in training**

```python
calibration_prediction = selected.estimator.predict(split.calibration[FEATURE_COLUMNS])
radius = float(np.quantile(np.abs(split.calibration.target_unit_price_twd - calibration_prediction), 0.90))
importance = permutation_importance(
    selected.estimator, split.test[FEATURE_COLUMNS], split.test.target_unit_price_twd,
    scoring="neg_mean_absolute_error", n_repeats=5, random_state=42,
)
```

- [ ] **Step 6: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_valuation.py tests/test_model_training.py -q
git add src/qingpu_insight/valuation.py src/qingpu_insight/model_training.py tests/test_valuation.py tests/test_model_training.py
git commit -m "feat: package evidence-first valuations"
```

---

### Task 5: Generate evaluation reports and model cards from the same metadata

**Files:**
- Create: `src/qingpu_insight/valuation_reporting.py`
- Create: `tests/test_valuation_reporting.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `write_evaluation(bundle, candidates, split, report_dir)`、`write_model_card(bundle, candidates, leakage, report_dir)`、CLI `model-train`.

- [ ] **Step 1: Write failing report and CLI tests**

```python
def test_model_card_discloses_required_evidence(tmp_path, trained_bundle):
    path = write_model_card(trained_bundle, candidate_results, leakage, tmp_path)
    text = path.read_text(encoding="utf-8")
    for heading in ("資料期間", "時間切割", "候選模型", "分群誤差", "區間覆蓋率", "限制", "不適用情境"):
        assert heading in text

def test_model_train_builds_both_types_without_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    copy_fixture_to_processed(tmp_path)
    assert main(["model-train"]) == 0
    assert (tmp_path / "artifacts/resale.joblib").exists()
    assert (tmp_path / "artifacts/presale.joblib").exists()
```

- [ ] **Step 2: Run the focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_valuation_reporting.py tests/test_cli.py -q -k "model"
```

- [ ] **Step 3: Implement atomic training outputs and CLI arguments**

```python
model_parser = subparsers.add_parser("model-train")
model_parser.add_argument("--input", default="data/processed/market_transactions.parquet")
model_parser.add_argument("--artifact-dir", default="artifacts")
model_parser.add_argument("--report-dir", default="outputs/reports")
```

The JSON schema must include `transaction_type`, `model_version`, `selected_model`, all candidate metrics, grouped metrics, split dates/counts, leakage audit, target-policy counts, calibration quantile, test coverage, average interval width, feature ranges and data date. Markdown model cards render only from this JSON-compatible metadata so the two documents cannot drift.

- [ ] **Step 4: Ignore generated artifacts and verify atomic replacement**

```gitignore
artifacts/
outputs/valuations/
```

Tests monkeypatch `joblib.dump` to raise and assert an existing approved artifact remains byte-for-byte unchanged.

- [ ] **Step 5: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_valuation_reporting.py tests/test_cli.py -q
git add .gitignore src/qingpu_insight/valuation_reporting.py src/qingpu_insight/cli.py tests/test_valuation_reporting.py tests/test_cli.py
git commit -m "feat: publish M2 model evaluations"
```

---

### Task 6: Add valuation API with durable local records and baseline degradation

**Files:**
- Modify: `src/qingpu_insight/web.py`
- Create: `src/qingpu_insight/valuation_store.py`
- Modify: `tests/test_web.py`
- Create: `tests/test_valuation_store.py`

**Interfaces:**
- Produces: `POST /api/valuations`, `GET /api/valuations/<valuation_id>`, `FileValuationStore`.

- [ ] **Step 1: Write failing API tests for success, validation, isolation, retrieval and degradation**

```python
def test_post_valuation_returns_evidence(client_with_models):
    response = client_with_models.post("/api/valuations", json=VALID_RESALE_PAYLOAD)
    assert response.status_code == 201
    body = response.get_json()
    assert body["transaction_type"] == "resale"
    assert body["interval_total_price_twd"][0] <= body["estimated_total_price_twd"]
    assert {"confidence", "factors", "comparables", "model", "data_date"} <= body.keys()

def test_post_valuation_never_uses_other_type_model(client_with_models):
    response = client_with_models.post("/api/valuations", json=VALID_PRESALE_PAYLOAD)
    assert response.get_json()["model"]["transaction_type"] == "presale"

def test_post_valuation_reports_field_errors(client_with_models):
    response = client_with_models.post("/api/valuations", json={"transaction_type": "resale"})
    assert response.status_code == 400
    assert "building_area_ping" in response.get_json()["error"]["fields"]

def test_missing_artifact_uses_explicit_baseline(client_without_models):
    response = client_without_models.post("/api/valuations", json=VALID_RESALE_PAYLOAD)
    assert response.status_code == 201
    assert response.get_json()["degraded"] is True
    assert response.get_json()["model"]["name"] == "recent_median_baseline"
```

- [ ] **Step 2: Run API tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py tests/test_valuation_store.py -q -k "valuation"
```

- [ ] **Step 3: Implement explicit JSON parsing and stable error fields**

```python
def parse_valuation_input(payload: dict[str, Any]) -> ValuationInput:
    required = ("transaction_type", "station_code", "building_area_ping",
                "station_distance_m", "building_type", "bedrooms", "living_rooms",
                "bathrooms", "floor", "total_floors")
    missing = {name: "required" for name in required if payload.get(name) in (None, "")}
    if missing:
        raise ApiInputError("請完整填寫估價條件。", missing)
    return ValuationInput(
        transaction_type=str(payload["transaction_type"]),
        station_code=str(payload["station_code"]),
        building_area_ping=float(payload["building_area_ping"]),
        station_distance_m=float(payload["station_distance_m"]),
        building_type=str(payload["building_type"]),
        bedrooms=int(payload["bedrooms"]), living_rooms=int(payload["living_rooms"]),
        bathrooms=int(payload["bathrooms"]),
        building_age_years=float(payload["building_age_years"]) if payload.get("building_age_years") is not None else None,
        floor=int(payload["floor"]), total_floors=int(payload["total_floors"]),
        parking_type=payload.get("parking_type"),
        parking_area_ping=float(payload.get("parking_area_ping", 0)),
        asking_total_price_twd=int(payload["asking_total_price_twd"]) if payload.get("asking_total_price_twd") else None,
    )
```

- [ ] **Step 4: Store JSON atomically and expose only generated UUID records**

```python
class FileValuationStore:
    def save(self, value: dict[str, Any]) -> str:
        valuation_id = str(uuid.uuid4())
        path = self.root / f"{valuation_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        return valuation_id

    def get(self, valuation_id: str) -> dict[str, Any] | None:
        try:
            parsed = uuid.UUID(valuation_id)
        except ValueError:
            return None
        path = self.root / f"{parsed}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
```

The fallback computes the same-type, same-station, same-building-type median from the latest 24 months; it uses the calibration residual stored in the latest report when available, otherwise the same cohort's 90th percentile absolute deviation. It always returns `confidence="low"`, `degraded=true`, and a Chinese reason.

- [ ] **Step 5: Run API tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py tests/test_valuation_store.py tests/test_valuation.py -q
git add src/qingpu_insight/web.py src/qingpu_insight/valuation_store.py tests/test_web.py tests/test_valuation_store.py
git commit -m "feat: expose resilient valuation API"
```

---

### Task 7: Add the evidence-first quick valuation experience

**Files:**
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `src/qingpu_insight/static/app.css`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: `POST /api/valuations` contract from Task 6.
- Produces: accessible form `#valuation-form`, result `#valuation-result`, factors and comparable table.

- [ ] **Step 1: Write failing HTML and JavaScript contract tests**

```python
def test_homepage_contains_complete_valuation_contract(client):
    html = client.get("/").get_data(as_text=True)
    for element_id in ("valuation-form", "valuation-type", "valuation-station",
                       "valuation-area", "valuation-distance", "valuation-age",
                       "valuation-floor", "valuation-total-floors", "valuation-bedrooms",
                       "valuation-parking-area", "asking-price", "valuation-result"):
        assert f'id="{element_id}"' in html

def test_frontend_renders_evidence_before_summary(client):
    script = client.get("/static/app.js").get_data(as_text=True)
    assert "interval_total_price_twd" in script
    assert "confidence_reasons" in script
    assert "comparables" in script
    assert "innerHTML =" not in script
```

- [ ] **Step 2: Run contract tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -q -k "valuation or evidence"
```

- [ ] **Step 3: Add semantic form controls and conditional age behavior**

```html
<section class="valuation-panel" aria-labelledby="valuation-title">
  <h2 id="valuation-title">AI 條件估價</h2>
  <form id="valuation-form">
    <label>市場<select id="valuation-type" required><option value="resale">中古屋</option><option value="presale">預售屋</option></select></label>
    <label>生活圈<select id="valuation-station" required><option value="A17">A17</option><option value="A18">A18</option><option value="A19">A19</option></select></label>
    <label>坪數<input id="valuation-area" type="number" min="5" max="200" step="0.1" required></label>
    <label>距捷運（公尺）<input id="valuation-distance" type="number" min="0" max="2000" required></label>
    <label id="valuation-age-label">屋齡<input id="valuation-age" type="number" min="0" max="100" step="0.1"></label>
    <button type="submit">開始估價</button>
  </form>
  <div id="valuation-status" role="status" aria-live="polite"></div>
  <section id="valuation-result" hidden></section>
</section>
```

All remaining fields from `ValuationInput` receive typed controls. Selecting presale disables and clears age; resale makes age required. The submit button stays enabled after server errors, and the first invalid field receives focus.

- [ ] **Step 4: Render results using DOM nodes and evidence order**

```javascript
function renderValuation(result) {
  valuationResult.replaceChildren();
  valuationResult.append(
    priceIntervalCard(result),
    askingAssessmentCard(result.asking_price_assessment),
    confidenceCard(result.confidence, result.confidence_reasons),
    factorList(result.factors),
    comparableTable(result.comparables),
    modelDisclosure(result.model, result.data_date, result.degraded),
    limitationNote()
  );
  valuationResult.hidden = false;
}
```

`asking_price_assessment` labels asking price below interval as `偏低`, inside interval as `合理區間`, and above interval as `偏高`; if the user omits asking price, omit the card. No narrative AI summary appears in M2.

- [ ] **Step 5: Add responsive styling and browser-independent loading/error states**

The valuation form uses `grid-template-columns: repeat(auto-fit, minmax(180px, 1fr))`; every grid child has `min-width: 0`; touch controls are at least 44px high. Confidence cannot rely only on color and must include `高／中／低` text. Comparable rows horizontally scroll inside their own container without increasing document width.

- [ ] **Step 6: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -q
git add src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js src/qingpu_insight/static/app.css tests/test_web.py
git commit -m "feat: add evidence-first valuation UI"
```

---

### Task 8: Document, train on formal data and pass the M2 release review

**Files:**
- Modify: `README.md`
- Create: `docs/m2-valuation-methodology.md`
- Modify: `docs/superpowers/specs/2026-07-19-qingpu-insight-design.md` only if implementation establishes a new permanent decision.

**Interfaces:**
- Documents exact commands, model limitations and five-minute portfolio demo.

- [ ] **Step 1: Add reproducible M2 commands and output inventory**

```powershell
# Build the already-validated M1 feature source
.\.venv\Scripts\qingpu-data.exe market-build

# Train and approve isolated resale/presale artifacts
.\.venv\Scripts\qingpu-data.exe model-train

# Run the valuation product
.\.venv\Scripts\qingpu-web.exe
```

README lists `artifacts/{resale,presale}.joblib`, `outputs/reports/m2-evaluation-*.json`, `outputs/reports/m2-model-card-*.md`, their ignored status, and how to regenerate them.

- [ ] **Step 2: Write methodology with explicit limitations**

The document must state: official-data-only source; 2 km A17–A19 scope; target parking policy; exact train/calibration/test periods and counts; candidate hyperparameters; release gate; full and grouped results; leakage audit; interval method and observed coverage; confidence rules; similarity expansion; inability to identify stable community/project IDs; no asking-listing ingestion; no future-price claim; no professional appraisal claim.

- [ ] **Step 3: Run the complete automated gate from a clean process**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\qingpu-data.exe analyse --allow-no-go
.\.venv\Scripts\qingpu-data.exe market-build
.\.venv\Scripts\qingpu-data.exe model-train
git diff --check
```

Expected: all tests and Ruff pass; M0 remains `GO`; market output remains 19,200 rows unless the official source changed; both transaction types produce an approved artifact or an explicit baseline artifact; no tracked generated binaries or full data.

- [ ] **Step 4: Run API smoke checks against both model types**

```powershell
$resale = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/api/valuations -ContentType application/json -Body ($resalePayload | ConvertTo-Json)
$presale = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/api/valuations -ContentType application/json -Body ($presalePayload | ConvertTo-Json)
if ($resale.model.transaction_type -ne 'resale') { throw 'resale model isolation failed' }
if ($presale.model.transaction_type -ne 'presale') { throw 'presale model isolation failed' }
```

- [ ] **Step 5: Perform browser acceptance at 1280×720 and 390×844**

At each viewport complete one resale and one presale valuation. Verify: no horizontal document overflow; no stale result after market change; interval low ≤ estimate ≤ high; confidence has text plus reasons; comparables match the selected type; model/data date and limitation are visible; an invalid payload remains editable; stopping or renaming the artifact produces a usable low-confidence baseline result.

- [ ] **Step 6: Commit documentation and final release evidence**

```powershell
git add README.md docs/m2-valuation-methodology.md
git commit -m "docs: publish M2 valuation methodology"
git status --short
```

Expected: clean working tree. Generated reports and artifacts exist locally but remain ignored.

---

## Self-Review

- **Spec coverage:** Separate resale/presale models, parking-aware unit-price target, baseline plus three learned candidates, chronological evaluation, grouped errors, leakage audit, 90% interval, explanations, similar transactions, confidence, asking-price comparison, degradation, API, UI, model cards and limitations are all assigned to concrete tasks.
- **Deliberate exclusions:** Listing-site crawling, community/project enrichment, amenity enrichment, Docker, scheduled retraining, model drift monitoring and LLM reports remain M3–M4 work. Their absence is disclosed and does not silently alter M2 behavior.
- **Type consistency:** `FEATURE_COLUMNS`, `ValuationInput`, `ValuationBundle`, `ModelRegistry`, `valuate`, API field names and frontend consumers use the same names throughout all tasks.
- **Release principle:** A complex model is not a success criterion. A reproducible baseline artifact with honest low confidence is the correct release whenever learned candidates fail the fixed gate.
