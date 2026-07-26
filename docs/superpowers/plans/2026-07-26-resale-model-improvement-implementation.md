# Resale Model Improvement Implementation Plan

> **For the implementation model:** REQUIRED SUB-SKILLS: Use `superpowers:executing-plans` to execute this plan task-by-task and `superpowers:test-driven-development` for Tasks 1–10. Do not self-approve the result. Hand the commit range and fresh verification evidence back to the planning/review agent at the review gates below.

**Goal:** Improve the resale valuation model's handling of rising prices and A18 subgroup stability, expose the evidence in the existing model observatory, and safely degrade stale production models to a recent baseline.

**Architecture:** Keep the current scikit-learn candidate family and time split. Add deterministic derived features and recency weights, isolate diagnostics/backtesting in a focused analysis module, persist schema-v2 experiment evidence with schema-v1 compatibility, then let valuation and observatory services consume explicit staleness metadata.

**Tech Stack:** Python 3.12, pandas, NumPy, scikit-learn, Pydantic, Flask, joblib, vanilla JavaScript, pytest, Node contract tests.

## Global Constraints

- Scope is resale only; presale behavior must remain unchanged.
- Do not add XGBoost, CatBoost, external data, project names, builder names, or new third-party dependencies.
- Do not add arbitrary hyperparameter controls, arbitrary paths, arbitrary commands, or automatic publishing.
- Do not claim future-price forecasting; predictions target the latest available official-data date.
- Derived features are exactly `transaction_month_index`, `station_building_type`, `building_age_band`, `area_band`, and `floor_band`.
- Recency weighting uses a 24-month half-life and a minimum weight of `0.10`.
- The recent baseline uses the latest 12 months and falls back from station/building type to station to global median.
- A candidate requires at least 2% overall MAE improvement, no station MAPE regression above 10%, strict A18 MAPE improvement, at least two of three backtests beating baseline, and no backtest station regression above 10%.
- A production model is stale when its data date trails the latest official data by more than 180 days.
- New behavior implementation uses strict TDD: failing test, observed RED, minimal implementation, observed GREEN, then refactor.
- Post-implementation review fixes do not require test-first work. Fix findings directly, run affected tests after each fix, and run the full verification suite at the end.
- The implementation model and review agent are separate roles. The implementation model executes the plan; the planning agent owns review acceptance.
- Review gates must use `superpowers:requesting-code-review`, evaluate findings with `superpowers:receiving-code-review`, and use `superpowers:verification-before-completion` before any acceptance claim.
- The implementation model must pause for review after Task 7 and after Task 11. It must provide `BASE_SHA`, `HEAD_SHA`, the plan path, changed-file list, and fresh test outputs.
- Preserve all unrelated dirty-worktree changes. At execution time, use `superpowers:using-git-worktrees` before implementation if the current workspace is still dirty.

---

## File Map

- `src/qingpu_insight/model_features.py`: canonical base/derived feature contract and train/inference feature generation.
- `src/qingpu_insight/model_training.py`: time split, recent baseline, recency-weighted model fitting, candidate metrics, and existing release selection.
- `src/qingpu_insight/model_analysis.py`: new resale diagnostics, feature experiments, annual backtests, and composed release checks.
- `src/qingpu_insight/model_artifacts.py`: schema-v2 manifest models with schema-v1 read compatibility.
- `src/qingpu_insight/model_training_service.py`: one-click orchestration and persistence of analysis evidence.
- `src/qingpu_insight/valuation.py`: bundle feature contract, staleness detection, and recent-baseline degradation.
- `src/qingpu_insight/model_observatory.py`: read-only official/candidate status projection.
- `src/qingpu_insight/templates/admin.html`: existing model detail panel.
- `src/qingpu_insight/static/models_admin.js`: pure formatting and view-model helpers for new evidence.
- `src/qingpu_insight/static/admin.css`: compact comparison, gate, and warning styles.
- `README.md`: portfolio-facing explanation and operational behavior.

---

### Task 1: Deterministic Derived Feature Contract

**Files:**
- Modify: `src/qingpu_insight/model_features.py`
- Test: `tests/test_model_features.py`

**Interfaces:**
- Produces: `BASE_FEATURE_COLUMNS: tuple[str, ...]`
- Produces: `DERIVED_FEATURE_COLUMNS: tuple[str, ...]`
- Produces: `FEATURE_COLUMNS = BASE_FEATURE_COLUMNS + DERIVED_FEATURE_COLUMNS`
- Produces: `add_derived_features(frame: pd.DataFrame) -> pd.DataFrame`
- Preserves: `build_model_frame(frame, transaction_type)` and `input_frame(value, data_date)`

- [ ] **Step 1: Write failing train/inference parity tests**

```python
def test_derived_features_are_identical_for_training_and_inference():
    value = ValuationInput(
        transaction_type="resale",
        station_code="A18",
        station_distance_m=500.0,
        building_area_ping=20.0,
        building_type="住宅大樓",
        bedrooms=3,
        living_rooms=2,
        bathrooms=2,
        building_age_years=5.0,
        floor=15,
        total_floors=30,
        parking_type="",
        parking_area_ping=0.0,
    )
    raw = pd.DataFrame([{
        "analysis_eligible": True,
        "transaction_type": "resale",
        "transaction_date": pd.Timestamp("2026-06-12"),
        "station_code": "A18",
        "station_distance_m": 500.0,
        "building_area_ping": 20.0,
        "building_type": "住宅大樓",
        "bedrooms": 3,
        "living_rooms": 2,
        "bathrooms": 2,
        "building_age_years": 5.0,
        "floor": "15層",
        "total_floors": "30層",
        "parking_type": "",
        "parking_area_sqm": 0.0,
        "parking_price_twd": 0.0,
        "total_price_twd": 12_000_000,
        "unit_price_per_ping_twd": 600_000.0,
    }])
    trained = build_model_frame(raw, "resale").iloc[0]
    inferred = input_frame(value, pd.Timestamp("2026-06-12")).iloc[0]

    assert trained["transaction_month_index"] == inferred["transaction_month_index"]
    assert trained["station_building_type"] == inferred["station_building_type"]
    assert trained["building_age_band"] == inferred["building_age_band"] == "5_10"
    assert trained["area_band"] == inferred["area_band"] == "small"
    assert trained["floor_band"] == inferred["floor_band"] == "middle"
```

```python
def test_derived_feature_boundaries_and_missing_values():
    frame = pd.DataFrame({
        "transaction_date": pd.to_datetime(["2026-01-01"] * 4),
        "station_code": ["A18"] * 4,
        "building_type": ["住宅大樓"] * 4,
        "building_age_years": [0.0, 5.0, 20.0, np.nan],
        "building_area_ping": [20.0, 20.01, 50.0, 50.01],
        "floor_ratio": [0.33, 0.34, 0.67, np.nan],
    })
    result = add_derived_features(frame)
    assert result["building_age_band"].tolist() == ["0_5", "5_10", "20_plus", "missing"]
    assert result["area_band"].tolist() == ["small", "standard", "standard", "large"]
    assert result["floor_band"].tolist() == ["low", "middle", "middle", "unknown"]
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_features.py -k "derived_feature" -v
```

Expected: import or key failures for the five derived features.

- [ ] **Step 3: Implement the shared feature builder**

Use fixed boundaries and a calendar-independent month index:

```python
BASE_FEATURE_COLUMNS = (
    "station_code", "station_distance_m", "building_area_ping", "building_type",
    "bedrooms", "living_rooms", "bathrooms", "building_age_years", "floor",
    "total_floors", "floor_ratio", "parking_type", "parking_area_ping",
    "transaction_year", "transaction_month",
)
DERIVED_FEATURE_COLUMNS = (
    "transaction_month_index", "station_building_type", "building_age_band",
    "area_band", "floor_band",
)
FEATURE_COLUMNS = BASE_FEATURE_COLUMNS + DERIVED_FEATURE_COLUMNS


def add_derived_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "transaction_date" in result:
        dates = pd.to_datetime(result["transaction_date"])
    else:
        dates = pd.to_datetime({
            "year": result["transaction_year"],
            "month": result["transaction_month"],
            "day": 1,
        })
    result["transaction_month_index"] = dates.dt.year * 12 + dates.dt.month
    result["station_building_type"] = (
        result["station_code"].fillna("unknown").astype(str)
        + "|"
        + result["building_type"].fillna("unknown").astype(str)
    )
    result["building_age_band"] = pd.cut(
        result["building_age_years"],
        bins=[-np.inf, 5, 10, 20, np.inf],
        labels=["0_5", "5_10", "10_20", "20_plus"],
        right=False,
    ).astype("object").fillna("missing")
    result["area_band"] = pd.cut(
        result["building_area_ping"],
        bins=[-np.inf, 20, 50, np.inf],
        labels=["small", "standard", "large"],
        right=True,
    ).astype("object").fillna("unknown")
    result["floor_band"] = pd.cut(
        result["floor_ratio"],
        bins=[-np.inf, 0.33, 0.67, np.inf],
        labels=["low", "middle", "high"],
        right=True,
    ).astype("object").fillna("unknown")
    return result
```

Call `add_derived_features` from both `build_model_frame` and `input_frame`.

- [ ] **Step 4: Run feature and valuation contract tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_features.py tests\test_valuation.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/model_features.py tests/test_model_features.py
git commit -m "feat(models): add deterministic resale features"
```

---

### Task 2: Recent Baseline and Recency-Weighted Fitting

**Files:**
- Modify: `src/qingpu_insight/model_training.py`
- Test: `tests/test_model_training.py`

**Interfaces:**
- Produces: `recency_weights(frame, reference_date=None, half_life_months=24, minimum=0.10) -> np.ndarray`
- Changes: `RecentMedianBaseline(months=12)`
- Changes: `candidate_estimators(feature_columns=FEATURE_COLUMNS)`
- Changes: `run_model_experiment(split, estimators=None, feature_columns=FEATURE_COLUMNS, use_recency_weights=True)`

- [ ] **Step 1: Write failing weight and baseline tests**

```python
def test_recency_weights_use_24_month_half_life_and_floor(model_frame):
    latest = model_frame["transaction_date"].max().normalize()
    sample = pd.DataFrame({
        "transaction_date": [
            latest,
            latest - pd.DateOffset(months=24),
            latest - pd.DateOffset(months=240),
        ]
    })
    weights = recency_weights(sample, reference_date=latest)
    assert weights[0] == pytest.approx(1.0)
    assert weights[1] == pytest.approx(0.5)
    assert weights[2] == pytest.approx(0.10)
```

```python
def test_recent_median_baseline_defaults_to_12_months(fallback_frame):
    baseline = RecentMedianBaseline()
    assert baseline.months == 12
```

```python
class RecordingRegressor(BaseEstimator):
    def fit(self, X, y, sample_weight=None):
        self.received_sample_weight = sample_weight
        self.mean_ = float(np.average(y, weights=sample_weight))
        return self

    def predict(self, X):
        return np.full(len(X), self.mean_)


def test_candidate_fit_receives_recency_weights(model_frame):
    split = split_by_time(model_frame)
    estimator = RecordingRegressor()
    run_model_experiment(
        split,
        estimators={"recording": estimator},
        feature_columns=BASE_FEATURE_COLUMNS,
    )
    assert estimator.received_sample_weight is not None
    assert estimator.received_sample_weight.max() == pytest.approx(1.0)
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_training.py -k "recency_weights or defaults_to_12 or receives_recency" -v
```

Expected: missing function/signature failures.

- [ ] **Step 3: Implement weights and feature-column-aware pipelines**

```python
def recency_weights(
    frame: pd.DataFrame,
    reference_date: pd.Timestamp | None = None,
    half_life_months: int = 24,
    minimum: float = 0.10,
) -> np.ndarray:
    latest = pd.Timestamp(reference_date or frame["transaction_date"].max()).normalize()
    dates = pd.to_datetime(frame["transaction_date"])
    ages = ((latest.year - dates.dt.year) * 12 + latest.month - dates.dt.month).clip(lower=0)
    return np.maximum(minimum, np.power(0.5, ages.to_numpy() / half_life_months))
```

Split numeric/categorical columns by intersection with the requested feature tuple. Fit sklearn pipelines with:

```python
est.fit(
    split.train[list(feature_columns)],
    split.train["target_unit_price_twd"],
    model__sample_weight=recency_weights(split.train),
)
```

Add a private `_fit_candidate` helper: pass `model__sample_weight` to `Pipeline`, pass `sample_weight` when a non-pipeline estimator's `fit` signature declares it, and otherwise call `fit(X, y)` so existing failure-isolation test doubles remain compatible. Keep baseline fitting unweighted and set its default window to 12 months.

- [ ] **Step 4: Run model-training tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_training.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/model_training.py tests/test_model_training.py
git commit -m "feat(models): weight recent resale transactions"
```

---

### Task 3: Resale Data Diagnostics

**Files:**
- Create: `src/qingpu_insight/model_analysis.py`
- Create: `tests/test_model_analysis.py`

**Interfaces:**
- Produces: `build_resale_diagnostics(frame: pd.DataFrame, split: TimeSplit) -> dict[str, object]`
- Dictionary keys: `station_counts`, `missing_rates`, `monthly_summary`, `building_type_summary`, `split_summary`

- [ ] **Step 1: Write failing diagnostics tests**

```python
def test_resale_diagnostics_exposes_a18_drift(model_frame):
    split = split_by_time(model_frame)
    diagnostics = build_resale_diagnostics(model_frame, split)

    assert set(diagnostics["station_counts"]) == {"A17", "A18", "A19"}
    assert "building_age_years" in diagnostics["missing_rates"]
    assert diagnostics["monthly_summary"]
    assert any(row["station_code"] == "A18" for row in diagnostics["building_type_summary"])
    assert set(diagnostics["split_summary"]) == {"train", "calibration", "test"}
```

```python
def test_diagnostics_are_json_serializable(model_frame):
    payload = build_resale_diagnostics(model_frame, split_by_time(model_frame))
    json.dumps(payload)
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_analysis.py -k "diagnostics" -v
```

Expected: module import failure.

- [ ] **Step 3: Implement diagnostics with bounded output**

Return native Python values only. Aggregate monthly rows by `transaction_date.dt.to_period("M")` and station. Include count and median target price. Aggregate building type by station with count and median target price. Report missing rates only for `FEATURE_COLUMNS`, rounded to six decimals. For each split, return row count, date min/max, median target, station proportions, and building-type proportions.

- [ ] **Step 4: Run analysis tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_analysis.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/model_analysis.py tests/test_model_analysis.py
git commit -m "feat(models): add resale drift diagnostics"
```

---

### Task 4: Base, Enhanced, and Ablation Experiments

**Files:**
- Modify: `src/qingpu_insight/model_analysis.py`
- Modify: `src/qingpu_insight/model_training.py`
- Test: `tests/test_model_analysis.py`
- Test: `tests/test_model_training.py`

**Interfaces:**
- Produces: `FeatureExperiment` dataclass with `name`, `feature_columns`, `selected_model`, `metrics`, `candidate_errors`
- Produces: `run_feature_experiments(split: TimeSplit) -> tuple[FeatureExperiment, ...]`
- Experiment names: `base`, `enhanced`, `without_transaction_trend`, `without_station_building_type`, `without_age_band`, `without_area_band`, `without_floor_band`

- [ ] **Step 1: Write failing experiment matrix tests**

```python
def test_feature_experiments_use_identical_time_rows(model_frame):
    split = split_by_time(model_frame)
    results = run_feature_experiments(split)
    assert [r.name for r in results] == [
        "base", "enhanced", "without_transaction_trend",
        "without_station_building_type", "without_age_band",
        "without_area_band", "without_floor_band",
    ]
    assert results[0].feature_columns == BASE_FEATURE_COLUMNS
    assert results[1].feature_columns == FEATURE_COLUMNS
    assert "transaction_month_index" not in results[2].feature_columns
```

```python
def test_feature_experiment_metrics_include_overall_and_a18(model_frame):
    results = run_feature_experiments(split_by_time(model_frame))
    for result in results:
        assert "overall" in result.metrics
        assert "station:A18" in result.metrics
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_analysis.py -k "feature_experiment" -v
```

Expected: missing dataclass/function failures.

- [ ] **Step 3: Implement a bounded experiment matrix**

Run all three existing learned candidates for `base` and `enhanced`. Select the best eligible learned estimator on calibration for the enhanced set, then clone that estimator recipe for the five ablations. Do not add a hyperparameter search. Store calibration metrics as `DataFrame.to_dict(orient="index")`.

Use these exact feature removals:

```python
ABLATIONS = {
    "without_transaction_trend": ("transaction_month_index",),
    "without_station_building_type": ("station_building_type",),
    "without_age_band": ("building_age_band",),
    "without_area_band": ("area_band",),
    "without_floor_band": ("floor_band",),
}
```

- [ ] **Step 4: Run analysis and training tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_analysis.py tests\test_model_training.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/model_analysis.py src/qingpu_insight/model_training.py tests/test_model_analysis.py tests/test_model_training.py
git commit -m "feat(models): compare enhanced and ablated features"
```

---

### Task 5: Three-Cutoff Backtests and Release Checks

**Files:**
- Modify: `src/qingpu_insight/model_analysis.py`
- Test: `tests/test_model_analysis.py`

**Interfaces:**
- Produces: `run_annual_backtests(frame, selected_model_name, feature_columns) -> list[dict[str, object]]`
- Produces: `evaluate_release_checks(candidate_metrics: dict[str, dict[str, float]], baseline_metrics: dict[str, dict[str, float]], backtests: list[dict[str, object]], data_max_date: date, latest_official_date: date) -> dict[str, bool]`
- Produces reason codes: `overall_mae_not_improved`, `station_regression`, `a18_not_improved`, `backtest_insufficient`, `backtest_station_regression`, `candidate_stale`

- [ ] **Step 1: Write failing no-leakage and gate tests**

```python
def test_backtests_never_train_on_future_rows(model_frame):
    rows = run_annual_backtests(
        model_frame,
        selected_model_name="hist_gradient_boosting",
        feature_columns=FEATURE_COLUMNS,
    )
    assert len(rows) == 3
    for row in rows:
        assert row["train_max_date"] < row["test_min_date"]
        assert row["source_max_date"] <= row["cutoff_date"]
```

```python
def _metrics(mae, a17=10.0, a18=20.0, a19=10.0):
    return {
        "overall": {"mae": mae},
        "station:A17": {"mape": a17},
        "station:A18": {"mape": a18},
        "station:A19": {"mape": a19},
    }


def _backtest(passed, stations_within_limit=True):
    return {
        "passed": passed,
        "stations_within_limit": stations_within_limit,
    }


def test_release_checks_require_strict_a18_improvement():
    checks = evaluate_release_checks(
        _metrics(98.0), _metrics(100.0),
        [_backtest(True), _backtest(True), _backtest(False)],
        date(2026, 6, 12), date(2026, 6, 12),
    )
    assert checks["overall_mae_improved"] is True
    assert checks["a18_improved"] is False
    assert checks["recommended"] is False
```

```python
def test_release_checks_require_two_passing_backtests():
    checks = evaluate_release_checks(
        _metrics(97.0, a18=18.0), _metrics(100.0),
        [_backtest(True), _backtest(False), _backtest(False)],
        date(2026, 6, 12), date(2026, 6, 12),
    )
    assert checks["backtests_passed"] is False
    assert checks["recommended"] is False
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_analysis.py -k "backtest or release_checks" -v
```

Expected: missing function failures.

- [ ] **Step 3: Implement annual cutoffs and composed gates**

Choose the three latest year-separated cutoffs ending at the dataset maximum month. For each cutoff, first filter `transaction_date <= cutoff`, then call `split_by_time`; never split the full frame before filtering. Fit only the selected recipe and the 12-month baseline. Store unweighted overall/station metrics and explicit per-backtest booleans.

Compose:

```python
recommended = all((
    overall_mae_improved,
    stations_within_limit,
    a18_improved,
    backtests_passed,
    backtest_stations_within_limit,
    candidate_fresh,
))
```

- [ ] **Step 4: Run analysis tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_analysis.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/model_analysis.py tests/test_model_analysis.py
git commit -m "feat(models): add annual release backtests"
```

---

### Task 6: Schema-v2 Evidence with Schema-v1 Compatibility

**Files:**
- Modify: `src/qingpu_insight/model_artifacts.py`
- Test: `tests/test_model_artifacts.py`

**Interfaces:**
- Changes: `TrainingManifest.schema_version: Literal[1, 2]`
- Adds optional/defaulted `MarketTrainingResult` fields: `feature_contract_version`, `feature_columns`, `diagnostics`, `feature_experiments`, `backtests`, `release_checks`
- New manifests write schema version `2`; old schema-v1 files validate without rewriting.

- [ ] **Step 1: Write failing compatibility tests**

```python
def test_schema_v1_manifest_loads_with_empty_analysis_fields(schema_v1_manifest_json):
    manifest = TrainingManifest.model_validate_json(schema_v1_manifest_json)
    result = manifest.results[0]
    assert manifest.schema_version == 1
    assert result.feature_columns == []
    assert result.diagnostics == {}
    assert result.feature_experiments == []
    assert result.backtests == []
    assert result.release_checks == {}
```

```python
def test_schema_v2_manifest_round_trips_analysis(manifest_v2):
    loaded = TrainingManifest.model_validate_json(manifest_v2.model_dump_json())
    assert loaded.schema_version == 2
    assert loaded.results[0].feature_contract_version == 2
    assert loaded.results[0].release_checks["a18_improved"] is True
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_artifacts.py -k "schema_v1 or schema_v2" -v
```

Expected: schema literal or missing-field failures.

- [ ] **Step 3: Implement defaulted Pydantic fields**

Use `Field(default_factory=dict)` and `Field(default_factory=list)` so schema-v1 manifests remain readable. Keep `extra="forbid"` so malformed unknown fields still fail closed. Set new manifests explicitly to `schema_version=2` in the training service; do not mutate schema-v1 files on read.

- [ ] **Step 4: Run artifact tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_artifacts.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/model_artifacts.py tests/test_model_artifacts.py
git commit -m "feat(models): persist model analysis evidence"
```

---

### Task 7: One-Click Training Orchestration and Reports

**Files:**
- Modify: `src/qingpu_insight/model_training_service.py`
- Modify: `src/qingpu_insight/valuation_reporting.py`
- Modify: `src/qingpu_insight/valuation.py`
- Test: `tests/test_model_training_service.py`
- Test: `tests/test_valuation_reporting.py`
- Test: `tests/test_valuation.py`

**Interfaces:**
- Resale `MarketTrainingResult` receives diagnostics, feature experiments, backtests, and release checks.
- Presale continues through the current experiment path and receives default empty analysis fields.
- Evaluation JSON and model card include time weighting, A18, backtests, ablations, and every release check.
- `ValuationBundle.feature_columns` stores the exact tuple used by its pipeline.
- `train_artifact(..., feature_columns: tuple[str, ...] = FEATURE_COLUMNS)` uses that tuple for prediction intervals, importance, ranges, and contract hashing.

- [ ] **Step 1: Write failing resale orchestration tests**

```python
def test_resale_training_writes_schema_v2_analysis(tmp_path, market_parquet):
    service, jobs = service_fixture(tmp_path, input_path=market_parquet)
    run = service.submit(ModelTrainingRequest(("resale",))).run
    jobs.start(run.run_id)
    manifest = service.execute(run.run_id, ModelTrainingRequest(("resale",)))
    result = manifest.results[0]
    assert manifest.schema_version == 2
    assert result.market == "resale"
    assert result.feature_contract_version == 2
    assert result.diagnostics["station_counts"]["A18"] > 0
    assert len(result.feature_experiments) == 7
    assert len(result.backtests) == 3
    assert "a18_improved" in result.release_checks
    assert result.recommended is result.release_checks["recommended"]
```

```python
def test_presale_training_does_not_run_resale_analysis(tmp_path, market_parquet):
    service, jobs = service_fixture(tmp_path, input_path=market_parquet)
    run = service.submit(ModelTrainingRequest(("presale",))).run
    jobs.start(run.run_id)
    manifest = service.execute(run.run_id, ModelTrainingRequest(("presale",)))
    result = manifest.results[0]
    assert result.market == "presale"
    assert result.feature_experiments == []
    assert result.backtests == []
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_training_service.py -k "schema_v2_analysis or does_not_run_resale" -v
```

Expected: schema or empty-analysis assertion failures.

- [ ] **Step 3: Integrate analysis without changing the web request**

For resale: build one split, diagnostics, feature experiments, selected enhanced recipe, final unweighted test metrics, three backtests, and release checks. Train the saved artifact with the exact selected feature tuple. Derive `recommended` and `reason_codes` from `release_checks`. For presale, preserve the existing call to `run_model_experiment`.

Add `feature_columns: tuple[str, ...] = FEATURE_COLUMNS` as the final defaulted `ValuationBundle` field. When consuming old joblib files, use `tuple(getattr(bundle, "feature_columns", BASE_FEATURE_COLUMNS))`. Update `train_artifact` to operate on its `feature_columns` argument instead of the module-global tuple.

Add report JSON keys:

```python
{
    "recency_weighting": {"half_life_months": 24, "minimum": 0.10},
    "diagnostics": diagnostics,
    "feature_experiments": feature_experiments,
    "backtests": backtests,
    "release_checks": release_checks,
}
```

- [ ] **Step 4: Run service and reporting tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_training_service.py tests\test_valuation_reporting.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/model_training_service.py src/qingpu_insight/valuation_reporting.py src/qingpu_insight/valuation.py tests/test_model_training_service.py tests/test_valuation_reporting.py tests/test_valuation.py
git commit -m "feat(models): orchestrate resale evidence pipeline"
```

---

### Reviewer Gate A: Model and Artifact Pipeline

**Owner:** Planning/review agent, not the implementation model.

**Required Superpowers workflow:**
- `superpowers:requesting-code-review`
- `superpowers:receiving-code-review`
- `superpowers:verification-before-completion`

- [ ] **Step 1: Receive a precise implementation handoff**

Require:

```text
PLAN: docs/superpowers/plans/2026-07-26-resale-model-improvement-implementation.md
SCOPE: Tasks 1-7
BASE_SHA: literal SHA recorded immediately before Task 1
HEAD_SHA: literal SHA printed by `git rev-parse HEAD` after Task 7
VERIFICATION: exact commands, exit codes, and failure counts
```

The implementation model records the first output before Task 1 and the second output after Task 7:

```powershell
git rev-parse HEAD
# Execute and commit Tasks 1-7.
git rev-parse HEAD
```

- [ ] **Step 2: Request an independent Superpowers code review**

Use `superpowers:requesting-code-review` with the Task 1–7 description, approved design, plan path, `BASE_SHA`, and `HEAD_SHA`. The reviewer receives the work product and requirements, not the planning session history.

- [ ] **Step 3: Evaluate the returned findings technically**

Use `superpowers:receiving-code-review`:

- Verify every finding against the actual diff and codebase.
- Fix Critical and Important findings before Task 8.
- Reject suggestions that add XGBoost, external data, project/builder names, generalized MLOps, or other YAGNI scope.
- Ask the user only if a finding conflicts with an approved architectural decision.

- [ ] **Step 4: Apply accepted review fixes without TDD**

The planning/review agent may edit accepted findings directly. Run the closest affected tests after each fix. This review-repair step is explicitly exempt from test-first work.

- [ ] **Step 5: Verify the reviewed subsystem before release to Task 8**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_features.py tests\test_model_training.py tests\test_model_analysis.py tests\test_model_artifacts.py tests\test_model_training_service.py tests\test_valuation_reporting.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

Only after fresh successful output may the planning/review agent approve continuation to Task 8.

---

### Task 8: Stale-Model Detection and Baseline Degradation

**Files:**
- Modify: `src/qingpu_insight/valuation.py`
- Modify: `src/qingpu_insight/web.py`
- Test: `tests/test_valuation.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `model_age_days(bundle: ValuationBundle, latest_data_date: pd.Timestamp) -> int`
- Changes: `valuate(..., latest_data_date: pd.Timestamp | None = None, stale_after_days: int = 180)`
- Adds response field: `degraded_reason: Literal["stale_model", "artifact_unavailable"] | None`
- Preserves existing response fields.

- [ ] **Step 1: Write failing stale/fresh behavior tests**

```python
def test_stale_model_uses_recent_baseline(bundle, market, valid_resale_input):
    stale_bundle = replace(bundle, data_max_date="2024-12-12")
    result = valuate(
        valid_resale_input,
        FakeRegistry(stale_bundle),
        market,
        latest_data_date=pd.Timestamp("2026-06-12"),
        stale_after_days=180,
    )
    assert result["degraded"] is True
    assert result["degraded_reason"] == "stale_model"
    assert result["model"]["name"] == "recent_median_baseline"
    assert "正式模型資料過舊" in result["confidence_reasons"]
```

```python
def test_fresh_model_remains_official(bundle, market, valid_resale_input):
    fresh_bundle = replace(bundle, data_max_date="2024-12-12")
    result = valuate(
        valid_resale_input,
        FakeRegistry(fresh_bundle),
        market,
        latest_data_date=pd.Timestamp("2025-01-01"),
        stale_after_days=180,
    )
    assert result["degraded"] is False
    assert result["degraded_reason"] is None
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_valuation.py -k "stale_model or fresh_model" -v
```

Expected: signature or response-field failures.

- [ ] **Step 3: Implement explicit staleness and 12-month fallback**

Compare normalized dates. If age is above 180 days, skip `bundle.pipeline.predict` and call a shared recent-baseline helper over the supplied market frame. Filter fallback rows to the 12 months ending at `latest_data_date`; apply station/building type, station, then global median fallback with the same minimum group count of 20.

In the valuation endpoint, obtain `latest_data_date` from the filtered official market data source and pass it to `estimate`.

- [ ] **Step 4: Run valuation and web tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_valuation.py tests\test_web.py -k "valuation or stale" -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/valuation.py src/qingpu_insight/web.py tests/test_valuation.py tests/test_web.py
git commit -m "feat(models): degrade stale valuations safely"
```

---

### Task 9: Observatory API Projection

**Files:**
- Modify: `src/qingpu_insight/model_observatory.py`
- Test: `tests/test_model_observatory.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Official model status adds `age_days: int | None`, `stale: bool`, `stale_after_days: 180`.
- Training-run detail exposes defaulted schema-v2 evidence without leaking artifact paths.
- Schema-v1 run detail returns empty analysis collections and `analysis_available: false`.

- [ ] **Step 1: Write failing observatory tests**

```python
def test_official_model_status_marks_2024_model_stale(tmp_path):
    observatory = observatory_fixture(
        tmp_path,
        official_models={"resale": bundle_fixture()},
        latest_data_date=pd.Timestamp("2026-06-12"),
    )
    status = observatory.status()
    resale = status["official_models"]["resale"]
    assert resale["stale"] is True
    assert resale["age_days"] > 180
    assert resale["stale_after_days"] == 180
```

```python
def test_schema_v1_run_detail_has_safe_empty_analysis(tmp_path):
    manifest = manifest_fixture(markets=["resale"])
    observatory = observatory_fixture(tmp_path, candidate_runs=[manifest])
    detail = observatory.get_run(str(manifest.run_id))
    result = detail["manifest"]["results"][0]
    assert result["analysis_available"] is False
    assert result["feature_experiments"] == []
    assert result["backtests"] == []
    assert result["release_checks"] == {}
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_observatory.py -k "stale or safe_empty_analysis" -v
```

Expected: missing projected fields.

- [ ] **Step 3: Add read-only projection helpers**

Extend the existing `observatory_fixture` test helper with `latest_data_date: pd.Timestamp | None = None`; generate its 100 fixture dates ending at that value when supplied. Calculate official-model age against `data_status.max_date`, never against wall-clock time. Add `analysis_available = bool(feature_experiments or backtests or release_checks)` to each run result. Continue returning public relative report identifiers only; do not return local filesystem paths.

- [ ] **Step 4: Run observatory and route tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_observatory.py tests\test_web.py -k "model_observatory or model_training_runs" -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/model_observatory.py tests/test_model_observatory.py tests/test_web.py
git commit -m "feat(models): expose resale evidence and staleness"
```

---

### Task 10: Existing Model Observatory UI

**Files:**
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/models_admin.js`
- Modify: `src/qingpu_insight/static/admin.css`
- Modify: `tests/js/model_admin_contract.cjs`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces pure helpers: `modelComparisonRows(result)`, `stationRows(result)`, `ablationRows(result)`, `backtestRows(result)`, `releaseCheckRows(result)`
- Keeps route `/admin#models`; no new page or free-form controls.

- [ ] **Step 1: Write failing Node view-model contracts**

```javascript
const result = {
  feature_experiments: [
    { name: "enhanced", selected_model: "hist_gradient_boosting",
      metrics: { overall: { mae: 67000 }, "station:A18": { mape: 16.2 } } }
  ],
  backtests: [{ cutoff_date: "2026-06-12", passed: true }],
  release_checks: { overall_mae_improved: true, a18_improved: true, recommended: true }
};
assert.equal(admin.ablationRows(result)[0].name, "enhanced");
assert.equal(admin.stationRows(result)[0].station, "A18");
assert.equal(admin.backtestRows(result)[0].passed, true);
assert.equal(admin.releaseCheckRows(result).at(-1).code, "recommended");
assert.deepEqual(admin.ablationRows({}), []);
```

- [ ] **Step 2: Run contracts and observe RED**

Run:

```powershell
node tests\js\model_admin_contract.cjs
```

Expected: missing helper failure.

- [ ] **Step 3: Implement compact, accessible sections**

Render inside `#ma-detail-content`:

1. Data diagnostics summary.
2. Overall candidate comparison table.
3. A17–A19 MAPE table with A18 change state.
4. Feature experiment/ablation table.
5. Three-cutoff backtest table.
6. Release-check list with pass/fail text.
7. Schema-v1 notice: `舊版模型未包含特徵實驗與時間回測。`

For official cards, render `模型已過期（落後 N 天）` when `stale === true`. Use text plus color; do not rely on color alone.

- [ ] **Step 4: Run Node and HTML contract tests**

Run:

```powershell
node tests\js\model_admin_contract.cjs
.\.venv\Scripts\python.exe -m pytest tests\test_web.py -k "model_admin_page" -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/templates/admin.html src/qingpu_insight/static/models_admin.js src/qingpu_insight/static/admin.css tests/js/model_admin_contract.cjs tests/test_web.py
git commit -m "feat(models): show resale experiments in observatory"
```

---

### Task 11: Documentation and End-to-End Feature Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-26-resale-model-improvement-design.md` only if implementation names differ from the approved design

**Interfaces:**
- Documents one-click training, release gates, stale fallback, limitations, and interview demo sequence.

- [ ] **Step 1: Add the portfolio-facing operational narrative**

Document:

- Data counts and date semantics.
- Existing estimator family and why XGBoost is intentionally excluded.
- Derived features and 24-month half-life.
- Strict temporal split and three annual backtests.
- Exact release checks.
- How stale fallback behaves.
- A five-minute demo: update data → train resale → inspect A18/backtests → preview publish → confirm only if eligible.
- Limitation: this estimates current reasonable price and does not forecast future prices.

- [ ] **Step 2: Run focused feature verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_features.py tests\test_model_training.py tests\test_model_analysis.py tests\test_model_artifacts.py tests\test_model_training_service.py tests\test_valuation.py tests\test_model_observatory.py tests\test_web.py -q
node tests\js\model_admin_contract.cjs
```

Expected: PASS.

- [ ] **Step 3: Run full quality gates**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Get-ChildItem tests\js -Filter '*.cjs' | ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
.\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: all Python tests pass, all Node contracts pass, Ruff reports `All checks passed!`.

- [ ] **Step 4: Perform browser feature acceptance**

Start `.\.venv\Scripts\qingpu-web.exe`, open `http://127.0.0.1:5000/admin#models`, and verify:

- Existing 2024 resale model shows stale warning.
- One-click resale training submits exactly one tracked job.
- Completed run detail shows diagnostics, model comparison, A18 MAPE, ablations, three backtests, and release checks.
- Schema-v1 historical runs show the legacy-analysis notice without console errors.
- Homepage valuation using a stale model reports degraded baseline and the stale reason.

- [ ] **Step 5: Commit documentation**

```powershell
git add README.md docs/superpowers/plans/2026-07-26-resale-model-improvement-design.md
git commit -m "docs: explain resale model evidence workflow"
```

---

### Reviewer Gate B: Final Superpowers Review and Direct Fixes

**Files:**
- Review all files changed by Tasks 1–11.
- Modify only files required by concrete review findings.

**Interfaces:**
- Owner: planning/review agent, not the implementation model.
- Required skills: `superpowers:requesting-code-review`, `superpowers:receiving-code-review`, and `superpowers:verification-before-completion`.
- This is the explicitly approved non-TDD repair phase.

- [ ] **Step 1: Receive the final implementation handoff**

Require the implementation model to provide:

```text
PLAN: docs/superpowers/plans/2026-07-26-resale-model-improvement-implementation.md
SCOPE: Tasks 8-11 plus the reviewed Task 1-7 base
BASE_SHA: literal SHA approved and recorded at Reviewer Gate A
HEAD_SHA: literal SHA printed by `git rev-parse HEAD` after Task 11
VERIFICATION: exact commands, exit codes, and failure counts
```

- [ ] **Step 2: Request the final independent code review**

Use `superpowers:requesting-code-review` with the approved design, complete plan, `BASE_SHA`, `HEAD_SHA`, and this checklist:

- Train/inference feature parity.
- No future rows in any backtest.
- Evaluation remains unweighted.
- Release booleans exactly match the approved thresholds.
- Schema-v1 manifests and old joblib bundles load safely.
- Presale remains unchanged.
- Stale fallback never silently reports itself as an official-model prediction.
- API responses contain no absolute local paths.
- UI handles empty/legacy evidence without exceptions.
- No scope expansion beyond the approved portfolio project.

- [ ] **Step 3: Receive and evaluate review findings**

Use `superpowers:receiving-code-review` before editing:

- Read all findings.
- Restate unclear technical requirements and stop until clarified.
- Verify each finding against this repository.
- Fix Critical and Important findings.
- Accept Minor findings only when they improve the approved deliverable without expanding scope.
- Push back with code/test evidence when a suggestion is incorrect or violates YAGNI.

- [ ] **Step 4: Fix accepted findings directly**

Do not require a new failing test before each review fix. Keep each change limited to the finding. After each fix, run the closest existing test file, for example:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_analysis.py -q
```

or:

```powershell
node tests\js\model_admin_contract.cjs
```

- [ ] **Step 5: Re-review repaired Critical or Important findings**

If the first review contained any Critical or Important finding, request a focused follow-up review over the repair commit range. Repeat technical evaluation until no unresolved Critical or Important finding remains.

- [ ] **Step 6: Use verification-before-completion**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Get-ChildItem tests\js -Filter '*.cjs' | ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
.\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: all checks pass.

- [ ] **Step 7: Commit review repairs if any**

```powershell
git add src/qingpu_insight tests README.md
git commit -m "fix: address resale model review findings"
```

If review finds nothing, do not create an empty commit.
