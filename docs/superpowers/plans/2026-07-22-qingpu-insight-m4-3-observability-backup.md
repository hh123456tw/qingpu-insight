# 青埔智價 M4.3 Lite 健康與備份 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以一個本機健康摘要回答系統是否正常，並以手動 MySQL 備份及隔離還原演練證明資料可恢復。

**Architecture:** `HealthService` 從 MySQL 已發布版本、工作紀錄、備份 metadata 與本機磁碟產生固定 `HealthSummary`；不建立漂移或通知平台。`BackupService` 透過可替換 `ProcessRunner` 執行 `mysqldump`／`mysql`，保存 SHA-256 與 restore evidence；Web 僅讀狀態，所有破壞性操作只存在 CLI。

**Tech Stack:** Python 3.11、PyMySQL、Flask、subprocess、SHA-256、pytest、Ruff

## Global Constraints

- 實作前先完成 M4.2 真實 MySQL、可見 Selenium、三類 591 手動驗收並保存結果。
- MySQL 是健康歷史與備份 metadata 的唯一 runtime repository。
- M4.3 Lite 不實作模型漂移、通知、Windows Task Scheduler、可編輯 threshold 或自動修復。
- `backup-create` 與 `backup-restore-drill` 只能由本機 CLI 執行；Web 不得提供對應 POST。
- MySQL 密碼只經 child-process environment 傳入，不得出現在 args、log、例外、API 或資料表。
- restore 只能指向 `qingpu_restore_drill_` 前綴的隔離資料庫，永遠不得覆寫 production database。
- 所有 production code 先新增能因正確原因失敗的 regression test，再寫最小實作。

## File Map

| File | Responsibility |
|---|---|
| `src/qingpu_insight/health.py` | 健康 contracts、門檻與摘要組合 |
| `src/qingpu_insight/health_repository.py` | MySQL health summary history |
| `src/qingpu_insight/backups.py` | dump、checksum、restore drill 與 process abstraction |
| `src/qingpu_insight/backup_repository.py` | MySQL backup／restore metadata |
| `database/005_m43_health_backup_schema.sql` | health 與 backup metadata schema |
| `src/qingpu_insight/cli.py` | `health-run`、`backup-create`、`backup-restore-drill` |
| `src/qingpu_insight/web.py` | 本機唯讀 `/api/ops/health`、`/api/ops/backups` |
| `tests/test_m43_release_gate.py` | M4.3 Lite deterministic release gate |

---

### Task 1: 固定健康 contract、核心 checks 與 MySQL repository

**Files:**
- Create: `src/qingpu_insight/health.py`
- Create: `src/qingpu_insight/health_repository.py`
- Create: `database/005_m43_health_backup_schema.sql`
- Create: `tests/test_health.py`
- Create: `tests/test_health_repository.py`

**Interfaces:**
- Consumes: `JobService.list_recent(limit)`, `MySQLVersionPublisher.current()` 與 listing runtime repository count query。
- Produces: `HealthStatus = Literal["healthy", "warning", "critical"]`。
- Produces: `HealthItem(code, status, observed_at, summary, value, unit)`。
- Produces: `HealthSummary(status, checked_at, items)`。
- Produces: `HealthProbes.mysql()`, `market_dataset()`, `listing_dataset(listing_type)`, `latest_listing_job()`, `latest_backup()` 與 `disk_free()`；每個方法只回傳 allowlisted observation。
- Produces: `HealthService.run() -> HealthSummary`。
- Produces: `MySQLHealthRepository.save(summary)` 與 `latest()`。

- [ ] **Step 1: 建立失敗的 contract 與狀態聚合測試**

```python
def test_summary_uses_worst_item_status() -> None:
    summary = summarize_health([
        HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean"),
        HealthItem("listing_sale_freshness", "warning", NOW, "stale", 30, "hours"),
    ], checked_at=NOW)
    assert summary.status == "warning"


def test_no_successful_listing_version_is_critical() -> None:
    service = HealthService(probes=FakeProbes(current_listing=None))
    result = service.run()
    assert item(result, "listing_dataset").status == "critical"
```

- [ ] **Step 2: 執行 RED**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
.\.venv\Scripts\python.exe -m pytest tests/test_health.py -q
```

Expected: collection fails because `qingpu_insight.health` does not exist.

- [ ] **Step 3: 實作最小 contract 與固定門檻**

固定檢查：

```python
DEFAULT_THRESHOLDS = {
    "market_freshness_warning_hours": 24 * 45,
    "listing_freshness_warning_hours": 24 * 7,
    "disk_warning_bytes": 10 * 1024**3,
    "disk_critical_bytes": 2 * 1024**3,
}
```

檢查項目只包含 `mysql`、`market_dataset`、三類 listing、`latest_listing_job`、`latest_backup`、`disk_free`。probe 失敗回固定安全摘要，不保存 DB URL 或原始 SQL。

- [ ] **Step 4: 建立 repository 與 migration RED**

測試 migration 包含 `health_runs`、`health_items`、`backup_records`，並驗證：

```python
saved = repository.save(summary)
assert repository.latest() == saved
assert connection.commits == 1
assert connection.rollbacks == 0
assert connection.closed is True
```

- [ ] **Step 5: 實作 transaction-safe repository**

repository 接受 connection factory，每次操作建立並關閉一條連線。`health_runs.run_id` 使用 UUID，`health_items` 以 `(run_id, code)` 為主鍵；JSON detail 只保存 allowlisted safe fields。

- [ ] **Step 6: 執行 GREEN 與 lint**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_health.py tests/test_health_repository.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/health.py src/qingpu_insight/health_repository.py tests/test_health.py tests/test_health_repository.py
```

- [ ] **Step 7: 提交**

```powershell
git add database/005_m43_health_backup_schema.sql src/qingpu_insight/health.py src/qingpu_insight/health_repository.py tests/test_health.py tests/test_health_repository.py
git commit -m "feat(m43): add local health summary"
```

### Task 2: 手動 MySQL dump、checksum 與 metadata

**Files:**
- Create: `src/qingpu_insight/backups.py`
- Create: `src/qingpu_insight/backup_repository.py`
- Create: `tests/test_backups.py`
- Create: `tests/test_backup_repository.py`
- Modify: `database/005_m43_health_backup_schema.sql`

**Interfaces:**
- Consumes: Task 1 migration 與 connection factory。
- Produces: `ProcessResult(returncode, stdout, stderr)`。
- Produces: `ProcessRunner.run(args: Sequence[str], env: Mapping[str, str]) -> ProcessResult`。
- Produces: `BackupRecord(backup_id, status, path, sha256, size_bytes, created_at, restore_status, restore_checked_at)`。
- Produces: `BackupService.create() -> BackupRecord`。
- Produces: `MySQLBackupRepository.create(record)`、`mark_restore(...)`、`latest()`、`list_recent(limit)`。

- [ ] **Step 1: 建立 dump 命令與 secret RED**

```python
def test_backup_uses_env_password_and_argument_array(tmp_path: Path) -> None:
    runner = RecordingRunner()
    service = BackupService(config=CONFIG, runner=runner, repository=REPO, backup_dir=tmp_path)
    record = service.create()
    assert runner.args[0] == "mysqldump"
    assert "--result-file" in runner.args
    assert all("password" not in arg.lower() for arg in runner.args)
    assert runner.env["MYSQL_PWD"] == "secret"
    assert "secret" not in repr(record)
```

- [ ] **Step 2: 執行 RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_backups.py tests/test_backup_repository.py -q
```

Expected: FAIL because backup service and repository do not exist.

- [ ] **Step 3: 實作 dump 與 SHA-256**

使用 `subprocess.run(args, env=..., capture_output=True, text=True, shell=False)`。dump 先寫 f-string `f"{backup_id}.sql.partial"`，process 成功且檔案非空後計算 SHA-256，再以 `os.replace` 發布為 `f"{backup_id}.sql"`。失敗時刪除 partial，保存固定 `dump_failed`，不保存 raw stderr。

- [ ] **Step 4: 實作 metadata transaction**

`backup_records` 保存 path 的 project-relative 值、hash、size、status 與 restore evidence，不保存 password、完整 database URL 或 process command。exact duplicate `backup_id` 必須拒絕，不使用 upsert 改寫 provenance。

- [ ] **Step 5: 補 checksum mismatch、空 dump 與 process failure 測試**

測試必須證明失敗不留下 ready backup，repository rollback 並關閉連線。

- [ ] **Step 6: 執行 GREEN、lint 與提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_backups.py tests/test_backup_repository.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/backups.py src/qingpu_insight/backup_repository.py tests/test_backups.py tests/test_backup_repository.py
git add database/005_m43_health_backup_schema.sql src/qingpu_insight/backups.py src/qingpu_insight/backup_repository.py tests/test_backups.py tests/test_backup_repository.py
git commit -m "feat(m43): create verified mysql backups"
```

### Task 3: 隔離還原演練與安全邊界

**Files:**
- Modify: `src/qingpu_insight/backups.py`
- Modify: `src/qingpu_insight/backup_repository.py`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_backup_repository.py`

**Interfaces:**
- Consumes: Task 2 `BackupRecord` 與 `ProcessRunner`。
- Produces: `RestoreEvidence(database_name, table_names, row_counts, published_versions, checked_at)`。
- Produces: `BackupService.restore_drill(backup_id: str) -> RestoreEvidence`。

- [ ] **Step 1: 建立安全名稱與 production protection RED**

```python
def test_restore_rejects_non_drill_database() -> None:
    with pytest.raises(UnsafeRestoreTarget):
        validate_restore_database("qingpu_insight")


def test_restore_database_has_fixed_prefix() -> None:
    assert build_restore_database("abc").startswith("qingpu_restore_drill_")
```

- [ ] **Step 2: 建立完整 restore flow RED**

Recording runner 必須觀察到依序執行：

1. `CREATE DATABASE` 加上 `build_restore_database(backup_id)` 的已驗證結果
2. `mysql` 加上同一個 `drill_database` 參數匯入 dump
3. 使用 PyMySQL 連到 drill database 執行 table／count／pointer smoke queries
4. 在 `finally` 對同一個 `drill_database` 執行 `DROP DATABASE`

任何步驟失敗也必須嘗試安全清除；production database name 不得出現在 restore/drop target。

- [ ] **Step 3: 實作 restore drill**

驗證 checksum 後才能 restore。核心表至少包含 `job_runs`、`dataset_versions`、`published_datasets`、`listing_current`；保存每表 row count 與 published pointer。DROP 使用程式生成且經正規表示式 `\Aqingpu_restore_drill_[a-f0-9]{12}\Z` 驗證的 identifier，不接受外部任意字串。

- [ ] **Step 4: 補失敗清理與 metadata 測試**

涵蓋 checksum mismatch、import failure、missing table、pointer query failure、DROP failure。restore status 只能是 `succeeded`、`failed`、`cleanup_failed`。

- [ ] **Step 5: 執行 GREEN、lint 與提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_backups.py tests/test_backup_repository.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/backups.py src/qingpu_insight/backup_repository.py tests/test_backups.py tests/test_backup_repository.py
git add src/qingpu_insight/backups.py src/qingpu_insight/backup_repository.py tests/test_backups.py tests/test_backup_repository.py
git commit -m "feat(m43): verify backups with isolated restore"
```

### Task 4: 維運 CLI、唯讀 Web 摘要與 M4.3 gate

**Files:**
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `README.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_web.py`
- Create: `tests/test_m43_release_gate.py`

**Interfaces:**
- Consumes: Tasks 1–3 services/repositories。
- Produces: CLI `health-run`、`backup-create`、`backup-restore-drill --backup-id ID`。
- Produces: trusted-local GET `/api/ops/health`、GET `/api/ops/backups?limit=N`。
- Produces: simple ops card；不產生 backup／restore HTTP route。

- [ ] **Step 1: 建立 CLI RED**

驗證成功 JSON、固定 exit code、未知 backup、checksum mismatch、unsafe target 及 secret-bearing process failure。CLI stdout/stderr 不得包含 DB URL、password、raw SQL 或 raw stderr。

- [ ] **Step 2: 建立 Web contract RED**

```python
assert client.get("/api/ops/health").status_code == 200
assert client.get("/api/ops/backups?limit=101").status_code == 400
assert client.post("/api/ops/backups").status_code == 405
assert client.post("/api/ops/restore").status_code == 404
```

兩個 GET 沿用 M4.2 loopback＋trusted Host 保護，repository 失敗回固定安全 `503 ops_unavailable`。

- [ ] **Step 3: 實作 CLI 與唯讀頁面**

健康卡只顯示總狀態、check 摘要、最後備份與還原狀態；不加入圖表、threshold editor、通知或自動按鈕。

- [ ] **Step 4: 建立 M4.3 deterministic gate**

使用 fake process runner 與 stateful repository 證明：

- health 聚合可區分 healthy/warning/critical；
- dump 產生非空檔與正確 checksum；
- restore drill 驗證核心表／pointer 並清除隔離 DB；
- import 或 smoke query 失敗仍保留原 backup metadata 並嘗試清除；
- Web 沒有任何 restore mutation route。

- [ ] **Step 5: 執行自動驗收**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
.\.venv\Scripts\python.exe -m pytest tests/test_health.py tests/test_health_repository.py tests/test_backups.py tests/test_backup_repository.py tests/test_m43_release_gate.py tests/test_cli.py tests/test_web.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

- [ ] **Step 6: 執行手動真實 MySQL release gate**

只使用 placeholder 環境變數文件，不提交實值：

```powershell
.\.venv\Scripts\qingpu-data.exe health-run
$backup = .\.venv\Scripts\qingpu-data.exe backup-create | ConvertFrom-Json
.\.venv\Scripts\qingpu-data.exe backup-restore-drill --backup-id $backup.backup_id
```

保存不含 secrets 的日期、backup ID、checksum、restore database、核心 row counts 與 cleanup 結果到 `outputs/m43-acceptance/`。

- [ ] **Step 7: 提交**

```powershell
git add README.md src/qingpu_insight/cli.py src/qingpu_insight/web.py src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js tests/test_cli.py tests/test_web.py tests/test_m43_release_gate.py
git commit -m "feat(m43): expose local health and backup status"
```

## M4.3 Lite 停止條件

完成 Task 4、真實隔離還原與獨立 review 後停止。不要開始模型漂移、排程、通知或 M4.4；先由使用者驗收健康頁、備份檔及 restore evidence。
