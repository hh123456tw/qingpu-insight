# 青埔智價前端運維中心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立只限本機的 `/admin` 運維中心，讓單一操作人員不需輸入 CLI 指令，即可安全完成資料、刊登、模型、備份還原與 LLM 維運。

**Architecture:** 沿用既有 Flask、`JobService`、MySQL job repository、`LocalJobExecutor`、刊登 pipeline、候選模型 store 與 `BackupService`。Web 與 CLI 只負責輸入轉換，長工作由共用 service 建立 MySQL job 並交給單一 worker；正式模型與正式資料庫 mutation 另經一次性預覽、確認、備份與驗證。

**Tech Stack:** Python 3.11+、Flask 3.1、PyMySQL、Pandas／PyArrow、scikit-learn／joblib、Pydantic 2、python-dotenv、原生 JavaScript／CSS、Node contract tests、pytest、Ruff、Windows、MySQL 8。

## Global Constraints

- 僅允許 `127.0.0.1`／`::1` 與可信 Host 存取；所有 mutation 必須驗證 `X-Qingpu-CSRF`。
- MySQL 是 job、正式版本 metadata 與操作預覽的唯一 runtime source of truth；MySQL 不可用時不得建立工作。
- 不新增登入、RBAC、排程、通知、多 worker、分散式 queue、任意 CLI／shell 執行或企業級秘密管理。
- 日常操作使用固定後端白名單參數；前端不得提交任意檔案路徑、executable、host、database 或模型類別。
- 模型訓練只建立不可變候選；中古屋與預售屋分開發布、分開回滾，永不自動發布。
- 591 必須使用可見 Chrome；不得規避 CAPTCHA；未完成 batch 不得發布或觸發 delisting。
- 591 開價資料不得進入 M1 官方成交資料或 M2 模型 train／calibration／test frame。
- Gemini Key 僅存 `instance/secrets.env`，該檔案不得進入 Git、備份、log、API response 或下載。
- 正式模型發布／回滾與正式資料庫還原必須 fail closed。
- 每個行為先跑出正確 RED，再做最小 GREEN；不得為測試新增不需要的框架或 production API。
- 保留現有 public API contract；`/admin/models` 在模型 UI 合併完成前仍可使用，完成後才改為 `/admin#models` redirect。
- 每個 Phase 結束先跑聚焦測試、完整 pytest、Ruff、Node contracts 與人工瀏覽器 smoke，再開始下一 Phase。

## File Structure

### New focused modules

- `src/qingpu_insight/admin_dashboard.py` — readiness、總覽卡片與待處理提醒，不執行 mutation。
- `src/qingpu_insight/admin_web.py` — `/admin` 與新增 admin API blueprint；統一 loopback、Host、CSRF 與 JSON error。
- `src/qingpu_insight/official_data.py` — official acquire／analyse／market-build 純流程與 `OfficialDataUpdateService`。
- `src/qingpu_insight/model_release.py` — candidate 驗證、正式模型 version store、發布／回滾 service。
- `src/qingpu_insight/model_release_repository.py` — MySQL 正式模型版本、current pointer、release event。
- `src/qingpu_insight/operation_previews.py` — 一次性、限時、MySQL-backed 高風險操作預覽。
- `src/qingpu_insight/local_secrets.py` — `instance/secrets.env` 的 Gemini Key 狀態、原子寫入與刪除。
- `src/qingpu_insight/provider_ops.py` — Rule／Ollama／Gemini readiness、smoke 與固定 benchmark job orchestration。
- `src/qingpu_insight/static/admin.js` — admin shell、分類載入、job polling 與 sequential listing controller。
- `src/qingpu_insight/static/admin.css` — admin-only responsive layout。
- `src/qingpu_insight/templates/admin.html` — 固定側欄與八個分類 section。
- `database/007_frontend_operations_schema.sql` — model versions／events 與 one-time operation previews。

### Existing modules changed in place

- `src/qingpu_insight/web.py` — compose shared services、register admin blueprint、保留 public routes。
- `src/qingpu_insight/cli.py` — 改呼叫共用 official／model／backup／provider services，不複製流程。
- `src/qingpu_insight/mysql_loader.py` — transaction-safe official market replacement。
- `src/qingpu_insight/backups.py` — backup job wrapper、restore drill job、白名單業務表正式 restore。
- `src/qingpu_insight/model_observatory.py` — read official manifest／release history。
- `src/qingpu_insight/valuation.py` — `ModelRegistry` 優先讀正式 manifest，保留 legacy fallback。
- `src/qingpu_insight/templates/index.html`、`static/app.js`、`static/app.css` — 最終移除維運控制，保留一般使用者功能及 `/admin` 入口。
- `README.md` — 無指令的前端操作、限制、真實驗收與秘密檔案說明。

---

## Phase 1 — 運維中心骨架、診斷與工作紀錄

### Task 1: 建立本機 admin blueprint 與固定側欄

**Files:**
- Create: `src/qingpu_insight/admin_web.py`
- Create: `src/qingpu_insight/templates/admin.html`
- Create: `src/qingpu_insight/static/admin.css`
- Create: `src/qingpu_insight/static/admin.js`
- Create: `tests/js/admin_contract.cjs`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/templates/index.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: existing `_is_trusted_local_request()` behavior and Flask session `_csrf_token`.
- Produces: `create_admin_blueprint(runtime: AdminRuntime) -> Blueprint` and protected `GET /admin`.
- Produces DOM ids: `admin-overview`, `admin-data`, `admin-listings`, `admin-models`, `admin-llm`, `admin-backups`, `admin-jobs`, `admin-diagnostics`.

- [ ] **Step 1: Write the failing route and DOM contract tests**

```python
def test_admin_page_is_local_only(model_admin_client):
    assert model_admin_client.get(
        "/admin", environ_base={"REMOTE_ADDR": "10.0.0.2"}
    ).status_code == 403


def test_admin_page_has_eight_classified_sections(model_admin_client):
    response = model_admin_client.get("/admin")
    soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    assert response.status_code == 200
    assert [node["id"] for node in soup.select("main > section[id]")] == [
        "admin-overview", "admin-data", "admin-listings", "admin-models",
        "admin-llm", "admin-backups", "admin-jobs", "admin-diagnostics",
    ]
    assert soup.select_one('meta[name="csrf-token"]')["content"]
```

```javascript
const assert = require("node:assert/strict");
const admin = require("../../src/qingpu_insight/static/admin.js");
assert.deepEqual(admin.SECTIONS, [
  "overview", "data", "listings", "models",
  "llm", "backups", "jobs", "diagnostics",
]);
assert.equal(admin.normalizeSection("#models"), "models");
assert.equal(admin.normalizeSection("#unknown"), "overview");
```

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py::test_admin_page_is_local_only tests\test_web.py::test_admin_page_has_eight_classified_sections -q
node tests\js\admin_contract.cjs
```

Expected: pytest fails because `/admin` does not exist; Node fails because `admin.js` does not exist.

- [ ] **Step 3: Implement the minimal blueprint and shell**

`admin_web.py` must define the dependency container without importing `web.py`:

```python
@dataclass(frozen=True)
class AdminRuntime:
    job_service: JobService | None
    executor: LocalJobExecutor | None
    listing_update_service: object | None = None
    model_training_service: object | None = None
    model_observatory: object | None = None
    dashboard_service: object | None = None
    official_data_service: object | None = None
    model_release_service: object | None = None
    backup_service: object | None = None
    preview_service: object | None = None
    provider_ops_service: object | None = None
    secrets_store: object | None = None
```

`create_admin_blueprint()` must reject non-loopback remote addresses and untrusted Host before rendering `admin.html`. `admin.html` contains one fixed sidebar link per section and renders only empty loading/status containers; it does not duplicate business data into Jinja.

`admin.js` exports `SECTIONS` and `normalizeSection` under CommonJS and attaches browser initialization only when `document` exists.

- [ ] **Step 4: Register the blueprint and add the homepage entry**

In `create_app()`, translate the existing `AdminServices` into `AdminRuntime`, register the blueprint once, and preserve current `/admin/models` behavior. Place the homepage link inside `header.hero`; do not add admin controls to the public page.

- [ ] **Step 5: Run GREEN and syntax checks**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py::test_admin_page_is_local_only tests\test_web.py::test_admin_page_has_eight_classified_sections -q
node tests\js\admin_contract.cjs
node --check src\qingpu_insight\static\admin.js
.\.venv\Scripts\python.exe -m ruff check src\qingpu_insight\admin_web.py tests\test_web.py
```

Expected: all exit 0.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/admin_web.py src/qingpu_insight/templates/admin.html src/qingpu_insight/static/admin.css src/qingpu_insight/static/admin.js src/qingpu_insight/web.py src/qingpu_insight/templates/index.html tests/test_web.py tests/js/admin_contract.cjs
git commit -m "feat(admin): add local operations center shell"
```

### Task 2: 建立 readiness 與白話總覽 read model

**Files:**
- Create: `src/qingpu_insight/admin_dashboard.py`
- Create: `tests/test_admin_dashboard.py`
- Modify: `src/qingpu_insight/web.py`

**Interfaces:**
- Consumes: `JobService.list_recent(limit)`, health／backup repositories and optional `ModelObservatory.status()`.
- Produces:

```python
@dataclass(frozen=True)
class ReadinessItem:
    code: str
    status: Literal["ready", "warning", "blocked"]
    message: str
    technical: dict[str, str | int | bool | None]


class AdminDashboardService:
    def read(self) -> dict[str, object]:
        raise NotImplementedError
```

- [ ] **Step 1: Write failing read-model tests**

```python
def test_dashboard_blocks_mutations_when_mysql_probe_fails():
    service = AdminDashboardService(
        probes={"mysql": lambda: ReadinessItem(
            "mysql", "blocked", "MySQL 無法連線。", {"reachable": False}
        )},
        jobs=StubJobs([]),
        health_repository=None,
        backup_repository=None,
        model_observatory=None,
    )
    result = service.read()
    assert result["mutation_ready"] is False
    assert result["readiness"][0]["message"] == "MySQL 無法連線。"


def test_dashboard_reports_action_items_without_inventing_status():
    result = dashboard_with(
        mysql_ready=True,
        backup=None,
        model_status={"candidate_count": 1, "official_models": {}},
    ).read()
    assert result["mutation_ready"] is True
    assert [item["code"] for item in result["action_items"]] == [
        "backup_missing", "candidate_waiting_review",
    ]
```

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_admin_dashboard.py -q
```

Expected: import failure for `qingpu_insight.admin_dashboard`.

- [ ] **Step 3: Implement deterministic probes and dashboard composition**

Use injected zero-argument callables for `mysql`, `chrome`, `ollama`, `mysqldump`, `mysql_client`, and required directories. Production probes may use `shutil.which`, `Path.exists`, a MySQL `SELECT 1`, and a bounded HTTP request to the configured loopback Ollama URL. They must never execute installers or start services.

`read()` returns only:

```python
{
    "mutation_ready": bool,
    "readiness": [ReadinessItem-as-dict],
    "active_jobs": [public job dicts],
    "recent_jobs": [public job dicts],
    "health": dict | None,
    "backup": dict | None,
    "models": dict | None,
    "action_items": [{"code": str, "message": str, "section": str}],
}
```

Only MySQL blocks every mutation. Other missing dependencies block their own section and become action items.

- [ ] **Step 4: Compose production probes**

Add `_create_admin_dashboard_service(root, connection_factory, admin_services, ops_services)` in `web.py`. Do not expose connection errors; convert them to the fixed `mysql` blocked item.

- [ ] **Step 5: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_admin_dashboard.py -q
.\.venv\Scripts\python.exe -m ruff check src\qingpu_insight\admin_dashboard.py tests\test_admin_dashboard.py
```

Expected: all exit 0.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/admin_dashboard.py src/qingpu_insight/web.py tests/test_admin_dashboard.py
git commit -m "feat(admin): add readiness and overview read model"
```

### Task 3: 接上總覽、診斷與工作紀錄 API／UI

**Files:**
- Modify: `src/qingpu_insight/admin_web.py`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/admin.js`
- Modify: `src/qingpu_insight/static/admin.css`
- Modify: `tests/test_web.py`
- Modify: `tests/js/admin_contract.cjs`

**Interfaces:**
- Consumes: `AdminRuntime.dashboard_service`, existing `JobService.get/list_recent`.
- Produces:
  - `GET /api/admin/overview`
  - `GET /api/admin/jobs?limit=20&job_type=<optional>`
  - existing `GET /api/jobs/<run_id>` remains canonical detail endpoint.

- [ ] **Step 1: Write failing API tests**

```python
def test_admin_overview_returns_readiness(model_admin_client):
    response = model_admin_client.get("/api/admin/overview")
    assert response.status_code == 200
    assert response.get_json()["mutation_ready"] is True


def test_admin_overview_never_leaks_probe_exception(model_admin_client, monkeypatch):
    monkeypatch.setattr(
        model_admin_client.application.extensions["qingpu_admin_runtime"].dashboard_service,
        "read",
        lambda: (_ for _ in ()).throw(RuntimeError("mysql://user:secret@localhost/db")),
    )
    response = model_admin_client.get("/api/admin/overview")
    assert response.status_code == 503
    assert "secret" not in response.get_data(as_text=True)
```

- [ ] **Step 2: Write failing frontend state tests**

```javascript
assert.deepEqual(
  admin.overviewView({
    mutation_ready: false,
    readiness: [{ code: "mysql", status: "blocked", message: "MySQL 無法連線。" }],
    action_items: [{ code: "mysql", message: "先啟動 MySQL。", section: "diagnostics" }],
  }),
  {
    ready: false,
    headline: "運維功能尚未就緒",
    blockedCodes: ["mysql"],
    actionCount: 1,
  }
);
assert.equal(admin.jobStatusLabel("succeeded"), "成功");
assert.equal(admin.jobStatusLabel("failed"), "失敗");
assert.equal(admin.jobStatusLabel("interrupted"), "已中斷");
```

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py -k "admin_overview" -q
node tests\js\admin_contract.cjs
```

Expected: missing route and missing JS exports.

- [ ] **Step 4: Implement API and UI**

All GET routes require loopback and trusted Host. `/api/admin/jobs` validates `limit` as canonical decimal `1..100` and `job_type` against:

```python
ADMIN_JOB_TYPES = frozenset({
    "official_data_update", "listing_update", "model_training",
    "model_release", "backup_create", "restore_drill",
    "database_restore", "provider_smoke", "llm_benchmark",
})
```

Keep the database `JobStatus` contract unchanged. Public serialization adds `display_status="queued"` for `pending` and `display_status="interrupted"` for `failed + worker_interrupted`; every other job uses its stored status. `admin.js` renders with `textContent`, never source `innerHTML`. It polls only visible active jobs using existing `job_polling.js`, disables mutation buttons when `mutation_ready` is false, and exposes a “技術詳情” `<details>` element.

During production composition, call `recover_interrupted(job_type)` once for every known admin job type before accepting new mutations. Service-specific cleanup must remove only that run's `.tmp-<run_id>` staging directory; the recovered job keeps stable code `worker_interrupted`.

- [ ] **Step 5: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py -k "admin_overview or admin_jobs" -q
node tests\js\admin_contract.cjs
node --check src\qingpu_insight\static\admin.js
```

Expected: all exit 0.

- [ ] **Step 6: Phase 1 verification and browser smoke**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
node tests\js\job_polling_contract.cjs
node tests\js\model_admin_contract.cjs
node tests\js\admin_contract.cjs
```

Open `http://127.0.0.1:5000/admin`; verify all eight nav items, MySQL readiness, recent jobs, mobile sidebar behavior, remote-address rejection, and no console errors.

- [ ] **Step 7: Commit**

```powershell
git add src/qingpu_insight/admin_web.py src/qingpu_insight/templates/admin.html src/qingpu_insight/static/admin.js src/qingpu_insight/static/admin.css tests/test_web.py tests/js/admin_contract.cjs
git commit -m "feat(admin): show readiness and job history"
```

---

## Phase 2 — 官方資料與 591 一鍵更新

### Task 4: 抽出 official data 共用流程並提供原子 MySQL replacement

**Files:**
- Create: `src/qingpu_insight/official_data.py`
- Create: `tests/test_official_data.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/mysql_loader.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class OfficialDataRequest:
    start_season: str
    end_season: str
    start_at: Literal["acquire", "analyse", "market_build", "mysql_publish"] = "acquire"
    trigger: str = "web"


@dataclass(frozen=True)
class OfficialDataResult:
    version: str
    row_count: int
    sha256: str
    minimum_date: str
    maximum_date: str
    quality_path: str


def replace_market_rows(connection: object, frame: pd.DataFrame, version: str) -> int:
    raise NotImplementedError
```

- Consumes: current download／parse／geocode／quality functions and `JobService`.

- [ ] **Step 1: Write RED for atomic replacement**

```python
def test_replace_market_rows_rolls_back_delete_and_insert_together():
    connection = FailingConnection(fail_on_batch=2)
    with pytest.raises(RuntimeError):
        replace_market_rows(connection, market_frame(1500), "v-test")
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_replace_market_rows_records_refresh_only_after_rows():
    connection = RecordingConnection()
    count = replace_market_rows(connection, market_frame(2), "v-test")
    assert count == 2
    assert connection.operation_names == [
        "delete_market_rows", "insert_market_rows", "insert_refresh", "commit",
    ]
```

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_official_data.py -q
```

Expected: import failure for `official_data`.

- [ ] **Step 3: Move official functions without changing CLI contract**

Move the bodies of `acquire`, `_transaction_files`, `analyse`, `market_build` and `mysql_load` from `cli.py` into focused functions:

```python
def acquire_official(root: Path, start: str, end: str) -> None
def analyse_official(root: Path) -> str
def build_official_market(root: Path) -> OfficialDataResult
def publish_official_market(
    root: Path, connection_factory: Callable[[], object], result: OfficialDataResult
) -> OfficialDataResult
```

`analyse_official()` raises `OfficialDataError("feasibility_no_go", "官方資料未通過可行性門檻。")` instead of returning exit code 2. CLI wrappers translate that error back to the existing exit code. Use only fixed settings paths; the Web-facing service never accepts path strings.

- [ ] **Step 4: Implement `replace_market_rows`**

Keep one explicit transaction: delete all `market_transactions`, batch insert all validated rows, insert one `data_refreshes` success record, then commit. Do not call the existing commit-owning `load_market_rows()` from inside this function; factor the batch insert loop into a private `_insert_market_rows()` used by both public loaders.

- [ ] **Step 5: Run GREEN and existing CLI contracts**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_official_data.py tests\test_cli.py -k "acquire or analyse or market_build or mysql_load" -q
.\.venv\Scripts\python.exe -m ruff check src\qingpu_insight\official_data.py src\qingpu_insight\mysql_loader.py src\qingpu_insight\cli.py tests\test_official_data.py
```

Expected: all exit 0 and CLI command names／defaults unchanged.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/official_data.py src/qingpu_insight/mysql_loader.py src/qingpu_insight/cli.py tests/test_official_data.py tests/test_cli.py
git commit -m "refactor(data): share official update workflow"
```

### Task 5: 建立 `OfficialDataUpdateService` job orchestration

**Files:**
- Modify: `src/qingpu_insight/official_data.py`
- Modify: `src/qingpu_insight/web.py`
- Test: `tests/test_official_data.py`

**Interfaces:**
- Produces:

```python
class OfficialDataUpdateService:
    def submit(self, request: OfficialDataRequest) -> JobSubmission:
        raise NotImplementedError
    def handoff(
        self, submission: JobSubmission, request: OfficialDataRequest,
        executor: LocalJobExecutor
    ) -> Future[None]:
        raise NotImplementedError
    def execute(self, run_id: str, request: OfficialDataRequest) -> OfficialDataResult:
        raise NotImplementedError
```

- Uses idempotency key `official_data_update:active`.
- Stages: `acquiring`, `analysing`, `building_market`, `publishing_mysql`, `verifying`.

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_official_service_runs_fixed_stages_and_succeeds(tmp_path):
    jobs = JobService(InMemoryJobRepository())
    runner = RecordingOfficialRunner()
    service = OfficialDataUpdateService(jobs, runner)
    request = OfficialDataRequest("110S3", "115S2")
    run = service.submit(request).run
    jobs.start(run.run_id)
    result = service.execute(run.run_id, request)
    assert runner.calls == [
        "acquire:110S3:115S2", "analyse", "build_market",
        "publish_mysql", "verify",
    ]
    assert jobs.get(run.run_id).status == "succeeded"
    assert jobs.get(run.run_id).output_version == result.version


def test_official_service_stops_before_publish_on_no_go():
    runner = RecordingOfficialRunner(fail_at="analyse")
    service, jobs, run = running_official_service(runner)
    with pytest.raises(OfficialDataError) as caught:
        service.execute(run.run_id, OfficialDataRequest("110S3", "115S2"))
    assert caught.value.error_code == "feasibility_no_go"
    assert "publish_mysql" not in runner.calls
    assert jobs.get(run.run_id).status == "failed"


def test_official_service_can_resume_only_from_fixed_checkpoint():
    runner = RecordingOfficialRunner(existing_inputs=True)
    service, jobs, run = running_official_service(runner)
    request = OfficialDataRequest(
        "110S3", "115S2", start_at="market_build"
    )
    service.execute(run.run_id, request)
    assert runner.calls == [
        "verify_analyse_input", "build_market", "publish_mysql", "verify",
    ]
```

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_official_data.py -k "official_service" -q
```

Expected: missing `OfficialDataUpdateService`.

- [ ] **Step 3: Implement service**

Define an injected `OfficialDataRunner` protocol with five fixed methods rather than arbitrary callable names. On every stage call `jobs.progress()` with the literal current stage and completed-stage list. For `start_at != "acquire"`, call a fixed checkpoint verifier that checks the required upstream manifest／Parquet／quality SHA256 before skipping earlier stages. Catch only `OfficialDataError` for stable safe codes; convert unknown exceptions to `OfficialDataError("official_update_failed", "官方資料更新安全失敗。")`.

Verification must re-read the final Parquet, compare SHA256／row count to `OfficialDataResult`, and query MySQL row count before calling `jobs.succeed`.

Before success, copy the quality JSON to `outputs/admin/official-data/<run_id>/quality.json`, verify it parses as JSON, and store only `{"quality_report": "quality"}` in job summary.

- [ ] **Step 4: Compose production service**

Extend `AdminServices`／`AdminRuntime` with `official_data_service`. Production composition reuses the same `JobService` and `LocalJobExecutor` already used by listing and model jobs.

- [ ] **Step 5: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_official_data.py -q
.\.venv\Scripts\python.exe -m ruff check src\qingpu_insight\official_data.py tests\test_official_data.py
```

Expected: all exit 0.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/official_data.py src/qingpu_insight/web.py tests/test_official_data.py
git commit -m "feat(data): run official updates as tracked jobs"
```

### Task 6: 接上資料中心 API 與一鍵 UI

**Files:**
- Modify: `src/qingpu_insight/admin_web.py`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/admin.js`
- Modify: `tests/test_web.py`
- Modify: `tests/js/admin_contract.cjs`

**Interfaces:**
- Produces: `POST /api/admin/official-data-updates`.
- Produces: `GET /api/admin/official-data-updates/<run_id>/reports/quality`.
- Request body:

```json
{"start_season":"110S3","end_season":"115S2","start_at":"acquire"}
```

- Response: public job plus `created`; `202` for a new active job, `200` for the existing active job.

- [ ] **Step 1: Write failing API validation tests**

```python
def test_official_update_rejects_paths_and_unknown_fields(admin_client):
    response = admin_client.post(
        "/api/admin/official-data-updates",
        json={"start_season": "110S3", "end_season": "115S2", "input": "C:/secret"},
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"]["input"] == "not_allowed"


def test_official_update_accepts_only_fixed_checkpoint_names(admin_client):
    response = admin_client.post(
        "/api/admin/official-data-updates",
        json={
            "start_season": "110S3", "end_season": "115S2",
            "start_at": "C:/processed/transactions.parquet",
        },
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"]["start_at"] == "unsupported"


def test_official_update_returns_existing_active_job(admin_client):
    first = post_official_update(admin_client)
    second = post_official_update(admin_client)
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.get_json()["run_id"] == first.get_json()["run_id"]
```

- [ ] **Step 2: Write failing JS payload and stage tests**

```javascript
assert.deepEqual(admin.buildOfficialUpdatePayload("110S3", "115S2", "acquire"), {
  start_season: "110S3",
  end_season: "115S2",
  start_at: "acquire",
});
assert.throws(
  () => admin.buildOfficialUpdatePayload("115S2", "110S3"),
  /起始季度/
);
assert.equal(admin.stageLabel("publishing_mysql"), "正在發布正式市場資料");
```

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py -k "official_update" -q
node tests\js\admin_contract.cjs
```

Expected: missing route and JS helpers.

- [ ] **Step 4: Implement route and UI**

Allow exactly `start_season`, `end_season` and `start_at`; validate seasons via shared `season_key()` and checkpoint via the literal set `acquire／analyse／market_build／mysql_publish`. Before submit, require `dashboard_service.read()["mutation_ready"] is True`. The default button always sends `start_at=acquire`; an expandable advanced panel exposes the other three fixed checkpoints with their prerequisite status.

The quality-report GET route validates UUID, confirms the job is a succeeded `official_data_update`, resolves only `outputs/admin/official-data/<run_id>/quality.json`, verifies containment below the fixed output root, and sends it as `application/json`. The UI never renders a filesystem path as a link.

- [ ] **Step 5: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py -k "official_update" -q
node tests\js\admin_contract.cjs
```

Expected: all exit 0.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/admin_web.py src/qingpu_insight/templates/admin.html src/qingpu_insight/static/admin.js tests/test_web.py tests/js/admin_contract.cjs
git commit -m "feat(admin): add one-click official data update"
```

### Task 7: 以既有單類型 job 組成 591 三類一鍵更新

**Files:**
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/admin.js`
- Modify: `src/qingpu_insight/static/admin.css`
- Modify: `tests/js/admin_contract.cjs`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: existing `POST /api/admin/listing-updates` and `GET /api/jobs/<run_id>`.
- Produces:

```javascript
async function runListingSequence({
  types, maxPages, submit, waitForTerminal, onTypeStart, onTypeDone,
})
```

- Fixed type order: `sale`, `newhouse`, `rental`.

- [ ] **Step 1: Write failing sequential-controller tests**

```javascript
async function testListingSequenceKeepsTypesIndependent() {
  const submitted = [];
  const result = await admin.runListingSequence({
    types: ["sale", "newhouse", "rental"],
    maxPages: 10,
    submit: async (type) => {
      submitted.push(type);
      return { run_id: `run-${type}`, created: true };
    },
    waitForTerminal: async (runId) => (
      runId === "run-newhouse"
        ? { status: "failed", error_code: "schema_error" }
        : { status: "succeeded", output_version: `v-${runId}` }
    ),
    onTypeStart: () => {},
    onTypeDone: () => {},
  });
  assert.deepEqual(submitted, ["sale", "newhouse", "rental"]);
  assert.equal(result.sale.status, "succeeded");
  assert.equal(result.newhouse.status, "failed");
  assert.equal(result.rental.status, "succeeded");
}
```

- [ ] **Step 2: Run RED**

```powershell
node tests\js\admin_contract.cjs
```

Expected: `runListingSequence is not a function`.

- [ ] **Step 3: Implement sequential controller**

The browser submits exactly one type per job, waits for terminal state, records its result, then continues with the next type even if one failed. This uses the existing server-side global active lock without nested or parent jobs. Reloading the page does not invent remaining jobs; the operator may press “更新未完成類型” based on job history.

- [ ] **Step 4: Add UI and preserve backend whitelist**

Render one status row per type, a max-pages input constrained to `1..50`, “更新三類” and per-type retry buttons. Add API tests confirming one submitted type cannot be outside `sale/newhouse/rental`, multiple types remain supported for backward compatibility, and no profile path／headless flag is accepted.

- [ ] **Step 5: Run GREEN and Phase 2 verification**

```powershell
node tests\js\admin_contract.cjs
.\.venv\Scripts\python.exe -m pytest tests\test_web.py -k "listing_update" -q
.\.venv\Scripts\python.exe -m pytest tests\test_official_data.py tests\test_listing_update.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

Open `/admin#data`, execute against a disposable fixture and verify stage rendering. Open `/admin#listings`, run one single-type fixture success and one fixture schema failure; confirm the previous published type remains visible.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/templates/admin.html src/qingpu_insight/static/admin.js src/qingpu_insight/static/admin.css tests/js/admin_contract.cjs tests/test_web.py
git commit -m "feat(admin): add independent listing update workflow"
```

---

## Phase 3 — 候選模型發布與回滾

### Task 8: 建立正式模型版本 schema、repository 與一次性預覽

**Files:**
- Create: `database/007_frontend_operations_schema.sql`
- Create: `src/qingpu_insight/model_release_repository.py`
- Create: `src/qingpu_insight/operation_previews.py`
- Create: `tests/test_model_release_repository.py`
- Create: `tests/test_operation_previews.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class ModelVersionRecord:
    version_id: str
    market: Literal["resale", "presale"]
    source_run_id: str
    model_name: str
    model_version: str
    artifact_path: str
    artifact_sha256: str
    metadata: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class OperationPreview:
    preview_id: str
    operation: Literal["model_publish", "model_rollback", "database_restore"]
    payload: dict[str, object]
    confirmation_text: str
    expires_at: datetime
    consumed_at: datetime | None
```

- Repository methods:

```python
ModelReleaseRepository.register_version(record) -> None
ModelReleaseRepository.current(market) -> ModelVersionRecord | None
ModelReleaseRepository.activate(market, version_id, job_run_id, action) -> None
ModelReleaseRepository.list_versions(market, limit) -> list[ModelVersionRecord]
OperationPreviewRepository.create(preview) -> None
OperationPreviewRepository.consume(preview_id, confirmation_text, now) -> OperationPreview


class OperationPreviewService:
    def create_for(
        self, operation: str, payload: dict[str, object],
        confirmation_text: str, ttl_seconds: int = 300
    ) -> OperationPreview:
        raise NotImplementedError
    def consume(
        self, preview_id: str, confirmation_text: str
    ) -> OperationPreview:
        raise NotImplementedError
```

- [ ] **Step 1: Write failing migration and repository tests**

```python
def test_frontend_ops_migration_has_market_scoped_current_pointer():
    sql = Path("database/007_frontend_operations_schema.sql").read_text("utf-8")
    assert "CREATE TABLE IF NOT EXISTS model_versions" in sql
    assert "CREATE TABLE IF NOT EXISTS published_models" in sql
    assert "PRIMARY KEY (market)" in sql
    assert "CREATE TABLE IF NOT EXISTS model_release_events" in sql
    assert "CREATE TABLE IF NOT EXISTS operation_previews" in sql


def test_activate_changes_only_requested_market(repository):
    repository.register_version(version("resale", "v-resale"))
    repository.register_version(version("presale", "v-presale"))
    repository.activate("resale", "v-resale", "job-1", "publish")
    assert repository.current("resale").version_id == "v-resale"
    assert repository.current("presale") is None
```

- [ ] **Step 2: Write failing one-time preview tests**

```python
def test_preview_is_single_use_and_requires_exact_text(preview_service, clock):
    preview = preview_service.create_for(
        "model_publish",
        {"market": "resale", "run_id": RUN_ID},
        "發布 resale " + RUN_ID,
        ttl_seconds=300,
    )
    with pytest.raises(PreviewConfirmationMismatch):
        preview_service.consume(preview.preview_id, "發布 resale")
    consumed = preview_service.consume(
        preview.preview_id, "發布 resale " + RUN_ID
    )
    assert consumed.consumed_at is not None
    with pytest.raises(PreviewAlreadyConsumed):
        preview_service.consume(
            preview.preview_id, "發布 resale " + RUN_ID
        )
```

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_release_repository.py tests\test_operation_previews.py -q
```

Expected: missing modules and migration.

- [ ] **Step 4: Implement additive schema and transaction boundaries**

`model_versions` is immutable by `(market, version_id)`; `published_models` contains one pointer per market; `activate()` verifies version market and writes pointer plus event in one transaction. `OperationPreviewService` owns UUID／clock／300-second TTL and delegates persistence. `OperationPreviewRepository.consume()` performs:

```sql
UPDATE operation_previews
SET consumed_at = UTC_TIMESTAMP(3)
WHERE preview_id = %s
  AND confirmation_text = %s
  AND consumed_at IS NULL
  AND expires_at >= UTC_TIMESTAMP(3)
```

If rowcount is zero, re-read the record and return a stable mismatch／expired／consumed error without exposing payload secrets.

- [ ] **Step 5: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_release_repository.py tests\test_operation_previews.py -q
.\.venv\Scripts\python.exe -m ruff check src\qingpu_insight\model_release_repository.py src\qingpu_insight\operation_previews.py tests\test_model_release_repository.py tests\test_operation_previews.py
```

Expected: all exit 0.

- [ ] **Step 6: Commit**

```powershell
git add database/007_frontend_operations_schema.sql src/qingpu_insight/model_release_repository.py src/qingpu_insight/operation_previews.py tests/test_model_release_repository.py tests/test_operation_previews.py
git commit -m "feat(models): persist release versions and previews"
```

### Task 9: 建立正式模型 version store 與 registry manifest

**Files:**
- Create: `src/qingpu_insight/model_release.py`
- Create: `tests/test_model_release.py`
- Modify: `src/qingpu_insight/valuation.py`
- Modify: `tests/test_valuation.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class OfficialModelManifest:
    schema_version: Literal[1]
    market: Literal["resale", "presale"]
    version_id: str
    source_run_id: str
    artifact_file: str
    artifact_sha256: str
    activated_at: datetime


class OfficialModelStore:
    def import_candidate(
        self, candidate_root: Path, training: TrainingManifest, market: str
    ) -> ModelVersionRecord:
        raise NotImplementedError
    def activate(self, market: str, version_id: str) -> OfficialModelManifest:
        raise NotImplementedError
    def current(self, market: str) -> OfficialModelManifest | None:
        raise NotImplementedError
    def load(self, market: str, version_id: str) -> ValuationBundle:
        raise NotImplementedError
```

- Path contract:
  - `artifacts/official/<market>/versions/<version_id>/model.joblib`
  - `artifacts/official/<market>/versions/<version_id>/manifest.json`
  - `artifacts/official/<market>/current.json`

- [ ] **Step 1: Write failing import and isolation tests**

```python
def test_import_candidate_verifies_hash_market_and_gate(tmp_path):
    store = OfficialModelStore(tmp_path / "artifacts")
    record = store.import_candidate(candidate_dir, approved_manifest, "resale")
    assert record.market == "resale"
    assert record.artifact_sha256 == approved_result.artifact_sha256
    assert joblib.load(
        tmp_path / "artifacts" / record.artifact_path
    ).transaction_type == "resale"


def test_activate_resale_does_not_touch_presale_pointer(tmp_path):
    store = populated_official_store(tmp_path)
    presale_before = store.current("presale")
    store.activate("resale", RESALE_VERSION_ID)
    assert store.current("presale") == presale_before
```

- [ ] **Step 2: Write failing registry test**

```python
def test_registry_prefers_official_manifest_over_legacy_file(tmp_path):
    write_legacy_bundle(tmp_path / "resale.joblib", version="legacy")
    write_official_version(tmp_path, market="resale", version="official-v2")
    bundle = ModelRegistry(tmp_path).load("resale")
    assert bundle.model_version == "official-v2"
```

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_release.py tests\test_valuation.py -k "official_manifest or import_candidate or activate_resale" -q
```

Expected: missing `OfficialModelStore` and legacy registry result.

- [ ] **Step 4: Implement immutable import and atomic pointer**

Reject candidate when result is absent, `recommended` is false, hash differs, loaded object is not `ValuationBundle`, or bundle market differs. Copy to `.tmp-<version_id>`, write and re-read manifest, then `os.replace` the directory. Activate by writing `current.json.tmp`, re-reading and loading the target artifact, then `os.replace` to `current.json`.

`ModelRegistry.load()` validates manifest path remains below `artifact_dir`, validates SHA256, then loads. If no current manifest exists, it may load legacy `<market>.joblib`; a corrupt present manifest must fail rather than silently fall back.

- [ ] **Step 5: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_release.py tests\test_valuation.py -q
.\.venv\Scripts\python.exe -m ruff check src\qingpu_insight\model_release.py src\qingpu_insight\valuation.py tests\test_model_release.py tests\test_valuation.py
```

Expected: all exit 0.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/model_release.py src/qingpu_insight/valuation.py tests/test_model_release.py tests/test_valuation.py
git commit -m "feat(models): add immutable official model store"
```

### Task 10: 建立模型發布／回滾 service 與安全 job

**Files:**
- Modify: `src/qingpu_insight/model_release.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/model_observatory.py`
- Modify: `tests/test_model_release.py`
- Modify: `tests/test_model_observatory.py`

**Interfaces:**
- Produces:

```python
class ModelReleaseService:
    def preview_publish(self, run_id: str, market: str) -> OperationPreview:
        raise NotImplementedError
    def preview_rollback(self, market: str, version_id: str) -> OperationPreview:
        raise NotImplementedError
    def submit(self, preview_id: str, confirmation_text: str) -> JobSubmission:
        raise NotImplementedError
    def execute(self, run_id: str, preview: OperationPreview) -> ModelVersionRecord:
        raise NotImplementedError
```

- Uses one active key per market: `model_release:<market>:active`.
- Stages: `validating_candidate`, `backing_up_pointer`, `importing_version`, `activating`, `smoke_testing`, `recording_release`.

- [ ] **Step 1: Write failing publish tests**

```python
def test_publish_requires_recommended_candidate_and_exact_preview():
    service = release_service(candidate=rejected_candidate())
    with pytest.raises(ModelReleaseError) as caught:
        service.preview_publish(RUN_ID, "resale")
    assert caught.value.error_code == "candidate_not_recommended"


def test_publish_preserves_previous_pointer_when_smoke_fails():
    service, repository, store, jobs = running_release_service(smoke_ok=False)
    previous = store.current("resale")
    with pytest.raises(ModelReleaseError):
        service.execute(RELEASE_JOB_ID, approved_publish_preview())
    assert store.current("resale") == previous
    assert jobs.get(RELEASE_JOB_ID).status == "failed"
```

- [ ] **Step 2: Write failing rollback test**

```python
def test_rollback_activates_only_recorded_loadable_version():
    service = release_service_with_history("resale", ["v1", "v2"])
    preview = service.preview_rollback("resale", "v1")
    run = submit_and_start(service, preview)
    result = service.execute(run.run_id, preview)
    assert result.version_id == "v1"
    assert service.current("resale").version_id == "v1"
```

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_release.py -k "publish or rollback" -q
```

Expected: missing `ModelReleaseService`.

- [ ] **Step 4: Implement fail-closed release**

Preview loads the candidate manifest and returns exact confirmation text:

```python
f"發布 {market} {run_id}"
f"回滾 {market} {version_id}"
```

`submit()` consumes the preview before creating a job. `execute()` records the previous version, imports candidate if needed, loads target, runs an injected fixed valuation smoke input for the same market, activates the file pointer, reads it back, then writes the MySQL current pointer／event. If smoke, read-back or repository activation fails after the file pointer changes, atomically restore the previous file pointer; the file pointer and MySQL pointer must never intentionally finish on different versions.

- [ ] **Step 5: Extend observatory**

`ModelObservatory.status()` reads the official store and repository. `get_run()` adds per-market:

```python
{
    "publishable": bool,
    "release_blockers": [str],
    "current_official_version_id": str | None,
}
```

Do not mark a candidate publishable using UI logic.

- [ ] **Step 6: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_release.py tests\test_model_observatory.py -q
.\.venv\Scripts\python.exe -m ruff check src\qingpu_insight\model_release.py src\qingpu_insight\model_observatory.py tests\test_model_release.py tests\test_model_observatory.py
```

Expected: all exit 0.

- [ ] **Step 7: Commit**

```powershell
git add src/qingpu_insight/model_release.py src/qingpu_insight/model_observatory.py src/qingpu_insight/web.py tests/test_model_release.py tests/test_model_observatory.py
git commit -m "feat(models): publish and rollback approved models"
```

### Task 11: 接上模型預覽、確認、發布與回滾 UI

**Files:**
- Modify: `src/qingpu_insight/admin_web.py`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/admin.js`
- Modify: `src/qingpu_insight/static/models_admin.js`
- Modify: `tests/test_web.py`
- Modify: `tests/js/admin_contract.cjs`
- Modify: `tests/js/model_admin_contract.cjs`

**Interfaces:**
- Produces:
  - `POST /api/admin/model-release-previews`
  - `POST /api/admin/model-releases`
  - `GET /api/admin/model-releases?market=resale&limit=20`
- Preview request is exactly one of:

```json
{"action":"publish","market":"resale","run_id":"<uuid>"}
{"action":"rollback","market":"resale","version_id":"<uuid>"}
```

- Execute request:

```json
{"preview_id":"<uuid>","confirmation_text":"發布 resale <run_id>"}
```

- [ ] **Step 1: Write failing API safety tests**

```python
def test_model_release_requires_preview_and_exact_confirmation(admin_client):
    response = admin_client.post(
        "/api/admin/model-releases",
        json={"preview_id": PREVIEW_ID, "confirmation_text": "發布 resale"},
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "confirmation_mismatch"


def test_model_release_rejects_estimator_and_artifact_path(admin_client):
    response = admin_client.post(
        "/api/admin/model-release-previews",
        json={
            "action": "publish", "market": "resale", "run_id": RUN_ID,
            "estimator": "xgboost", "artifact_path": "C:/tmp/model.joblib",
        },
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Write failing JS confirmation tests**

```javascript
assert.deepEqual(
  admin.buildReleasePreviewPayload("publish", "resale", "run-1"),
  { action: "publish", market: "resale", run_id: "run-1" }
);
assert.equal(
  admin.canConfirmDangerousAction("發布 resale run-1", "發布 resale run-1"),
  true
);
assert.equal(
  admin.canConfirmDangerousAction("發布 resale", "發布 resale run-1"),
  false
);
```

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py -k "model_release" -q
node tests\js\admin_contract.cjs
```

Expected: missing routes and JS helpers.

- [ ] **Step 4: Implement two-step UI**

The first click fetches server preview and renders source／target versions, metrics, blockers and exact confirmation text. The second button remains disabled until the typed value matches byte-for-byte. Submission sends only preview id and confirmation text. Poll the returned `model_release` job and refresh model status after terminal state.

Move existing model observatory rendering into `#admin-models`; keep pure helpers in `models_admin.js`. Only after contract and browser smoke pass, change `/admin/models` to `302 /admin#models`.

- [ ] **Step 5: Run GREEN and Phase 3 verification**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py -k "model_admin or model_release" -q
.\.venv\Scripts\python.exe -m pytest tests\test_model_release.py tests\test_model_observatory.py tests\test_valuation.py -q
node tests\js\model_admin_contract.cjs
node tests\js\admin_contract.cjs
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

Browser smoke:

1. train a resale-only candidate;
2. verify no automatic official change;
3. preview and publish it;
4. verify homepage resale valuation reports the new version;
5. rollback;
6. verify presale current version never changed.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/admin_web.py src/qingpu_insight/templates/admin.html src/qingpu_insight/static/admin.js src/qingpu_insight/static/models_admin.js src/qingpu_insight/web.py tests/test_web.py tests/js/admin_contract.cjs tests/js/model_admin_contract.cjs
git commit -m "feat(admin): manage model release lifecycle"
```

---

## Phase 4 — 備份還原、Gemini 設定與 LLM benchmark

### Task 12: 將備份建立與隔離還原演練接入 job

**Files:**
- Modify: `src/qingpu_insight/backups.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/admin_web.py`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/admin.js`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Produces:

```python
class BackupJobService:
    def submit_create(self) -> JobSubmission:
        raise NotImplementedError
    def submit_restore_drill(self, backup_id: str) -> JobSubmission:
        raise NotImplementedError
    def execute_create(self, run_id: str) -> BackupRecord:
        raise NotImplementedError
    def execute_restore_drill(self, run_id: str, backup_id: str) -> RestoreEvidence:
        raise NotImplementedError
```

- Active keys: `backup_create:active`, `restore_drill:active`.
- Routes:
  - `POST /api/ops/backups`
  - `POST /api/ops/backups/<backup_id>/restore-drills`

- [ ] **Step 1: Write failing job tests**

```python
def test_backup_job_succeeds_only_for_completed_nonempty_dump():
    service, jobs = backup_job_service(backup_status="completed", size_bytes=100)
    run = start(service.submit_create(), jobs)
    record = service.execute_create(run.run_id)
    assert jobs.get(run.run_id).status == "succeeded"
    assert jobs.get(run.run_id).output_version == record.backup_id


def test_restore_drill_job_fails_on_checksum_mismatch():
    service, jobs = restore_drill_job_service(checksum_ok=False)
    run = start(service.submit_restore_drill(BACKUP_ID), jobs)
    with pytest.raises(ValueError, match="Checksum mismatch"):
        service.execute_restore_drill(run.run_id, BACKUP_ID)
    assert jobs.get(run.run_id).status == "failed"
```

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_backups.py -k "backup_job or restore_drill_job" -q
```

Expected: missing `BackupJobService`.

- [ ] **Step 3: Implement service and routes**

Use the existing `BackupService.create()` and `restore_drill()`; do not duplicate runner args. API accepts no body for create and no path／database fields for drill. Return `202` job records. Replace the current intentional `405` only after local／CSRF tests exist.

- [ ] **Step 4: Add UI**

Show backup id, created time, size, abbreviated SHA256, restore drill status, “建立備份” and “隔離還原演練”. The browser never sees `path`.

- [ ] **Step 5: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_backups.py tests\test_web.py -k "backup or restore_drill" -q
node tests\js\admin_contract.cjs
```

Expected: all exit 0.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/backups.py src/qingpu_insight/web.py src/qingpu_insight/admin_web.py src/qingpu_insight/templates/admin.html src/qingpu_insight/static/admin.js tests/test_backups.py tests/test_web.py
git commit -m "feat(ops): run backup and restore drills from admin"
```

### Task 13: 實作保留控制表的正式資料庫還原

**Files:**
- Modify: `src/qingpu_insight/backups.py`
- Modify: `src/qingpu_insight/admin_web.py`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/admin.js`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Produces:

```python
RESTORABLE_TABLES: tuple[str, ...] = (
    "data_refreshes",
    "market_transactions",
    "listing_batches",
    "listing_snapshots",
    "listing_current",
    "listing_events",
    "listing_valuations",
    "dataset_versions",
    "dataset_version_batches",
    "dataset_version_rows",
    "dataset_version_events",
    "published_datasets",
    "buyer_reports",
)


@dataclass(frozen=True)
class ProductionRestoreResult:
    source_backup_id: str
    safety_backup_id: str
    restored_tables: tuple[str, ...]
    health_status: str


class ProductionRestoreService:
    def preview(self, backup_id: str) -> OperationPreview:
        raise NotImplementedError
    def submit(self, preview_id: str, confirmation_text: str) -> JobSubmission:
        raise NotImplementedError
    def execute(
        self, run_id: str, preview: OperationPreview
    ) -> ProductionRestoreResult:
        raise NotImplementedError
```

- Routes:
  - `POST /api/ops/restore-previews`
  - `POST /api/ops/restores`

- [ ] **Step 1: Write failing table-boundary tests**

```python
def test_restore_table_whitelist_excludes_control_plane():
    assert "market_transactions" in RESTORABLE_TABLES
    assert "listing_current" in RESTORABLE_TABLES
    assert "buyer_reports" in RESTORABLE_TABLES
    assert "job_runs" not in RESTORABLE_TABLES
    assert "backup_records" not in RESTORABLE_TABLES
    assert "health_runs" not in RESTORABLE_TABLES
    assert "model_versions" not in RESTORABLE_TABLES
    assert "operation_previews" not in RESTORABLE_TABLES
    assert "geocode_cache" not in RESTORABLE_TABLES
```

- [ ] **Step 2: Write failing pre-backup and rollback tests**

```python
def test_production_restore_stops_when_safety_backup_fails():
    service = production_restore_service(safety_backup_status="dump_failed")
    with pytest.raises(ProductionRestoreError) as caught:
        service.execute(RESTORE_JOB_ID, valid_restore_preview())
    assert caught.value.error_code == "safety_backup_failed"
    assert service.runner.rename_calls == []


def test_production_restore_preserves_recovery_assets_when_health_fails():
    service = production_restore_service(post_swap_health="critical")
    with pytest.raises(ProductionRestoreError) as caught:
        service.execute(RESTORE_JOB_ID, valid_restore_preview())
    assert service.runner.swap_calls == ["forward"]
    assert caught.value.error_code == "post_restore_health_failed"
    assert service.runner.dropped_databases == []
```

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_backups.py -k "production_restore or restore_table_whitelist" -q
```

Expected: missing service and whitelist.

- [ ] **Step 4: Implement isolated import and atomic business-table swap**

Exact flow:

1. consume preview with confirmation `還原資料庫 <backup_id>`;
2. verify selected backup record, file name, containment and SHA256;
3. ensure no other active mutation job except this restore job;
4. call `BackupService.create()` and require `completed` plus non-empty SHA256;
5. import selected dump into `qingpu_restore_stage_<16 hex>`;
6. verify every `RESTORABLE_TABLES` table exists and run fixed row-count checks;
7. create `qingpu_restore_rollback_<16 hex>`;
8. execute one MySQL `RENAME TABLE` statement that moves current business tables to rollback DB and stage business tables to production DB;
9. run `HealthService` plus fixed market／listing row-count smoke;
10. on health failure, fail the job without starting a second destructive action; retain the rollback database and safety backup ID in the safe error details for explicit operator recovery;
11. on success, drop stage／rollback databases and succeed the job.

Use `validate_restore_database()`-style strict regexes for both temporary database names. Never restore `job_runs`, backup／health tables, model release tables or operation previews, so the control-plane job remains observable. `geocode_cache` is a rebuildable derived cache and is deliberately excluded from the formal restore boundary.

- [ ] **Step 5: Implement API and confirmation UI**

Preview response displays backup time, SHA256, safety-backup requirement, exact business table list and confirmation text. Execute accepts only `preview_id` and exact confirmation. No API accepts database or path.

- [ ] **Step 6: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_backups.py tests\test_web.py -k "production_restore or restore_preview" -q
.\.venv\Scripts\python.exe -m ruff check src\qingpu_insight\backups.py tests\test_backups.py
```

Expected: all exit 0.

- [ ] **Step 7: Commit**

```powershell
git add src/qingpu_insight/backups.py src/qingpu_insight/admin_web.py src/qingpu_insight/templates/admin.html src/qingpu_insight/static/admin.js tests/test_backups.py tests/test_web.py
git commit -m "feat(ops): add guarded production restore"
```

### Task 14: 建立精簡 Gemini secrets 與 provider smoke

**Files:**
- Create: `src/qingpu_insight/local_secrets.py`
- Create: `src/qingpu_insight/provider_ops.py`
- Create: `tests/test_local_secrets.py`
- Create: `tests/test_provider_ops.py`
- Modify: `tests/test_report_service.py`
- Modify: `.gitignore`
- Modify: `src/qingpu_insight/report_composition.py`
- Modify: `src/qingpu_insight/report_service.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/admin_web.py`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/admin.js`
- Modify: `tests/test_web.py`

**Interfaces:**
- Produces:

```python
class LocalSecretsStore:
    def status(self) -> dict[str, bool]:
        raise NotImplementedError
    def set_gemini_key(self, key: str) -> None:
        raise NotImplementedError
    def delete_gemini_key(self) -> None:
        raise NotImplementedError
    def merged_env(self, base: Mapping[str, str]) -> dict[str, str]:
        raise NotImplementedError


class ProviderOpsService:
    def status(self) -> list[dict[str, object]]:
        raise NotImplementedError
    def submit_smoke(self, provider: Literal["rule", "ollama", "gemini"]) -> JobSubmission:
        raise NotImplementedError
    def execute_smoke(self, run_id: str, provider: str) -> dict[str, object]:
        raise NotImplementedError
```

- Routes:
  - `GET /api/admin/providers`
  - `PUT /api/admin/providers/gemini-key`
  - `DELETE /api/admin/providers/gemini-key`
  - `POST /api/admin/provider-smoke-runs`

- [ ] **Step 1: Write failing secrets tests**

```python
def test_secret_store_writes_atomically_and_never_returns_key(tmp_path):
    store = LocalSecretsStore(tmp_path / "instance" / "secrets.env")
    store.set_gemini_key("free-demo-key")
    assert store.status() == {"gemini_configured": True}
    assert "free-demo-key" not in repr(store.status())
    assert not (tmp_path / "instance" / "secrets.env.tmp").exists()


def test_delete_gemini_key_preserves_supported_nonsecret_lines(tmp_path):
    path = tmp_path / "instance" / "secrets.env"
    path.parent.mkdir()
    path.write_text(
        "QINGPU_GEMINI_API_KEY=old\nQINGPU_OLLAMA_MODEL=qwen-demo\n",
        encoding="utf-8",
    )
    LocalSecretsStore(path).delete_gemini_key()
    assert path.read_text("utf-8") == "QINGPU_OLLAMA_MODEL=qwen-demo\n"
```

- [ ] **Step 2: Write failing provider redaction tests**

```python
def test_provider_smoke_failure_redacts_key():
    service, jobs = provider_service(
        gemini_error="request failed api_key=free-demo-key"
    )
    run = start(service.submit_smoke("gemini"), jobs)
    with pytest.raises(ProviderOpsError):
        service.execute_smoke(run.run_id, "gemini")
    public = jobs.get(run.run_id)
    assert "free-demo-key" not in (public.error_message or "")


def test_report_service_sees_key_added_after_app_start(tmp_path):
    store = LocalSecretsStore(tmp_path / "instance" / "secrets.env")
    resolver = create_dynamic_provider_resolver(store, BASE_ENV)
    assert resolver("gemini") is None
    store.set_gemini_key("free-demo-key")
    assert resolver("gemini") is not None
```

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_local_secrets.py tests\test_provider_ops.py tests\test_report_service.py -q
```

Expected: missing modules.

- [ ] **Step 4: Implement secrets and provider service**

Add `/instance/secrets.env` to `.gitignore`. Parse only `KEY=VALUE` lines with supported names. Reject blank Gemini values, newlines, NUL and values over 4096 characters. Write a same-directory temp file with UTF-8 and `os.replace`.

`merged_env()` overlays the local Gemini Key onto a copy of process env for provider construction; never mutate `os.environ`. Add `create_dynamic_provider_resolver(store, base_env) -> Callable[[str], ReportProvider | None]` in `report_composition.py` and a `provider_resolver` constructor dependency to `ReportService`; production resolution builds the requested provider from `store.merged_env(base_env)` at request time, so adding／replacing／deleting a key works without restarting Flask. Preserve the existing static provider-dict constructor path for focused tests and CLI compatibility.

Provider smoke uses fixed anonymous `EvidencePack` data already used by `llm-smoke`; the API accepts no prompt, model path, base URL or case data. Rule has no network. Ollama uses configured loopback URL and model. Gemini uses configured Key and existing provider.

- [ ] **Step 5: Implement API／UI and run GREEN**

Key PUT body is exactly `{"key":"free-demo-key"}` in the contract example. Responses are always status-only. UI uses a password input that clears after submission, plus set／replace／delete and test buttons.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_local_secrets.py tests\test_provider_ops.py tests\test_report_service.py tests\test_web.py -k "provider or gemini_key" -q
node tests\js\admin_contract.cjs
.\.venv\Scripts\python.exe -m ruff check src\qingpu_insight\local_secrets.py src\qingpu_insight\provider_ops.py tests\test_local_secrets.py tests\test_provider_ops.py
```

Expected: all exit 0 and no test output contains fixture keys.

- [ ] **Step 6: Commit**

```powershell
git add .gitignore src/qingpu_insight/local_secrets.py src/qingpu_insight/provider_ops.py src/qingpu_insight/report_composition.py src/qingpu_insight/report_service.py src/qingpu_insight/web.py src/qingpu_insight/admin_web.py src/qingpu_insight/templates/admin.html src/qingpu_insight/static/admin.js tests/test_local_secrets.py tests/test_provider_ops.py tests/test_report_service.py tests/test_web.py
git commit -m "feat(llm): manage local providers from admin"
```

### Task 15: 接入固定案例 LLM benchmark job

**Files:**
- Modify: `src/qingpu_insight/provider_ops.py`
- Modify: `src/qingpu_insight/admin_web.py`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/admin.js`
- Modify: `tests/test_provider_ops.py`
- Modify: `tests/test_web.py`
- Modify: `tests/js/admin_contract.cjs`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class BenchmarkRequest:
    provider: Literal["ollama", "gemini"]
    model: str


ProviderOpsService.submit_benchmark(request) -> JobSubmission
ProviderOpsService.execute_benchmark(run_id, request) -> dict[str, object]
```

- Route: `POST /api/admin/llm-benchmark-runs`.
- Route: `GET /api/admin/llm-benchmark-runs/<run_id>/reports/<report_type>`.
- Fixed cases: `benchmarks/m44_cases.json`.
- Fixed output root: `outputs/m44-benchmark/<run_id>/`.
- Allowed reports:
  - `json` → `benchmark_results.json`
  - `markdown` → `benchmark_results.md`

- [ ] **Step 1: Write failing whitelist tests**

```python
def test_benchmark_request_cannot_choose_case_or_output_path(admin_client):
    response = admin_client.post(
        "/api/admin/llm-benchmark-runs",
        json={
            "provider": "ollama", "model": "qwen-demo",
            "cases": "C:/private.json", "output_dir": "C:/tmp",
        },
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 400


def test_benchmark_uses_fixed_cases_and_run_directory(tmp_path):
    service, jobs, runner = benchmark_service(tmp_path)
    run = start(service.submit_benchmark(BenchmarkRequest("ollama", "qwen-demo")), jobs)
    service.execute_benchmark(run.run_id, BenchmarkRequest("ollama", "qwen-demo"))
    assert runner.cases_path == tmp_path / "benchmarks" / "m44_cases.json"
    assert runner.output_dir == tmp_path / "outputs" / "m44-benchmark" / run.run_id
```

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_provider_ops.py tests\test_web.py -k "benchmark" -q
```

Expected: missing service methods and route.

- [ ] **Step 3: Implement service and API**

Model must appear in the backend provider status list. Gemini uses one backend-defined model id; Ollama models come from the configured loopback `/api/tags`. Job summary exposes aggregate scores and whitelist report ids, not raw prompts or file paths.

- [ ] **Step 4: Add benchmark history UI**

Show provider, model, schema success, fact accuracy, required-section success, p50／p95 latency, started time and run status. The report GET route validates run UUID and `report_type`, requires a succeeded `llm_benchmark` job, resolves the two fixed filenames below `outputs/m44-benchmark/<run_id>/`, verifies containment, and rejects every other name or path.

- [ ] **Step 5: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_provider_ops.py tests\test_web.py -k "benchmark" -q
node tests\js\admin_contract.cjs
```

Expected: all exit 0.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/provider_ops.py src/qingpu_insight/admin_web.py src/qingpu_insight/templates/admin.html src/qingpu_insight/static/admin.js tests/test_provider_ops.py tests/test_web.py tests/js/admin_contract.cjs
git commit -m "feat(llm): run fixed benchmarks from admin"
```

### Task 16: 收斂首頁、文件與完整真實驗收

**Files:**
- Modify: `.gitignore`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `src/qingpu_insight/static/app.css`
- Modify: `README.md`
- Modify: `tests/test_web.py`
- Modify: `tests/js/admin_contract.cjs`

**Interfaces:**
- Public homepage retains market dashboard, listings, valuation, buyer report and `/admin` link.
- Public homepage removes `job-panel` and `ops-panel`.
- `/admin` becomes the sole mutation UI.

- [ ] **Step 1: Write failing homepage separation test**

```python
def test_homepage_keeps_user_features_and_moves_ops_to_admin(client):
    home = BeautifulSoup(client.get("/").get_data(as_text=True), "html.parser")
    admin = BeautifulSoup(client.get("/admin").get_data(as_text=True), "html.parser")
    assert home.select_one("#valuation-form") is not None
    assert home.select_one("#report-form") is not None
    assert home.select_one("#job-submit") is None
    assert home.select_one(".ops-panel") is None
    assert home.select_one('a[href="/admin"]') is not None
    assert admin.select_one("#admin-data") is not None
    assert admin.select_one("#admin-backups") is not None
```

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web.py::test_homepage_keeps_user_features_and_moves_ops_to_admin -q
```

Expected: homepage still contains current job／ops controls.

- [ ] **Step 3: Remove duplicate public controls**

Delete only the admin update and ops markup plus their now-unused public-page event handlers. Keep market, listing, valuation and report code unchanged. Preserve the fixed Leaflet CSS behavior and marker URLs.

- [ ] **Step 4: Update README**

Document:

- starting `qingpu-web.exe`;
- `/admin` eight categories;
- MySQL-required behavior;
- one-click data／listing workflow;
- candidate-only training and independent publish／rollback;
- backup drill and guarded production restore;
- Rule／Ollama／Gemini setup;
- `instance/secrets.env` local-project security limitation;
- no scheduler／login／remote access;
- generated data、candidate、official version、backup and benchmark paths;
- exact five real acceptance flows.

- [ ] **Step 5: Run all automated verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
node tests\js\job_polling_contract.cjs
node tests\js\model_admin_contract.cjs
node tests\js\admin_contract.cjs
node --check src\qingpu_insight\static\app.js
node --check src\qingpu_insight\static\admin.js
node --check src\qingpu_insight\static\models_admin.js
```

Expected: every command exits 0. Warnings may be recorded, but no failure is accepted.

- [ ] **Step 6: Execute the five real browser acceptance flows**

With a disposable verified MySQL backup available:

1. run one official data update and verify row count／date／SHA256;
2. run sale／newhouse／rental updates and verify a forced fixture failure preserves the previous type;
3. train one market candidate, publish it, verify valuation version, and rollback;
4. create backup, run isolated restore drill, create production-restore preview, verify wrong confirmation is rejected, then run the approved disposable restore;
5. verify Rule status, one available Ollama or Gemini smoke, and one fixed benchmark.

Save only safe JSON／Markdown summaries under `outputs/m45-admin-acceptance/`; do not save API keys, Cookie, database URL, raw HTML, full addresses, dumps, data or model binaries. Add `/outputs/m45-admin-acceptance/` to `.gitignore`; the committed plan and README are the durable audit record, while machine-specific acceptance evidence stays local.

- [ ] **Step 7: Review dirty-worktree boundaries**

```powershell
git status --short
git diff --check
git diff --stat
```

Confirm generated `candidates/`, data, backup, benchmark and acceptance artifacts remain untracked／ignored as intended. Do not stage unrelated pre-existing M4.4 edits.

- [ ] **Step 8: Commit**

```powershell
git add .gitignore src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js src/qingpu_insight/static/app.css README.md tests/test_web.py tests/js/admin_contract.cjs
git commit -m "docs(admin): complete operations center acceptance"
```

## Final Completion Gate

- [ ] Phase 1 shell／readiness／jobs passed full tests and browser smoke.
- [ ] Phase 2 official data and all three listing types can be operated without CLI.
- [ ] Phase 3 candidate training remains non-publishing; each market publishes and rolls back independently.
- [ ] Phase 4 backup、restore drill、guarded production restore、provider secret、smoke and benchmark can be operated without CLI.
- [ ] Public homepage contains no mutation／ops controls and still supports market、listing、valuation and buyer report flows.
- [ ] MySQL unavailable state is visible and disables every mutation.
- [ ] No route accepts arbitrary path、host、database、executable、prompt、case file、model class or estimator.
- [ ] No API／job／log／artifact leaks Gemini Key、Cookie、database password、raw HTML or full address.
- [ ] Full pytest、Ruff、three Node contracts and three JavaScript syntax checks exit 0.
- [ ] Five real acceptance summaries exist and contain no secret or disallowed artifact.
