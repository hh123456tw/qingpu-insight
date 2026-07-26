# 青埔智價模型觀測台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立只供本機使用的模型觀測台，讓操作人員可查看正式模型與資料品質、以固定設定啟動背景訓練、比較候選結果並保存版本化候選產物，同時保證 Web 無法發布或覆寫正式模型。

**Architecture:** 從 `cli.py` 抽出共用 `ModelTrainingService`，用既有 `JobService`、MySQL job repository 與單 worker `LocalJobExecutor` 執行。模型候選先在 calibration 選定，鎖定後才以 test 做 final gate；候選 artifact 以 run ID 寫入獨立不可變目錄，正式 `artifacts/*.joblib` 只讀且不在任何 Web mutation 路徑中。

**Tech Stack:** Python 3.12、pandas、NumPy、scikit-learn、joblib、Pydantic、Flask、PyMySQL/MySQL、Vanilla JavaScript、pytest、Node.js contract tests、Ruff

## Global Constraints

- 第一版採「模型觀測台」，不擴張成完整維運台。
- 訓練使用固定安全設定；前端只允許選擇中古屋、預售屋或全部。
- 每次成功訓練保存版本化候選模型與完整評估資料。
- 畫面同時呈現原始指標與可解釋的系統判定。
- 候選模型維持 Recent Median Baseline、Ridge、Random Forest 與 HistGradientBoosting；第一版不加入 XGBoost。
- Web、CLI 必須呼叫同一個 application service，不維護兩套訓練邏輯。
- 第一版不存在由 Web 發布或回復正式模型的路徑。
- 嚴格遵守 `superpowers:test-driven-development`：沒有先看到正確 RED，不得寫 production code。
- 每個測試先說明它要抓到的 production break；期望值使用手算 literal，不以被測 helper 反算。
- 測試優先使用真實 component 與暫存檔案；只在 Selenium、MySQL 或背景 thread 等慢／外部邊界使用 narrow fake。
- 工作區目前有既有未提交修改；每次只 stage 本 task 明列的檔案，不碰其他 dirty files。
- 所有管理 mutation 維持 loopback、trusted Host、session CSRF 與固定 payload 白名單。
- 591 開價資料不得進入 M2 官方成交模型。
- 第一版不自動刪除候選 artifact、不提供 `.joblib` 下載，也不加入排程、發布、回復或任意命令功能。

---

## File Structure

| File | Responsibility |
|---|---|
| `src/qingpu_insight/model_training.py` | calibration 選模、locked candidate 的 final-test 評估與 release gate |
| `src/qingpu_insight/valuation.py` | 將已鎖定 estimator 包裝成 `ValuationBundle`；不得重新 fit 或用 test 選模 |
| `src/qingpu_insight/valuation_reporting.py` | 輸出 selection/final test 分離的 JSON 與模型卡 |
| `src/qingpu_insight/model_artifacts.py` | Pydantic manifest、hash、暫存目錄驗證、原子候選提交、只讀下載映射 |
| `src/qingpu_insight/model_training_service.py` | 固定 request、資料快照、訓練 orchestration、job progress/success/failure |
| `src/qingpu_insight/model_observatory.py` | 正式模型、資料狀態、候選歷史與詳情的 read model |
| `src/qingpu_insight/jobs.py` | job type filter、running progress、啟動時中斷工作恢復 |
| `src/qingpu_insight/job_repository.py` | 上述 job 操作的 MySQL persistence |
| `src/qingpu_insight/cli.py` | `model-train` 改呼叫共用 service，保留既有命令介面 |
| `src/qingpu_insight/web.py` | model admin composition、固定 API、本機安全檢查與報告下載 |
| `src/qingpu_insight/templates/models_admin.html` | 獨立模型觀測台 HTML |
| `src/qingpu_insight/static/models_admin.js` | 狀態載入、固定送出、job polling、詳情呈現 |
| `src/qingpu_insight/static/models_admin.css` | 模型觀測台專用 responsive 樣式 |
| `src/qingpu_insight/templates/index.html` | 加入模型觀測台導覽連結 |
| `tests/test_model_training.py` | 選模／final-test 隔離與 release gate |
| `tests/test_model_artifacts.py` | manifest、路徑安全、不可變與原子提交 |
| `tests/test_model_training_service.py` | 固定訓練流程、資料證據、失敗不污染正式模型 |
| `tests/test_model_observatory.py` | 正式模型與候選 read model |
| `tests/test_jobs.py` | progress、job type filter、interrupted recovery |
| `tests/test_job_repository.py` | 新增 job repository SQL 行為 |
| `tests/test_cli.py` | CLI 與 service 共用契約 |
| `tests/test_web.py` | 管理頁、API 白名單、安全、重複工作、下載 |
| `tests/js/model_admin_contract.cjs` | 前端 payload、狀態與 recommendation view-model 契約 |
| `README.md` | 本機模型觀測台操作與安全限制 |
| `docs/m2-valuation-methodology.md` | calibration 選模、test final gate 的正確方法論 |

---

### Task 1: 擴充可稽核的 Job Progress 與 Model Job 查詢

**Files:**
- Modify: `src/qingpu_insight/jobs.py`
- Modify: `src/qingpu_insight/job_repository.py`
- Modify: `tests/test_jobs.py`
- Modify: `tests/test_job_repository.py`

**Interfaces:**
- Produces: `JobRepository.list_recent(limit: int = 20, job_type: str | None = None)`
- Produces: `JobRepository.list_active(job_type: str) -> list[JobRun]`
- Produces: `JobRepository.update_summary(run_id: str, expected_status: JobStatus, summary: dict[str, object]) -> bool`
- Produces: `JobService.progress(run_id: str, summary: dict[str, object]) -> JobRun`
- Produces: `JobService.recover_interrupted(job_type: str) -> list[JobRun]`
- Consumes: existing `JobRun`, `JobService.fail()` and MySQL `job_runs`

- [ ] **Step 1: RED — add focused service tests**

Name the breaks:

- a progress write on a non-running job must not silently succeed;
- model history must not be displaced by unrelated listing jobs;
- pending/running/retry-wait model jobs left by a dead process must become terminal failures.

Add to `tests/test_jobs.py`:

```python
def test_progress_replaces_summary_only_while_running(service: JobService) -> None:
    run = service.create("model_training", "model-training", "web").run
    service.start(run.run_id)
    updated = service.progress(
        run.run_id,
        {"stage": "training_resale", "completed_markets": []},
    )
    assert updated.status == "running"
    assert updated.summary == {
        "stage": "training_resale",
        "completed_markets": [],
    }
    service.succeed(run.run_id, run.run_id, {"stage": "complete"})
    with pytest.raises(InvalidJobTransition):
        service.progress(run.run_id, {"stage": "late_write"})


def test_list_recent_can_filter_job_type(service: JobService) -> None:
    model = service.create("model_training", "model-1", "web").run
    service.create("listing_update", "listing-1", "web")
    assert service.list_recent(job_type="model_training") == [model]


def test_recover_interrupted_marks_only_requested_job_type_failed(
    service: JobService,
) -> None:
    pending = service.create("model_training", "model-pending", "web").run
    running = service.create("model_training", "model-running", "web").run
    listing = service.create("listing_update", "listing-running", "web").run
    service.start(running.run_id)
    service.start(listing.run_id)

    recovered = service.recover_interrupted("model_training")

    assert {run.run_id for run in recovered} == {pending.run_id, running.run_id}
    assert all(run.status == "failed" for run in recovered)
    assert all(run.error_code == "worker_interrupted" for run in recovered)
    assert service.get(listing.run_id).status == "running"  # type: ignore[union-attr]
```

Update the test fake with the wished-for repository methods; do not put test-only helpers in production classes.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_jobs.py -q
```

Expected: FAIL because `JobService.progress`, `recover_interrupted`, filtered `list_recent`, and repository methods do not exist. If it errors due only to the fake signature, fix the fake and rerun until assertions fail for missing production behavior.

- [ ] **Step 3: GREEN — implement the minimal service/repository contract**

In `jobs.py`:

```python
class JobRepository(Protocol):
    def list_recent(
        self, limit: int = 20, job_type: str | None = None
    ) -> list[JobRun]: ...
    def list_active(self, job_type: str) -> list[JobRun]: ...
    def update_summary(
        self,
        run_id: str,
        expected_status: JobStatus,
        summary: dict[str, object],
    ) -> bool: ...


class JobService:
    def list_recent(
        self, limit: int = 20, job_type: str | None = None
    ) -> list[JobRun]:
        return self._repository.list_recent(limit, job_type)

    def progress(self, run_id: str, summary: dict[str, object]) -> JobRun:
        run = self._repository.get(run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        if run.status != "running":
            raise InvalidJobTransition(run_id, run.status, "running")
        if not self._repository.update_summary(run_id, "running", summary):
            raise InvalidJobTransition(run_id, "running", "running")
        updated = self._repository.get(run_id)
        assert updated is not None
        return updated

    def recover_interrupted(self, job_type: str) -> list[JobRun]:
        recovered = []
        for run in self._repository.list_active(job_type):
            recovered.append(
                self.fail(run.run_id, "worker_interrupted", "背景工作因網站重啟而中斷。")
            )
        return recovered
```

Allow `pending -> failed` and `retry_wait -> failed` in `ALLOWED_TRANSITIONS`. In
`MySQLJobRepository`, use parameterized SQL:

```sql
SELECT *
FROM job_runs
WHERE job_type = %s
  AND status IN ('pending', 'running', 'retry_wait')
ORDER BY created_at ASC, run_id ASC
```

and:

```sql
UPDATE job_runs
SET summary = %s, updated_at = %s
WHERE run_id = %s AND status = %s
```

`list_recent()` adds `WHERE job_type = %s` only when a filter is supplied. Keep all JSON serialization through `json.dumps(..., ensure_ascii=False)`.

- [ ] **Step 4: Verify GREEN at service level**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_jobs.py -q
```

Expected: PASS.

- [ ] **Step 5: RED/GREEN repository integration**

Add real repository contract tests in `tests/test_job_repository.py` for:

- filtered ordering and limit;
- active-only filtering;
- `update_summary` succeeds for running and returns `False` after terminal transition.

First run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_job_repository.py -q
```

Expected RED: SQL fake/real test connection does not observe the new filtering/update behavior.

Implement only the repository SQL required by those failures, rerun, and expect PASS.

- [ ] **Step 6: Refactor and regression**

Remove duplicate status tuples by reusing `ACTIVE_STATUSES`. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_jobs.py tests/test_job_repository.py tests/test_job_executor.py -q
```

Expected: PASS with no warnings.

- [ ] **Step 7: Commit**

```powershell
git add src/qingpu_insight/jobs.py src/qingpu_insight/job_repository.py tests/test_jobs.py tests/test_job_repository.py
git commit -m "feat(models): add progress-aware model jobs"
```

---

### Task 2: 修正 Calibration 選模與 Final Test 隔離

**Files:**
- Modify: `src/qingpu_insight/model_training.py`
- Modify: `src/qingpu_insight/valuation.py`
- Modify: `tests/test_model_training.py`
- Modify: `tests/test_valuation.py`

**Interfaces:**
- Produces: `ModelExperiment`
- Produces: `run_model_experiment(split: TimeSplit, estimators: dict[str, BaseEstimator] | None = None) -> ModelExperiment`
- Produces: `passes_release_gate(candidate: CandidateEvaluation, baseline: CandidateEvaluation) -> bool`
- Changes: `evaluate_candidate(name, estimator, train_frame, evaluation_frame)` explicitly receives the evaluation frame
- Consumes: `TimeSplit`, `RecentMedianBaseline`, `candidate_estimators`, `metric_rows`

- [ ] **Step 1: RED — prove test data cannot choose a candidate**

Name the break: changing only test targets must not change `selected_name`; it may only change `recommended` and final metrics.

Add to `tests/test_model_training.py` using real `DummyRegressor` instances:

```python
def test_experiment_selects_on_calibration_before_reading_final_test(
    model_frame: pd.DataFrame,
) -> None:
    from sklearn.dummy import DummyRegressor

    split = split_by_time(model_frame)
    estimators = {
        "low_quantile": DummyRegressor(strategy="quantile", quantile=0.25),
        "high_quantile": DummyRegressor(strategy="quantile", quantile=0.75),
    }
    first = run_model_experiment(split, estimators)

    hostile_test = split.test.copy()
    hostile_test["target_unit_price_twd"] = 2_000_000.0
    second = run_model_experiment(
        TimeSplit(split.train, split.calibration, hostile_test),
        {
            "low_quantile": DummyRegressor(strategy="quantile", quantile=0.25),
            "high_quantile": DummyRegressor(strategy="quantile", quantile=0.75),
        },
    )

    assert second.selected_name == first.selected_name
    assert set(second.final_test_results) == {"baseline", first.selected_name}
```

Add a separate test that a locked candidate failing the final gate returns
`recommended is False` without selecting the other candidate.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py -q
```

Expected: FAIL because `run_model_experiment` and `ModelExperiment` do not exist and current `evaluate_candidate()` hardcodes `split.test`.

- [ ] **Step 3: GREEN — implement the explicit experiment protocol**

Add:

```python
@dataclass(frozen=True)
class ModelExperiment:
    selection_results: tuple[CandidateEvaluation, ...]
    selected_name: str
    selected_estimator: Any
    final_test_results: dict[str, CandidateEvaluation]
    candidate_errors: dict[str, str]
    recommended: bool
    reason_codes: tuple[str, ...]


def evaluate_candidate(
    name: str,
    estimator: Any,
    train_frame: pd.DataFrame,
    evaluation_frame: pd.DataFrame,
) -> CandidateEvaluation:
    estimator.fit(
        train_frame[list(FEATURE_COLUMNS)],
        train_frame["target_unit_price_twd"],
    )
    return evaluate_fitted_candidate(name, estimator, evaluation_frame)


def evaluate_fitted_candidate(
    name: str,
    estimator: Any,
    evaluation_frame: pd.DataFrame,
) -> CandidateEvaluation:
    predicted = estimator.predict(evaluation_frame[list(FEATURE_COLUMNS)])
    actual = evaluation_frame["target_unit_price_twd"].to_numpy()
    metrics = metric_rows(actual, predicted, evaluation_frame)
    return CandidateEvaluation(
        name=name,
        estimator=estimator,
        overall_mae=float(metrics.loc["overall", "mae"]),
        station_mape={
            index.split(":", 1)[1]: float(row["mape"])
            for index, row in metrics.iterrows()
            if index.startswith("station:")
        },
        metrics=metrics,
    )
```

Make the gate a single reusable predicate:

```python
def passes_release_gate(
    candidate: CandidateEvaluation,
    baseline: CandidateEvaluation,
) -> bool:
    published_stations = set(baseline.station_mape)
    return (
        candidate.name != "baseline"
        and candidate.overall_mae <= baseline.overall_mae * 0.98
        and published_stations <= set(candidate.station_mape)
        and all(
            candidate.station_mape[station]
            <= baseline.station_mape[station] * 1.10
            for station in published_stations
        )
    )
```

`select_release_candidate()` filters with this predicate. Final recommendation is:

```python
recommended = (
    selected_name != "baseline"
    and passes_release_gate(final_selected, final_baseline)
)
```

`run_model_experiment()` must:

1. fit Baseline and all supplied/default candidates on `split.train`;
2. evaluate all on `split.calibration`;
3. catch each non-Baseline exception, record a stable `candidate_failed` entry in
   `candidate_errors`, and continue with remaining candidates;
4. fail the experiment when Baseline itself cannot be evaluated;
5. call existing release selection on only the successful calibration results;
6. lock `selected_name`;
7. evaluate only Baseline and the locked estimator on `split.test`;
8. set `recommended` from the final-test release gate;
9. return `final_gate_failed` when calibration selected a non-Baseline model but final test rejects it;
10. return `baseline_selected` when no non-Baseline candidate passed calibration.

Add a real `FailingRegressor` test double implementing `fit()` by raising
`RuntimeError("fit failed")`; assert another real estimator is still evaluated and
`candidate_errors == {"broken": "candidate_failed"}`. A separate Baseline failure
test must assert the experiment raises `BaselineEvaluationError`.

- [ ] **Step 4: Verify GREEN and preserve existing gates**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py -q
```

Expected: PASS. Update old `evaluate_candidate` tests to pass explicit train and evaluation frames; do not weaken assertions.

- [ ] **Step 5: RED — keep artifact generation away from test-based fitting/importance**

Add a focused test in `tests/test_valuation.py` with a recording estimator showing:

- `train_artifact()` never calls `.fit()`;
- calibration rows are used for interval residuals;
- permutation importance receives calibration, not test.

Monkeypatch only `sklearn.inspection.permutation_importance`, because it is the slow calculation boundary; assert the resulting bundle and captured frame dates, not the mock's existence.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_valuation.py -q
```

Expected RED: current permutation importance consumes `split.test`.

- [ ] **Step 6: GREEN — make artifact packaging consume the locked estimator**

Change `train_artifact()` so permutation importance uses `split.calibration`, and set
`bundle.metrics` from the locked candidate's final-test evaluation passed by the caller. Do not fit in this function.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py tests/test_valuation.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/qingpu_insight/model_training.py src/qingpu_insight/valuation.py tests/test_model_training.py tests/test_valuation.py
git commit -m "fix(models): isolate selection from final test"
```

---

### Task 3: 建立不可變 Candidate Manifest 與 Artifact Store

**Files:**
- Create: `src/qingpu_insight/model_artifacts.py`
- Create: `tests/test_model_artifacts.py`

**Interfaces:**
- Produces: `DataSnapshot`, `MarketTrainingResult`, `TrainingManifest`
- Produces: `sha256_file(path: Path) -> str`
- Produces: `CandidateArtifactStore.begin(run_id: str) -> Path`
- Produces: `CandidateArtifactStore.commit(run_id: str, manifest: TrainingManifest) -> Path`
- Produces: `CandidateArtifactStore.discard_staging(run_id: str) -> None`
- Produces: `CandidateArtifactStore.get(run_id: str) -> TrainingManifest`
- Produces: `CandidateArtifactStore.list_recent(limit: int = 20) -> list[TrainingManifest]`
- Produces: `CandidateArtifactStore.report_path(run_id: str, report_type: str) -> Path`

- [ ] **Step 1: RED — define observable filesystem safety**

Name the breaks: path traversal must escape neither root nor report whitelist; completed runs are immutable; incomplete temp directories are invisible; manifest/hash mismatch cannot commit.

Create `tests/test_model_artifacts.py`:

```python
def test_candidate_store_commits_a_valid_run_atomically(tmp_path: Path) -> None:
    store = CandidateArtifactStore(tmp_path / "artifacts" / "candidates")
    stage = store.begin("00000000-0000-4000-8000-000000000001")
    artifact = stage / "resale.joblib"
    artifact.write_bytes(b"candidate-model")
    report = stage / "reports" / "resale-evaluation.json"
    report.parent.mkdir()
    report.write_text('{"selected_model":"ridge"}', encoding="utf-8")
    manifest = manifest_fixture(
        run_id="00000000-0000-4000-8000-000000000001",
        artifact_hash=sha256_file(artifact),
        report_hash=sha256_file(report),
    )
    (stage / "manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )

    committed = store.commit(str(manifest.run_id), manifest)

    assert committed.name == manifest.run_id
    assert not stage.exists()
    assert store.get(str(manifest.run_id)) == manifest


@pytest.mark.parametrize("run_id", ["../escape", "not-a-uuid", ""])
def test_candidate_store_rejects_unsafe_run_ids(tmp_path: Path, run_id: str) -> None:
    store = CandidateArtifactStore(tmp_path / "candidates")
    with pytest.raises(ValueError, match="run_id"):
        store.begin(run_id)


def test_candidate_store_never_overwrites_a_completed_run(tmp_path: Path) -> None:
    store, manifest = committed_store_fixture(tmp_path)
    with pytest.raises(FileExistsError):
        store.begin(str(manifest.run_id))


def test_report_lookup_accepts_only_the_fixed_whitelist(tmp_path: Path) -> None:
    store, manifest = committed_store_fixture(tmp_path)
    with pytest.raises(ValueError, match="report_type"):
        store.report_path(str(manifest.run_id), "../../resale.joblib")
```

Use literal 64-character hashes in `manifest_fixture`; do not compute expected hashes with store internals.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_artifacts.py -q
```

Expected: FAIL because the module does not exist.

- [ ] **Step 3: GREEN — implement manifest schemas**

Use Pydantic with `extra="forbid"`:

```python
class DataSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_count: int = Field(ge=0)
    usable_counts: dict[Literal["resale", "presale"], int]
    excluded_counts: dict[Literal["resale", "presale"], int]
    station_counts: dict[Literal["A17", "A18", "A19"], int]
    min_date: date
    max_date: date


class MarketTrainingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["resale", "presale"]
    selected_model: str
    recommended: bool
    reason_codes: list[str]
    selection_metrics: dict[str, dict[str, object]]
    final_test_metrics: dict[str, dict[str, object]]
    artifact_file: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_files: dict[str, str]
    report_sha256: dict[str, str]


class TrainingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    run_id: UUID
    created_at: datetime
    markets: list[Literal["resale", "presale"]]
    source_commit: str
    source_dirty: bool
    runtime_versions: dict[str, str]
    data_snapshot: DataSnapshot
    results: list[MarketTrainingResult]
```

Serialize `run_id` to canonical string for directory names.

- [ ] **Step 4: GREEN — implement store and verify real files**

Use a sibling staging directory `.tmp-<run_id>` under the candidate root. Resolve both stage/final paths and confirm `candidate_root` is in `path.parents` before any cleanup or move. `commit()` must:

1. reject an existing final directory;
2. load `manifest.json` back through `TrainingManifest`;
3. require loaded manifest equals the supplied manifest;
4. verify every listed artifact/report exists under stage and matches SHA-256;
5. verify each `.joblib` loads as a `ValuationBundle` with matching market;
6. call `os.replace(stage, final)` on the same filesystem.

`report_path()` maps only:

```python
REPORT_TYPES = {
    "resale-evaluation": "reports/resale-evaluation.json",
    "resale-model-card": "reports/resale-model-card.md",
    "presale-evaluation": "reports/presale-evaluation.json",
    "presale-model-card": "reports/presale-model-card.md",
    "manifest": "manifest.json",
}
```

`discard_staging()` resolves `.tmp-<run_id>`, confirms its parent is exactly the
resolved candidate root, and removes only that directory. It is a no-op when the
staging directory does not exist and must refuse an invalid UUID.

- [ ] **Step 5: Verify GREEN and mutation cases**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_artifacts.py -q
```

Expected: PASS. Then mutate a copied fixture's artifact bytes and assert `commit()` rejects it; restore production code and keep the test.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/model_artifacts.py tests/test_model_artifacts.py
git commit -m "feat(models): add immutable candidate artifacts"
```

---

### Task 4: 建立共用 ModelTrainingService

**Files:**
- Create: `src/qingpu_insight/model_training_service.py`
- Create: `tests/test_model_training_service.py`
- Modify: `src/qingpu_insight/valuation_reporting.py`
- Modify: `tests/test_valuation_reporting.py`

**Interfaces:**
- Produces: `ModelTrainingRequest(markets: tuple[Literal["resale", "presale"], ...], trigger: str = "web")`
- Produces: `ModelTrainingService.submit(request) -> JobSubmission`
- Produces: `ModelTrainingService.handoff(submission, request, executor) -> Future`
- Produces: `ModelTrainingService.execute(run_id, request) -> TrainingManifest`
- Produces: `build_data_snapshot(input_path: Path, frame: pd.DataFrame) -> DataSnapshot`
- Produces: `market_result_from_files(market: Literal["resale", "presale"], bundle: ValuationBundle, experiment: ModelExperiment, artifact_path: Path, evaluation_path: Path, card_path: Path, stage: Path) -> MarketTrainingResult`
- Produces: `public_training_summary(manifest: TrainingManifest) -> dict[str, object]`
- Produces: JSON report keys `selection_metrics`, `final_test_metrics`, `recommendation`
- Changes: `write_evaluation(bundle, experiment, split, report_dir) -> Path`
- Changes: `write_model_card(bundle, experiment, leakage, report_dir) -> Path`
- Consumes: Task 1 job progress; Task 2 `run_model_experiment`; Task 3 `CandidateArtifactStore`

- [ ] **Step 1: RED — request validation and single active reservation**

Name the breaks: an unsupported/duplicate/empty market must never enter a job; every model market combination shares one active lock key.

Add:

```python
@pytest.mark.parametrize(
    "markets",
    [(), ("resale", "resale"), ("sale",)],
)
def test_training_request_rejects_nonfixed_markets(markets) -> None:
    with pytest.raises(ValueError):
        ModelTrainingRequest(markets=markets)


def test_submit_returns_the_existing_active_model_job(tmp_path: Path) -> None:
    service, jobs = service_fixture(tmp_path)
    first = service.submit(ModelTrainingRequest(("resale",)))
    second = service.submit(ModelTrainingRequest(("presale",)))
    assert first.created is True
    assert second.created is False
    assert second.run.run_id == first.run.run_id
    assert jobs.get(first.run.run_id).idempotency_key == "model_training:active"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training_service.py -q
```

Expected: FAIL because `ModelTrainingRequest` and `ModelTrainingService` do not exist.

- [ ] **Step 3: GREEN — implement fixed request and submission**

Canonicalize market order as `resale`, then `presale`; reject duplicates rather than silently deduplicating. `submit()` calls:

```python
self._jobs.create(
    job_type="model_training",
    idempotency_key="model_training:active",
    trigger=request.trigger,
)
```

Do not hash or load the dataset inside the HTTP submission path.

- [ ] **Step 4: RED — data evidence and atomic all-market behavior**

Add real-Parquet tests with the existing synthetic market fixture:

```python
def test_execute_writes_traceable_candidate_without_touching_official_models(
    tmp_path: Path,
    market_parquet: Path,
) -> None:
    service, jobs = service_fixture(tmp_path, input_path=market_parquet)
    official = tmp_path / "artifacts" / "resale.joblib"
    official.parent.mkdir(parents=True)
    official.write_bytes(b"official-model")
    before = sha256_file(official)
    run = service.submit(ModelTrainingRequest(("resale",))).run
    jobs.start(run.run_id)

    manifest = service.execute(run.run_id, ModelTrainingRequest(("resale",)))

    assert manifest.run_id == UUID(run.run_id)
    assert manifest.markets == ["resale"]
    assert manifest.data_snapshot.raw_count == 1_600
    assert manifest.data_snapshot.usable_counts == {
        "resale": 800,
        "presale": 800,
    }
    assert manifest.data_snapshot.station_counts == {
        "A17": 534,
        "A18": 534,
        "A19": 532,
    }
    assert sha256_file(official) == before
    assert jobs.get(run.run_id).status == "succeeded"  # type: ignore[union-attr]
```

The `market_parquet` fixture writes exactly 800 resale and 800 presale rows. Each
market cycles stations in A17/A18/A19 order, producing 267/267/266 rows per market;
the expected totals above are independently derived literals.

Add another test where presale training raises after resale completes. Assert:

```python
assert jobs.get(run.run_id).status == "failed"
assert not (candidate_root / run.run_id).exists()
assert sha256_file(official_resale) == before_resale
assert sha256_file(official_presale) == before_presale
```

- [ ] **Step 5: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training_service.py -q
```

Expected: FAIL because execute/data snapshot/report/atomic commit behavior is absent. Ensure failure is not caused by invalid synthetic split sizes.

- [ ] **Step 6: GREEN — implement orchestration**

`execute()` performs, in order:

```python
self._jobs.progress(run_id, {"stage": "validating_data", "markets": markets})
frame = pd.read_parquet(self._input_path)
snapshot = build_data_snapshot(self._input_path, frame)
source_version = self._source_version_provider.read()
stage = self._store.begin(run_id)
try:
    results: list[MarketTrainingResult] = []
    for market in markets:
        self._jobs.progress(
            run_id,
            {"stage": f"training_{market}", "completed_markets": completed},
        )
        model_frame = build_model_frame(frame, market)
        split = split_by_time(model_frame)
        experiment = run_model_experiment(split)
        locked = experiment.final_test_results[experiment.selected_name]
        seed_bundle = ValuationBundle(
            transaction_type=market,
            model_name="",
            model_version="",
            pipeline=None,
            interval_abs_residual_twd_per_ping=0,
            feature_ranges={},
            feature_hard_ranges={},
            feature_medians={},
            global_importance=[],
            reference_rows=pd.DataFrame(),
            data_min_date="",
            data_max_date=str(split.train["transaction_date"].max().date()),
            metrics={},
        )
        artifact_path = train_artifact(market, locked, split, seed_bundle, stage)
        bundle: ValuationBundle = joblib.load(artifact_path)
        self._jobs.progress(
            run_id,
            {"stage": f"evaluating_{market}", "completed_markets": completed},
        )
        report_dir = stage / "reports"
        evaluation_path = write_evaluation(
            bundle,
            experiment,
            split,
            report_dir,
        )
        card_path = write_model_card(
            bundle,
            experiment,
            leakage_audit(split),
            report_dir,
        )
        results.append(
            market_result_from_files(
                market=market,
                bundle=bundle,
                experiment=experiment,
                artifact_path=artifact_path,
                evaluation_path=evaluation_path,
                card_path=card_path,
                stage=stage,
            )
        )
        completed.append(market)
    self._jobs.progress(
        run_id,
        {"stage": "writing_artifacts", "completed_markets": completed},
    )
    manifest = TrainingManifest(
        run_id=UUID(run_id),
        created_at=self._clock(),
        markets=list(markets),
        source_commit=source_version.commit,
        source_dirty=source_version.dirty,
        runtime_versions=runtime_versions(),
        data_snapshot=snapshot,
        results=results,
    )
    (stage / "manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    self._store.commit(run_id, manifest)
    self._jobs.succeed(run_id, run_id, public_training_summary(manifest))
    return manifest
except ModelTrainingError as error:
    self._store.discard_staging(run_id)
    self._jobs.fail(run_id, error.error_code, error.safe_message)
    raise
except Exception:
    self._store.discard_staging(run_id)
    self._jobs.fail(run_id, "training_failed", "模型訓練失敗。")
    raise
```

Define stable errors:

- `training_data_missing`
- `training_data_invalid`
- `training_data_insufficient`
- `baseline_failed`
- `candidate_write_failed`
- `candidate_validation_failed`

An individual non-Baseline estimator failure is captured in the report and does not fail the market if Baseline and another candidate remain evaluable.

The data snapshot also records Git commit/dirty and exact Python, NumPy, pandas, scikit-learn versions. Inject a `SourceVersionProvider` in tests instead of invoking real Git there.

- [ ] **Step 7: RED/GREEN — reporting separates selection and final test**

Update `tests/test_valuation_reporting.py` first:

```python
assert report["selection_metrics"]["ridge"]["overall"]["count"] == 100
assert report["final_test_metrics"]["ridge"]["overall"]["count"] == 200
assert report["recommendation"] == {
    "status": "recommended",
    "reason_codes": [],
}
```

Run and expect RED because current report has only `candidates` and `grouped_metrics`.

Modify `write_evaluation()` and `write_model_card()` to consume `ModelExperiment`. The model card must label calibration comparison separately from final test and state:

```text
此版本為未發布候選模型，不會替換網站正式估價模型。
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_valuation_reporting.py tests/test_model_training_service.py -q
```

Expected: PASS.

- [ ] **Step 8: Refactor and focused regression**

Extract functions only after green:

- `build_data_snapshot()`
- `runtime_versions()`
- `public_training_summary()`

Do not create `safe_remove_stage()` as a second deletion implementation; use
`CandidateArtifactStore.discard_staging()` as the single containment-checked path.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py tests/test_valuation.py tests/test_valuation_reporting.py tests/test_model_artifacts.py tests/test_model_training_service.py -q
```

Expected: PASS without warnings.

- [ ] **Step 9: Commit**

```powershell
git add src/qingpu_insight/model_training_service.py src/qingpu_insight/valuation_reporting.py tests/test_model_training_service.py tests/test_valuation_reporting.py
git commit -m "feat(models): add versioned training service"
```

---

### Task 5: 建立 Model Observatory Read Model 並讓 CLI 共用 Service

**Files:**
- Create: `src/qingpu_insight/model_observatory.py`
- Create: `tests/test_model_observatory.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `ModelObservatory.status() -> dict[str, object]`
- Produces: `ModelObservatory.list_runs(limit: int) -> list[dict[str, object]]`
- Produces: `ModelObservatory.get_run(run_id: str) -> dict[str, object] | None`
- Changes: CLI `model-train` calls `ModelTrainingService`, waits synchronously, and prints the run ID/result
- Consumes: `CandidateArtifactStore`, official `ValuationBundle`, fixed input Parquet, filtered `JobService`

- [ ] **Step 1: RED — official/candidate separation in read model**

Name the break: the status page must never label a candidate as official, and a corrupt/missing official artifact must return a warning rather than a 500.

Create tests:

```python
def test_status_separates_official_models_from_candidate_runs(tmp_path: Path) -> None:
    observatory = observatory_fixture(
        tmp_path,
        official_models={"resale": bundle_fixture(model_name="ridge")},
        candidate_runs=[manifest_fixture(selected_model="hist_gradient_boosting")],
    )
    status = observatory.status()
    assert status["official_models"]["resale"]["name"] == "ridge"
    assert status["official_models"]["resale"]["role"] == "official"
    assert status["candidate_count"] == 1


def test_missing_official_artifact_is_a_safe_status_warning(tmp_path: Path) -> None:
    status = observatory_fixture(tmp_path).status()
    assert status["official_models"]["resale"] == {
        "available": False,
        "role": "official",
        "warning": "resale_model_unavailable",
    }
```

- [ ] **Step 2: Verify RED, implement GREEN**

Run and expect missing module:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_observatory.py -q
```

Implement read-only behavior. `status()` may cache the dataset snapshot by `(resolved_path, size, mtime_ns)` but must invalidate when any tuple member changes. It must never write an artifact.

`list_runs()` first asks `JobService.list_recent(limit, job_type="model_training")`, then merges a validated manifest only for succeeded runs. A missing/corrupt manifest becomes a safe warning, not raw traceback.

Rerun and expect PASS.

- [ ] **Step 3: RED — CLI must use the common service**

Add a CLI contract test by injecting/monkeypatching the service factory, not model internals:

```python
def test_model_train_cli_submits_and_executes_common_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = RecordingModelTrainingService()
    monkeypatch.setattr(cli, "_create_model_training_service", lambda root: fake)

    result = cli.main(["model-train", "--markets", "resale"])

    assert result == 0
    assert fake.requests == [ModelTrainingRequest(("resale",), trigger="manual")]
    assert "candidate run:" in capsys.readouterr().out
```

The fake must implement the complete public service interface and real job lifecycle effects required by CLI; do not assert private calls.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli.py -q
```

Expected RED: parser lacks `--markets` and current function contains duplicated training logic.

- [ ] **Step 4: GREEN — migrate CLI without changing its safety boundary**

Add:

```text
qingpu-data model-train --markets resale presale
```

Default remains both markets. Keep existing `--input`, `--artifact-dir`, and `--report-dir` accepted for backward compatibility only when invoked from CLI; the Web factory always supplies fixed project paths. CLI custom output paths still go through a candidate store and never write official `artifacts/resale.joblib` or `presale.joblib`.

Delete the duplicated loop from `cli.model_train()` after tests are red, then call the common service synchronously:

```python
submission = service.submit(ModelTrainingRequest(tuple(markets), trigger="manual"))
jobs.start(submission.run.run_id)
manifest = service.execute(submission.run.run_id, request)
print(f"candidate run: {manifest.run_id}")
```

Do not call `LocalJobExecutor` from CLI.

- [ ] **Step 5: Verify GREEN and regress old CLI behavior**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_model_observatory.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/model_observatory.py src/qingpu_insight/cli.py tests/test_model_observatory.py tests/test_cli.py
git commit -m "refactor(models): share training across cli and web"
```

---

### Task 6: 加入受保護的 Model Admin API

**Files:**
- Modify: `src/qingpu_insight/web.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Produces: `GET /api/admin/models/status`
- Produces: `GET /api/admin/model-training-runs?limit=20`
- Produces: `GET /api/admin/model-training-runs/<run_id>`
- Produces: `POST /api/admin/model-training-runs`
- Produces: `GET /api/admin/model-training-runs/<run_id>/reports/<report_type>`
- Changes: `AdminServices` gains optional `model_training_service` and `model_observatory`

- [ ] **Step 1: RED — exact request whitelist and local security**

Add parameterized API tests:

```python
@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "markets"),
        ({"markets": []}, "markets"),
        ({"markets": ["resale", "resale"]}, "markets"),
        ({"markets": ["sale"]}, "markets"),
        ({"markets": ["resale"], "path": "C:/secret"}, "path"),
        ({"markets": ["resale"], "model": "xgboost"}, "model"),
    ],
)
def test_model_training_post_rejects_nonfixed_payload(
    model_admin_client: FlaskClient,
    payload: dict[str, object],
    field: str,
) -> None:
    response = model_admin_client.post(
        "/api/admin/model-training-runs",
        json=payload,
        headers={"X-CSRF-Token": "test-token"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"][field]
```

Also test every model-admin API GET rejects an untrusted Host and POST rejects missing/wrong CSRF.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "model_admin" -q
```

Expected: FAIL/404 because routes and dependencies do not exist.

- [ ] **Step 3: GREEN — add composition and fixed parser**

Extend without breaking existing injected listing tests:

```python
@dataclass(frozen=True)
class AdminServices:
    job_service: JobService
    listing_update_service: ListingUpdateService
    executor: LocalJobExecutor
    model_training_service: ModelTrainingService | None = None
    model_observatory: ModelObservatory | None = None
```

Production composition must build both model services with the same `job_service` and same `executor` used by listing jobs. At composition startup:

```python
for interrupted in job_service.recover_interrupted("model_training"):
    candidate_store.discard_staging(interrupted.run_id)
```

The POST parser accepts exactly one key, `markets`, and returns canonical tuple order.

- [ ] **Step 4: RED — successful submission, history/detail, downloads**

Add tests asserting:

- new POST returns 202 and `created: true`;
- repeat while active returns the same run and `created: false`;
- markets are canonicalized;
- history contains only `model_training`;
- detail validates UUID and returns 404 when absent;
- report whitelist sends UTF-8 JSON/Markdown;
- `../../resale.joblib` and unknown report types return 400;
- `.joblib` is not downloadable;
- status distinguishes official and candidate.

Run and expect RED because handlers are absent.

- [ ] **Step 5: GREEN — implement routes**

Use existing `_require_trusted_local_post()`, `_is_trusted_local_request()`,
`_public_job()`, `_invalid_request()` and global redaction. For POST:

```python
submission = admin_services.model_training_service.submit(request_obj)
if submission.created:
    admin_services.model_training_service.handoff(
        submission,
        request_obj,
        admin_services.executor,
    )
body = _public_job(submission.run)
body["created"] = submission.created
return jsonify(body), 202 if submission.created else 200
```

Use Flask `send_file(path, as_attachment=True, download_name=path.name)` only with the path returned by `ModelObservatory.report_path()`.

Do not add any `promote`, `publish`, `rollback`, artifact upload, path query, estimator or hyperparameter route.

- [ ] **Step 6: Verify GREEN and full Web regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -q
```

Expected: PASS with no warning or traceback output.

- [ ] **Step 7: Commit**

```powershell
git add src/qingpu_insight/web.py tests/test_web.py
git commit -m "feat(models): expose protected observatory api"
```

---

### Task 7: 建立獨立模型觀測台 UI

**Files:**
- Create: `src/qingpu_insight/templates/models_admin.html`
- Create: `src/qingpu_insight/static/models_admin.js`
- Create: `src/qingpu_insight/static/models_admin.css`
- Create: `tests/js/model_admin_contract.cjs`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: Task 6 API and existing `QingpuJobPolling.createPollController`
- Produces: `QingpuModelAdmin.buildTrainingPayload(value)`
- Produces: `QingpuModelAdmin.derivePageState(statusPayload, activeRun)`
- Produces: browser page at `/admin/models`

- [ ] **Step 1: RED — pure front-end contract**

Name the breaks: the UI must never submit arbitrary strings; active work must disable submission; candidate/official labels must not be confused.

Create CommonJS-compatible tests:

```javascript
const assert = require("node:assert/strict");
const admin = require("../../src/qingpu_insight/static/models_admin.js");

assert.deepEqual(admin.buildTrainingPayload("all"), {
  markets: ["resale", "presale"],
});
assert.deepEqual(admin.buildTrainingPayload("resale"), {
  markets: ["resale"],
});
assert.throws(() => admin.buildTrainingPayload("xgboost"));

assert.deepEqual(
  admin.derivePageState(
    { official_models: { resale: { role: "official", name: "ridge" } } },
    { status: "running", summary: { stage: "training_resale" } }
  ),
  {
    canSubmit: false,
    stageLabel: "正在訓練中古屋模型",
    officialLabel: "目前正式模型",
    candidateNotice: "本次只建立候選版本，不會替換正式模型。",
  }
);
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
node tests/js/model_admin_contract.cjs
```

Expected: FAIL because `models_admin.js` does not exist.

- [ ] **Step 3: GREEN — implement UMD helpers and controller**

Follow `job_polling.js` UMD style so the same real code runs in Node and browser. Keep payload choices as a closed map:

```javascript
var MARKET_PAYLOADS = {
  resale: ["resale"],
  presale: ["presale"],
  all: ["resale", "presale"],
};
```

`derivePageState()` maps only the fixed stages:

```javascript
var STAGE_LABELS = {
  validating_data: "正在檢查訓練資料",
  training_resale: "正在訓練中古屋模型",
  evaluating_resale: "正在評估中古屋模型",
  training_presale: "正在訓練預售屋模型",
  evaluating_presale: "正在評估預售屋模型",
  writing_artifacts: "正在驗證候選模型產物",
};
```

Unknown stages display `正在處理模型工作` and never render raw HTML.
The UI maps `recommended: true` to `建議` and `recommended: false` to
`不建議（not_recommended）`; it must not invent a third confidence state.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
node tests/js/model_admin_contract.cjs
node --check src/qingpu_insight/static/models_admin.js
```

Expected: both exit 0.

- [ ] **Step 5: RED — server-rendered page contract**

Add Flask tests asserting `/admin/models` contains:

- untrusted Host/remote address receives 403;
- CSRF meta tag;
- official model section;
- data status section;
- market select with only resale/presale/all;
- permanent non-publish notice;
- history/detail containers;
- `job_polling.js` before `models_admin.js`;
- no input for path, command, estimator, hyperparameter, publish or rollback.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "model_admin_page" -q
```

Expected RED: template/page content absent.

- [ ] **Step 6: GREEN — implement page and browser behavior**

`models_admin.html` must use `textContent` for all API-originated strings. Render:

1. page header and link back to `/`;
2. official resale/presale cards;
3. data snapshot cards/table;
4. fixed training form and permanent notice;
5. active job status/progress;
6. recent run table;
7. selected run detail with calibration candidate table and final-test table;
8. reason codes translated by a fixed client dictionary;
9. JSON/Markdown download links only from API-provided allowed report types.

On submit:

```javascript
fetch("/api/admin/model-training-runs", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-CSRF-Token": csrfToken,
  },
  body: JSON.stringify(buildTrainingPayload(marketSelect.value)),
});
```

Use the existing poll controller with `GET /api/admin/model-training-runs/<run_id>`.
On terminal status, reload status, history and detail. Disable the button while POST is in flight or an active run exists.

Add a simple link in `index.html`: `模型觀測台`.

- [ ] **Step 7: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "model_admin_page" -q
node tests/js/model_admin_contract.cjs
node --check src/qingpu_insight/static/models_admin.js
```

Expected: PASS / exit 0.

- [ ] **Step 8: Refactor UI only while green**

Extract DOM helpers `setText`, `renderMetricTable`, and `renderWarnings`; all accept plain data and write through `textContent`. Do not add Chart.js, a second CSS framework, modal library or client-side model calculations.

Re-run Step 7.

- [ ] **Step 9: Commit**

```powershell
git add src/qingpu_insight/templates/models_admin.html src/qingpu_insight/static/models_admin.js src/qingpu_insight/static/models_admin.css src/qingpu_insight/templates/index.html tests/js/model_admin_contract.cjs tests/test_web.py
git commit -m "feat(models): add local model observatory ui"
```

---

### Task 8: 方法論、操作紀錄與端到端驗證

**Files:**
- Modify: `docs/m2-valuation-methodology.md`
- Modify: `README.md`
- Modify only if a discovered regression requires a test-first fix: relevant `tests/` and `src/` files

**Interfaces:**
- Documents: fixed Web workflow, candidate/official boundary, calibration selection, final-test gate
- Verifies: all automated suites and manual browser flow

- [ ] **Step 1: Update documentation after behavior is green**

In `docs/m2-valuation-methodology.md`, replace ambiguous release text with:

```text
所有候選模型只在校準集比較並鎖定一個候選。鎖定後，測試集只比較該候選與 Baseline；
測試結果不得用來改選另一個候選。候選必須在校準與最終測試兩階段都通過 release gate，
系統判定才可為 recommended。
```

In `README.md`, document:

```text
http://127.0.0.1:5000/admin/models
```

and state that Web training:

- accepts only resale/presale/all;
- writes `artifacts/candidates/<run_id>/`;
- never replaces `artifacts/resale.joblib` or `artifacts/presale.joblib`;
- requires a strong `QINGPU_SECRET_KEY`, MySQL, and loopback access.

- [ ] **Step 2: Run focused model and admin suites**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_training.py tests/test_valuation.py tests/test_valuation_reporting.py tests/test_model_artifacts.py tests/test_model_training_service.py tests/test_model_observatory.py tests/test_jobs.py tests/test_job_repository.py tests/test_cli.py tests/test_web.py -q
```

Expected: PASS with no warnings. If a failure reveals a bug, stop, add the smallest failing regression test, verify RED, implement minimal fix, then rerun.

- [ ] **Step 3: Run complete automated verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
node tests/js/job_polling_contract.cjs
node tests/js/model_admin_contract.cjs
node --check src/qingpu_insight/static/app.js
node --check src/qingpu_insight/static/job_polling.js
node --check src/qingpu_insight/static/models_admin.js
```

Expected: every command exits 0, with no traceback or warning.

- [ ] **Step 4: Record official artifact hashes before manual smoke**

```powershell
Get-FileHash .\artifacts\resale.joblib -Algorithm SHA256
Get-FileHash .\artifacts\presale.joblib -Algorithm SHA256
```

Save both values in a temporary operator note outside Git or in the terminal transcript.

- [ ] **Step 5: Manual browser smoke**

Start the existing local app and open `http://127.0.0.1:5000/admin/models`.

Verify:

1. official model cards and data snapshot load;
2. only three market choices exist;
3. permanent non-publish notice is visible;
4. one resale run moves through pending/running/fixed stages;
5. a second click cannot create a concurrent run;
6. succeeded detail separates calibration candidate metrics from final-test metrics;
7. recommendation reasons and low-sample warnings are readable;
8. JSON and Markdown reports download;
9. no `.joblib`, publish, rollback, path, model or hyperparameter control exists;
10. valuation page still reports the same official model version.

- [ ] **Step 6: Recheck official artifact hashes**

```powershell
Get-FileHash .\artifacts\resale.joblib -Algorithm SHA256
Get-FileHash .\artifacts\presale.joblib -Algorithm SHA256
```

Expected: both hashes exactly equal Step 4. Also verify a new immutable directory exists under `artifacts/candidates/<run_id>/`.

- [ ] **Step 7: Restart recovery smoke**

Start a model job, stop the Web process while status is running, restart it, and verify:

- old run becomes `failed`;
- `error_code` is `worker_interrupted`;
- UI does not claim the old job is still running;
- a new run can be submitted;
- incomplete temp output is not listed as a completed candidate.

- [ ] **Step 8: Commit documentation and any test-first regression fixes**

```powershell
git add README.md docs/m2-valuation-methodology.md
git commit -m "docs(models): document observatory workflow"
```

- [ ] **Step 9: Final TDD audit**

Before claiming completion, confirm:

- every new production function has a test that was observed failing first;
- every RED failed because behavior was absent, not because of syntax/import fixture mistakes;
- expected metrics/counts use hand-checked literals;
- no test asserts only that a mock was called;
- temporary filesystem and real serialization behavior are exercised;
- mutations to market validation, final-test isolation, path containment, CSRF, official artifact hashes, and active job uniqueness would each fail at least one test;
- full pytest, Ruff, Node contracts and manual smoke all passed.

---

## Execution Notes

- Execute tasks in order. Task 4 depends on Tasks 1–3; Task 6 depends on Tasks 4–5; Task 7 depends on Task 6.
- Do not combine RED and GREEN into one tool call. The terminal transcript must show the failing test before the production edit.
- If a newly written test passes immediately, stop and rewrite it so it proves the missing behavior.
- If production code is accidentally written before its test, remove that production change and restart the cycle.
- Keep each task commit limited to its declared files. Existing unrelated dirty worktree changes remain untouched.
