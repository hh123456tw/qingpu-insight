# Controlled AutoML Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a budget-based AutoML-lite mode that searches the existing regression models, shows a trial leaderboard, and produces only candidates that pass the existing release gates.

**Architecture:** Keep guided tuning unchanged and add a separate Optuna search boundary. Optuna proposes bounded parameters and returns ranked in-memory trials; `ModelTrainingService` remains responsible for final holdout tests, annual backtests, artifact creation, reports, and human-only publication. Partial and stopped searches live under `outputs/automl`, while only fully validated candidates enter `candidates`.

**Tech Stack:** Python 3.11+, Optuna 4.x, pandas, scikit-learn, Pydantic, Flask, vanilla JavaScript, pytest, Node.js contracts

## Global Constraints

- Implementation MUST use TDD: write and observe each focused failing test before implementation.
- Add only `optuna>=4,<5`; do not add FLAML, AutoGluon, XGBoost, or an external tracking service.
- Supported estimators remain Ridge baseline, Random Forest, and HistGradientBoosting.
- AutoML may create candidates but MUST never activate, publish, or roll back an official model.
- Search budgets are exactly `quick=300s/12 trials`, `standard=900s/35 trials`, and `deep=1800s/70 trials`.
- Markets run independently; AutoML UI defaults to resale only.
- Search trials run sequentially with Optuna sampler seed `42`.
- Stop is cooperative: finish the current trial, start no new trial, retain partial output, create no candidate.
- Existing loopback, Host, CSRF, artifact SHA-256, parking consistency, and release-preview protections remain mandatory.
- Existing guided tuning and schema-v1/v2/v3 artifacts remain readable.
- Do not discard or overwrite unrelated dirty-worktree changes; execution should begin from a clean isolated worktree after the current parking-review fixes are committed.

---

## File and Responsibility Map

- Create `src/qingpu_insight/automl_search.py`: immutable search contracts, estimator construction, Optuna objective, stable ranking, shortlist selection.
- Create `src/qingpu_insight/automl_control.py`: process-local cooperative-stop registry.
- Create `src/qingpu_insight/automl_outputs.py`: atomic partial-result storage under `outputs/automl`.
- Create `tests/test_automl_search.py`: parameter, ranking, failure-isolation, budget, and determinism tests.
- Create `tests/test_automl_control.py`: cancellation and partial-output tests.
- Modify `src/qingpu_insight/model_tuning.py`: parse the mutually exclusive guided and AutoML plans.
- Modify `src/qingpu_insight/model_training.py`: build estimators from an explicit fit specification and reuse final evaluation logic.
- Modify `src/qingpu_insight/model_analysis.py`: replay exact AutoML parameters in annual backtests.
- Modify `src/qingpu_insight/model_artifacts.py`: schema-v4 AutoML evidence and trial-file hash validation.
- Modify `src/qingpu_insight/model_training_service.py`: branch orchestration while sharing artifact/report construction.
- Modify `src/qingpu_insight/model_observatory.py`: project complete and stopped AutoML results.
- Modify `src/qingpu_insight/jobs.py`: legal `running -> skipped` service operation.
- Modify `src/qingpu_insight/web.py`: AutoML request validation and stop endpoint.
- Modify `src/qingpu_insight/static/models_admin.js`: payload, progress, leaderboard, and stop view models.
- Modify `src/qingpu_insight/templates/_model_training_controls.html`: guided/AutoML controls.
- Modify `src/qingpu_insight/templates/admin.html`: progress, stop action, and result rendering.
- Modify `src/qingpu_insight/static/model_training.css`: compact responsive leaderboard styles.
- Modify `README.md`: user workflow, guarantees, and five-minute acceptance instructions.

---

### Task 1: AutoML Plan Contract and Dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/qingpu_insight/model_tuning.py`
- Modify: `tests/test_model_tuning.py`
- Modify: `tests/test_model_training_service.py`

**Interfaces:**
- Produces: `AutoMLBudget`, `AutoMLTuningPlan`, and `TrainingPlan`.
- Produces: `parse_tuning_plan(markets, payload) -> TrainingPlan`.
- Preserves: `TrainingTuningPlan` and every existing guided payload.

- [ ] **Step 1: Write failing domain-contract tests**

Add tests proving the three budgets, defaults, strict keys, and guided compatibility:

```python
from qingpu_insight.model_tuning import AutoMLTuningPlan


def test_parse_automl_quick_plan() -> None:
    plan = parse_tuning_plan(("resale",), {"mode": "automl", "budget": "quick"})
    assert isinstance(plan, AutoMLTuningPlan)
    assert plan.version == 2
    assert plan.budget.name == "quick"
    assert plan.budget.seconds == 300
    assert plan.budget.max_trials == 12
    assert plan.seed == 42


@pytest.mark.parametrize("name,seconds,trials", [
    ("quick", 300, 12),
    ("standard", 900, 35),
    ("deep", 1800, 70),
])
def test_automl_budget_contract(name: str, seconds: int, trials: int) -> None:
    plan = parse_tuning_plan(("resale",), {"mode": "automl", "budget": name})
    assert plan.budget.seconds == seconds
    assert plan.budget.max_trials == trials


def test_automl_rejects_custom_search_space() -> None:
    with pytest.raises(TuningValidationError) as exc:
        parse_tuning_plan(
            ("resale",),
            {"mode": "automl", "budget": "quick", "estimators": ["xgboost"]},
        )
    assert exc.value.fields["estimators"] == "not_allowed"
```

Keep an explicit regression assertion that `preset_comparison` still returns
`TrainingTuningPlan` with the existing three profiles.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_tuning.py tests/test_model_training_service.py -q
```

Expected: collection or assertions fail because the AutoML plan types do not exist.

- [ ] **Step 3: Add Optuna and implement the immutable plan types**

Add `optuna>=4,<5` to project dependencies. Implement:

```python
AutoMLBudgetName = Literal["quick", "standard", "deep"]


@dataclass(frozen=True)
class AutoMLBudget:
    name: AutoMLBudgetName
    seconds: int
    max_trials: int


@dataclass(frozen=True)
class AutoMLTuningPlan:
    version: Literal[2]
    mode: Literal["automl"]
    budget: AutoMLBudget
    seed: Literal[42] = 42


TrainingPlan = TrainingTuningPlan | AutoMLTuningPlan

AUTOML_BUDGETS = {
    "quick": AutoMLBudget("quick", 300, 12),
    "standard": AutoMLBudget("standard", 900, 35),
    "deep": AutoMLBudget("deep", 1800, 70),
}
```

Make `parse_tuning_plan` dispatch on `mode`. For `automl`, allow only `mode` and
`budget`; reject missing/unknown budget and every extra key with field-specific
`TuningValidationError`.

- [ ] **Step 4: Run focused tests and Ruff**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_tuning.py tests/test_model_training_service.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/model_tuning.py tests/test_model_tuning.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml src/qingpu_insight/model_tuning.py tests/test_model_tuning.py tests/test_model_training_service.py
git commit -m "feat(models): define automl search plans"
```

---

### Task 2: Explicit Model Fit Specifications

**Files:**
- Modify: `src/qingpu_insight/model_training.py`
- Modify: `src/qingpu_insight/model_analysis.py`
- Create: `tests/test_automl_search.py`
- Modify: `tests/test_model_training.py`
- Modify: `tests/test_model_analysis.py`

**Interfaces:**
- Produces: `ModelFitSpec`.
- Produces: `build_estimator(spec, feature_columns, seed=42) -> Pipeline`.
- Produces: `evaluate_fit_spec(split, spec, feature_columns, baseline_months) -> ModelExperiment`.
- Extends: `run_annual_backtests(frame, selected_model_name, feature_columns=FEATURE_COLUMNS, profile=BALANCED_PROFILE, fit_spec: ModelFitSpec | None = None)` while preserving guided `profile`.

- [ ] **Step 1: Write failing estimator and replay tests**

```python
def test_build_hgb_from_exact_fit_spec() -> None:
    spec = ModelFitSpec(
        model_name="hist_gradient_boosting",
        parameters={
            "learning_rate": 0.07,
            "max_iter": 275,
            "max_leaf_nodes": 47,
            "l2_regularization": 2.5,
        },
        recency_half_life_months=36,
    )
    pipeline = build_estimator(spec, FEATURE_COLUMNS, seed=42)
    model = pipeline.named_steps["model"]
    assert model.learning_rate == 0.07
    assert model.max_iter == 275
    assert model.max_leaf_nodes == 47
    assert model.l2_regularization == 2.5


def test_annual_backtest_replays_automl_fit_spec(monkeypatch, resale_frame) -> None:
    seen: list[ModelFitSpec] = []
    spec = ModelFitSpec("random_forest", {
        "n_estimators": 222,
        "min_samples_leaf": 7,
        "max_features": 0.65,
    }, 60)
    monkeypatch.setattr(model_analysis, "build_estimator",
                        lambda actual, columns, seed=42: seen.append(actual) or DummyEstimator())
    run_annual_backtests(resale_frame, "random_forest", fit_spec=spec)
    assert seen and all(item == spec for item in seen)
```

Also assert that invalid model names and parameters fail before scikit-learn is
called.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_automl_search.py tests/test_model_training.py tests/test_model_analysis.py -q
```

Expected: imports fail for `ModelFitSpec` and `build_estimator`.

- [ ] **Step 3: Implement the shared fit specification**

Use a frozen dataclass:

```python
@dataclass(frozen=True)
class ModelFitSpec:
    model_name: Literal["random_forest", "hist_gradient_boosting"]
    parameters: dict[str, int | float]
    recency_half_life_months: int | None

    def snapshot(self) -> dict[str, object]:
        return {
            "model_name": self.model_name,
            "parameters": dict(self.parameters),
            "recency_half_life_months": self.recency_half_life_months,
        }
```

`build_estimator` must construct the existing preprocessing pipeline and pass only
the allowlisted parameters. `evaluate_fit_spec` must train with
`recency_weights(split.train,
half_life_months=fit_spec.recency_half_life_months)` only when the half-life is
present, then evaluate calibration and final test using existing metric functions.

Keep `candidate_estimators(feature_columns=FEATURE_COLUMNS, seed=42,
profile=BALANCED_PROFILE)` as the guided adapter so existing callers and pickles
remain compatible.

- [ ] **Step 4: Replay the exact spec in annual backtests**

When `fit_spec` is provided, `run_annual_backtests` must call
`build_estimator(fit_spec, feature_columns)` for every cutoff and call
`recency_weights(split.train, half_life_months=fit_spec.recency_half_life_months)`
when the exact half-life is present. When `fit_spec` is absent, preserve the
current profile-based behavior.

- [ ] **Step 5: Run focused tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_automl_search.py tests/test_model_training.py tests/test_model_analysis.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/model_training.py src/qingpu_insight/model_analysis.py tests/test_automl_search.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/model_training.py src/qingpu_insight/model_analysis.py tests/test_automl_search.py tests/test_model_training.py tests/test_model_analysis.py
git commit -m "refactor(models): support exact fit specifications"
```

---

### Task 3: Optuna Search, Stable Ranking, and Progress

**Files:**
- Create: `src/qingpu_insight/automl_search.py`
- Modify: `tests/test_automl_search.py`

**Interfaces:**
- Produces: `AutoMLTrialResult`, `AutoMLSearchResult`, and `AutoMLSearchStopped`.
- Produces: `json_safe(value: object) -> object`.
- Produces: `run_automl_search(split, plan, feature_columns, use_recency_weights, baseline_months, *, should_stop, on_progress) -> AutoMLSearchResult`.
- `AutoMLTrialResult.estimator` remains in memory and is excluded from serialized snapshots.

Use these exact contracts:

```python
TrialState = Literal["completed", "rejected", "failed"]


@dataclass(frozen=True)
class AutoMLTrialResult:
    trial_number: int
    state: TrialState
    fit_spec: ModelFitSpec | None
    estimator: Any | None
    metrics: dict[str, dict[str, int | float]]
    overall_mae: float | None
    overall_mape: float | None
    station_mape: dict[str, float]
    calibration_passed: bool
    reason_codes: tuple[str, ...]
    duration_seconds: float

    def snapshot(self) -> dict[str, object]:
        return {
            "trial_number": self.trial_number,
            "state": self.state,
            "fit_spec": self.fit_spec.snapshot() if self.fit_spec else None,
            "metrics": json_safe(self.metrics),
            "overall_mae": self.overall_mae,
            "overall_mape": self.overall_mape,
            "station_mape": dict(self.station_mape),
            "calibration_passed": self.calibration_passed,
            "reason_codes": list(self.reason_codes),
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class AutoMLSearchResult:
    market: Literal["resale", "presale"]
    budget_name: Literal["quick", "standard", "deep"]
    budget_seconds: int
    max_trials: int
    seed: int
    elapsed_seconds: float
    stopped: bool
    trials: tuple[AutoMLTrialResult, ...]
    ranked_trials: tuple[AutoMLTrialResult, ...]
    shortlisted_trials: tuple[AutoMLTrialResult, ...]
```

The `snapshot()` body must explicitly omit `estimator` and recursively normalize
NumPy scalars. The type ellipsis above denotes Python's variadic tuple annotation,
not an unfinished implementation.

- [ ] **Step 1: Write failing search tests**

Cover deterministic suggestions, bounds, failure isolation, gated ranking, duplicate
parameter removal, progress, timeout, and maximum trials:

```python
def test_ranked_trials_put_gate_passes_before_lower_mae_failures() -> None:
    failed = trial_result(1, mae=40_000, calibration_passed=False)
    passed = trial_result(2, mae=45_000, calibration_passed=True)
    result = rank_trials((failed, passed))
    assert [row.trial_number for row in result] == [2, 1]


def test_failed_trial_does_not_abort_study(fake_split) -> None:
    calls = 0
    def evaluator(spec):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic failure")
        return passing_evaluation(spec)
    result = run_automl_search(
        fake_split,
        quick_plan(),
        FEATURE_COLUMNS,
        True,
        12,
        should_stop=lambda: False,
        on_progress=lambda _: None,
        trial_evaluator=evaluator,
    )
    assert result.failed_trials == 1
    assert result.completed_trials >= 1
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_automl_search.py -q
```

Expected: the search module and contracts are missing.

- [ ] **Step 3: Implement bounded suggestions and JSON-safe snapshots**

Use `optuna.samplers.TPESampler(seed=plan.seed)` and sequential
`study.optimize(objective, timeout=plan.budget.seconds,
n_trials=plan.budget.max_trials, n_jobs=1)`. Suggest exactly:

```python
model_name = trial.suggest_categorical(
    "model_name", ["random_forest", "hist_gradient_boosting"]
)
half_life = (
    trial.suggest_categorical("recency_half_life_months", [24, 36, 48, 60, 72])
    if use_recency_weights else None
)
```

Use the ranges from the approved spec for model-specific parameters. Convert all
NumPy values to Python numbers in `snapshot()`.

- [ ] **Step 4: Implement stable ranking and shortlist**

Sort completed trials by:

```python
(
    not trial.calibration_passed,
    trial.overall_mae,
    trial.overall_mape,
    trial.trial_number,
)
```

Shortlist at most three calibration-passing trials with distinct normalized
`ModelFitSpec` snapshots. Preserve failed and rejected trials in the complete
result but never shortlist them.

- [ ] **Step 5: Implement budget, cooperative checks, and progress callbacks**

Check `should_stop()` before starting each trial. Emit progress after every trial
with stage, model, completed/failed counts, elapsed seconds, best MAE, and a
short parameter summary. Use both `timeout=plan.budget.seconds` and
`n_trials=plan.budget.max_trials`.

- [ ] **Step 6: Run focused tests and Ruff**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_automl_search.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/automl_search.py tests/test_automl_search.py
```

Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add src/qingpu_insight/automl_search.py tests/test_automl_search.py
git commit -m "feat(models): add bounded optuna search"
```

---

### Task 4: Cooperative Stop and Atomic Partial Outputs

**Files:**
- Create: `src/qingpu_insight/automl_control.py`
- Create: `src/qingpu_insight/automl_outputs.py`
- Create: `tests/test_automl_control.py`
- Modify: `src/qingpu_insight/jobs.py`
- Modify: `tests/test_jobs.py`

**Interfaces:**
- Produces: `AutoMLControlRegistry.register(run_id)`, `request_stop(run_id)`, `should_stop(run_id)`, and `unregister(run_id)`.
- Produces: `AutoMLRunOutputStore.write(run_id, snapshot)`, `get(run_id)`, and `copy_trials_to(run_id, market, stage)`.
- Produces: `JobService.skip(run_id, summary) -> JobRun`.

- [ ] **Step 1: Write failing cancellation and filesystem tests**

```python
def test_stop_request_is_idempotent_and_scoped() -> None:
    registry = AutoMLControlRegistry()
    registry.register("run-a")
    registry.register("run-b")
    assert registry.request_stop("run-a") is True
    assert registry.request_stop("run-a") is True
    assert registry.should_stop("run-a") is True
    assert registry.should_stop("run-b") is False


def test_partial_output_write_is_atomic_and_json_safe(tmp_path) -> None:
    store = AutoMLRunOutputStore(tmp_path)
    store.write("00000000-0000-0000-0000-000000000001", {
        "completed_trials": np.int64(2),
        "trials": [{"mae": np.float64(123.5)}],
    })
    assert store.get("00000000-0000-0000-0000-000000000001")[
        "completed_trials"
    ] == 2
    assert not list(tmp_path.rglob("*.tmp"))
```

Add `JobService.skip` tests for `running -> skipped`, summary preservation, and
illegal pending/succeeded transitions.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_automl_control.py tests/test_jobs.py -q
```

- [ ] **Step 3: Implement the thread-safe process-local registry**

Protect a `dict[str, threading.Event]` with `threading.Lock`. Unknown runs return
`False` from `request_stop`; registered runs return `True` on every repeated request.
Always unregister in the training service `finally` block.

- [ ] **Step 4: Implement atomic output storage**

Resolve and validate UUID run IDs, write UTF-8 JSON to a sibling temporary file,
flush it, then use `os.replace`. Store per-market files as:

```text
outputs/automl/<run_id>/<market>-trials.json
```

`copy_trials_to` copies only a completed market file into
`<candidate-stage>/automl/<market>-trials.json` and returns relative path plus
SHA-256.

- [ ] **Step 5: Add `JobService.skip`**

Implement it through `_transition(run_id, "skipped", summary=summary)` without
changing repository schema or other transition rules.

- [ ] **Step 6: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_automl_control.py tests/test_jobs.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/automl_control.py src/qingpu_insight/automl_outputs.py src/qingpu_insight/jobs.py
git add src/qingpu_insight/automl_control.py src/qingpu_insight/automl_outputs.py src/qingpu_insight/jobs.py tests/test_automl_control.py tests/test_jobs.py
git commit -m "feat(models): persist stoppable automl searches"
```

---

### Task 5: Schema-v4 Evidence and Observatory Projection

**Files:**
- Modify: `src/qingpu_insight/model_artifacts.py`
- Modify: `src/qingpu_insight/model_observatory.py`
- Modify: `tests/test_model_artifacts.py`
- Modify: `tests/test_model_observatory.py`

**Interfaces:**
- Produces: `AutoMLTrialSnapshot`, `AutoMLMarketSearchSnapshot`, and `AutoMLRunSnapshot`.
- Extends: `TrainingManifest.schema_version` to include `4`.
- Adds: `TrainingManifest.automl: AutoMLRunSnapshot | None`.
- Preserves: strict schema-v3 profile validation.

The schema boundary uses these fields:

```python
class AutoMLTrialSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trial_number: int = Field(ge=0)
    state: Literal["completed", "rejected", "failed"]
    fit_spec: dict[str, object] | None
    metrics: dict[str, dict[str, int | float]]
    overall_mae: float | None
    overall_mape: float | None
    station_mape: dict[str, float]
    calibration_passed: bool
    reason_codes: list[str]
    duration_seconds: float = Field(ge=0)


class AutoMLMarketSearchSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget_name: Literal["quick", "standard", "deep"]
    budget_seconds: Literal[300, 900, 1800]
    max_trials: Literal[12, 35, 70]
    completed_trials: int = Field(ge=0)
    failed_trials: int = Field(ge=0)
    seed: Literal[42]
    stopped: bool
    top_trials: list[AutoMLTrialSnapshot] = Field(max_length=10)
    trial_file: str
    trial_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shortlisted_trial_numbers: list[int] = Field(max_length=3)
    selected_trial_number: int | None = Field(default=None, ge=0)
    release_blockers: list[str] = Field(default_factory=list)


class AutoMLRunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["automl"]
    markets: dict[Literal["resale", "presale"], AutoMLMarketSearchSnapshot]
```

- [ ] **Step 1: Write failing schema-v4 tests**

```python
def test_schema_v4_requires_automl_and_allows_no_profiles(valid_manifest_data) -> None:
    data = {
        **valid_manifest_data,
        "schema_version": 4,
        "tuning_plan_version": 2,
        "profiles": [],
        "automl": automl_snapshot_data(),
    }
    manifest = TrainingManifest.model_validate(data)
    assert manifest.automl is not None
    assert manifest.automl.markets["resale"].budget_name == "quick"


def test_candidate_store_rejects_tampered_trial_file(tmp_path) -> None:
    store, run_id, manifest = staged_v4_candidate(tmp_path)
    trial_path = tmp_path / f".tmp-{run_id}" / "automl/resale-trials.json"
    trial_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="Hash mismatch"):
        store.commit(run_id, manifest)
```

Also prove schema-v1/v2/v3 fixtures still validate unchanged.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_artifacts.py tests/test_model_observatory.py -q
```

- [ ] **Step 3: Add strict v4 Pydantic contracts**

The market snapshot includes budget name/seconds, max/completed/failed trials,
seed, stopped flag, top-ten snapshots, trial file path/hash, shortlisted trial
numbers, and selected trial number or `None`.

Update the manifest validator:

- schema 3 continues to require matching guided profiles.
- schema 4 requires `tuning_plan_version == 2`, empty profiles, and `automl`.
- schemas below 4 reject a non-null `automl`.

Extend `CandidateArtifactStore.commit` to validate the trial JSON path stays under
the stage directory and matches its SHA-256.

- [ ] **Step 4: Project complete and partial AutoML evidence**

Inject `AutoMLRunOutputStore` into `ModelObservatory`. For a complete v4 manifest,
include `manifest.automl`. When no manifest exists but a stopped or no-candidate
output exists, include:

```json
{
  "automl": {
    "candidate_available": false,
    "markets": {},
    "stopped": true
  }
}
```

Never set `markets[market].publishable=true` without a committed artifact and
passing result.

- [ ] **Step 5: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_artifacts.py tests/test_model_observatory.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/model_artifacts.py src/qingpu_insight/model_observatory.py
git add src/qingpu_insight/model_artifacts.py src/qingpu_insight/model_observatory.py tests/test_model_artifacts.py tests/test_model_observatory.py
git commit -m "feat(models): version automl experiment evidence"
```

---

### Task 6: Training-Service Orchestration and Top-Three Validation

**Files:**
- Modify: `src/qingpu_insight/model_training_service.py`
- Modify: `src/qingpu_insight/valuation_reporting.py`
- Modify: `tests/test_model_training_service.py`
- Modify: `tests/test_valuation_reporting.py`

**Interfaces:**
- `ModelTrainingRequest.tuning_plan` accepts `TrainingPlan`.
- `ModelTrainingService` receives `AutoMLControlRegistry` and `AutoMLRunOutputStore`.
- Produces: `_execute_guided_market(self, run_id: str, market: MarketName, frame: pd.DataFrame, stage: Path, plan: TrainingTuningPlan) -> MarketTrainingResult`.
- Produces: `_execute_automl_market(self, run_id: str, market: MarketName, frame: pd.DataFrame, stage: Path, plan: AutoMLTuningPlan) -> tuple[MarketTrainingResult | None, AutoMLMarketSearchSnapshot]`.
- Returns: `TrainingManifest | None`; `None` means successful search with no publishable candidate or user stop.

- [ ] **Step 1: Write failing orchestration tests**

Cover:

1. guided execution takes the unchanged path;
2. AutoML progress is written to the job summary and partial-output store;
3. no more than three distinct shortlisted trials get final validation;
4. the first full-gate pass wins even when leaderboard rank one fails annual backtest;
5. all three failures produce no candidate manifest and a successful
   `candidate_available=false` summary;
6. stop produces `skipped`, no manifest, no artifact, and retained trials;
7. mixed markets can commit the passing market while showing the other as blocked;
8. no code path calls `OfficialModelStore.activate`.

Example:

```python
def test_automl_selects_first_shortlisted_candidate_that_passes_full_gate(service):
    service.searcher.return_value = search_result(shortlist=[trial(1), trial(2), trial(3)])
    service.final_validator.side_effect = [
        validation(trial=1, recommended=False, reason="backtest_station_regression"),
        validation(trial=2, recommended=True),
    ]
    manifest = service.execute(RUN_ID, automl_request("resale", "quick"))
    assert manifest.automl.markets["resale"].selected_trial_number == 2
    assert service.final_validator.call_count == 2
```

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training_service.py tests/test_valuation_reporting.py -q
```

- [ ] **Step 3: Extract shared artifact/report completion**

Move the existing post-selection behavior into a private helper that consumes:

- selected `CandidateEvaluation`;
- final baseline and candidate metrics;
- exact `ModelFitSpec`;
- diagnostics/backtests/release checks;
- optional guided profile evidence or AutoML evidence.

Both guided and AutoML paths must call this helper so parking policy, interval
summary, report hashes, and artifact validation cannot drift.

- [ ] **Step 4: Implement AutoML market orchestration**

For each selected market:

1. build the same model frame, features, and time split as guided training;
2. register stop control;
3. run search and persist each progress snapshot;
4. if stopped, discard candidate staging and call `jobs.skip`;
5. report `validating_shortlist` after search;
6. validate up to three shortlisted trials in rank order;
7. run exact-parameter annual backtests for resale;
8. use existing `evaluate_release_checks` plus parking consistency;
9. stop after the first full pass;
10. create an artifact only for a passing candidate.

Always unregister the run in `finally`.

- [ ] **Step 5: Handle no-candidate and mixed-market results**

If every requested market has no full-gate pass, discard candidate staging and
mark the job succeeded with `candidate_available=false`; retain output JSON.

If at least one market passes, create schema-v4 manifest results only for markets
with artifacts, while `manifest.automl.markets` records every requested market and
its blockers. The release UI must therefore derive actions from `results`, not
from requested `markets`.

- [ ] **Step 6: Make reports identify AutoML**

Evaluation JSON and model card must show mode, budget, completed trial count,
selected trial, exact fit parameters, and release blockers. Do not call the
selected trial a guided profile.

- [ ] **Step 7: Run focused and regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training_service.py tests/test_valuation_reporting.py tests/test_model_release.py tests/test_model_observatory.py -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/model_training_service.py src/qingpu_insight/valuation_reporting.py
```

- [ ] **Step 8: Commit**

```powershell
git add src/qingpu_insight/model_training_service.py src/qingpu_insight/valuation_reporting.py tests/test_model_training_service.py tests/test_valuation_reporting.py
git commit -m "feat(models): orchestrate governed automl candidates"
```

---

### Task 7: Admin HTTP Contracts and Stop Endpoint

**Files:**
- Modify: `src/qingpu_insight/web.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Extends: `POST /api/admin/model-training-runs`.
- Adds: `POST /api/admin/model-training-runs/<run_id>/stop`.
- Stop success body: `{"run_id": "00000000-0000-0000-0000-000000000001", "stop_requested": true}`.

- [ ] **Step 1: Write failing API tests**

Test accepted payload:

```json
{
  "markets": ["resale"],
  "tuning": {"mode": "automl", "budget": "quick"}
}
```

Test rejection of unknown budget, missing budget, custom fields, duplicate markets,
non-JSON bodies, and mixed guided/AutoML fields.

For stop, test invalid UUID, unknown run, wrong job type, guided run, non-active
run, repeated request, missing CSRF, hostile Host, and non-loopback access.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "model_training and (automl or stop)" -q
```

- [ ] **Step 3: Extend request parsing**

Continue routing all tuning validation through `parse_tuning_plan`; convert
`TuningValidationError.fields` to `tuning.<field>` API errors. Do not accept a
separate top-level AutoML object.

- [ ] **Step 4: Implement the protected stop endpoint**

After existing loopback/Host and CSRF checks, validate UUID and delegate to:

```python
accepted = admin_services.model_training_service.request_stop(run_id)
```

Return:

- `202` with `stop_requested=true` for accepted and repeated active requests;
- `404 not_found` for unknown runs;
- `409 not_stoppable` for guided or terminal runs;
- `503 admin_unavailable` for unavailable services.

- [ ] **Step 5: Run API tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "model_training" -q
.\.venv\Scripts\ruff.exe check src/qingpu_insight/web.py tests/test_web.py
git add src/qingpu_insight/web.py tests/test_web.py
git commit -m "feat(web): expose automl training controls"
```

---

### Task 8: Beginner-First Admin UI

**Files:**
- Modify: `src/qingpu_insight/static/models_admin.js`
- Modify: `src/qingpu_insight/templates/_model_training_controls.html`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/model_training.css`
- Modify: `tests/js/model_admin_contract.cjs`
- Modify: `tests/test_web.py`

**Interfaces:**
- Produces: `buildAutoMLPayload(market, budget)`.
- Produces: `automlProgressView(summary)`.
- Produces: `automlLeaderboardRows(detail)`.
- Produces: `canStopAutoML(run)`.

- [ ] **Step 1: Write failing Node contracts**

```javascript
assert.deepEqual(admin.buildAutoMLPayload("resale", "quick"), {
  markets: ["resale"],
  tuning: { mode: "automl", budget: "quick" },
});
assert.throws(() => admin.buildAutoMLPayload("all", "hour"), /budget/i);

assert.deepEqual(
  admin.automlLeaderboardRows(automlFixture).slice(0, 2).map(x => x.trialNumber),
  [7, 3]
);
assert.equal(admin.canStopAutoML({
  status: "running",
  summary: { mode: "automl" },
}), true);
```

Add rendered-HTML assertions for training-mode controls, three budget options,
default resale, live status region, stop button, top-ten table, and expandable
trial details.

- [ ] **Step 2: Run contracts and verify RED**

```powershell
node tests/js/model_admin_contract.cjs
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "admin_page" -q
```

- [ ] **Step 3: Implement mutually exclusive controls**

Use a `訓練方式` select with guided and AutoML modes. Hide and disable guided
parameter controls in AutoML mode; show three plain-language budget cards. Reset
AutoML market selection to resale when the mode is first chosen, without
overwriting a later explicit user choice.

- [ ] **Step 4: Implement progress and stop behavior**

Show market, model, completed/failed trials, elapsed time, best MAE, and current
stage. During `validating_shortlist`, display exactly:

`搜尋完成，正在驗證前三名`

Stop must disable itself after a successful request and show:

`已要求停止；目前這次嘗試完成後會結束`

- [ ] **Step 5: Implement the compact leaderboard**

Render at most ten rows initially with columns from the spec. Use text nodes, not
`innerHTML`, for parameters and errors. Put full parameters and trials after rank
10 inside accessible `<details>`. Keep `搜尋排名` and `發布資格` visually separate.

- [ ] **Step 6: Run frontend and Flask contracts**

```powershell
node tests/js/model_admin_contract.cjs
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "admin_page or model_training" -q
```

- [ ] **Step 7: Commit**

```powershell
git add src/qingpu_insight/static/models_admin.js src/qingpu_insight/templates/_model_training_controls.html src/qingpu_insight/templates/admin.html src/qingpu_insight/static/model_training.css tests/js/model_admin_contract.cjs tests/test_web.py
git commit -m "feat(web): visualize automl model search"
```

---

### Task 9: Documentation, Full Verification, and Real Five-Minute Acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/project-issue-log.md`
- Modify: `.gitignore`

**Interfaces:**
- Documents the UI workflow, search-vs-release distinction, output locations,
  stop semantics, metrics, and interview explanation.

- [ ] **Step 1: Update documentation and ignored runtime outputs**

Add `/outputs/automl/` to `.gitignore`. Document:

- guided versus AutoML modes;
- 5/15/30-minute budgets;
- why leaderboard rank one may not be publishable;
- cooperative stop behavior;
- output and candidate locations;
- exact local verification workflow;
- no automatic publication guarantee.

Add a project-issue-log entry explaining the existing A18 regression as the
motivation for release-gate-first AutoML.

- [ ] **Step 2: Run the complete automated verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
$contracts = Get-ChildItem tests\js -File | Sort-Object Name
foreach ($contract in $contracts) {
  node $contract.FullName
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
git diff --check
```

Expected: all 1,600+ Python tests pass, every Node contract exits zero, Ruff
reports `All checks passed!`, and diff check has no errors.

- [ ] **Step 3: Capture the official pointers before acceptance**

Read both `artifacts/official/<market>/current.json` files and record version IDs
and SHA-256 values. Do not activate or publish anything during acceptance.

- [ ] **Step 4: Run one real five-minute resale AutoML search**

Verify port 5010 is unused, then start a separate hidden acceptance process:

```powershell
if (Get-NetTCPConnection -LocalPort 5010 -State Listen -ErrorAction SilentlyContinue) {
  throw "Port 5010 is already in use"
}
$env:QINGPU_PORT = "5010"
$acceptanceProcess = Start-Process -FilePath ".\.venv\Scripts\qingpu-web.exe" -PassThru -WindowStyle Hidden
```

Use the browser-control skill to open `http://127.0.0.1:5010/admin#models` and
choose:

- 訓練方式：自動探索
- 市場：中古屋
- 預算：快速探索（5 分鐘）

Verify live progress changes, the stop button is available while searching, the
shortlist-validation message appears, and the completed detail shows a leaderboard
plus publish qualification.

After verification, stop only `$acceptanceProcess` by its captured process object;
do not stop the user's existing port-5000 process.

- [ ] **Step 5: Verify artifact and publication invariants**

Confirm:

- `outputs/automl/<run_id>/resale-trials.json` exists and parses;
- if publishable, candidate trial file hash matches schema-v4 manifest;
- if blocked, no publishable artifact is exposed;
- official resale and presale pointer version IDs and hashes exactly match Step 3;
- no secret or absolute local path appears in public JSON.

- [ ] **Step 6: Run final focused regression after the real search**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_automl_search.py tests/test_model_training_service.py tests/test_model_artifacts.py tests/test_model_release.py tests/test_web.py -q
.\.venv\Scripts\ruff.exe check .
git diff --check
```

- [ ] **Step 7: Commit documentation**

```powershell
git add README.md docs/project-issue-log.md .gitignore
git commit -m "docs: explain governed automl workflow"
```

- [ ] **Step 8: Perform final review**

Use `superpowers:requesting-code-review`, fix every confirmed high/medium issue,
rerun Step 2, and confirm the branch contains no generated trial JSON, model
artifact, API key, `.env`, or benchmark output.
