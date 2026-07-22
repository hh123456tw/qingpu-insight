# 青埔智價 M4.3 維運監控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓資料、工作、模型與備份狀態可觀測，並以真正的隔離還原演練證明備份可用。

**Architecture:** 所有 health result 與 threshold 存 MySQL；checks 只回傳固定 `HealthResult`，不直接發通知。漂移由 deterministic pandas 計算、只警示不自動訓練；backup service 封裝 `mysqldump`／`mysql` process runner 並保存 checksum 與 restore evidence。

**Tech Stack:** Python 3.11、pandas、NumPy、PyMySQL、subprocess、SHA-256、Flask、pytest、PowerShell

## Global Constraints

- MySQL 是 health、drift、backup metadata 與 runtime 狀態的唯一正式 repository。
- 健康狀態由最後成功資料版本計算，不以排程程序 exit code 代替。
- 漂移只能建立 warning／critical，不得自動訓練或發布新模型。
- 還原演練只能使用明確的隔離 database name，禁止覆蓋 production database。
- 日誌、健康 detail 與 process command 不得保存密碼或完整連線 URL。

## File Map

| File | Responsibility |
|---|---|
| `health.py` | Health contracts、thresholds 與 checks |
| `health_repository.py` | MySQL health history/query |
| `drift.py` | Deterministic PSI、missingness 與狀態判定 |
| `drift_repository.py` | MySQL drift report transaction |
| `backups.py` | mysqldump、checksum、isolated restore drill |
| `backup_repository.py` | MySQL backup/restore evidence metadata |
| `cli.py` | 維運 commands 與固定 exit codes |
| `web.py` | 唯讀 ops APIs；不從 HTTP 執行 restore |

---

### Task 1: Health contract、MySQL repository 與核心 checks

**Files:**
- Create: `src/qingpu_insight/health.py`
- Create: `src/qingpu_insight/health_repository.py`
- Create: `tests/test_health.py`
- Create: `tests/test_health_repository.py`

**Interfaces:**
- Produces: `HealthResult`, `HealthCheck`, `HealthService.run_all()`, `MySQLHealthRepository.save/latest/history`。

- [ ] **Step 1: 寫入新鮮度邊界與 exception isolation 測試**

```python
def test_freshness_check_uses_last_success_not_last_attempt() -> None:
    check = FreshnessCheck("listings", warning_after=timedelta(days=7),
                           critical_after=timedelta(days=14))
    result = check.run(HealthContext(now=utc(2026, 7, 22),
                                     last_success=utc(2026, 7, 10),
                                     last_attempt=utc(2026, 7, 22)))
    assert result.status == "warning"


def test_service_converts_check_exception_to_critical(repository) -> None:
    results = HealthService(repository, [ExplodingCheck("mysql")]).run_all()
    assert results[0].status == "critical"
    assert results[0].code == "check_failed"
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_health.py tests/test_health_repository.py -q`
Expected: FAIL，health modules 不存在。

- [ ] **Step 3: 實作 contract、checks 與 MySQL table**

```python
HealthStatus = Literal["healthy", "warning", "critical", "unknown"]

@dataclass(frozen=True)
class HealthResult:
    check_name: str
    status: HealthStatus
    code: str
    checked_at: datetime
    summary: str
    details: dict[str, object]


class HealthCheck(Protocol):
    name: str
    def run(self, context: HealthContext) -> HealthResult: ...
```

實作 `FreshnessCheck`、`MySQLCheck(SELECT 1)`、`DiskSpaceCheck`、`LastJobCheck`；
`MySQLHealthRepository` 建立 `health_results`，JSON detail 使用 MySQL JSON 欄位，所有時間為 UTC。
details 只保存數值、版本與安全 reason code。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_health.py tests/test_health_repository.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/health.py src/qingpu_insight/health_repository.py tests/test_health.py tests/test_health_repository.py
git commit -m "feat(m4): add operational health checks"
```

### Task 2: 資料與模型漂移報告

**Files:**
- Create: `src/qingpu_insight/drift.py`
- Create: `src/qingpu_insight/drift_repository.py`
- Create: `tests/test_drift.py`
- Create: `tests/test_drift_repository.py`

**Interfaces:**
- Produces: `DriftBaseline`, `FeatureDrift`, `DriftReport`, `compute_drift(baseline, current)`、`MySQLDriftRepository`。

- [ ] **Step 1: 寫入 deterministic drift 測試**

```python
def test_numeric_shift_and_missingness_raise_warning() -> None:
    baseline = pd.DataFrame({"building_area_ping": [20, 21, 22, 23],
                             "station_code": ["A17", "A18", "A19", "A18"]})
    current = pd.DataFrame({"building_area_ping": [40, 41, None, 43],
                            "station_code": ["A17", "A17", "A17", "A17"]})
    report = compute_drift(baseline, current, DriftThresholds(psi_warning=0.2,
                                                              missing_delta_warning=0.1))
    assert report.status in {"warning", "critical"}
    assert {item.feature for item in report.features} == {
        "building_area_ping", "station_code"
    }
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_drift.py tests/test_drift_repository.py -q`
Expected: FAIL，drift contract 尚不存在。

- [ ] **Step 3: 實作 PSI、類別分布與 missing delta**

```python
@dataclass(frozen=True)
class FeatureDrift:
    feature: str
    kind: Literal["numeric", "categorical"]
    psi: float
    missing_rate_baseline: float
    missing_rate_current: float
    status: Literal["healthy", "warning", "critical"]


@dataclass(frozen=True)
class DriftReport:
    model_version: str
    dataset_version: str
    computed_at: datetime
    status: Literal["healthy", "warning", "critical"]
    features: tuple[FeatureDrift, ...]
```

Numeric bins 由 baseline quantile 固定，零機率以 `1e-6` 平滑；categorical 包含 `__OTHER__` 與
`__MISSING__`。Repository 建立 `drift_reports`／`drift_features`，一份 report 在同一 transaction 保存。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_drift.py tests/test_drift_repository.py -q`
Expected: PASS；相同輸入重跑結果一致。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/drift.py src/qingpu_insight/drift_repository.py tests/test_drift.py tests/test_drift_repository.py
git commit -m "feat(m4): monitor valuation data drift"
```

### Task 3: MySQL backup、checksum 與隔離還原 drill

**Files:**
- Create: `src/qingpu_insight/backups.py`
- Create: `src/qingpu_insight/backup_repository.py`
- Create: `tests/test_backups.py`
- Create: `tests/test_backup_repository.py`

**Interfaces:**
- Produces: `ProcessRunner.run(args, env)`, `BackupService.create()`, `BackupService.restore_drill()`、`BackupRecord`、`MySQLBackupRepository`。

- [ ] **Step 1: 寫入 command safety 與 production guard 測試**

```python
def test_backup_password_is_environment_not_command(tmp_path, fake_runner) -> None:
    BackupService(fake_runner, settings(tmp_path)).create()
    args, env = fake_runner.calls[0]
    assert all("password" not in value.lower() for value in args)
    assert env["MYSQL_PWD"] == "secret"


def test_restore_drill_rejects_production_database(fake_runner, tmp_path) -> None:
    service = BackupService(fake_runner, settings(tmp_path, database="qingpu_insight"))
    with pytest.raises(UnsafeRestoreTarget):
        service.restore_drill(tmp_path / "dump.sql", target_database="qingpu_insight")
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_backups.py tests/test_backup_repository.py -q`
Expected: FAIL，backup service 尚不存在。

- [ ] **Step 3: 實作不經 shell 的 process runner 與 drill**

`ProcessRunner` 必須使用 `subprocess.run(args: list[str], shell=False, check=True, capture_output=True)`；
dump command 為 `mysqldump --single-transaction --routines --triggers --host ... --user ... db`，stdout
直接寫日期化 `.sql` artifact，另建 SHA-256。Restore target 必須匹配
`qingpu_insight_restore_[0-9]{8}_[0-9]{6}`，流程為 create database、import、執行固定 row-count／
published-version／smoke queries、drop database；`finally` 保證嘗試清理。

```python
@dataclass(frozen=True)
class BackupRecord:
    backup_id: str
    created_at: datetime
    artifact_path: str
    sha256: str
    size_bytes: int
    restore_status: Literal["not_tested", "passed", "failed"]
    restored_at: datetime | None
```

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_backups.py tests/test_backup_repository.py -q`
Expected: PASS，fake runner 證明沒有 shell string 或 command-line password。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/backups.py src/qingpu_insight/backup_repository.py tests/test_backups.py tests/test_backup_repository.py
git commit -m "feat(m4): verify MySQL backups by restore drill"
```

### Task 4: 維運 CLI、API 與 Dashboard

**Files:**
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `src/qingpu_insight/static/app.css`
- Test: `tests/test_cli.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `health-run`、`drift-run`、`backup-create`、`backup-restore-drill` CLI；`GET /api/ops/health`、`GET /api/ops/drift`、`GET /api/ops/backups`。

- [ ] **Step 1: 寫入安全 API 與 CLI exit-code 測試**

```python
def test_health_endpoint_returns_latest_per_check(client) -> None:
    response = client.get("/api/ops/health")
    assert response.status_code == 200
    assert response.json["overall_status"] == "warning"
    assert {item["check_name"] for item in response.json["items"]} >= {"mysql", "listings"}


def test_health_run_returns_two_for_critical(monkeypatch) -> None:
    monkeypatch.setattr(cli, "run_health", lambda *_: "critical")
    assert cli.main(["health-run"]) == 2
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_cli.py tests/test_web.py -q`
Expected: FAIL，commands/routes 尚不存在。

- [ ] **Step 3: 接上 services 與唯讀 dashboard**

CLI exit code 固定為 0=healthy、1=warning／操作失敗、2=critical；`backup-restore-drill` 必須要求
`--confirm-isolated-target`。Web routes 只讀 repository，不從 request 執行 backup 或 drift；畫面顯示
最後成功、最後嘗試、serving version、threshold 與安全 reason，不顯示 DB URL 或 artifact 絕對路徑。

- [ ] **Step 4: 執行 M4.3 gate**

Run: `python -m pytest tests/test_health.py tests/test_drift.py tests/test_backups.py tests/test_cli.py tests/test_web.py -q`
Expected: PASS。

Run: `python -m pytest -q && python -m ruff check . && git diff --check`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/cli.py src/qingpu_insight/web.py src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js src/qingpu_insight/static/app.css tests/test_cli.py tests/test_web.py
git commit -m "feat(m4): expose local operations dashboard"
```
