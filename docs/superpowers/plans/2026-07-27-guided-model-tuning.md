# Guided Model Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe quick/balanced/thorough/custom model comparison for resale and presale, persist the exact training evidence, and present beginner-first metrics in both admin model surfaces.

**Architecture:** Introduce an immutable tuning-plan domain module, parameterize the existing sklearn estimator factory, and compare every requested profile on one shared chronological split before touching final test data. Persist schema-v3 evidence in the candidate manifest, project it through the existing observatory API, and use one shared JavaScript/Jinja/CSS presentation layer in `/admin` and the model observatory.

**Tech Stack:** Python 3.11+, dataclasses, Pydantic v2, pandas, NumPy, scikit-learn, Flask, pytest, joblib, browser JavaScript, Node.js contract tests, HTML/CSS.

## Global Constraints

- Always compare server-defined `quick`, `balanced`, and `thorough` profiles.
- Optionally compare one validated `custom` profile.
- Support resale, presale, or both; recency half-life applies only to resale.
- Fixed profile parameters are exact: quick `0.08/180/160`, balanced `0.06/350/400`, thorough `0.04/600/700` for HGB learning rate/HGB iterations/RF trees.
- Custom ranges are exact: HGB learning rate `0.01–0.20`, HGB iterations `100–1000`, RF trees `100–1000`, resale half-life `12–84` months.
- Use calibration MAE to select the profile+model winner; final test must never change the winner.
- Deterministic tie order: calibration MAPE, RMSE, profile `quick → balanced → thorough → custom`, then model name.
- Preserve the current Ridge, Random Forest, HistGradientBoosting, Recent Median Baseline, feature evidence, station metrics, annual backtests, and release gates.
- Training creates immutable candidates only; never auto-publish.
- Schema-v1 and schema-v2 manifests remain readable and visibly marked as legacy.
- Reports must use the actual selected half-life; remove the hard-coded 24-month claim.
- Do not add XGBoost, AutoML, arbitrary estimators, scheduling, cancellation, or new E2E frameworks.
- Follow strict TDD and commit after every independently reviewable task.

---

## File Structure

- Create `src/qingpu_insight/model_tuning.py`: immutable profile catalog, request validation, snapshots, and profile ordering.
- Modify `src/qingpu_insight/model_training.py`: parameterized estimators and calibration-only multi-profile experiment.
- Modify `src/qingpu_insight/valuation.py`: retrain the deployed winner with its actual half-life.
- Modify `src/qingpu_insight/model_analysis.py`: backtest the exact winning profile.
- Modify `src/qingpu_insight/model_artifacts.py`: schema-v3 profile evidence and legacy defaults.
- Modify `src/qingpu_insight/model_training_service.py`: profile orchestration, progress, artifacts, and fail-closed behavior.
- Modify `src/qingpu_insight/valuation_reporting.py`: actual parameters, profile comparisons, and interval summary.
- Modify `src/qingpu_insight/model_observatory.py`: expose schema-v3 tuning metadata.
- Modify `src/qingpu_insight/web.py`: validated tuning request contract.
- Modify `src/qingpu_insight/static/models_admin.js`: shared form, metric, profile, and legacy view models.
- Create `src/qingpu_insight/static/model_training.css`: shared controls and beginner-summary styles.
- Create `src/qingpu_insight/templates/_model_training_controls.html`: shared Jinja form.
- Modify `src/qingpu_insight/templates/admin.html`: shared controls and guided result.
- Modify `src/qingpu_insight/templates/models_admin.html`: shared controls and guided result.
- Create `tests/test_model_tuning.py`; extend existing model, service, artifact, reporting, observatory, Web, and Node contracts.
- Modify `docs/m2-valuation-methodology.md` and `README.md`: actual profiles and guided operation.

### Task 1: Immutable tuning plan and validation

**Files:**
- Create: `src/qingpu_insight/model_tuning.py`
- Create: `tests/test_model_tuning.py`

**Interfaces:**
- Produces: `TrainingProfile`, `TrainingTuningPlan`, `TuningValidationError`, `parse_tuning_plan(markets, payload)`, `BALANCED_PROFILE`, and `PROFILE_ORDER`.
- Consumed later by: estimator construction, request parsing, service execution, artifacts, reports, and UI payload tests.

- [ ] **Step 1: Write failing catalog and parsing tests**

Create `tests/test_model_tuning.py` with:

```python
import math

import pytest

from qingpu_insight.model_tuning import (
    PROFILE_ORDER,
    TuningValidationError,
    parse_tuning_plan,
)


def test_default_plan_contains_exact_server_profiles() -> None:
    plan = parse_tuning_plan(("resale", "presale"), None)
    assert PROFILE_ORDER == ("quick", "balanced", "thorough", "custom")
    assert [p.name for p in plan.profiles] == ["quick", "balanced", "thorough"]
    assert [
        (p.hgb_learning_rate, p.hgb_max_iter, p.rf_n_estimators)
        for p in plan.profiles
    ] == [(0.08, 180, 160), (0.06, 350, 400), (0.04, 600, 700)]
    assert [p.recency_half_life_months for p in plan.profiles] == [48, 48, 48]


def test_resale_custom_profile_round_trips_exact_values() -> None:
    plan = parse_tuning_plan(
        ("resale",),
        {
            "mode": "preset_comparison",
            "include_custom": True,
            "custom": {
                "hgb_learning_rate": 0.05,
                "hgb_max_iter": 420,
                "rf_n_estimators": 520,
                "recency_half_life_months": 36,
            },
        },
    )
    custom = plan.profiles[-1]
    assert custom.name == "custom"
    assert custom.source == "custom"
    assert custom.snapshot()["recency_half_life_months"] == 36


def test_presale_custom_rejects_recency_half_life() -> None:
    with pytest.raises(TuningValidationError) as caught:
        parse_tuning_plan(
            ("presale",),
            {
                "mode": "preset_comparison",
                "include_custom": True,
                "custom": {
                    "hgb_learning_rate": 0.05,
                    "hgb_max_iter": 420,
                    "rf_n_estimators": 520,
                    "recency_half_life_months": 36,
                },
            },
        )
    assert caught.value.fields == {
        "custom.recency_half_life_months": "not_applicable"
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hgb_learning_rate", 0.0),
        ("hgb_learning_rate", 0.21),
        ("hgb_learning_rate", math.nan),
        ("hgb_max_iter", 99),
        ("hgb_max_iter", 1001),
        ("hgb_max_iter", True),
        ("rf_n_estimators", 99),
        ("rf_n_estimators", 1001),
        ("recency_half_life_months", 11),
        ("recency_half_life_months", 85),
    ],
)
def test_custom_profile_rejects_invalid_numeric_fields(field, value) -> None:
    custom = {
        "hgb_learning_rate": 0.05,
        "hgb_max_iter": 420,
        "rf_n_estimators": 520,
        "recency_half_life_months": 36,
    }
    custom[field] = value
    with pytest.raises(TuningValidationError) as caught:
        parse_tuning_plan(
            ("resale",),
            {"mode": "preset_comparison", "include_custom": True, "custom": custom},
        )
    assert f"custom.{field}" in caught.value.fields
```

Also test unknown tuning keys, an unsupported mode, `custom` when `include_custom` is false, missing custom fields, and presale custom with exactly the three model fields.

- [ ] **Step 2: Run the new test module and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_tuning.py -q
```

Expected: collection fails because `qingpu_insight.model_tuning` does not exist.

- [ ] **Step 3: Implement the immutable domain types**

Create `model_tuning.py` with these exact public types:

```python
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping

MarketName = Literal["resale", "presale"]
ProfileName = Literal["quick", "balanced", "thorough", "custom"]
ProfileSource = Literal["preset", "custom"]
PROFILE_ORDER: tuple[ProfileName, ...] = (
    "quick", "balanced", "thorough", "custom",
)


class TuningValidationError(ValueError):
    def __init__(self, fields: dict[str, str]) -> None:
        self.fields = fields
        super().__init__("invalid tuning plan")


@dataclass(frozen=True)
class TrainingProfile:
    name: ProfileName
    source: ProfileSource
    hgb_learning_rate: float
    hgb_max_iter: int
    rf_n_estimators: int
    recency_half_life_months: int | None

    def snapshot(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source,
            "hgb_learning_rate": self.hgb_learning_rate,
            "hgb_max_iter": self.hgb_max_iter,
            "rf_n_estimators": self.rf_n_estimators,
            "recency_half_life_months": self.recency_half_life_months,
        }


@dataclass(frozen=True)
class TrainingTuningPlan:
    version: int
    mode: Literal["preset_comparison"]
    profiles: tuple[TrainingProfile, ...]

    @property
    def include_custom(self) -> bool:
        return any(profile.name == "custom" for profile in self.profiles)
```

Define the exact presets and `BALANCED_PROFILE`:

```python
PRESET_PROFILES = (
    TrainingProfile("quick", "preset", 0.08, 180, 160, 48),
    TrainingProfile("balanced", "preset", 0.06, 350, 400, 48),
    TrainingProfile("thorough", "preset", 0.04, 600, 700, 48),
)
BALANCED_PROFILE = PRESET_PROFILES[1]
```

Implement validation with explicit numeric guards:

```python
def _finite_float(value: Any, low: float, high: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and low <= float(value) <= high
    )


def _bounded_int(value: Any, low: int, high: int) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and low <= value <= high
    )


def parse_tuning_plan(
    markets: tuple[MarketName, ...],
    payload: Mapping[str, Any] | None,
) -> TrainingTuningPlan:
    if payload is None:
        return TrainingTuningPlan(1, "preset_comparison", PRESET_PROFILES)
    if not isinstance(payload, Mapping):
        raise TuningValidationError({"body": "object"})

    fields: dict[str, str] = {}
    allowed = {"mode", "include_custom", "custom"}
    for key in sorted(set(payload) - allowed):
        fields[key] = "not_allowed"

    if payload.get("mode") != "preset_comparison":
        fields["mode"] = "preset_comparison"
    include_custom = payload.get("include_custom")
    if not isinstance(include_custom, bool):
        fields["include_custom"] = "boolean"

    raw_custom = payload.get("custom")
    if include_custom is False and raw_custom is not None:
        fields["custom"] = "not_allowed"
    if include_custom is True and not isinstance(raw_custom, Mapping):
        fields["custom"] = "object"
    if fields:
        raise TuningValidationError(fields)
    if include_custom is False:
        return TrainingTuningPlan(1, "preset_comparison", PRESET_PROFILES)

    assert isinstance(raw_custom, Mapping)
    custom_allowed = {
        "hgb_learning_rate",
        "hgb_max_iter",
        "rf_n_estimators",
        "recency_half_life_months",
    }
    for key in sorted(set(raw_custom) - custom_allowed):
        fields[f"custom.{key}"] = "not_allowed"

    if not _finite_float(raw_custom.get("hgb_learning_rate"), 0.01, 0.20):
        fields["custom.hgb_learning_rate"] = "number_0_01_to_0_20"
    if not _bounded_int(raw_custom.get("hgb_max_iter"), 100, 1000):
        fields["custom.hgb_max_iter"] = "integer_100_to_1000"
    if not _bounded_int(raw_custom.get("rf_n_estimators"), 100, 1000):
        fields["custom.rf_n_estimators"] = "integer_100_to_1000"

    resale_requested = "resale" in markets
    half_life = raw_custom.get("recency_half_life_months")
    if resale_requested:
        if not _bounded_int(half_life, 12, 84):
            fields[
                "custom.recency_half_life_months"
            ] = "integer_12_to_84"
    elif "recency_half_life_months" in raw_custom:
        fields["custom.recency_half_life_months"] = "not_applicable"

    if fields:
        raise TuningValidationError(fields)

    custom = TrainingProfile(
        name="custom",
        source="custom",
        hgb_learning_rate=float(raw_custom["hgb_learning_rate"]),
        hgb_max_iter=int(raw_custom["hgb_max_iter"]),
        rf_n_estimators=int(raw_custom["rf_n_estimators"]),
        recency_half_life_months=int(half_life) if resale_requested else None,
    )
    return TrainingTuningPlan(
        1,
        "preset_comparison",
        PRESET_PROFILES + (custom,),
    )
```

- [ ] **Step 4: Run domain tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_tuning.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the tuning domain**

```powershell
git add src/qingpu_insight/model_tuning.py tests/test_model_tuning.py
git commit -m "feat(models): add safe tuning profile contract"
```

### Task 2: Parameterize estimators and deployed recency fitting

**Files:**
- Modify: `tests/test_model_training.py`
- Modify: `tests/test_valuation.py`
- Modify: `src/qingpu_insight/model_training.py`
- Modify: `src/qingpu_insight/valuation.py`

**Interfaces:**
- Consumes: `TrainingProfile` and `BALANCED_PROFILE`.
- Produces: `candidate_estimators(..., profile: TrainingProfile = BALANCED_PROFILE)`, `evaluate_candidate(..., recency_half_life_months: int = 48)`, and `train_artifact(..., recency_half_life_months: int = 48)`.

- [ ] **Step 1: Write failing estimator-profile tests**

Add to `tests/test_model_training.py`:

```python
from qingpu_insight.model_tuning import TrainingProfile


def test_candidate_estimators_use_profile_parameters() -> None:
    profile = TrainingProfile("custom", "custom", 0.03, 444, 555, 30)
    estimators = candidate_estimators(seed=42, profile=profile)
    hgb = estimators["hist_gradient_boosting"].named_steps["model"]
    forest = estimators["random_forest"].named_steps["model"]
    assert hgb.learning_rate == 0.03
    assert hgb.max_iter == 444
    assert forest.n_estimators == 555
    assert len({
        id(pipeline.named_steps["features"])
        for pipeline in estimators.values()
    }) == 3


def test_evaluate_candidate_uses_requested_half_life(
    model_frame, monkeypatch,
) -> None:
    captured = {}
    original = model_training.recency_weights

    def capture(frame, reference_date=None, half_life_months=48, minimum=0.10):
        captured["half_life"] = half_life_months
        return original(frame, reference_date, half_life_months, minimum)

    monkeypatch.setattr(model_training, "recency_weights", capture)
    split = split_by_time(model_frame)
    evaluate_candidate(
        "ridge",
        candidate_estimators()["ridge"],
        split.train,
        split.calibration,
        use_recency_weights=True,
        recency_half_life_months=30,
    )
    assert captured["half_life"] == 30
```

- [ ] **Step 2: Write a failing deployed-artifact half-life test**

In `tests/test_valuation.py`, monkeypatch `qingpu_insight.model_training.recency_weights`, invoke `train_artifact(..., use_recency_weights=True, recency_half_life_months=30)`, and assert the captured value is 30. Use the existing trained bundle/split fixtures and keep the output in `tmp_path`.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py -k "profile_parameters or requested_half_life" -q
.\.venv\Scripts\python.exe -m pytest tests/test_valuation.py -k "half_life" -q
```

Expected: signatures reject `profile` and `recency_half_life_months`.

- [ ] **Step 4: Parameterize estimator construction**

Change the estimator factory signature:

```python
def candidate_estimators(
    feature_columns=FEATURE_COLUMNS,
    seed: int = 42,
    profile: TrainingProfile = BALANCED_PROFILE,
) -> dict[str, Pipeline]:
```

Build a separate preprocessor for each pipeline and use:

```python
HistGradientBoostingRegressor(
    learning_rate=profile.hgb_learning_rate,
    max_iter=profile.hgb_max_iter,
    max_leaf_nodes=31,
    l2_regularization=1.0,
    random_state=seed,
)
```

and:

```python
RandomForestRegressor(
    n_estimators=profile.rf_n_estimators,
    min_samples_leaf=5,
    max_features=0.8,
    random_state=seed,
    n_jobs=-1,
)
```

Keep no-profile callers exactly equivalent to the current balanced parameters.

- [ ] **Step 5: Thread the half-life through evaluation and artifact fitting**

Add `recency_half_life_months: int = 48` to `evaluate_candidate` and call:

```python
weights = (
    recency_weights(
        train_frame,
        half_life_months=recency_half_life_months,
    )
    if use_recency_weights else None
)
```

Add the same defaulted parameter to `train_artifact` and use it when retraining `training_frame`:

```python
weights = (
    recency_weights(
        train_frame,
        half_life_months=recency_half_life_months,
    )
    if use_recency_weights else None
)
```

- [ ] **Step 6: Run focused and existing model tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py tests/test_valuation.py -q
```

Expected: PASS, including existing 48-month default behavior.

- [ ] **Step 7: Commit parameterized fitting**

```powershell
git add src/qingpu_insight/model_training.py src/qingpu_insight/valuation.py tests/test_model_training.py tests/test_valuation.py
git commit -m "feat(models): parameterize estimator training profiles"
```

### Task 3: Calibration-only multi-profile selection

**Files:**
- Modify: `tests/test_model_training.py`
- Modify: `src/qingpu_insight/model_training.py`

**Interfaces:**
- Consumes: `tuple[TrainingProfile, ...]`, `candidate_estimators`, chronological `TimeSplit`.
- Produces: `ProfileCandidateEvaluation`, `ProfileEvaluation`, `TunedModelExperiment`, `ProfileEvaluationError`, and `run_tuned_model_experiment(...)`.

- [ ] **Step 1: Write failing selection and final-test isolation tests**

Add deterministic tests using a fake estimator whose `predict` returns a configured constant:

```python
class ConstantEstimator:
    def __init__(self, value: float) -> None:
        self.value = value

    def fit(self, X, y, **kwargs):
        return self

    def predict(self, X):
        return np.full(len(X), self.value)
```

Monkeypatch `candidate_estimators` to return profile-specific constants. Construct a `TimeSplit` where:

- `quick:ridge` has the lowest calibration MAE;
- `thorough:ridge` has the lowest test MAE.

Assert:

```python
experiment = run_tuned_model_experiment(
    split,
    profiles=profiles,
    feature_columns=("building_area_ping",),
    use_recency_weights=False,
)
assert experiment.selected_profile == "quick"
assert experiment.selected_model == "ridge"
assert set(experiment.final_test_results) == {"baseline", "ridge"}
```

Add a tie test that exercises MAPE, RMSE, profile order, and model-name order. Add a failure test where one profile returns no successful candidates and assert `ProfileEvaluationError.profile_name`.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py -k "tuned_model or profile_selection" -q
```

Expected: imports fail because the tuned experiment types do not exist.

- [ ] **Step 3: Add the experiment result types**

Implement:

```python
@dataclass(frozen=True)
class ProfileCandidateEvaluation:
    profile_name: str
    model_name: str
    evaluation: CandidateEvaluation


@dataclass(frozen=True)
class ProfileEvaluation:
    profile: TrainingProfile
    candidates: tuple[ProfileCandidateEvaluation, ...]
    candidate_errors: dict[str, str]


@dataclass(frozen=True)
class TunedModelExperiment:
    profile_results: tuple[ProfileEvaluation, ...]
    selected_profile: str
    selected_model: str
    selected_evaluation: CandidateEvaluation
    selected_estimator: Any
    final_test_results: dict[str, CandidateEvaluation]
    recommended: bool
    reason_codes: tuple[str, ...]


class ProfileEvaluationError(Exception):
    def __init__(self, profile_name: str) -> None:
        self.profile_name = profile_name
        super().__init__(f"profile evaluation failed: {profile_name}")
```

- [ ] **Step 4: Implement calibration-only comparison**

`run_tuned_model_experiment` must:

1. Fit/evaluate one baseline on calibration.
2. For each profile, build all three candidate estimators.
3. Use the profile half-life only when resale weighting is enabled.
4. Record safe `candidate_failed` codes without raw exceptions.
5. Raise `ProfileEvaluationError` if a requested profile has zero successful candidates.
6. Select the minimum key:

```python
def tuned_candidate_sort_key(item: ProfileCandidateEvaluation) -> tuple:
    overall = item.evaluation.metrics.loc["overall"]
    return (
        float(overall["mae"]),
        float(overall["mape"]),
        float(overall["rmse"]),
        PROFILE_ORDER.index(item.profile_name),
        item.model_name,
    )
```

7. Evaluate only the baseline and locked winner on final test.
8. Set `recommended` with the existing final-test `passes_release_gate`.
9. Never consult final-test metrics before the winner is stored.

Accept an optional `on_profile_start: Callable[[str], None] | None` and invoke it once before each profile, enabling truthful job progress later.

- [ ] **Step 5: Run model-training tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit calibrated profile selection**

```powershell
git add src/qingpu_insight/model_training.py tests/test_model_training.py
git commit -m "feat(models): compare tuning profiles on calibration data"
```

### Task 4: Schema-v3 tuning evidence

**Files:**
- Modify: `tests/test_model_artifacts.py`
- Modify: `tests/test_model_observatory.py`
- Modify: `src/qingpu_insight/model_artifacts.py`
- Modify: `src/qingpu_insight/model_observatory.py`

**Interfaces:**
- Consumes: resolved profile snapshots and profile calibration metrics.
- Produces: `TrainingProfileSnapshot`, `ProfileTrainingResult`, schema-v3 `TrainingManifest`, and API projection with legacy fallback.

- [ ] **Step 1: Write failing schema-v3 and legacy tests**

Add a Pydantic round-trip test by extending the existing `manifest_v2` fixture:

```python
def test_schema_v3_round_trips_tuning_evidence(manifest_v2) -> None:
    quick = TrainingProfileSnapshot(
        name="quick",
        source="preset",
        hgb_learning_rate=0.08,
        hgb_max_iter=180,
        rf_n_estimators=160,
        recency_half_life_months=48,
    )
    result = manifest_v2.results[0].model_copy(update={
        "selected_profile": "quick",
        "profile_results": [
            ProfileTrainingResult(
                profile_name="quick",
                parameters={
                    "hgb_learning_rate": 0.08,
                    "hgb_max_iter": 180,
                    "rf_n_estimators": 160,
                    "recency_half_life_months": 48,
                },
                selection_metrics={"ridge": {"overall": {"mae": 50_000}}},
                candidate_errors={},
            )
        ],
        "test_coverage": 0.9,
        "average_interval_width_twd_per_ping": 120_000,
    })
    manifest = manifest_v2.model_copy(update={
        "schema_version": 3,
        "tuning_plan_version": 1,
        "profiles": [quick],
        "results": [result],
    })
    loaded = TrainingManifest.model_validate_json(manifest.model_dump_json())
    assert loaded.schema_version == 3
    assert loaded.results[0].selected_profile == "quick"
    assert loaded.results[0].test_coverage == 0.9
```

Extend v1/v2 tests to assert:

```python
assert loaded.tuning_plan_version is None
assert loaded.profiles == []
assert loaded.results[0].selected_profile is None
assert loaded.results[0].profile_results == []
assert loaded.results[0].test_coverage is None
```

Add an observatory test asserting schema-v3 fields are present and schema-v2 detail includes `legacy_tuning_record: true`.

- [ ] **Step 2: Run artifact and observatory tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_artifacts.py tests/test_model_observatory.py -q
```

Expected: schema version 3 and the new fields are rejected or missing.

- [ ] **Step 3: Add strongly validated artifact models**

In `model_artifacts.py`, add:

```python
class TrainingProfileSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["quick", "balanced", "thorough", "custom"]
    source: Literal["preset", "custom"]
    hgb_learning_rate: float = Field(ge=0.01, le=0.20)
    hgb_max_iter: int = Field(ge=100, le=1000)
    rf_n_estimators: int = Field(ge=100, le=1000)
    recency_half_life_months: int | None = Field(default=None, ge=12, le=84)


class ProfileTrainingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_name: Literal["quick", "balanced", "thorough", "custom"]
    parameters: dict[str, int | float | None]
    selection_metrics: dict[str, dict[str, object]]
    candidate_errors: dict[str, str] = Field(default_factory=dict)
```

Extend `MarketTrainingResult` with defaulted legacy-safe fields:

```python
selected_profile: Literal["quick", "balanced", "thorough", "custom"] | None = None
profile_results: list[ProfileTrainingResult] = Field(default_factory=list)
test_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
average_interval_width_twd_per_ping: float | None = Field(default=None, ge=0.0)
```

Extend `TrainingManifest`:

```python
schema_version: Literal[1, 2, 3] = 1
tuning_plan_version: int | None = Field(default=None, ge=1)
profiles: list[TrainingProfileSnapshot] = Field(default_factory=list)
```

- [ ] **Step 4: Project v3 and legacy metadata**

In `ModelObservatory.get_run`, include top-level manifest fields:

```python
"tuning_plan_version": manifest.tuning_plan_version,
"profiles": [profile.model_dump(mode="json") for profile in manifest.profiles],
"legacy_tuning_record": manifest.schema_version < 3,
```

`_project_result` already uses `model_dump`, so the result-level profile evidence remains public without adding paths or secrets.

- [ ] **Step 5: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_artifacts.py tests/test_model_observatory.py -q
git add src/qingpu_insight/model_artifacts.py src/qingpu_insight/model_observatory.py tests/test_model_artifacts.py tests/test_model_observatory.py
git commit -m "feat(models): persist schema v3 tuning evidence"
```

Expected: tests PASS and commit succeeds.

### Task 5: Exact-profile backtests and reports

**Files:**
- Modify: `tests/test_model_analysis.py`
- Modify: `tests/test_valuation_reporting.py`
- Modify: `src/qingpu_insight/model_analysis.py`
- Modify: `src/qingpu_insight/valuation_reporting.py`

**Interfaces:**
- Consumes: `TunedModelExperiment`, selected `TrainingProfile`, and the locked algorithm.
- Produces: `run_annual_backtests(..., profile)`, `compute_interval_summary(...)`, JSON/Markdown reports with actual tuning parameters.

- [ ] **Step 1: Write failing exact-profile backtest tests**

Monkeypatch `candidate_estimators` in `test_model_analysis.py`, call:

```python
run_annual_backtests(
    frame,
    selected_model_name="hist_gradient_boosting",
    feature_columns=BASE_FEATURE_COLUMNS,
    profile=TrainingProfile("custom", "custom", 0.05, 420, 520, 36),
)
```

Assert the factory received the custom profile and `evaluate_candidate` received `recency_half_life_months=36`.

- [ ] **Step 2: Write failing report tests for actual parameters**

Build a `TunedModelExperiment` fixture with selected custom profile and assert:

```python
payload = json.loads(
    write_evaluation(
        bundle, experiment, split, tmp_path,
        selected_profile=custom_profile,
    ).read_text(encoding="utf-8")
)
assert payload["selected_profile"] == "custom"
assert payload["recency_weighting"]["half_life_months"] == 36
assert payload["profile_results"]["custom"]["parameters"]["hgb_max_iter"] == 420
assert "24 個月" not in write_model_card(
    bundle, experiment, leakage, tmp_path,
    selected_profile=custom_profile,
).read_text(encoding="utf-8")
```

Add a presale assertion that `recency_weighting` is absent.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_analysis.py -k "profile" -q
.\.venv\Scripts\python.exe -m pytest tests/test_valuation_reporting.py -k "profile or half_life" -q
```

Expected: report/backtest signatures do not accept a profile.

- [ ] **Step 4: Thread the profile through annual backtests**

Change the signature:

```python
def run_annual_backtests(frame, selected_model_name, feature_columns, profile):
```

Build the exact candidate:

```python
candidate_est = candidate_estimators(
    feature_columns=feature_columns,
    profile=profile,
)[selected_model_name]
```

Pass:

```python
recency_half_life_months=profile.recency_half_life_months or 48
```

to `evaluate_candidate`.

- [ ] **Step 5: Add one shared interval-summary helper**

In `valuation_reporting.py`, implement:

```python
def compute_interval_summary(bundle, evaluated, split) -> dict[str, float]:
    test_pred = evaluated.estimator.predict(
        split.test[list(bundle.feature_columns)]
    )
    actual = split.test["target_unit_price_twd"].to_numpy()
    radius = bundle.interval_abs_residual_twd_per_ping
    lows = np.maximum(0, test_pred - radius)
    highs = test_pred + radius
    return {
        "test_coverage": float(((actual >= lows) & (actual <= highs)).mean()),
        "average_interval_width_twd_per_ping": float(np.mean(highs - lows)),
    }
```

Use this helper in `write_evaluation`, serialize every profile’s parameters/calibration metrics/errors, and use `selected_profile.recency_half_life_months` rather than literal 24. Render the same actual value in `write_model_card`.

- [ ] **Step 6: Run report and analysis tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_analysis.py tests/test_valuation_reporting.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit exact evidence reporting**

```powershell
git add src/qingpu_insight/model_analysis.py src/qingpu_insight/valuation_reporting.py tests/test_model_analysis.py tests/test_valuation_reporting.py
git commit -m "feat(models): report exact tuning and backtest profile"
```

### Task 6: Orchestrate profiles in the training service

**Files:**
- Modify: `tests/test_model_training_service.py`
- Modify: `src/qingpu_insight/model_training_service.py`

**Interfaces:**
- Consumes: `TrainingTuningPlan`, `run_tuned_model_experiment`, schema-v3 models, exact-profile backtests/reports.
- Produces: `ModelTrainingRequest(..., tuning_plan)`, schema-v3 candidate runs, truthful profile progress, fail-closed profile errors.

- [ ] **Step 1: Write failing request and orchestration tests**

Add:

```python
def test_training_request_keeps_tuning_plan() -> None:
    plan = parse_tuning_plan(("resale",), None)
    request = ModelTrainingRequest(("resale",), tuning_plan=plan)
    assert request.tuning_plan == plan
    assert request.tuning_plan.profiles[1].name == "balanced"
```

Monkeypatch `run_tuned_model_experiment` with a spy and assert both markets receive all three profiles and `use_recency_weights` is `True` only for resale. Add a custom-plan test asserting four profiles.

Add a failure test:

```python
def test_profile_failure_discards_candidate_and_fails_job(
    tmp_path: Path,
    market_parquet: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, jobs = service_fixture(tmp_path, input_path=market_parquet)
    plan = parse_tuning_plan(("resale",), None)
    request = ModelTrainingRequest(("resale",), tuning_plan=plan)
    run = service.submit(request).run
    jobs.start(run.run_id)

    monkeypatch.setattr(
        model_training_service,
        "run_tuned_model_experiment",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ProfileEvaluationError("thorough")
        ),
    )
    with pytest.raises(ModelTrainingError) as caught:
        service.execute(run.run_id, request)
    assert caught.value.error_code == "profile_failed"
    assert jobs.get(run.run_id).status == "failed"
    assert not (tmp_path / "candidates" / run.run_id).exists()
```

- [ ] **Step 2: Run service tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training_service.py -k "tuning_plan or profile" -q
```

Expected: `ModelTrainingRequest` rejects `tuning_plan` and the service calls the old experiment.

- [ ] **Step 3: Extend `ModelTrainingRequest`**

Use:

```python
def __init__(
    self,
    markets: tuple[Literal["resale", "presale"], ...],
    trigger: str = "web",
    tuning_plan: TrainingTuningPlan | None = None,
) -> None:
    if not markets:
        raise ValueError("markets must not be empty")
    if len(set(markets)) != len(markets):
        raise ValueError("duplicate market")
    if any(market not in self.SUPPORTED for market in markets):
        raise ValueError("unsupported market")
    self._markets = tuple(
        market for market in ("resale", "presale") if market in markets
    )
    self.trigger = trigger
    self.tuning_plan = tuning_plan or parse_tuning_plan(self._markets, None)
```

Include `tuning_plan` in equality and repr so tests and queued handoff preserve the exact request.

- [ ] **Step 4: Replace the final experiment with profile comparison**

Keep resale feature experiments for feature-set evidence, but use their enhanced feature columns only. Then call:

```python
experiment = run_tuned_model_experiment(
    split,
    profiles=request.tuning_plan.profiles,
    feature_columns=enhanced_features,
    use_recency_weights=is_resale,
    baseline_months=12 if is_resale else 24,
    on_profile_start=lambda profile_name: self._jobs.progress(
        run_id,
        {
            "stage": f"training_{market}",
            "profile": profile_name,
            "completed_markets": list(completed),
        },
    ),
)
```

Catch `ProfileEvaluationError` and raise:

```python
ModelTrainingError(
    "profile_failed",
    f"{market} 設定 {exc.profile_name} 無法完成",
)
```

Use `experiment.selected_evaluation` for diagnostics and artifact training, `experiment.selected_profile`/`selected_model` for evidence, and pass the winning profile half-life to `train_artifact`.

- [ ] **Step 5: Build schema-v3 market and manifest evidence**

Update `market_result_from_files` to serialize each `ProfileEvaluation` into `ProfileTrainingResult`, store `selected_profile`, and store `compute_interval_summary` values.

Create every new manifest as:

```python
TrainingManifest(
    schema_version=3,
    tuning_plan_version=request.tuning_plan.version,
    profiles=[
        TrainingProfileSnapshot.model_validate(profile.snapshot())
        for profile in request.tuning_plan.profiles
    ],
    run_id=UUID(run_id),
    created_at=self._clock(),
    markets=list(markets),
    source_commit=source_version.commit,
    source_dirty=source_version.dirty,
    runtime_versions=runtime_versions(),
    data_snapshot=snapshot,
    results=results,
)
```

Pass the selected profile to reports and resale annual backtests. Add `selected_profile` to `public_training_summary`.

- [ ] **Step 6: Run service and adjacent tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training_service.py tests/test_model_observatory.py tests/test_model_artifacts.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit service orchestration**

```powershell
git add src/qingpu_insight/model_training_service.py tests/test_model_training_service.py
git commit -m "feat(models): orchestrate safe profile comparison"
```

### Task 7: Accept safe tuning through the Admin API

**Files:**
- Modify: `tests/test_web.py`
- Modify: `src/qingpu_insight/web.py`

**Interfaces:**
- Consumes: `parse_tuning_plan` and `ModelTrainingRequest`.
- Produces: `_parse_model_training_request() -> ModelTrainingRequest` with field-addressable validation errors.

- [ ] **Step 1: Make the model-training stub capture requests**

Add `self.requests = []` to `StubModelTrainingService`, append in `submit`, and retain all existing job behavior. In `model_admin_client`, expose the stub only to tests:

```python
app.extensions["test_model_training_service"] = mts
```

- [ ] **Step 2: Write failing API tests**

Add:

```python
def test_model_training_post_accepts_four_profile_plan(
    model_admin_client,
) -> None:
    response = model_admin_client.post(
        "/api/admin/model-training-runs",
        json={
            "markets": ["resale", "presale"],
            "tuning": {
                "mode": "preset_comparison",
                "include_custom": True,
                "custom": {
                    "hgb_learning_rate": 0.05,
                    "hgb_max_iter": 420,
                    "rf_n_estimators": 520,
                    "recency_half_life_months": 36,
                },
            },
        },
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    assert response.status_code == 202
    service = model_admin_client.application.extensions[
        "test_model_training_service"
    ]
    assert [p.name for p in service.requests[-1].tuning_plan.profiles] == [
        "quick", "balanced", "thorough", "custom",
    ]
```

Add parameterized invalid payloads for unknown tuning fields, unsupported mode, bool integers, non-finite floats, missing custom fields, resale/all missing half-life, and presale-only half-life. Assert the exact field path, for example:

```python
assert body["error"]["fields"]["tuning.custom.hgb_learning_rate"]
```

Keep a backward-compatibility test showing `{"markets": ["resale"]}` produces the three defaults.

- [ ] **Step 3: Run API tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "model_training_post" -q
```

Expected: `tuning` is rejected as `not_allowed`.

- [ ] **Step 4: Return a request object from the parser**

Allow only `markets` and `tuning`. After market validation:

```python
try:
    tuning_plan = parse_tuning_plan(
        tuple(ordered),
        payload.get("tuning"),
    )
except TuningValidationError as exc:
    for key, value in exc.fields.items():
        fields[f"tuning.{key}"] = value
```

Return:

```python
ModelTrainingRequest(
    markets=tuple(ordered),
    tuning_plan=tuning_plan,
)
```

Update the POST route to use this returned object directly instead of reconstructing `ModelTrainingRequest` from a tuple.

- [ ] **Step 5: Run Web tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "model_admin or model_training" -q
git add src/qingpu_insight/web.py tests/test_web.py
git commit -m "feat(admin): accept validated model tuning plans"
```

Expected: tests PASS and commit succeeds.

### Task 8: Shared JavaScript form and result view models

**Files:**
- Modify: `tests/js/model_admin_contract.cjs`
- Modify: `src/qingpu_insight/static/models_admin.js`

**Interfaces:**
- Consumes: form values and schema-v1/v2/v3 result JSON.
- Produces: `validateCustomTuning`, `buildTrainingPayload`, `trainingSubmitSummary`, `metricCards`, `profileComparisonRows`, and `trainingRecordState`.

- [ ] **Step 1: Write failing payload contracts**

Add:

```javascript
var custom = {
  hgb_learning_rate: "0.05",
  hgb_max_iter: "420",
  rf_n_estimators: "520",
  recency_half_life_months: "36",
};

assert.deepEqual(admin.buildTrainingPayload("all", true, custom), {
  markets: ["resale", "presale"],
  tuning: {
    mode: "preset_comparison",
    include_custom: true,
    custom: {
      hgb_learning_rate: 0.05,
      hgb_max_iter: 420,
      rf_n_estimators: 520,
      recency_half_life_months: 36,
    },
  },
});
assert.equal(
  admin.validateCustomTuning("presale", custom)
    .recency_half_life_months,
  "not_applicable"
);
assert.equal(admin.trainingSubmitSummary("all", true), "2 個市場 × 4 組設定");
```

Add boundary and bool/non-finite tests for all four fields.

- [ ] **Step 2: Write failing result-view contracts**

Use one schema-v3 result and assert:

```javascript
assert.deepEqual(admin.metricCards(v3Result).map(function (x) {
  return x.key;
}), ["mape", "mae", "coverage"]);
assert.equal(admin.profileComparisonRows(v3Result).length, 4);
assert.equal(admin.trainingRecordState({schema_version: 2}).legacy, true);
assert.match(
  admin.trainingRecordState({schema_version: 2}).notice,
  /舊版未保存調參快照/
);
```

The MAE card value is formatted in 萬元／坪, MAPE and coverage are percentages, and station regression remains visible through `stationRows`.

- [ ] **Step 3: Run Node contract and verify RED**

```powershell
node tests/js/model_admin_contract.cjs
```

Expected: the new functions are undefined or the old payload lacks tuning.

- [ ] **Step 4: Implement pure shared helpers**

Keep the UMD wrapper. `buildTrainingPayload` must always include:

```javascript
tuning: {
  mode: "preset_comparison",
  include_custom: includeCustom,
}
```

and include parsed numeric `custom` only when selected. `validateCustomTuning` returns an object keyed by invalid field. For presale, omit half-life from the payload and return `not_applicable` only if a caller attempts to include it.

`metricCards(result)` reads the locked winner from `final_test_metrics`, computes display values without changing raw values, and uses `result.test_coverage`. `profileComparisonRows` reads `profile_results`. `trainingRecordState` treats `schema_version < 3` as legacy and never invents parameters.

- [ ] **Step 5: Run Node contract and commit**

```powershell
node tests/js/model_admin_contract.cjs
git add src/qingpu_insight/static/models_admin.js tests/js/model_admin_contract.cjs
git commit -m "feat(admin): add shared guided training view models"
```

Expected: contract prints `model admin contract passed`.

### Task 9: Shared training controls in both admin surfaces

**Files:**
- Create: `src/qingpu_insight/templates/_model_training_controls.html`
- Create: `src/qingpu_insight/static/model_training.css`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/templates/models_admin.html`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: shared `models_admin.js` helpers and existing POST/job polling APIs.
- Produces: identical accessible form controls on both pages.

- [ ] **Step 1: Write failing rendered-template assertions**

For both `/admin` and the model observatory route, assert:

```python
page.select_one("#ma-profile-quick").get("data-locked") == "true"
page.select_one("#ma-profile-balanced").get("data-locked") == "true"
page.select_one("#ma-profile-thorough").get("data-locked") == "true"
assert page.select_one("#ma-custom-enabled") is not None
assert page.select_one("#ma-custom-hgb-learning-rate") is not None
assert page.select_one("#ma-custom-hgb-max-iter") is not None
assert page.select_one("#ma-custom-rf-n-estimators") is not None
assert page.select_one("#ma-custom-half-life") is not None
assert "不會自動發布" in page.get_text()
```

Also assert both pages load `model_training.css`.

- [ ] **Step 2: Run template tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "model_admin_page or training_controls" -q
```

Expected: the profile/custom elements are absent.

- [ ] **Step 3: Create the shared Jinja partial**

The partial contains:

- the existing market selector;
- three locked profile cards with the exact parameters;
- a checkbox `#ma-custom-enabled`;
- a `<details id="ma-custom-fields">` containing four labelled numeric inputs;
- `min/max/step` attributes matching the backend;
- a live summary `#ma-submit-summary`;
- the existing submit button and notice IDs.

For the half-life input use:

```html
<input id="ma-custom-half-life" name="recency_half_life_months"
       type="number" min="12" max="84" step="1" value="48">
```

Both pages include:

```jinja2
{% include "_model_training_controls.html" %}
```

- [ ] **Step 4: Wire the same form behavior on both pages**

On market/custom changes:

1. Disable and clear half-life for presale-only.
2. Re-enable it for resale/all.
3. Update `ma-submit-summary`.
4. Map validation errors beside their fields.

On submit, both pages call the same `ma.buildTrainingPayload(...)`; do not manually assemble a second payload in inline script.

- [ ] **Step 5: Add shared styles**

`model_training.css` owns profile cards, custom grid, field errors, summary strip, metric cards, warning states, and disclosure spacing. Load it after each page’s existing stylesheet so shared classes render consistently without moving unrelated admin CSS.

- [ ] **Step 6: Run Web and Node tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "model_admin_page or training_controls" -q
node tests/js/model_admin_contract.cjs
```

Expected: PASS.

- [ ] **Step 7: Commit shared controls**

```powershell
git add src/qingpu_insight/templates/_model_training_controls.html src/qingpu_insight/static/model_training.css src/qingpu_insight/templates/admin.html src/qingpu_insight/templates/models_admin.html tests/test_web.py
git commit -m "feat(admin): share safe model tuning controls"
```

### Task 10: Beginner-first result rendering and full verification

**Files:**
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/templates/models_admin.html`
- Modify: `src/qingpu_insight/static/models_admin.js`
- Modify: `tests/js/model_admin_contract.cjs`
- Modify: `tests/test_web.py`
- Modify: `docs/m2-valuation-methodology.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: schema-v3 manifest detail and shared view models.
- Produces: identical beginner summary and advanced evidence disclosure on both pages; complete operator documentation.

- [ ] **Step 1: Add failing result-state contracts**

Extend the Node fixture to assert:

```javascript
var overview = admin.trainingOverview(v3Result);
assert.equal(overview.publishable, true);
assert.equal(overview.selectedProfileLabel, "快速");
assert.equal(overview.selectedModelLabel, "HistGradientBoosting");
assert.deepEqual(overview.readingOrder, [
  "先看是否通過發布門檻",
  "再看 MAPE 與 MAE",
  "最後確認各站與年度回測沒有明顯退步",
]);
assert.equal(overview.baselineMaeDelta < 0, true);
```

Add a regression fixture where A18 candidate MAPE exceeds baseline and assert the warning includes `A18`.

- [ ] **Step 2: Run Node contract and verify RED**

```powershell
node tests/js/model_admin_contract.cjs
```

Expected: `trainingOverview` is undefined.

- [ ] **Step 3: Implement the overview view model**

Add fixed labels:

```javascript
var PROFILE_LABELS = {
  quick: "快速",
  balanced: "平衡",
  thorough: "精細",
  custom: "自訂",
};
```

`trainingOverview` returns:

- publishable/recommended state and reason labels;
- selected profile/model labels;
- MAE, MAPE, coverage cards;
- baseline MAE delta;
- station warnings from `stationRows`;
- fixed reading order;
- `legacy` notice from `trainingRecordState`.

Do not turn a missing value into zero or a passing state.

- [ ] **Step 4: Render summary before existing advanced evidence**

In both `renderTrainingDetail` implementations, render in this order:

1. pass/fail status badge;
2. selected profile and model;
3. three metric cards;
4. baseline comparison and warnings;
5. reading-order note;
6. training flow `資料 → 訓練 → 校正 → 測試 → 等待人工發布`;
7. `<details>` containing the existing full metrics, model comparison, station table, diagnostics, feature experiments, backtests, release checks, and report downloads.

For schema-v1/v2, show the legacy notice and existing metrics without profile cards.

- [ ] **Step 5: Update methodology and README**

Document:

- exact quick/balanced/thorough parameters;
- custom ranges;
- calibration-only selection and deterministic tie-breaker;
- final-test isolation;
- actual snapshot-derived half-life;
- schema-v3 evidence;
- manual publication requirement;
- how MAE, MAPE, RMSE, R², count, and coverage are read.

Remove every statement that hard-codes a 24-month half-life. The production default is 48 months unless the selected custom profile records another value.

- [ ] **Step 6: Run all focused model and UI tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_tuning.py tests/test_model_training.py tests/test_model_training_service.py tests/test_model_artifacts.py tests/test_model_analysis.py tests/test_valuation_reporting.py tests/test_model_observatory.py tests/test_web.py -q
node tests/js/model_admin_contract.cjs
```

Expected: PASS.

- [ ] **Step 7: Commit guided results and docs**

```powershell
git add src/qingpu_insight/templates/admin.html src/qingpu_insight/templates/models_admin.html src/qingpu_insight/static/models_admin.js tests/js/model_admin_contract.cjs tests/test_web.py docs/m2-valuation-methodology.md README.md
git commit -m "feat(admin): explain model training results"
```

- [ ] **Step 8: Run the complete quality gate**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Get-ChildItem tests\js\*.cjs | ForEach-Object { node $_.FullName }
.\.venv\Scripts\python.exe -m ruff check src tests
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 9: Perform real browser smoke checks**

Start:

```powershell
.\.venv\Scripts\qingpu-web.exe
```

At `/admin` and the model observatory verify:

1. Both pages show the same three locked profiles.
2. Presale-only disables half-life.
3. Invalid custom values show field errors and do not create a job.
4. Default submission displays `1 個市場 × 3 組設定`.
5. Custom all-market submission displays `2 個市場 × 4 組設定`.
6. Progress names the current market and profile.
7. A successful run shows winner, MAE, MAPE, coverage, baseline delta, station warnings, and manual-publish state.
8. Advanced disclosure retains every existing evidence table and report link.
9. One schema-v2 run displays the legacy notice without invented parameters.
10. Publishing still requires the existing preview and confirmation flow.

- [ ] **Step 10: Save final verification evidence**

If smoke testing exposes no code issue, do not create an empty commit. Record the tested commands and browser outcomes in the implementation handoff. If only documentation needed correction:

```powershell
git add README.md docs/m2-valuation-methodology.md
git commit -m "docs: finalize guided tuning operations"
```
