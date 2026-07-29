# Resale Common-Area and Community Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely test whether public-area ratio and time-safe community statistics improve Qingpu resale valuation, while keeping manual valuation and 591 analysis usable when those fields are missing.

**Architecture:** Extend the official-data contract with raw area components and a validated public-area ratio. Add a versioned, human-curated Qingpu community registry and derive strictly historical 24-month community statistics. Compare fixed feature families on calibration data, lock one winner, and evaluate only the baseline and locked winner on the final test. Persist the exact registry, feature contract, and inference snapshot with each candidate artifact. Reuse the same optional inputs in manual valuation and 591 detail analysis.

**Tech Stack:** Python 3.11, pandas, scikit-learn, Optuna, Pydantic, Flask, BeautifulSoup, MySQL, vanilla JavaScript, pytest, Node contract tests, Ruff.

## Global Constraints

- Follow [the approved design spec](../specs/2026-07-29-resale-common-area-community-features-design.md).
- Use red-green-refactor for every behavior change. Do not weaken an assertion merely to make a test pass.
- Preserve all existing untracked `candidates/` directories. They are user-generated model evidence.
- Do not use raw community names as one-hot model features.
- Do not use final-test results to select a feature family or estimator.
- Do not automatically publish a trained model. Training creates a reviewable candidate only.
- Do not require public-area ratio or community name for manual valuation or 591 analysis.
- Do not infer or persist community identity with an LLM. Matching must be deterministic and auditable.
- Do not retain complete 591 HTML in committed fixtures. Keep only minimal, de-identified fragments.
- Keep legacy `ValuationBundle` pickles loadable through explicit `__getattr__` fallbacks.
- Store all generated timestamps in UTC; present them in the existing Asia/Taipei UI convention.
- Run focused tests after each task and the full release gate before completion.

---

## Task 1: Preserve Official Area Components and Derive Public-Area Ratio

**Files:**

- Modify: `src/qingpu_insight/moi.py`
- Modify: `src/qingpu_insight/market_cleaning.py`
- Modify: `src/qingpu_insight/mysql_loader.py`
- Modify: `database/001_market_schema.sql`
- Create: `database/009_market_shared_features.sql`
- Test: `tests/test_moi.py`
- Test: `tests/test_market_cleaning.py`
- Test: `tests/test_mysql_loader.py`

- [ ] **Step 1: Write failing MOI parsing tests**

Add a resale row fixture containing `主建物面積`, `附屬建物面積`, `建物移轉總面積`,
`車位移轉總面積(平方公尺)`, and `有無管理組織`. Assert the normalized row contains:

```python
assert row["main_building_area_sqm"] == 61.2
assert row["auxiliary_building_area_sqm"] == 4.8
assert row["building_area_sqm"] == 110.0
assert row["parking_area_sqm"] == 25.0
assert row["has_management"] is True
```

Also cover blank area components and `無` management.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_moi.py -q
```

Expected: assertions fail because the new normalized fields do not exist.

- [ ] **Step 3: Extend the MOI normalized contract**

Map the official columns to:

```python
main_building_area_sqm: float | None
auxiliary_building_area_sqm: float | None
has_management: bool | None
```

Use the existing numeric and yes/no normalization helpers. Unknown source values must become
`None`, not truthy strings.

- [ ] **Step 4: Write failing public-area-ratio tests**

Add cases for:

```python
usable_area = main_building_area_sqm + auxiliary_building_area_sqm
non_parking_total = building_area_sqm - parking_area_sqm
common_area_ratio = 1 - usable_area / non_parking_total
```

Assert a normal case, missing components, zero denominator, parking larger than total, negative
ratio, and ratio above `0.70`. Invalid cases must produce `NaN` and
`common_area_ratio_valid == False`.

- [ ] **Step 5: Implement one derivation helper and call it from cleaning**

Add a vectorized helper in `market_cleaning.py`:

```python
def add_shared_property_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    usable = result["main_building_area_sqm"] + result["auxiliary_building_area_sqm"]
    non_parking_total = result["building_area_sqm"] - result["parking_area_sqm"].fillna(0)
    ratio = 1 - usable / non_parking_total
    valid = non_parking_total.gt(0) & ratio.between(0, 0.70, inclusive="both")
    result["common_area_ratio"] = ratio.where(valid)
    result["common_area_ratio_valid"] = valid
    return result
```

It must produce:

```text
main_building_area_sqm
auxiliary_building_area_sqm
common_area_ratio
common_area_ratio_valid
has_management
```

Call it before processed parquet output. Do not impute public-area ratio here.

- [ ] **Step 6: Add backward-compatible MySQL columns**

Create migration `009_market_shared_features.sql` with nullable columns. Mirror them in the base
schema for clean installs:

```sql
main_building_area_sqm DECIMAL(12,4) NULL
auxiliary_building_area_sqm DECIMAL(12,4) NULL
common_area_ratio DECIMAL(8,6) NULL
has_management BOOLEAN NULL
```

Update `mysql_loader.py` column ordering and ensure old frames missing these columns are padded with
`NULL` rather than rejected.

- [ ] **Step 7: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_moi.py tests/test_market_cleaning.py tests/test_mysql_loader.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/moi.py src/qingpu_insight/market_cleaning.py src/qingpu_insight/mysql_loader.py tests/test_moi.py tests/test_market_cleaning.py tests/test_mysql_loader.py
git add src/qingpu_insight/moi.py src/qingpu_insight/market_cleaning.py src/qingpu_insight/mysql_loader.py database/001_market_schema.sql database/009_market_shared_features.sql tests/test_moi.py tests/test_market_cleaning.py tests/test_mysql_loader.py
git commit -m "feat(data): derive resale public area ratio"
```

---

## Task 2: Add a Versioned and Validated Qingpu Community Registry

**Files:**

- Create: `src/qingpu_insight/community_registry.py`
- Create: `data/reference/qingpu_communities.csv`
- Create: `tests/test_community_registry.py`

- [ ] **Step 1: Define the registry CSV contract in failing tests**

Require these columns:

```text
community_id,canonical_name,aliases,station_code,address_patterns,
twd97_x,twd97_y,completion_year,source_notes
```

Test that loading fails for duplicate IDs, duplicate normalized aliases, invalid stations, missing
evidence, ambiguous address patterns, invalid coordinates, or fewer than 20 entries.

- [ ] **Step 2: Define deterministic matching tests**

Cover the priority order:

1. exact normalized canonical name;
2. exact normalized alias;
3. unique normalized address pattern;
4. coordinates within the configured radius plus compatible completion year;
5. otherwise unknown.

Use a result contract:

```python
@dataclass(frozen=True)
class CommunityMatch:
    community_id: str | None
    canonical_name: str | None
    method: Literal["canonical", "alias", "address", "coordinate", "unknown"]
    confidence: Literal["high", "medium", "none"]
```

Name and alias matches are high confidence; address and coordinate matches are medium. Ambiguous
matches return unknown and record no community ID.

- [ ] **Step 3: Implement normalization, validation, and matching**

Expose these public methods:

- `CommunityRegistry.from_csv(path: Path) -> CommunityRegistry`
- `CommunityRegistry.version -> str`
- `CommunityRegistry.match_transaction(address, twd97_x, twd97_y, completion_year) -> CommunityMatch`
- `CommunityRegistry.match_listing(name, address, twd97_x, twd97_y, completion_year) -> CommunityMatch`
- `CommunityRegistry.public_catalog(station_code: str | None) -> list[dict[str, str]]`

The version must be a SHA-256 digest of canonical serialized rows, not a file modification time.
Normalization may remove whitespace and common punctuation but must not perform fuzzy string
similarity.

- [ ] **Step 4: Curate the initial registry**

Create 20–30 verified A17/A18/A19 communities. Start from already observed names such as
`竹風青庭`, `竹風青田`, `桃大真`, `桃大詠`, `威均帝璽`, `城市的遠見`, `太睿A19`,
`川睦叡極`, `問星`, and `美術水公園`, then verify enough additional high-volume Qingpu
communities to reach the minimum.

For every row:

- record the human-readable evidence source in `source_notes`;
- use stable slug-like IDs;
- include only aliases actually observed in official or 591 data;
- leave coordinates/year blank when not verified;
- never invent a builder or alias to increase coverage.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_community_registry.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/community_registry.py tests/test_community_registry.py
git add src/qingpu_insight/community_registry.py data/reference/qingpu_communities.csv tests/test_community_registry.py
git commit -m "feat(data): add curated Qingpu community registry"
```

---

## Task 3: Build Leakage-Safe Historical Community Features

**Files:**

- Create: `src/qingpu_insight/community_features.py`
- Create: `tests/test_community_features.py`
- Modify: `src/qingpu_insight/model_features.py`
- Test: `tests/test_model_features.py`

- [ ] **Step 1: Write failing chronological feature tests**

Build a small ordered frame with two communities, two stations, and transactions inside and outside
the prior 24-month window. For each row, assert that only strictly earlier transactions contribute.
Transactions on the same date must not see each other.

Required output:

```text
community_id
community_known
community_prior_count_24m
community_prior_median_twd_per_ping_24m
community_premium_vs_station_24m
```

The station comparison group is same `station_code` and `building_type`, also using strictly prior
24 months. When community count is below 5, median and premium must be `NaN`.

- [ ] **Step 2: Run the test and confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_community_features.py -q
```

- [ ] **Step 3: Implement chronological materialization**

Expose:

```python
COMMUNITY_FEATURE_COLUMNS = (
    "community_known",
    "community_prior_count_24m",
    "community_prior_median_twd_per_ping_24m",
    "community_premium_vs_station_24m",
)

def add_historical_community_features(
    frame: pd.DataFrame,
    registry: CommunityRegistry,
    *,
    lookback_months: int = 24,
    minimum_transactions: int = 5,
) -> pd.DataFrame:
    """Return a copy with strictly-prior community features in original row order."""
```

Sort internally but restore original row order. Use the dwelling-only
`target_unit_price_twd`; do not use asking prices or parking-inclusive totals.

- [ ] **Step 4: Build an inference snapshot**

Add immutable serializable values:

```python
@dataclass(frozen=True)
class CommunityFeatureValues:
    known: str
    prior_count_24m: int
    prior_median_twd_per_ping_24m: float | None
    premium_vs_station_24m: float | None

def build_community_feature_snapshot(
    frame: pd.DataFrame,
    *,
    cutoff: pd.Timestamp,
) -> dict[str, CommunityFeatureValues]:
    """Aggregate inference values using rows at or before the artifact cutoff."""
```

Assert the snapshot uses no row after `cutoff` and omits unreliable median/premium values.

- [ ] **Step 5: Define feature families without changing legacy defaults**

In `model_features.py`, preserve existing `FEATURE_COLUMNS` as the v3 baseline and add:

```python
COMMON_AREA_FEATURE_COLUMNS = ("common_area_ratio",)
MANAGEMENT_FEATURE_COLUMNS = ("has_management",)
COMMUNITY_FEATURE_COLUMNS = (
    "community_known",
    "community_prior_count_24m",
    "community_prior_median_twd_per_ping_24m",
    "community_premium_vs_station_24m",
)

RESALE_FEATURE_SETS = {
    "baseline_v3": FEATURE_COLUMNS,
    "common_area": FEATURE_COLUMNS + COMMON_AREA_FEATURE_COLUMNS,
    "community": FEATURE_COLUMNS + COMMUNITY_FEATURE_COLUMNS,
    "common_area_community": (
        FEATURE_COLUMNS + COMMON_AREA_FEATURE_COLUMNS + COMMUNITY_FEATURE_COLUMNS
    ),
    "common_area_community_management": (
        FEATURE_COLUMNS
        + COMMON_AREA_FEATURE_COLUMNS
        + COMMUNITY_FEATURE_COLUMNS
        + MANAGEMENT_FEATURE_COLUMNS
    ),
}
```

Add tests proving legacy feature order remains unchanged.

- [ ] **Step 6: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_community_features.py tests/test_model_features.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/community_features.py src/qingpu_insight/model_features.py tests/test_community_features.py tests/test_model_features.py
git add src/qingpu_insight/community_features.py src/qingpu_insight/model_features.py tests/test_community_features.py tests/test_model_features.py
git commit -m "feat(models): add time-safe community features"
```

---

## Task 4: Add Governed Shared-Feature Experiments

**Files:**

- Modify: `src/qingpu_insight/model_training.py`
- Modify: `src/qingpu_insight/model_analysis.py`
- Test: `tests/test_model_training.py`
- Test: `tests/test_model_analysis.py`

- [ ] **Step 1: Write failing preprocessor tests**

Assert:

- public-area ratio, prior count, prior median, and premium are numeric;
- `community_known` and `has_management` are categorical;
- missing optional values pass through the existing imputation pipeline;
- each `RESALE_FEATURE_SETS` entry builds and fits.

- [ ] **Step 2: Write failing experiment-matrix tests**

Replace implicit selection of `exp_list[1]` with an explicit result:

```python
@dataclass(frozen=True)
class SharedFeatureExperimentResult:
    calibration_experiments: Sequence[FeatureExperiment]
    locked_feature_set_name: str
    locked_feature_columns: Sequence[str]
    selection_reason: str
```

Assert the matrix is exactly:

```text
baseline_v3
common_area
community
common_area_community
common_area_community_management
```

Selection must use calibration MAE first, then MAPE, then fewer features as a deterministic
tie-breaker. Only `baseline_v3`, `common_area`, `community`, and
`common_area_community` may be locked. The management experiment is always report-only.

- [ ] **Step 3: Prove final-test isolation**

Patch the final-test evaluator to raise if called during
`run_shared_feature_experiments()`. The experiment function must still return the locked family,
proving selection did not inspect final-test labels.

- [ ] **Step 4: Implement candidate preprocessing and experiments**

Update numeric/categorical routing in `model_training.py`. In `model_analysis.py`, retain the
existing v3 ablation evidence and add a separate `run_shared_feature_experiments()` function.
Do not overload the existing ablation names with the new experiment matrix.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py tests/test_model_analysis.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/model_training.py src/qingpu_insight/model_analysis.py tests/test_model_training.py tests/test_model_analysis.py
git add src/qingpu_insight/model_training.py src/qingpu_insight/model_analysis.py tests/test_model_training.py tests/test_model_analysis.py
git commit -m "feat(models): govern shared feature experiments"
```

---

## Task 5: Persist Exact Feature Evidence and Inference State

**Files:**

- Modify: `src/qingpu_insight/valuation.py`
- Modify: `src/qingpu_insight/model_artifacts.py`
- Modify: `src/qingpu_insight/model_training_service.py`
- Test: `tests/test_valuation.py`
- Test: `tests/test_model_artifacts.py`
- Test: `tests/test_model_training_service.py`

- [ ] **Step 1: Write failing legacy and new bundle tests**

Add optional fields to `ValuationBundle`:

```python
community_registry_version: str | None = None
community_registry_rows: Sequence[dict[str, object]] = ()
community_feature_snapshot: dict[str, CommunityFeatureValues] | None = None
shared_feature_experiment: dict[str, object] | None = None
```

Test that an old pickle without these attributes loads and returns safe defaults through
`__getattr__`.

- [ ] **Step 2: Write failing training-service orchestration tests**

For both guided and AutoML resale training, assert the service:

1. loads and validates the registry;
2. assigns communities before splitting;
3. materializes historical features;
4. runs the shared-feature experiment matrix on calibration;
5. locks one family;
6. evaluates only baseline and locked family on final test;
7. stores the registry digest, rows, snapshot, coverage, and selection evidence.

AutoML may tune only the locked feature family; it must not reopen feature-family selection with
final-test feedback.

For presale training, assert behavior and feature contract remain unchanged.

- [ ] **Step 3: Implement feature-contract versioning**

Use feature contract version `4` only when the locked resale family includes new shared features;
otherwise retain version `3`. Do not change artifact manifest schema merely because the feature
contract changes. Keep schema 3 for guided runs and schema 4 for AutoML runs.

- [ ] **Step 4: Implement the final release gate**

The locked family is recommendation-eligible only when all hold:

```text
final-test MAE improves by at least 2% versus baseline
overall final-test MAPE does not worsen
no A17/A18/A19 MAPE worsens by more than 1 percentage point
at least 2 of 3 annual backtests pass
prediction-interval coverage remains within the existing accepted range
the committed 591 validation evidence has at least 20 labeled pages
591 public-area parsing success is at least 70%
known-community recognition is at least 80%
```

If it fails, still persist the experiment evidence but create the candidate with the baseline v3
feature family and an explicit rejection reason. Never silently substitute an unreported family.
If 591 validation evidence is absent or its registry digest differs from the training registry,
fail the shared-feature gate closed while keeping baseline training available.

- [ ] **Step 5: Build the inference snapshot at the artifact cutoff**

The snapshot must represent only transactions on or before `data_max_date`. Serialize the exact
registry rows used for training so inference does not change when the repository CSV changes.

- [ ] **Step 6: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_valuation.py tests/test_model_artifacts.py tests/test_model_training_service.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/valuation.py src/qingpu_insight/model_artifacts.py src/qingpu_insight/model_training_service.py tests/test_valuation.py tests/test_model_artifacts.py tests/test_model_training_service.py
git add src/qingpu_insight/valuation.py src/qingpu_insight/model_artifacts.py src/qingpu_insight/model_training_service.py tests/test_valuation.py tests/test_model_artifacts.py tests/test_model_training_service.py
git commit -m "feat(models): persist shared feature evidence"
```

---

## Task 6: Apply Optional Features Consistently at Valuation Time

**Files:**

- Modify: `src/qingpu_insight/model_features.py`
- Modify: `src/qingpu_insight/valuation.py`
- Test: `tests/test_model_features.py`
- Test: `tests/test_valuation.py`

- [ ] **Step 1: Write failing input-contract tests**

Extend `ValuationInput` with:

```python
common_area_ratio: float | None = None
community_id: str | None = None
```

Assert public-area ratio accepts `0 <= value <= 0.70`, rejects non-finite/out-of-range values, and
allows omission. Assert unknown community IDs do not crash valuation.

- [ ] **Step 2: Write inference-parity tests**

Given an artifact whose `feature_columns` contains shared features:

- a known community receives the artifact snapshot values;
- an unknown community receives `community_known="unknown"` and missing numeric statistics;
- omitted public-area ratio remains missing for the model imputer;
- old v3 artifacts receive exactly the old feature frame;
- changing the repository CSV after training does not change predictions.

- [ ] **Step 3: Implement bundle-aware input framing**

Change the valuation boundary from a global feature list to the artifact’s exact list:

```python
def input_frame(
    value: ValuationInput,
    data_date: pd.Timestamp,
    *,
    feature_columns: Sequence[str] = FEATURE_COLUMNS,
    community_snapshot: dict[str, CommunityFeatureValues] | None = None,
) -> pd.DataFrame:
    """Build one inference row in the artifact's exact feature-column order."""
```

The returned columns must exactly match `bundle.feature_columns` and preserve order.

- [ ] **Step 4: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_features.py tests/test_valuation.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/model_features.py src/qingpu_insight/valuation.py tests/test_model_features.py tests/test_valuation.py
git add src/qingpu_insight/model_features.py src/qingpu_insight/valuation.py tests/test_model_features.py tests/test_valuation.py
git commit -m "feat(valuation): apply optional shared features"
```

---

## Task 7: Parse Public-Area Ratio and Community Evidence from 591

**Files:**

- Modify: `src/qingpu_insight/conversation_listing_parser.py`
- Modify: `src/qingpu_insight/conversation_evidence.py`
- Modify: `src/qingpu_insight/conversation_service.py`
- Test: `tests/test_conversation_listing_parser.py`
- Test: `tests/test_conversation_evidence.py`
- Test: `tests/test_conversation_service.py`
- Add minimal fixtures under: `tests/fixtures/conversation_listings/`
- Create: `data/reference/591_shared_feature_validation.json`

- [ ] **Step 1: Write failing parser tests**

Add minimal sale and newhouse fragments covering:

- `公設比 34.8%`;
- JSON-LD/embedded-state value plus DOM fallback;
- absent value;
- malformed `348%`, negative, and non-numeric values;
- community canonical name and alias;
- a name not present in the registry.

Extend `ParsedListingDetail` with:

```python
common_area_ratio: float | None
common_area_ratio_source: str | None
```

Store ratios as decimal fractions, so `34.8%` becomes `0.348`.

- [ ] **Step 2: Implement bounded parsing**

Accept only `0 <= ratio <= 0.70`. Prefer structured page data, then use narrowly scoped DOM labels.
Do not parse arbitrary percentages elsewhere on the page.

- [ ] **Step 3: Write failing evidence and valuation tests**

Assert conversation evidence includes:

```text
listing.common_area_ratio
listing.community_name
listing.community_match_method
listing.community_known
```

The `_conversation_valuation` path must pass the ratio and matched community ID to
`ValuationInput`. Unknown community or invalid ratio must add a warning and continue with the
existing station/location estimate.

- [ ] **Step 4: Add the manual 591 validation corpus**

Create a de-identified JSON manifest for at least 20 user-submitted or manually authorized Qingpu
resale detail samples. Store only expected URL type, expected public-area ratio, expected community
ID, and parser outcome. Report:

- public-area parsing success over all labeled pages;
- known-community recognition over pages whose community is in the registry;
- full-sample community coverage.

Do not make the automated test fetch live 591 pages.

Write the aggregate result to `data/reference/591_shared_feature_validation.json` with
`sample_count`, `public_area_success_rate`, `known_community_success_rate`,
`full_sample_community_coverage`, `community_registry_version`, `corpus_sha256`, and `checked_at`.
Tests must reject a report whose digest, sample count, or rates do not match the labeled corpus.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_listing_parser.py tests/test_conversation_evidence.py tests/test_conversation_service.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/conversation_listing_parser.py src/qingpu_insight/conversation_evidence.py src/qingpu_insight/conversation_service.py tests/test_conversation_listing_parser.py tests/test_conversation_evidence.py tests/test_conversation_service.py
git add src/qingpu_insight/conversation_listing_parser.py src/qingpu_insight/conversation_evidence.py src/qingpu_insight/conversation_service.py data/reference/591_shared_feature_validation.json tests/test_conversation_listing_parser.py tests/test_conversation_evidence.py tests/test_conversation_service.py tests/fixtures/conversation_listings
git commit -m "feat(assistant): capture shared listing features"
```

---

## Task 8: Expose the Community Catalog and Optional Manual Inputs

**Files:**

- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `src/qingpu_insight/static/valuation_form.js`
- Test: `tests/test_web.py`
- Test: `tests/js/valuation_form_contract.cjs`

- [ ] **Step 1: Write failing API tests**

Add:

```text
GET /api/communities
GET /api/communities?station_code=A18
```

The response exposes only `community_id`, `canonical_name`, and `station_code`; it must not expose
aliases, full addresses, coordinates, or evidence notes. Invalid station returns HTTP 400.

Extend valuation payload tests for optional:

```json
{
  "common_area_ratio_percent": 34.8,
  "community_id": "verified-stable-id"
}
```

Convert percentage input to decimal at the HTTP boundary.

- [ ] **Step 2: Implement the catalog and validation boundary**

Load the application registry once during app creation. For valuation, validate the selected ID
against the registry but permit an empty value. If an ID is no longer in the current registry,
return a clear field error rather than silently mapping it to another project.

- [ ] **Step 3: Write the failing browser contract**

Assert the valuation form:

- shows `公設比（選填）`;
- filters community options after station selection;
- includes `不確定／不提供`;
- clears a previously selected A18 community when station changes to A17;
- submits the two optional fields only when supplied;
- preserves the current valuation flow when both are blank.

- [ ] **Step 4: Implement compact optional controls**

Place both controls in the existing building-information group, not in a new top-level panel.
Explain in one short hint that missing values are allowed. Do not add a builder field.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -q
node tests/js/valuation_form_contract.cjs
.\.venv\Scripts\ruff.exe check src/qingpu_insight/web.py tests/test_web.py
git add src/qingpu_insight/web.py src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js src/qingpu_insight/static/valuation_form.js tests/test_web.py tests/js/valuation_form_contract.cjs
git commit -m "feat(web): accept optional community valuation context"
```

---

## Task 9: Make Feature Research Understandable in Model Reports

**Files:**

- Modify: `src/qingpu_insight/model_observatory.py`
- Modify: `src/qingpu_insight/valuation_reporting.py`
- Modify: `src/qingpu_insight/static/models_admin.js`
- Modify: `src/qingpu_insight/templates/admin.html`
- Test: `tests/test_model_observatory.py`
- Test: `tests/test_valuation_reporting.py`
- Test: `tests/js/model_admin_contract.cjs`

- [ ] **Step 1: Write failing report-projection tests**

Require the candidate detail API/report to include:

```text
calibration results for baseline_v3 and E1–E4
locked feature family and plain-language reason
baseline versus locked final-test metrics
A17/A18/A19 final-test MAPE deltas
annual backtest pass count
public-area coverage and distribution
community match coverage
known versus unknown community error
registry version and statistics cutoff
release eligibility and failed gates
```

Do not label calibration metrics as final-test metrics.

- [ ] **Step 2: Implement stable report projection**

Use explicit keys and safe defaults so older candidates display `未提供此版本證據` instead of
throwing. Do not recompute model evidence in the web request.

- [ ] **Step 3: Write the failing JavaScript contract**

Assert the full model report contains a compact section titled `新增特徵研究` with:

- a one-line verdict;
- five calibration rows;
- one baseline-versus-winner final-test comparison;
- coverage warnings;
- collapsible technical evidence.

It must explain MAE/MAPE in existing beginner-friendly language and avoid dumping raw JSON.

- [ ] **Step 4: Implement the admin presentation**

Use existing admin colors and components. Highlight `有改善／未證明改善`, not only raw numbers.
Keep rejected experiments visible because they are useful report and interview evidence.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_observatory.py tests/test_valuation_reporting.py -q
node tests/js/model_admin_contract.cjs
.\.venv\Scripts\ruff.exe check src/qingpu_insight/model_observatory.py src/qingpu_insight/valuation_reporting.py tests/test_model_observatory.py tests/test_valuation_reporting.py
git add src/qingpu_insight/model_observatory.py src/qingpu_insight/valuation_reporting.py src/qingpu_insight/static/models_admin.js src/qingpu_insight/templates/admin.html tests/test_model_observatory.py tests/test_valuation_reporting.py tests/js/model_admin_contract.cjs
git commit -m "feat(models): explain shared feature research"
```

---

## Task 10: Rebuild Data and Train One Reviewable Candidate

**Files:**

- Modify only if defects are found: relevant implementation and test files from Tasks 1–9
- Generate locally, do not commit by default: `data/processed/market_transactions.parquet`
- Generate locally, do not commit by default: `candidates/`
- Record results: `docs/research/2026-07-29-resale-shared-features-results.md`

- [ ] **Step 1: Rebuild the processed official dataset**

Use the repository’s documented `qingpu-data` command or management UI. Confirm:

```text
row count is non-zero and comparable to the existing dataset
resale count is non-zero
common_area_ratio valid coverage is reported
ratio median and p10/p90 are plausible
community matching produces known and unknown groups
no future transaction contributes to prior features
```

- [ ] **Step 2: Train one bounded resale candidate**

Use the balanced guided profile first. AutoML is allowed only after the fixed E1–E4 feature-family
comparison, so feature selection and hyperparameter search remain separate questions.

Do not publish the candidate.

- [ ] **Step 3: Inspect gate evidence**

Record the actual:

- baseline and locked family calibration metrics;
- baseline and locked family final-test metrics;
- station deltas;
- 3 annual backtests;
- interval coverage;
- public-area and community coverage;
- selected estimator and training profile;
- gate result and rejection reasons.

- [ ] **Step 4: Add the research record**

Write `docs/research/2026-07-29-resale-shared-features-results.md` with:

1. research question;
2. data and leakage controls;
3. experiment table;
4. result;
5. whether the official model should change;
6. limitations;
7. concise midterm-presentation narrative;
8. concise interview narrative.

If the features do not pass, state that clearly and keep the baseline. A negative result is valid
research evidence.

- [ ] **Step 5: Run evidence-specific tests and commit documentation**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training_service.py tests/test_model_observatory.py tests/test_valuation.py -q
git add docs/research/2026-07-29-resale-shared-features-results.md
git commit -m "docs: record resale shared feature study"
```

Do not add generated candidate artifacts or parquet files unless the repository’s existing tracking
policy and file-size checks explicitly require them.

---

## Task 11: Full Verification, Browser QA, and Documentation

**Files:**

- Modify: `README.md`
- Modify: `docs/m2-valuation-methodology.md`
- Modify if present: project operations/model documentation that lists feature contract versions
- Test: all relevant Python and JavaScript suites

- [ ] **Step 1: Update user and methodology documentation**

Document:

- which official fields feed public-area ratio;
- why builder/material/partition/elevator were not promoted now;
- how the curated registry is governed;
- how 24-month historical features prevent leakage;
- why missing optional inputs are safe;
- calibration selection versus one-time final test;
- how to review rather than automatically publish a candidate.

- [ ] **Step 2: Run the full automated gate**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
Get-ChildItem tests/js/*_contract.cjs | ForEach-Object { node $_.FullName }
.\.venv\Scripts\ruff.exe check src tests
git diff --check
```

Expected: all tests pass, all contracts pass, Ruff is clean, and no whitespace errors remain.

- [ ] **Step 3: Start the application and perform browser QA**

Start:

```powershell
.\.venv\Scripts\qingpu-web.exe
```

Verify in a real browser:

1. homepage loads without console errors;
2. resale valuation succeeds with both optional fields blank;
3. station selection filters the community list;
4. known community plus valid public-area ratio succeeds;
5. invalid ratio shows a field-level error;
6. one supported 591 resale detail URL captures evidence and completes analysis;
7. an unknown community degrades to station/location evidence;
8. model observatory opens the complete candidate report;
9. experiment rows are clearly calibration evidence;
10. only baseline and locked winner are labeled final-test evidence.

- [ ] **Step 4: Review security and privacy boundaries**

Confirm:

- community API exposes no full addresses or coordinates;
- committed fixtures contain no session cookies, API keys, personal data, or full scraped pages;
- model reports show aggregate evidence only;
- registry aliases cannot inject HTML into the homepage or admin UI.

- [ ] **Step 5: Commit final documentation and fixes**

Run:

```powershell
git add README.md docs/m2-valuation-methodology.md
git commit -m "docs: explain resale shared features"
git status --short
```

Expected final status: only the user’s pre-existing untracked candidate directories may remain.

---

## Final Acceptance Checklist

- [ ] Public-area ratio is derived from official area components and bounded to 0–70%.
- [ ] The registry contains at least 20 verified Qingpu communities and has a content digest.
- [ ] Community statistics use strictly prior 24-month transactions and a minimum sample of 5.
- [ ] Raw community names never enter the estimator.
- [ ] E1–E4 selection uses calibration only.
- [ ] Only baseline and the locked winner touch final test.
- [ ] New features must pass all release gates or the candidate safely retains baseline v3.
- [ ] Legacy artifacts, missing public-area ratio, and unknown community all remain usable.
- [ ] 591 parsing is measured on at least 20 labeled, de-identified samples.
- [ ] Manual valuation offers optional public-area and station-filtered community controls.
- [ ] Model reports clearly separate calibration, final-test, coverage, and limitations.
- [ ] One real candidate run is documented for the midterm report and portfolio narrative.
- [ ] Full Python tests, JavaScript contracts, Ruff, browser QA, and privacy review pass.
