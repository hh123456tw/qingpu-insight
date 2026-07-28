# Parking Valuation Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make parking valuation additive and explainable so an otherwise identical home with a valid parking space cannot be valued below the no-parking case.

**Architecture:** The ML pipeline predicts only the dwelling unit price and excludes parking inputs from its feature contract. A versioned `ParkingPricePolicy`, built from positive official parking-price observations during training, adds a deterministic parking component to the dwelling estimate and interval. The API, homepage, model reports, and release smoke tests expose and enforce the same policy.

**Tech Stack:** Python 3.11, pandas, scikit-learn, joblib, Pydantic, Flask, vanilla JavaScript, pytest, Node.js contract tests.

## Global Constraints

- `building_area_ping` means dwelling area and excludes parking area.
- `parking_area_ping` is retained for validation and display but is not an ML feature.
- New ML artifacts must exclude `parking_type` and `parking_area_ping`.
- Parking estimates come from positive official `parking_price_twd` observations stored in the same artifact.
- A valid parking estimate must be strictly greater than zero.
- No separate parking ML model, AutoML, monotonic model constraint, or 591 crawler change.
- User-visible monetary values use `萬` or `萬／坪`.
- Existing schema-v1/v2/v3 manifests and legacy joblib artifacts remain readable.
- Do not commit API keys, database URLs, generated candidate artifacts, benchmark output, or user listing details.
- Preserve all pre-existing working-tree changes; never reset or discard files outside the task.

---

## File Map

- Create `src/qingpu_insight/parking_valuation.py`: immutable parking-policy types, policy construction, lookup, serialization-friendly output.
- Modify `src/qingpu_insight/model_features.py`: dwelling-only feature contract and parking input invariants.
- Modify `src/qingpu_insight/valuation.py`: persist policy in `ValuationBundle`, calculate dwelling/parking/total values, and support legacy artifacts.
- Modify `src/qingpu_insight/model_training_service.py`: train with dwelling-only features and attach the policy.
- Modify `src/qingpu_insight/valuation_reporting.py`: add parking policy to JSON evaluation and Markdown model card.
- Modify `src/qingpu_insight/model_observatory.py`: expose official-model parking policy.
- Modify `src/qingpu_insight/model_release.py`: reject new candidates that fail parking consistency.
- Modify `src/qingpu_insight/web.py`: validate and normalize parking fields and preserve the expanded response.
- Modify `src/qingpu_insight/templates/index.html`: clarify area semantics and add parking-field help.
- Modify `src/qingpu_insight/static/valuation_form.js`: pure parking-form state and validation helpers.
- Modify `src/qingpu_insight/static/app.js`: wire form behavior and render dwelling/parking/total breakdown.
- Modify `src/qingpu_insight/static/app.css`: compact price-breakdown styling.
- Modify `src/qingpu_insight/templates/admin.html` and `src/qingpu_insight/static/models_admin.js`: display parking policy and consistency gate.
- Create `docs/project-issue-log.md`: project/report/interview-ready engineering incident record.
- Modify `README.md`: document area semantics, pricing formula, and retraining behavior.

---

### Task 1: Parking Price Policy Domain

**Files:**
- Create: `src/qingpu_insight/parking_valuation.py`
- Create: `tests/test_parking_valuation.py`

**Interfaces:**
- Consumes: market model frame columns `parking_type`, `parking_price_twd`.
- Produces:
  - `ParkingPriceStat(price_twd: int, sample_size: int)`
  - `ParkingPriceEstimate(price_twd: int, sample_size: int, source: str, parking_type: str)`
  - `ParkingPricePolicy(version: int, minimum_type_samples: int, by_type: dict[str, ParkingPriceStat], market_fallback: ParkingPriceStat | None)`
  - `build_parking_price_policy(frame: pd.DataFrame, *, minimum_type_samples: int = 20) -> ParkingPricePolicy`
  - `estimate_parking_price(policy: ParkingPricePolicy | None, parking_type: str) -> ParkingPriceEstimate | None`

- [ ] **Step 1: Write policy construction tests**

```python
def test_build_policy_uses_positive_prices_and_type_threshold():
    frame = pd.DataFrame({
        "parking_type": ["坡道平面"] * 20 + ["坡道機械"] * 2 + [""],
        "parking_price_twd": [1_700_000] * 20 + [800_000, 900_000, 0],
    })
    policy = build_parking_price_policy(frame, minimum_type_samples=20)
    assert policy.by_type["坡道平面"] == ParkingPriceStat(1_700_000, 20)
    assert "坡道機械" not in policy.by_type
    assert policy.market_fallback == ParkingPriceStat(1_700_000, 22)


def test_estimate_uses_type_then_market_fallback():
    policy = ParkingPricePolicy(
        version=1,
        minimum_type_samples=20,
        by_type={"坡道平面": ParkingPriceStat(1_700_000, 40)},
        market_fallback=ParkingPriceStat(1_200_000, 60),
    )
    assert estimate_parking_price(policy, "坡道平面").source == "type_median"
    fallback = estimate_parking_price(policy, "坡道機械")
    assert fallback.price_twd == 1_200_000
    assert fallback.source == "market_median"
    assert estimate_parking_price(policy, "") == ParkingPriceEstimate(
        price_twd=0, sample_size=0, source="none", parking_type=""
    )
```

- [ ] **Step 2: Run tests and verify the module is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_parking_valuation.py
```

Expected: collection fails because `qingpu_insight.parking_valuation` does not exist.

- [ ] **Step 3: Implement immutable policy types and builders**

Use frozen dataclasses. Normalize parking labels with `" ".join(value.split())`; discard empty types and non-positive/non-numeric prices. Calculate medians with pandas and round to integer TWD. Only add a type to `by_type` when `sample_size >= minimum_type_samples`; calculate `market_fallback` from every valid positive observation.

- [ ] **Step 4: Run policy tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_parking_valuation.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/parking_valuation.py tests/test_parking_valuation.py
git commit -m "feat(models): add parking price policy"
```

---

### Task 2: Dwelling-Only Feature and Input Contract

**Files:**
- Modify: `src/qingpu_insight/model_features.py`
- Modify: `src/qingpu_insight/model_training.py`
- Modify: `src/qingpu_insight/model_analysis.py`
- Modify: `tests/test_model_features.py`
- Modify: `tests/test_model_training.py`
- Modify: `tests/test_model_analysis.py`

**Interfaces:**
- Consumes: `ParkingPricePolicy` from Task 1 only during artifact creation, not preprocessing.
- Produces:
  - `PARKING_FEATURE_COLUMNS = ("parking_type", "parking_area_ping")`
  - `BASE_FEATURE_COLUMNS` without either parking column.
  - `FEATURE_COLUMNS` without either parking column.
  - `ValuationInput` that normalizes no-parking area to zero and rejects a selected parking type with non-positive area.

- [ ] **Step 1: Add feature and invariant tests**

```python
def test_house_feature_contract_excludes_parking():
    assert "parking_type" not in FEATURE_COLUMNS
    assert "parking_area_ping" not in FEATURE_COLUMNS


def test_no_parking_normalizes_stale_area(valid_resale_input):
    value = replace(valid_resale_input, parking_type="", parking_area_ping=8)
    assert value.parking_area_ping == 0


def test_selected_parking_requires_positive_area(valid_resale_input):
    with pytest.raises(ValueError, match="parking_area_ping must be greater than 0"):
        replace(valid_resale_input, parking_type="坡道平面", parking_area_ping=0)
```

Update experiment assertions so baseline and enhanced candidates both use the new dwelling-only contracts.

- [ ] **Step 2: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_model_features.py tests/test_model_training.py tests/test_model_analysis.py
```

Expected: failures show parking remains in the feature contract and zero-area parking is accepted.

- [ ] **Step 3: Remove parking from ML columns and enforce input invariants**

Keep parking columns in `build_model_frame()` because Task 3 needs them for policy construction. Remove them only from estimator feature tuples. In frozen `ValuationInput.__post_init__`, normalize no-parking area with:

```python
if not self.parking_type:
    object.__setattr__(self, "parking_area_ping", 0)
elif self.parking_area_ping <= 0:
    raise ValueError("parking_area_ping must be greater than 0 when parking_type is selected")
```

- [ ] **Step 4: Run focused tests**

Run the Step 2 command.

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/model_features.py src/qingpu_insight/model_training.py src/qingpu_insight/model_analysis.py tests/test_model_features.py tests/test_model_training.py tests/test_model_analysis.py
git commit -m "fix(models): isolate dwelling price features"
```

---

### Task 3: Persist Parking Policy in Model Artifacts

**Files:**
- Modify: `src/qingpu_insight/valuation.py`
- Modify: `src/qingpu_insight/model_training_service.py`
- Modify: `src/qingpu_insight/model_artifacts.py`
- Modify: `tests/test_valuation.py`
- Modify: `tests/test_model_training_service.py`
- Modify: `tests/test_model_artifacts.py`

**Interfaces:**
- Consumes: `build_parking_price_policy()` from Task 1 and dwelling-only `FEATURE_COLUMNS` from Task 2.
- Produces:
  - `ValuationBundle.parking_price_policy: ParkingPricePolicy | None`
  - legacy-safe `ValuationBundle.__getattr__("parking_price_policy") -> None`
  - schema-v3 manifest result field `parking_policy: dict[str, object]`

- [ ] **Step 1: Add artifact persistence and compatibility tests**

```python
def test_train_artifact_persists_parking_policy(tmp_path, training_frame, selected, split, seed_bundle):
    path = train_artifact(
        "resale",
        selected,
        split,
        seed_bundle,
        tmp_path,
        feature_columns=FEATURE_COLUMNS,
        training_frame=training_frame,
    )
    bundle = joblib.load(path)
    assert bundle.parking_price_policy.version == 1
    assert bundle.parking_price_policy.by_type["坡道平面"].price_twd > 0
    assert "parking_type" not in bundle.feature_columns


def test_old_bundle_pickle_gets_no_parking_policy(old_bundle):
    del old_bundle.__dict__["parking_price_policy"]
    assert old_bundle.parking_price_policy is None
```

Add Pydantic round-trip coverage showing a v3 manifest accepts and returns:

```python
{
    "version": 1,
    "minimum_type_samples": 20,
    "by_type": {"坡道平面": {"price_twd": 1700000, "sample_size": 40}},
    "market_fallback": {"price_twd": 1200000, "sample_size": 60},
}
```

- [ ] **Step 2: Run artifact tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_valuation.py tests/test_model_training_service.py tests/test_model_artifacts.py
```

Expected: failures show the bundle and manifest do not contain parking policy.

- [ ] **Step 3: Add the bundle field and training integration**

Add the field after `feature_columns` with a default of `None`. In `train_artifact()`, build the policy from `train_frame` and persist it. In `ModelTrainingService.execute()`, serialize the policy into `MarketTrainingResult.parking_policy`. Keep manifest schema version 3; add the field as a defaulted object so old v3 manifests remain valid.

- [ ] **Step 4: Run artifact tests**

Run the Step 2 command.

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/valuation.py src/qingpu_insight/model_training_service.py src/qingpu_insight/model_artifacts.py tests/test_valuation.py tests/test_model_training_service.py tests/test_model_artifacts.py
git commit -m "feat(models): version parking policy with artifacts"
```

---

### Task 4: Additive Valuation Result and Legacy Behavior

**Files:**
- Modify: `src/qingpu_insight/valuation.py`
- Modify: `src/qingpu_insight/listing_valuation.py`
- Modify: `src/qingpu_insight/conversation_evidence.py`
- Modify: `tests/test_valuation.py`
- Modify: `tests/test_listing_valuation.py`
- Modify: `tests/test_conversation_evidence.py`

**Interfaces:**
- Consumes: `estimate_parking_price()` and `ValuationBundle.parking_price_policy`.
- Produces API result fields:
  - `estimated_building_price_twd: int`
  - `estimated_parking_price_twd: int | None`
  - `estimated_total_price_twd: int`
  - `parking_price_policy: {"parking_type": str, "sample_size": int, "source": str} | None`

- [ ] **Step 1: Add counterfactual result tests**

```python
def test_parking_is_additive_and_does_not_change_building_estimate(bundle, market):
    no_parking = valuate(no_parking_input, registry_for(bundle), market)
    flat = valuate(flat_parking_input, registry_for(bundle), market)
    mechanical = valuate(mechanical_input, registry_for(bundle), market)

    assert flat["estimated_building_price_twd"] == no_parking["estimated_building_price_twd"]
    assert mechanical["estimated_building_price_twd"] == no_parking["estimated_building_price_twd"]
    assert flat["estimated_total_price_twd"] == (
        flat["estimated_building_price_twd"] + flat["estimated_parking_price_twd"]
    )
    assert flat["estimated_total_price_twd"] > no_parking["estimated_total_price_twd"]
    assert mechanical["estimated_total_price_twd"] > no_parking["estimated_total_price_twd"]
```

Add interval assertions showing the same parking amount is added to both interval endpoints. Add a legacy bundle test that returns `estimated_parking_price_twd=None`, preserves the old total calculation, and includes `legacy_parking_policy` in limitations/confidence reasons.

- [ ] **Step 2: Run valuation consumers**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_valuation.py tests/test_listing_valuation.py tests/test_conversation_evidence.py
```

Expected: new fields and additive invariants fail.

- [ ] **Step 3: Implement one shared price composition helper**

Add a private or public helper with this exact behavior:

```python
def compose_total_price(
    building_unit_price_twd: float,
    building_area_ping: float,
    parking_estimate: ParkingPriceEstimate | None,
) -> tuple[int, int | None, int]:
    building = round(building_unit_price_twd * building_area_ping)
    if parking_estimate is None:
        return building, None, building
    parking = parking_estimate.price_twd
    return building, parking, building + parking
```

Use it in normal, stale, and artifact-unavailable valuation paths. For artifact-unavailable fallback, build a temporary policy from the same market frame and report `source="market_median"`. For legacy artifacts, preserve old totals and state the limitation explicitly.

- [ ] **Step 4: Update evidence and listing consumers**

Add evidence facts:

- `valuation.building`
- `valuation.parking`
- `valuation.total`
- `valuation.parking_policy`

Keep `valuation.point` as an alias for total to preserve existing conversations and validators.

- [ ] **Step 5: Run valuation consumer tests**

Run the Step 2 command.

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/valuation.py src/qingpu_insight/listing_valuation.py src/qingpu_insight/conversation_evidence.py tests/test_valuation.py tests/test_listing_valuation.py tests/test_conversation_evidence.py
git commit -m "fix(valuation): compose dwelling and parking prices"
```

---

### Task 5: API Validation and Homepage Presentation

**Files:**
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/valuation_form.js`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `src/qingpu_insight/static/app.css`
- Modify: `tests/test_web.py`
- Modify: `tests/js/valuation_form_contract.cjs`
- Modify: `tests/js/display_format_contract.cjs`

**Interfaces:**
- Consumes: expanded valuation JSON from Task 4.
- Produces:
  - `QingpuValuationForm.parkingState(parkingType: string, parkingArea: number) -> {disabled: boolean, normalizedArea: number, valid: boolean, message: string}`
  - visible dwelling/parking/total breakdown.

- [ ] **Step 1: Add API and JavaScript contract tests**

Python:

```python
def test_valuation_rejects_selected_parking_with_zero_area(client, valid_payload):
    valid_payload.update(parking_type="坡道平面", parking_area_ping=0)
    response = client.post("/api/valuations", json=valid_payload)
    assert response.status_code == 400
    assert response.json["error"]["fields"]["parking_area_ping"] == "positive_when_parking_selected"


def test_valuation_normalizes_no_parking_area(client, valid_payload):
    valid_payload.update(parking_type="", parking_area_ping=8)
    response = client.post("/api/valuations", json=valid_payload)
    assert response.status_code == 201
    assert response.json["estimated_parking_price_twd"] == 0
```

Node:

```javascript
assert.deepEqual(form.parkingState("", 8), {
  disabled: true, normalizedArea: 0, valid: true, message: "",
});
assert.equal(form.parkingState("坡道平面", 0).valid, false);
assert.equal(form.parkingState("坡道平面", 8).valid, true);
```

- [ ] **Step 2: Run API and JS contracts**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_web.py
node tests/js/valuation_form_contract.cjs
node tests/js/display_format_contract.cjs
```

Expected: failures show missing field validation, state helper, and response breakdown.

- [ ] **Step 3: Implement backend field-specific errors**

In `parse_valuation_payload()`, distinguish parking invariant failures from generic valuation failures and return:

```python
ApiInputError(
    "有車位時請填寫大於 0 的車位面積。",
    {"parking_area_ping": "positive_when_parking_selected"},
)
```

Normalize `parking_area_ping=0` when `parking_type` is empty before constructing `ValuationInput`.

- [ ] **Step 4: Implement form state**

Change the label to `房屋坪數（不含車位）`. Add an `id` to the parking-area label and a short hint: `僅供資料合理性檢查，不會直接改變房屋每坪單價。`

On parking-type change:

- no parking: set area to `0`, disable the input, clear custom validity;
- parking selected: enable the input and use `setCustomValidity()` until area is greater than zero.

- [ ] **Step 5: Render the price breakdown**

Inside the primary valuation card render:

```text
房屋本體　1,686 萬
車位　　　170 萬（坡道平面，有效車位價樣本 11,725 筆）
估計總價　1,856 萬
```

Use `formatTotalWan()` for all amounts. When policy source is `market_median`, label it `市場車位中位數`; when legacy, show one short warning and omit a fabricated parking amount.

- [ ] **Step 6: Run API and frontend contracts**

Run the Step 2 command.

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add src/qingpu_insight/web.py src/qingpu_insight/templates/index.html src/qingpu_insight/static/valuation_form.js src/qingpu_insight/static/app.js src/qingpu_insight/static/app.css tests/test_web.py tests/js/valuation_form_contract.cjs tests/js/display_format_contract.cjs
git commit -m "feat(web): explain dwelling and parking valuation"
```

---

### Task 6: Release Gate, Reports, and Observatory

**Files:**
- Modify: `src/qingpu_insight/model_release.py`
- Modify: `src/qingpu_insight/valuation_reporting.py`
- Modify: `src/qingpu_insight/model_observatory.py`
- Modify: `src/qingpu_insight/static/models_admin.js`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `tests/test_model_release.py`
- Modify: `tests/test_valuation_reporting.py`
- Modify: `tests/test_model_observatory.py`
- Modify: `tests/js/model_admin_contract.cjs`

**Interfaces:**
- Consumes: `ValuationBundle.parking_price_policy` and dwelling-only feature contract.
- Produces release check `parking_price_consistency: bool` plus report/observatory `parking_policy`.

- [ ] **Step 1: Add release and projection tests**

```python
def test_release_smoke_rejects_missing_or_nonpositive_parking_policy(bundle):
    bundle.parking_price_policy = ParkingPricePolicy(
        version=1,
        minimum_type_samples=20,
        by_type={},
        market_fallback=None,
    )
    with pytest.raises(ValueError, match="parking price policy"):
        ModelReleaseService._smoke_test("resale", bundle)


def test_release_smoke_accepts_additive_parking(bundle_with_policy):
    ModelReleaseService._smoke_test("resale", bundle_with_policy)
```

Assert JSON report, Markdown model card, and observatory output contain policy version, per-type price/sample size, fallback price/sample size, and `parking_price_consistency=True`.

- [ ] **Step 2: Run release/report tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_model_release.py tests/test_valuation_reporting.py tests/test_model_observatory.py
node tests/js/model_admin_contract.cjs
```

Expected: missing policy projection and smoke checks fail.

- [ ] **Step 3: Enforce candidate consistency**

In `_smoke_test()`:

1. Verify new bundles exclude both parking features.
2. Verify the policy has a positive market fallback.
3. Predict the same smoke dwelling row once.
4. Compose no-parking, slope-flat, and slope-mechanical totals.
5. Require both parking totals to be strictly greater than the no-parking total.

Legacy official bundles remain viewable and rollback-capable, but a newly trained candidate without the policy cannot be published.

- [ ] **Step 4: Add report and UI projection**

Add a compact `車位估值政策` section to official and candidate reports:

- policy version;
- dwelling model excludes parking features;
- type price and sample count;
- fallback price and sample count;
- consistency gate result.

- [ ] **Step 5: Run release/report tests**

Run the Step 2 command.

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/model_release.py src/qingpu_insight/valuation_reporting.py src/qingpu_insight/model_observatory.py src/qingpu_insight/static/models_admin.js src/qingpu_insight/templates/admin.html tests/test_model_release.py tests/test_valuation_reporting.py tests/test_model_observatory.py tests/js/model_admin_contract.cjs
git commit -m "feat(models): gate and report parking consistency"
```

---

### Task 7: Project Issue Record and README

**Files:**
- Create: `docs/project-issue-log.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: verified before/after numbers from Tasks 4–6.
- Produces: a concise engineering incident record suitable for the course report and interviews.

- [ ] **Step 1: Write the issue record**

Use these exact sections:

```markdown
# Qingpu Insight 專案問題與工程決策紀錄

## 1. 問題摘要
## 2. 使用者如何發現
## 3. 可重現條件與修正前數字
## 4. 根因分析
## 5. 評估過的方案
## 6. 最終架構決策
## 7. 修正內容
## 8. 修正後驗證
## 9. 已知限制
## 10. 期中專題報告說法
## 11. 求職面試 STAR 說法
## 12. 延伸改善方向
```

Record the verified original values:

- no parking: 1,686 萬;
- slope flat, 0 ping: 1,333 萬;
- slope flat, 8 ping: 1,408 萬;
- slope mechanical, 5 ping: 1,513 萬;
- resale counts: slope flat 14,955 and no parking 799;
- A18 matched cohort: slope flat 808 and no parking 33.

Do not include the previously tested 591 title, URL, conversation UUID, API key, database URL, or local secret path.

- [ ] **Step 2: Document the stable user contract in README**

Add:

```text
房屋坪數不含車位。
房屋模型估算本體每坪價格；車位採同版官方資料的類型中位價。
估計總價 = 房屋本體估值 + 車位估值。
```

Explain that training artifacts and reports are generated locally and excluded from Git.

- [ ] **Step 3: Validate documentation**

Run:

```powershell
rg -n "1,686|14,955|STAR|房屋坪數不含車位|估計總價" docs/project-issue-log.md README.md
rg -n "AQ\\.|AIza|QINGPU_DATABASE_URL=.*@" docs/project-issue-log.md README.md
```

Expected: the first command finds every required topic; the second command returns no matches.

- [ ] **Step 4: Commit**

```powershell
git add docs/project-issue-log.md README.md
git commit -m "docs: record parking valuation incident"
```

---

### Task 8: Retrain, Publish, and End-to-End Verification

**Files:**
- Generated, not committed: `outputs/model-candidates/<run-id>/`
- Generated, not committed: `artifacts/official/resale/versions/<version-id>/`
- Generated, not committed: `artifacts/official/presale/versions/<version-id>/`
- Modify only if verification finds a real defect: source/test files from Tasks 1–6.

**Interfaces:**
- Consumes: complete implementation and local `data/processed/market_transactions.parquet`.
- Produces: a candidate run, published official models, and verified homepage behavior.

- [ ] **Step 1: Run the full automated gate**

```powershell
$ErrorActionPreference = "Stop"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
Get-ChildItem tests/js -Filter "*_contract.cjs" | Sort-Object Name | ForEach-Object {
  node $_.FullName
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
node tests/js/market_map_contract.mjs
git diff --check
```

Expected: pytest reaches 100% with zero failures, Ruff reports `All checks passed!`, every Node contract exits zero, and `git diff --check` reports no errors.

- [ ] **Step 2: Train both markets**

```powershell
.\.venv\Scripts\qingpu-data.exe model-train --markets resale presale
```

Expected: exit zero and `candidate run: <uuid>`. Record the run ID in the issue log verification section without committing generated artifacts.

- [ ] **Step 3: Inspect candidate evidence before publication**

From `/admin#models`, open both candidate reports and verify:

- parking features are absent;
- parking policy version is 1;
- each displayed price has a positive sample count;
- `parking_price_consistency` passes;
- MAE, MAPE, RMSE, R², station errors, and release checks remain visible.

Do not publish a market whose existing release checks regress or whose parking consistency check fails.

- [ ] **Step 4: Publish through the existing two-step admin flow**

For each acceptable market:

1. Click `發布`.
2. Read the generated preview and candidate version.
3. Enter the exact confirmation text shown by the preview.
4. Execute publication.
5. Confirm the release job succeeds and the official-model card shows the new version.

- [ ] **Step 5: Verify the original counterfactual scenario**

Use A18, resale, 30 dwelling ping, 500 m, residential tower, 3 bedrooms, 2 living rooms, 2 bathrooms, age 5, floor 10 of 20:

1. no parking, area 0;
2. slope flat, area 8;
3. slope mechanical, area 5.

Record:

- identical dwelling estimate for all three;
- positive parking estimate for cases 2 and 3;
- total equals dwelling plus parking;
- both parking totals exceed no-parking total;
- all amounts display in `萬`;
- no field error, console error, or horizontal overflow.

- [ ] **Step 6: Update issue log with actual post-fix values**

Replace the verification section with the exact trained model version, candidate run ID, parking medians/sample counts, three estimates, and automated test results. Keep generated IDs limited to model/run identifiers; do not include conversation or listing identifiers.

- [ ] **Step 7: Commit final verified documentation**

```powershell
git add docs/project-issue-log.md README.md
git commit -m "docs: add verified parking valuation results"
```

- [ ] **Step 8: Final status check**

```powershell
git status --short
git log --oneline -10
```

Expected: only intentional generated artifacts remain ignored; source, tests, spec, plan, and documentation are committed. Do not push unless the user explicitly requests it.
