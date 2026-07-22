# 青埔智價 M4.2 工作與發布 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可稽核、冪等、可重試的工作狀態機與兩階段發布，並提供一次完成三類 591 的本機更新入口。

**Architecture:** `JobService` 以 MySQL repository 管理狀態；`PipelineRunner` 只執行有明確輸入／輸出的 steps；`ListingUpdateService` 組裝既有 capture/build 流程。管線可輸出 Parquet 分析快照，但所有正式 rows 先寫入 MySQL versioned staging，成功後在單一 transaction 原子切換 published version。

**Tech Stack:** Python 3.11、dataclasses、JSON、PyMySQL、Flask、concurrent.futures、PowerShell、pytest

## Global Constraints

- 狀態只能依 `pending -> running -> succeeded|retry_wait|skipped|failed -> needs_attention` 轉移。
- 相同冪等鍵不得建立兩個活動工作；同一時間最多一個 591 更新。
- 不完整批次、驗證頁或 schema failure 不得切換 published version。
- 591 預設由管理頁或 PowerShell 手動觸發；自動排程不在本階段預設安裝。
- Web request 不等待 Selenium 完成，只回傳 `202` 與 `run_id`。

## File Map

| File | Responsibility |
|---|---|
| `jobs.py` | JobRun、狀態機與安全錯誤契約 |
| `job_repository.py` | MySQL job persistence 與 concurrency control |
| `pipeline.py` | 依序執行、重試與停止 pipeline steps |
| `publishing.py` | Parquet artifact contract 與 MySQL version pointer transaction |
| `listing_update.py` | 組裝三類 591 capture/build/publish application flow |
| `job_executor.py` | 單 worker 本機背景執行，避免阻塞 HTTP |
| `cli.py` | listing-update/job-status 命令組裝 |
| `web.py` | localhost/CSRF 管理 API 與 job query API |

---

### Task 1: Job domain、狀態轉移與 repository

**Files:**
- Create: `src/qingpu_insight/jobs.py`
- Create: `src/qingpu_insight/job_repository.py`
- Create: `tests/test_jobs.py`
- Create: `tests/test_job_repository.py`

**Interfaces:**
- Produces: `JobStatus`, `JobRun`, `JobRepository`, `MySQLJobRepository`, `JobService.start/succeed/fail/retry/needs_attention`。

- [ ] **Step 1: 寫入 illegal transition 與 active idempotency 測試**

```python
def test_job_service_rejects_illegal_transition(repo) -> None:
    run = JobService(repo).create("listing_update", "same-key", "manual")
    with pytest.raises(InvalidJobTransition):
        JobService(repo).succeed(run.run_id)


def test_active_idempotency_key_returns_existing_run(repo) -> None:
    service = JobService(repo)
    first = service.create("listing_update", "same-key", "manual")
    second = service.create("listing_update", "same-key", "manual")
    assert second.run_id == first.run_id
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_jobs.py tests/test_job_repository.py -q`
Expected: FAIL，modules 尚不存在。

- [ ] **Step 3: 實作 domain 與 MySQL repository**

```python
JobStatus = Literal[
    "pending", "running", "succeeded", "retry_wait", "skipped", "failed",
    "needs_attention",
]

@dataclass(frozen=True)
class JobRun:
    run_id: str
    job_type: str
    trigger: str
    idempotency_key: str
    status: JobStatus
    started_at: datetime | None
    finished_at: datetime | None
    attempt: int
    input_version: str | None
    output_version: str | None
    summary: dict[str, object]
    error_code: str | None
    error_message: str | None
```

`ALLOWED_TRANSITIONS` 明確列出合法 edge；`MySQLJobRepository` 建立 `job_runs` 表，活動冪等鍵以
transaction 中的 `SELECT ... FOR UPDATE` 保證唯一。每次狀態轉移使用
`UPDATE ... WHERE run_id=%s AND status=%s`，affected rows 不為 1 即丟出 concurrent transition。
錯誤訊息先經 `redact_job_message()` 移除 URL credential、API key pattern 與疑似電話。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_jobs.py tests/test_job_repository.py -q`
Expected: PASS，非法 edge、重複 active key 與 atomic write 均覆蓋。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/jobs.py src/qingpu_insight/job_repository.py tests/test_jobs.py tests/test_job_repository.py
git commit -m "feat(m4): add durable job state machine"
```

### Task 2: Pipeline runner 與兩階段發布

**Files:**
- Create: `src/qingpu_insight/pipeline.py`
- Create: `src/qingpu_insight/publishing.py`
- Create: `tests/test_pipeline.py`
- Create: `tests/test_publishing.py`

**Interfaces:**
- Produces: `PipelineStep.run(context) -> StepResult`、`PipelineRunner.run()`、`DatasetVersion`、`MySQLVersionPublisher.stage/publish/current`。

- [ ] **Step 1: 寫入 stop-on-failure 與 atomic publish 測試**

```python
def test_runner_does_not_execute_after_required_failure(tmp_path) -> None:
    calls: list[str] = []
    runner = PipelineRunner([PassingStep("capture", calls), FailingStep("validate", calls),
                             PassingStep("publish", calls)])
    result = runner.run(PipelineContext("run-1", tmp_path, {}))
    assert calls == ["capture", "validate"]
    assert result.status == "failed"


def test_failed_build_keeps_previous_published_version(mysql_publisher) -> None:
    publisher = mysql_publisher
    publisher.stage(DatasetVersion("v1", "run-1", "ready", {"rows": 10}))
    publisher.publish("v1")
    publisher.stage(DatasetVersion("v2", "run-2", "building", {"rows": 3}))
    assert publisher.current().version == "v1"
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_pipeline.py tests/test_publishing.py -q`
Expected: FAIL，runner 與 publisher 尚未定義。

- [ ] **Step 3: 實作明確 step contract 與原子 manifest**

```python
class PipelineStep(Protocol):
    name: str
    required: bool
    max_attempts: int

    def run(self, context: PipelineContext) -> StepResult: ...


@dataclass(frozen=True)
class StepResult:
    name: str
    status: Literal["succeeded", "skipped", "failed"]
    output: dict[str, object]
    error_code: str | None = None
```

Retry 只處理 step 明確標記的 `TransientStepError`，delay 由可注入 clock 執行，公式為
`min(base_seconds * 2 ** (attempt - 1), max_seconds)`；unit test 使用零等待 fake clock。
`MySQLVersionPublisher` 建立 `dataset_versions` 與 `published_datasets`；所有 listing staging rows 帶
`dataset_version`。Pipeline 必須先寫 versioned Parquet、計算 SHA-256，再載入 MySQL staging；
`stage()` 保存 artifact hash 與 row count。`publish(version)` 在一個 transaction 內鎖定 dataset key、
驗證 version=`ready`、Parquet/MySQL row-count 及 hash contract，再更新 active pointer。任何 artifact
或 MySQL 驗證失敗都 rollback／不發布，舊 pointer 必須不變。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_pipeline.py tests/test_publishing.py -q`
Expected: PASS，失敗不切換版本且 transient retry 次數有上限。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/pipeline.py src/qingpu_insight/publishing.py tests/test_pipeline.py tests/test_publishing.py
git commit -m "feat(m4): add staged pipeline publishing"
```

### Task 3: 三類 591 一鍵更新 application service 與 CLI

**Files:**
- Create: `src/qingpu_insight/listing_update.py`
- Modify: `src/qingpu_insight/cli.py`
- Test: `tests/test_listing_update.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ListingSource`、M3 normalize/build functions、`JobService`、`MySQLVersionPublisher`。
- Produces: `ListingUpdateRequest`, `ListingUpdateService.submit/execute`, CLI `listing-update` 與 `job-status`。

- [ ] **Step 1: 寫入三類順序、互斥與 failure isolation 測試**

```python
def test_listing_update_runs_all_types_then_publishes(fake_dependencies) -> None:
    request = ListingUpdateRequest(types=("sale", "newhouse", "rental"), max_pages=1)
    run = fake_dependencies.service.submit(request)
    result = fake_dependencies.service.execute(run.run_id, request)
    assert fake_dependencies.calls == ["sale", "newhouse", "rental", "publish"]
    assert result.status == "succeeded"


def test_incomplete_type_never_publishes(fake_dependencies) -> None:
    fake_dependencies.source.incomplete_type = "newhouse"
    request = ListingUpdateRequest()
    run = fake_dependencies.service.submit(request)
    result = fake_dependencies.service.execute(run.run_id, request)
    assert result.status == "failed"
    assert "publish" not in fake_dependencies.calls
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_listing_update.py tests/test_cli.py -q`
Expected: FAIL，`listing-update` command 與 service 尚不存在。

- [ ] **Step 3: 實作 service 與 CLI 組裝**

```python
@dataclass(frozen=True)
class ListingUpdateRequest:
    types: tuple[ListingType, ...] = ("sale", "newhouse", "rental")
    max_pages: int = 10
    trigger: str = "manual"


class ListingUpdateService:
    def submit(self, request: ListingUpdateRequest) -> JobRun: ...
    def execute(self, run_id: str, request: ListingUpdateRequest) -> JobRun: ...
```

冪等鍵使用 UTC 日期、types、max_pages 與 source config hash；更新前以 Windows-compatible exclusive
lock file 取得全域鎖。CLI：

```powershell
qingpu-data listing-update --types sale newhouse rental --max-pages 10
qingpu-data job-status --run-id <uuid>
```

CLI foreground 執行並輸出安全 JSON 摘要；exit code 0=succeeded、2=already running、1=failed。

- [ ] **Step 4: 執行聚焦與既有 M3 測試**

Run: `python -m pytest tests/test_listing_update.py tests/test_cli.py tests/test_listing_capture.py tests/test_listing_repository.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/listing_update.py src/qingpu_insight/cli.py tests/test_listing_update.py tests/test_cli.py
git commit -m "feat(m4): add one-command 591 update"
```

### Task 4: 非阻塞 Web 工作中心

**Files:**
- Create: `src/qingpu_insight/job_executor.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `src/qingpu_insight/static/app.css`
- Test: `tests/test_job_executor.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `LocalJobExecutor.submit(run_id, callable)`, `POST /api/admin/listing-updates`, `GET /api/jobs/<run_id>`, `GET /api/jobs`。

- [ ] **Step 1: 寫入 202、localhost 與 CSRF 測試**

```python
def test_listing_update_returns_202_without_waiting(client, fake_executor) -> None:
    response = client.post(
        "/api/admin/listing-updates",
        json={"types": ["sale", "newhouse", "rental"], "max_pages": 1},
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    assert response.status_code == 202
    assert response.json["status"] == "pending"
    assert fake_executor.submitted == [response.json["run_id"]]


def test_admin_update_rejects_non_loopback(client) -> None:
    response = client.post("/api/admin/listing-updates", environ_base={"REMOTE_ADDR": "10.0.0.2"})
    assert response.status_code == 403
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_job_executor.py tests/test_web.py -q`
Expected: FAIL，routes 尚不存在。

- [ ] **Step 3: 實作單 worker executor 與 polling UI**

`LocalJobExecutor` 使用 `ThreadPoolExecutor(max_workers=1)`，例外交由 `JobService.fail()` 保存，不在
HTTP response 洩漏 traceback。`create_app()` 注入 `job_service`、`listing_update_service` 與
`job_executor`；POST 驗證 remote address 為 loopback、CSRF header 與 session token 相等。
前端每兩秒 poll job，terminal status 後停止並重新載入 listing summary。

- [ ] **Step 4: 執行 Web、executor 與完整測試**

Run: `python -m pytest tests/test_job_executor.py tests/test_web.py -q`
Expected: PASS，測試不建立真實 thread 或 Chrome。

Run: `python -m pytest -q && python -m ruff check .`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/job_executor.py src/qingpu_insight/web.py src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js src/qingpu_insight/static/app.css tests/test_job_executor.py tests/test_web.py
git commit -m "feat(m4): add local listing update job center"
```
