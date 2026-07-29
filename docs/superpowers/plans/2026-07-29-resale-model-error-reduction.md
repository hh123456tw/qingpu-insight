# Resale Model Error Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct official-model evaluation reporting, remove confirmed non-market transactions, add spatial and log-target candidates, expose residual diagnostics, and publish a freshly verified resale model.

**Architecture:** Keep raw official transactions recoverable while expressing model eligibility through explicit reason codes. Version the feature contract to add optional spatial coordinates, keep legacy artifacts readable, and store final-test metrics in the deployed bundle. Extend the existing diagnostics/report pipeline instead of adding a separate analytics system.

**Tech Stack:** Python 3.12, pandas, scikit-learn, pyproj, Flask, vanilla JavaScript, pytest, Node.js contract tests, MySQL-backed admin jobs.

## Global Constraints

- Follow test-driven development: every behavior change starts with a test that fails for the expected reason.
- Do not delete raw source data, database history, old candidates, or old official model versions.
- Do not exclude every row containing `預售屋、或土地及建物分件登記案件`.
- Only publish a new model if the existing final-test, station, three-period backtest, freshness, and parking consistency checks pass.
- Keep legacy feature-contract artifacts loadable.
- Do not expose full doorplate addresses or personal data in residual reports.
- Use final-test metrics for official model cards; calibration metrics remain candidate-selection evidence.

---

### Task 1: Explicit Non-Market Transaction Eligibility

**Files:**
- Modify: `src/qingpu_insight/market_cleaning.py`
- Modify: `tests/test_market_cleaning.py`
- Modify: `tests/test_official_data.py`

**Interfaces:**
- Produces: `SPECIAL_RELATIONSHIP_PATTERN: re.Pattern[str]`
- Produces: `market_eligibility(frame: pd.DataFrame) -> tuple[pd.Series, dict[str, pd.Series]]`
- `build_market_dataset()` continues returning `(DataFrame, MarketQuality)` but retains deduplicated ineligible rows with `analysis_eligible=False`.

- [ ] **Step 1: Write failing eligibility tests**

Add rows representing a normal sale, a `建物`-only transaction, a special-relationship transaction, and an ambiguous presale/split-registration note:

```python
def test_market_dataset_marks_confirmed_non_market_transactions_ineligible(base_frame):
    frame = pd.concat(
        [
            base_frame.assign(record_id="normal", transaction_subject="房地(土地+建物)"),
            base_frame.assign(record_id="building", transaction_subject="建物"),
            base_frame.assign(
                record_id="relative",
                transaction_subject="房地(土地+建物)+車位",
                remarks="親友、員工、共有人或其他特殊關係間之交易；",
            ),
            base_frame.assign(
                record_id="ambiguous",
                transaction_subject="房地(土地+建物)+車位",
                remarks="預售屋、或土地及建物分件登記案件；",
            ),
        ],
        ignore_index=True,
    )

    result, quality = build_market_dataset(frame)
    eligible = result.set_index("record_id")["analysis_eligible"].to_dict()

    assert eligible["normal"] is True
    assert eligible["building"] is False
    assert eligible["relative"] is False
    assert eligible["ambiguous"] is True
    assert quality.exclusion_reasons["non_market_subject"] == 1
    assert quality.exclusion_reasons["special_relationship"] == 1
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_market_cleaning.py -q
```

Expected: FAIL because non-market subjects and relationship remarks are not part of eligibility and ineligible rows are currently discarded.

- [ ] **Step 3: Implement eligibility masks**

Add exact accepted subjects and the confirmed relationship pattern:

```python
MARKET_TRANSACTION_SUBJECTS = frozenset(
    {"房地(土地+建物)", "房地(土地+建物)+車位"}
)
SPECIAL_RELATIONSHIP_PATTERN = re.compile(
    r"親友|員工|共有人|特殊關係|二等親"
)

def market_eligibility(frame: pd.DataFrame):
    residential = frame["main_use"].fillna("").str.contains("住家")
    market_subject = frame["transaction_subject"].isin(MARKET_TRANSACTION_SUBJECTS)
    special_relationship = frame["remarks"].fillna("").str.contains(
        SPECIAL_RELATIONSHIP_PATTERN
    )
    # Return the final mask and named masks so quality reporting cannot drift.
```

Return all deduplicated rows from `build_market_dataset`; set `analysis_eligible` from the combined mask. Keep `MarketQuality.output_records` as the eligible row count and add the two stable exclusion reasons.

- [ ] **Step 4: Run market and official-data tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_market_cleaning.py tests/test_official_data.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/market_cleaning.py tests/test_market_cleaning.py tests/test_official_data.py
git commit -m "fix(data): exclude confirmed non-market transactions"
```

---

### Task 2: Store Final-Test Metrics in Official Artifacts

**Files:**
- Modify: `src/qingpu_insight/valuation.py`
- Modify: `src/qingpu_insight/model_training_service.py`
- Modify: `src/qingpu_insight/model_observatory.py`
- Modify: `tests/test_valuation.py`
- Modify: `tests/test_model_training_service.py`
- Modify: `tests/test_model_observatory.py`

**Interfaces:**
- `train_artifact()` gains keyword argument `reporting_metrics: dict[str, Any] | None = None` and still returns `Path`.
- `ValuationBundle.metrics` means final-test metrics for newly trained artifacts.
- Official reports expose `evaluation_split: "final_test"`.

- [ ] **Step 1: Write failing artifact and observatory tests**

```python
def test_train_artifact_persists_explicit_final_test_metrics(training_case):
    final_metrics = {"overall": {"mae": 42_000.0, "count": 696.0}}
    path = train_artifact(
        **training_case.train_artifact_kwargs,
        reporting_metrics=final_metrics,
    )
    bundle = joblib.load(path)
    assert bundle.metrics == final_metrics

def test_official_report_labels_bundle_metrics_as_final_test(bundle):
    report = _official_model_report(bundle)
    assert report["evaluation_split"] == "final_test"
    assert report["test_count"] == 696
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_valuation.py tests/test_model_training_service.py tests/test_model_observatory.py -q
```

Expected: FAIL because `train_artifact` only persists the selected calibration evaluation and the report has no split label.

- [ ] **Step 3: Implement the explicit reporting metric contract**

In `train_artifact`, use:

```python
metrics=(
    deepcopy(reporting_metrics)
    if reporting_metrics is not None
    else selected.metrics.to_dict(orient="index")
)
```

In `_finalize_candidate`, pass:

```python
final_evaluation = experiment.final_test_results[model_name]
reporting_metrics=final_evaluation.metrics.to_dict(orient="index")
```

Add `evaluation_split` to `_official_model_report`. Legacy bundles without the new metadata remain readable; only newly trained artifacts claim `final_test`.

- [ ] **Step 4: Verify GREEN**

Run the same three test modules and expect PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/valuation.py src/qingpu_insight/model_training_service.py src/qingpu_insight/model_observatory.py tests/test_valuation.py tests/test_model_training_service.py tests/test_model_observatory.py
git commit -m "fix(models): report final test metrics"
```

---

### Task 3: Feature Contract v3 with Optional Spatial Coordinates

**Files:**
- Modify: `src/qingpu_insight/model_features.py`
- Modify: `src/qingpu_insight/model_training.py`
- Modify: `src/qingpu_insight/geo.py`
- Modify: `src/qingpu_insight/valuation.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `tests/test_model_features.py`
- Modify: `tests/test_model_training.py`
- Modify: `tests/test_geo.py`
- Modify: `tests/test_valuation.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Add feature names `twd97_x`, `twd97_y`, `location_known`.
- Add `wgs84_to_twd97(longitude: float, latitude: float) -> tuple[float, float]`.
- Extend `ValuationInput` with optional `twd97_x: float | None` and `twd97_y: float | None`.
- Feature contract version becomes `3` for resale candidates.

- [ ] **Step 1: Write failing model-frame and input tests**

```python
def test_build_model_frame_adds_location_known(market_frame):
    result = build_model_frame(market_frame, "resale")
    assert result.loc[0, "twd97_x"] == pytest.approx(276000.0)
    assert result.loc[0, "location_known"] == "known"

def test_input_frame_supports_missing_exact_location(valid_resale_input):
    value = replace(valid_resale_input, twd97_x=None, twd97_y=None)
    row = input_frame(value, pd.Timestamp("2026-06-13")).iloc[0]
    assert pd.isna(row["twd97_x"])
    assert row["location_known"] == "missing"
```

Also test that only one coordinate is rejected and non-finite values are rejected.

- [ ] **Step 2: Write failing coordinate conversion and conversation tests**

```python
def test_wgs84_to_twd97_round_trip():
    x, y = wgs84_to_twd97(121.2187076, 25.0094795)
    assert x == pytest.approx(272_000, abs=2_000)
    assert y == pytest.approx(2_766_000, abs=2_000)

def test_conversation_valuation_passes_exact_listing_location(
    market_data_source,
    model_registry,
    valid_listing_payload,
    captured,
):
    payload = {
        **valid_listing_payload,
        "longitude": 121.2187,
        "latitude": 25.0094,
    }
    _conversation_valuation(market_data_source, model_registry, payload)
    assert captured["input"].twd97_x is not None
    assert captured["input"].twd97_y is not None
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_features.py tests/test_geo.py tests/test_valuation.py tests/test_web.py -q
```

Expected: FAIL for missing fields and conversion helper.

- [ ] **Step 4: Implement v3 spatial features**

Add coordinates to numeric preprocessing and `location_known` to categorical preprocessing. Use an `EPSG:4326 -> EPSG:3826` transformer cached at module level:

```python
_WGS84_TO_TWD97 = Transformer.from_crs(
    "EPSG:4326", "EPSG:3826", always_xy=True
)

def wgs84_to_twd97(longitude, latitude):
    x, y = _WGS84_TO_TWD97.transform(longitude, latitude)
    if not all(math.isfinite(v) for v in (x, y)):
        raise ValueError("coordinates must be finite")
    return float(x), float(y)
```

When conversation listing coordinates are present, convert and pass them. Manual estimates continue with missing coordinates; add confidence reason `未提供精確位置，僅能依生活圈與捷運距離估價`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the four focused test modules plus `tests/test_model_training.py`; expect PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/model_features.py src/qingpu_insight/model_training.py src/qingpu_insight/geo.py src/qingpu_insight/valuation.py src/qingpu_insight/web.py tests/test_model_features.py tests/test_model_training.py tests/test_geo.py tests/test_valuation.py tests/test_web.py
git commit -m "feat(models): add spatial valuation features"
```

---

### Task 4: Log-Target HGB Research Candidate

**Files:**
- Modify: `src/qingpu_insight/model_training.py`
- Modify: `src/qingpu_insight/model_analysis.py`
- Modify: `src/qingpu_insight/valuation_reporting.py`
- Modify: `tests/test_model_training.py`
- Modify: `tests/test_model_analysis.py`
- Modify: `tests/test_valuation_reporting.py`

**Interfaces:**
- Candidate name: `hist_gradient_boosting_log`
- Metrics and predictions always remain in original TWD-per-ping units.
- Exact fit specs support `target_transform: Literal["identity", "log"]`.

- [ ] **Step 1: Write failing candidate contract tests**

```python
def test_candidate_estimators_include_log_target_hgb():
    candidates = candidate_estimators(profile=PRESET_PROFILES[0])
    assert "hist_gradient_boosting_log" in candidates

def test_log_target_candidate_returns_predictions_in_original_units(split):
    result = evaluate_candidate(
        "hist_gradient_boosting_log",
        candidate_estimators()["hist_gradient_boosting_log"],
        split.train,
        split.test,
    )
    assert result.metrics.loc["overall", "mae"] > 1_000
    assert result.metrics.loc["overall", "mae"] < 1_000_000
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py tests/test_model_analysis.py tests/test_valuation_reporting.py -q
```

Expected: FAIL because the candidate and target transform do not exist.

- [ ] **Step 3: Implement with `TransformedTargetRegressor`**

Wrap the same HGB regressor:

```python
TransformedTargetRegressor(
    regressor=HistGradientBoostingRegressor(
        learning_rate=profile.hgb_learning_rate,
        max_iter=profile.hgb_max_iter,
        max_leaf_nodes=31,
        l2_regularization=1.0,
        random_state=seed,
    ),
    func=np.log,
    inverse_func=np.exp,
    check_inverse=False,
)
```

Keep all scoring paths unchanged because `predict()` already returns original-scale values. Ensure sample weights route through `model__sample_weight`.

- [ ] **Step 4: Add report labels**

Map the candidate to `HGB（對數價格）` in model cards and explain that it is selected only when validation and backtests justify it.

- [ ] **Step 5: Verify GREEN**

Run the three focused modules and expect PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/model_training.py src/qingpu_insight/model_analysis.py src/qingpu_insight/valuation_reporting.py tests/test_model_training.py tests/test_model_analysis.py tests/test_valuation_reporting.py
git commit -m "feat(models): evaluate log target candidate"
```

---

### Task 5: Residual and Data-Quality Diagnostics

**Files:**
- Modify: `src/qingpu_insight/model_analysis.py`
- Modify: `src/qingpu_insight/model_artifacts.py`
- Modify: `src/qingpu_insight/model_observatory.py`
- Modify: `tests/test_model_analysis.py`
- Modify: `tests/test_model_artifacts.py`
- Modify: `tests/test_model_observatory.py`

**Interfaces:**
- `diagnostics["top_residuals"]`: at most 20 public residual rows.
- `diagnostics["data_quality"]`: exclusion counts and ambiguous-registration count.
- Each residual includes `record_id`, `transaction_date`, `station_code`, `road_key`, `building_type`, `actual_twd_per_ping`, `predicted_twd_per_ping`, `absolute_error_twd_per_ping`, `absolute_percentage_error`, and `flags`.

- [ ] **Step 1: Write failing diagnostics tests**

```python
def test_resale_diagnostics_projects_largest_errors_without_full_address(
    resale_frame,
    resale_split,
    fitted_candidate,
):
    diagnostics = build_resale_diagnostics(
        resale_frame,
        resale_split,
        fitted_candidate,
        FEATURE_COLUMNS,
    )
    rows = diagnostics["top_residuals"]
    assert len(rows) <= 20
    assert rows[0]["absolute_error_twd_per_ping"] >= rows[-1]["absolute_error_twd_per_ping"]
    assert "address" not in rows[0]
    assert set(rows[0]) >= {
        "record_id", "transaction_date", "station_code", "road_key",
        "actual_twd_per_ping", "predicted_twd_per_ping",
        "absolute_error_twd_per_ping", "absolute_percentage_error", "flags",
    }
```

Add a test that ambiguous presale/split notes are counted but not automatically flagged as excluded.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_analysis.py tests/test_model_artifacts.py tests/test_model_observatory.py -q
```

Expected: FAIL because diagnostics only contain grouped errors.

- [ ] **Step 3: Implement public residual projection**

Build predictions once, calculate signed/absolute errors, sort descending, and project only allowlisted fields. Calculate APE with the existing 100,000 TWD denominator floor.

Add:

```python
"data_quality": {
    "special_relationship_excluded": int(special_relationship.sum()),
    "non_market_subject_excluded": int(non_market_subject.sum()),
    "ambiguous_registration_note_count": int(ambiguous_note.sum()),
}
```

Persist through the existing manifest schema using backward-compatible default dictionaries.

- [ ] **Step 4: Verify GREEN**

Run the three focused modules and expect PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/model_analysis.py src/qingpu_insight/model_artifacts.py src/qingpu_insight/model_observatory.py tests/test_model_analysis.py tests/test_model_artifacts.py tests/test_model_observatory.py
git commit -m "feat(models): expose residual diagnostics"
```

---

### Task 6: Observatory Presentation

**Files:**
- Modify: `src/qingpu_insight/static/models_admin.js`
- Modify: `src/qingpu_insight/templates/models_admin.html`
- Modify: `src/qingpu_insight/static/model_training.css`
- Modify: `tests/js/model_admin_contract.cjs`
- Modify: `tests/test_web.py`

**Interfaces:**
- Official model report heading: `最終測試集`.
- Collapsible section label: `誤差診斷（前 20 筆）`.
- Calibration and final-test labels must be distinct on candidate cards.

- [ ] **Step 1: Write failing JS contract**

```javascript
var report = modelsAdmin.buildOfficialReportView({
  evaluation_split: "final_test",
  test_count: 630,
  diagnostics: {
    top_residuals: [{
      transaction_date: "2026-05-01",
      station_code: "A18",
      road_key: "青商路",
      actual_twd_per_ping: 600000,
      predicted_twd_per_ping: 480000,
      absolute_error_twd_per_ping: 120000,
      flags: ["高價尾端"]
    }]
  }
});
assert.equal(report.splitLabel, "最終測試集");
assert.equal(report.residualRows[0].actual, "60 萬／坪");
assert.equal(report.residualRows[0].error, "12 萬／坪");
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
node tests/js/model_admin_contract.cjs
```

Expected: FAIL because the report view does not expose split labels or residual rows.

- [ ] **Step 3: Implement compact accessible UI**

Render diagnostics in a closed `<details>` element. Show no more than 20 rows with date, station, road, actual, prediction, absolute error, percentage error, and flags. Reuse existing `萬／坪` formatting and admin color tokens.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
node tests/js/model_admin_contract.cjs
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/static/models_admin.js src/qingpu_insight/templates/models_admin.html src/qingpu_insight/static/model_training.css tests/js/model_admin_contract.cjs tests/test_web.py
git commit -m "feat(web): visualize model residuals"
```

---

### Task 7: Research Record, Rebuild, Retrain, Release, and Verify

**Files:**
- Create: `docs/research/2026-07-29-resale-model-error-analysis.md`
- Modify: `docs/project-issue-log.md`
- Modify: `docs/m2-valuation-methodology.md`
- Modify: `README.md`
- Add: one new candidate directory under `candidates/<run_id>/` only if it becomes the published source candidate.

**Interfaces:**
- Research document records hypotheses, experiments, rejected approaches, final metrics, limitations, and STAR interview narrative.
- `main` contains the source candidate of the published official model.

- [ ] **Step 1: Rebuild processed market data**

Use the existing CLI path that rebuilds `data/processed/market_transactions.parquet` from `data/processed/transactions.parquet`. Before running, inspect `qingpu-data --help` and use the documented non-network market-build command.

Verify:

```powershell
.\.venv\Scripts\qingpu-data.exe health
```

Expected: resale usable count decreases only by confirmed exclusions; ambiguous registration rows remain.

- [ ] **Step 2: Run guided resale training**

Start from the admin UI or its public admin API with:

```json
{
  "markets": ["resale"],
  "tuning": {"mode": "preset_comparison"}
}
```

Poll the tracked job until terminal. Record run ID, selected model, selected profile, final-test metrics, backtests, spatial-feature evidence, data-quality counts, and residual summary.

- [ ] **Step 3: Compare candidate to official model**

Acceptance criteria:

- Official report source is final test.
- New candidate passes all release checks.
- Parking consistency passes.
- Final-test MAE is no worse than the current 53,822 TWD/ping.
- Final-test R² is at least 0.635.
- A17, A18, and A19 MAPE remain within release limits.
- At least two of three annual backtests pass.

If the candidate fails, do not publish; preserve its report and document the failed hypothesis.

- [ ] **Step 4: Publish through preview/confirm**

Use the existing two-step model-release API. Verify the release job reaches `succeeded`, the official pointer changes atomically, and `/api/admin/models/status` reports the new version as non-stale.

- [ ] **Step 5: Browser verification**

Verify:

1. Admin official model card says `最終測試集`.
2. Test count and final metrics match the candidate report.
3. Residual diagnostics are collapsed by default and expand correctly.
4. Refresh one real A17, A18, and A19 conversation.
5. Each uses the new official model, does not degrade, and displays coherent parking and confidence results.
6. Manual valuation without exact coordinates remains usable and explains its medium-confidence cap.

- [ ] **Step 6: Write the research record**

Document:

- Initial misleading calibration metrics.
- Exact residual findings.
- Cleaning experiment.
- Coordinate, road-key, management, registration-note, robust-loss, and log-target experiments.
- Why raw road key and blanket registration-note deletion were rejected.
- Before/after final metrics and three-period results.
- Remaining 2025 A18 drift limitation.
- A concise STAR narrative for interviews.

- [ ] **Step 7: Run full verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
$node = "C:\Users\cygnu\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
Get-ChildItem tests\js -File | Sort-Object Name | ForEach-Object { & $node $_.FullName }
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: all Python tests, all JavaScript contracts, Ruff, and diff checks pass.

- [ ] **Step 8: Commit and push**

Stage only intentional source, tests, docs, and the published source candidate. Do not stage the three pre-existing untracked candidate directories.

```powershell
git commit -m "feat(models): reduce resale valuation error"
git push origin main
```
