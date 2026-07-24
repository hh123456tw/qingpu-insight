# M4.3/M4.4 Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修復 M4.3/M4.4 review 發現的資料安全、正式組裝、證據驗證、benchmark 與驗收問題，完成可供期中專題、自用及求職展示的本機封版。

**Architecture:** MySQL migration 是唯一 schema owner；CLI 與 Web 共用 production service factories；報告只能使用匿名 EvidencePack，所有數字必須能回指 numeric fact。Ollama/Gemini 是可選 provider，Rule provider 永遠可用並明確記錄 fallback；備份與 LLM 工作保持有界、可觀察且不把秘密寫入 artifact。

**Tech Stack:** Python 3.11、Flask、Pydantic、PyMySQL、MySQL 8、pandas/Parquet、requests、pytest、Ruff、Ollama、Gemini API。

## Global Constraints

- 專案定位固定為期中專題、自用工具與求職作品集；完成本計畫後封版，不開 M4.5/M4.6。
- 僅支援 Windows 本機執行；Web 固定綁定 loopback。
- 591 僅使用已授權的正常瀏覽流程，不新增規避網站控制的機制。
- 原始 HTML、電話、email、資料庫密碼、Gemini API key 與完整 provider response 不得進入 Git、log、報告或驗收 artifact。
- MySQL 是正式資料來源；Parquet 保留作 immutable artifact 與無 MySQL 時的展示 fallback。
- Migration 必須保留既有資料並可安全重跑。
- 所有 POST 必須同時通過 loopback、trusted Host 與 session CSRF 驗證。
- LLM 失敗、逾時或驗證失敗時必須明確降級 Rule；不得把 Rule 結果標成 Ollama/Gemini benchmark。
- 不新增登入、使用者 profile、收藏、通知、聊天、公開部署或常駐 benchmark service。
- 每個 task 完成後執行指定測試並建立獨立 commit；不得順手重構無關 M0–M4.2 程式。

---

## File Map

| File | Responsibility |
|---|---|
| `database/005_m43_health_backup_schema.sql` | 保留資料的 M4.3 health/backup migration |
| `database/006_m44_reports_schema.sql` | M4.4 buyer report schema |
| `src/qingpu_insight/backup_repository.py` | 備份 metadata 讀寫 |
| `src/qingpu_insight/backups.py` | dump、streaming checksum、隔離 restore 與 cleanup |
| `src/qingpu_insight/report_validation.py` | fact、numeric fact、敏感內容與單位驗證 |
| `src/qingpu_insight/report_composition.py` | 共用 provider、evidence、report service production factories |
| `src/qingpu_insight/evidence_repository.py` | 正式 MySQL EvidenceRepository adapter |
| `src/qingpu_insight/cli.py` | CLI parser 與薄 command handlers |
| `src/qingpu_insight/web.py` | 本機 API、CSRF、bounded report jobs 與 production composition |
| `src/qingpu_insight/llm_benchmark.py` | 真實 provider benchmark 結果與失敗紀錄 |
| `src/qingpu_insight/health.py` | 健康檢查 probes |
| `src/qingpu_insight/static/app.js` | 防重送 report UI 與 bounded health polling |
| `README.md` | 安裝、migration、操作與驗收 runbook |
| `outputs/m43-acceptance/` | 不含秘密的真實 backup/restore 證據 |
| `outputs/m44-benchmark/` | RTX 3080 benchmark 與 GTX 1050 Ti smoke/fallback 證據 |

---

### Task 1: 固定工作樹基準並補齊 committed repository contract

**Files:**
- Modify: `.gitignore`
- Modify: `src/qingpu_insight/backup_repository.py`
- Modify: `tests/test_backup_repository.py`

**Interfaces:**
- Consumes: `BackupRecord`
- Produces: `MySQLBackupRepository.get(backup_id: str) -> BackupRecord | None`

- [ ] **Step 1: 確認目前非預期檔案**

Run:

```powershell
git status --short
git diff -- src/qingpu_insight/backup_repository.py tests/test_backup_repository.py
Get-ChildItem data -Force | Select-Object Name
```

Expected: `get()` 與測試仍是未提交修改；只列出 `data/` 第一層名稱，不輸出原始內容。

- [ ] **Step 2: 把本機 runtime 目錄排除，但保留明確 fixture**

在 `.gitignore` 加入：

```gitignore
/data/raw/
/data/processed/
/data/locks/
/outputs/backups/
/outputs/reports/
```

驗收 artifact 只允許明確 `git add -f outputs/m43-acceptance/... outputs/m44-benchmark/...`。

- [ ] **Step 3: 完成 repository get contract**

保留目前 working tree 的參數化查詢：

```python
def get(self, backup_id: str) -> BackupRecord | None:
    with self._connection() as conn:
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM backup_records WHERE backup_id = %s",
                    (backup_id,),
                )
                row = cursor.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return None if row is None else self._to_record(row)
```

新增私有 `_to_record(row)`，並讓 `get()`、`list_recent()` 共用，避免欄位轉換漂移。

- [ ] **Step 4: 補齊成功、not-found、rollback 測試**

測試名稱固定為：

```python
def test_get_returns_record() -> None: ...
def test_get_returns_none_when_missing() -> None: ...
def test_get_rolls_back_when_select_fails() -> None: ...
```

- [ ] **Step 5: 執行測試並提交**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_backup_repository.py -q
git diff --check
git add .gitignore src/qingpu_insight/backup_repository.py tests/test_backup_repository.py
git commit -m "fix(m43): complete backup repository contract"
```

Expected: tests PASS；`git status --short` 不再出現未追蹤 `data/`。

---

### Task 2: 以保留資料的方式修復 M4.3 migration

**Files:**
- Modify: `database/005_m43_health_backup_schema.sql`
- Modify: `tests/test_m43_release_gate.py`
- Create: `tests/test_m43_migration_contract.py`

**Interfaces:**
- Produces: 可在空 schema、舊 schema、最新 schema 上安全執行的 migration 005

- [ ] **Step 1: 寫 migration 靜態防破壞測試**

```python
def test_m43_migration_never_drops_backup_records() -> None:
    sql = Path("database/005_m43_health_backup_schema.sql").read_text("utf-8")
    normalized = " ".join(sql.lower().split())
    assert "drop table if exists backup_records" not in normalized
    assert "create table if not exists backup_records" in normalized
```

- [ ] **Step 2: 先執行並確認測試失敗**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_m43_migration_contract.py -q
```

Expected: FAIL，指出 migration 含 `DROP TABLE`。

- [ ] **Step 3: 改成保留資料的 forward migration**

`backup_records` 的新安裝路徑使用：

```sql
CREATE TABLE IF NOT EXISTS backup_records (
    backup_id VARCHAR(36) NOT NULL PRIMARY KEY,
    status VARCHAR(32) NOT NULL,
    path VARCHAR(1024) NOT NULL,
    sha256 CHAR(64) NOT NULL DEFAULT '',
    size_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    restore_status VARCHAR(32) NULL,
    restore_checked_at DATETIME(3) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

舊表升級使用 `information_schema.COLUMNS` 加欄位；若存在舊 `checksum`，以 `UPDATE backup_records SET sha256 = checksum WHERE sha256 = ''` 回填。不得刪除任何 row，也不得改寫既有 `backup_id`。

- [ ] **Step 4: 在 disposable MySQL database 執行三段 rehearsal**

Run:

```powershell
mysql -h 127.0.0.1 -u root -p -e "DROP DATABASE IF EXISTS qingpu_m43_migration_test; CREATE DATABASE qingpu_m43_migration_test;"
Get-Content database/005_m43_health_backup_schema.sql | mysql -h 127.0.0.1 -u root -p qingpu_m43_migration_test
mysql -h 127.0.0.1 -u root -p qingpu_m43_migration_test -e "INSERT INTO backup_records(backup_id,status,path,sha256,size_bytes) VALUES('migration-proof','completed','proof.sql',REPEAT('a',64),1);"
Get-Content database/005_m43_health_backup_schema.sql | mysql -h 127.0.0.1 -u root -p qingpu_m43_migration_test
mysql -h 127.0.0.1 -u root -p qingpu_m43_migration_test -e "SELECT backup_id,status,sha256 FROM backup_records WHERE backup_id='migration-proof';"
mysql -h 127.0.0.1 -u root -p -e "DROP DATABASE qingpu_m43_migration_test;"
```

Expected: 第二次 migration 成功；`migration-proof` 仍存在且 checksum 未變。

- [ ] **Step 5: 測試並提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_m43_migration_contract.py tests/test_m43_release_gate.py -q
git add database/005_m43_health_backup_schema.sql tests/test_m43_migration_contract.py tests/test_m43_release_gate.py
git commit -m "fix(m43): preserve backup history during migration"
```

---

### Task 3: 修復 restore drill 安全性、並行與記憶體使用

**Files:**
- Modify: `src/qingpu_insight/backups.py`
- Modify: `src/qingpu_insight/backup_repository.py`
- Modify: `database/005_m43_health_backup_schema.sql`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_backup_repository.py`

**Interfaces:**
- Produces: `build_restore_database(backup_id: str, attempt_id: str) -> str`
- Produces: `hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str`
- Produces: cleanup failure 時非零 CLI result

- [ ] **Step 1: 寫真實 UUID round-trip 名稱測試**

```python
def test_restore_database_name_accepts_generated_backup_uuid() -> None:
    backup_id = str(uuid.uuid4())
    attempt_id = uuid.uuid4().hex
    name = build_restore_database(backup_id, attempt_id)
    validate_restore_database(name)
    assert "-" not in name.removeprefix("qingpu_restore_drill_")
```

- [ ] **Step 2: 寫 cleanup、path confinement、streaming 與 concurrent-attempt 測試**

測試必須涵蓋：

```python
def test_restore_rejects_metadata_path_outside_backup_dir() -> None: ...
def test_restore_cleanup_failure_raises_and_records_cleanup_failed() -> None: ...
def test_restore_create_failure_does_not_drop_another_attempt_database() -> None: ...
def test_hash_file_reads_in_bounded_chunks() -> None: ...
def test_restore_runner_receives_file_stream_not_dump_text() -> None: ...
```

- [ ] **Step 3: 改用 UUID hex 與每次 drill 唯一名稱**

```python
def build_restore_database(backup_id: str, attempt_id: str) -> str:
    backup_hex = uuid.UUID(backup_id).hex[:8]
    attempt_hex = uuid.UUID(attempt_id).hex[:8]
    return f"qingpu_restore_drill_{backup_hex}{attempt_hex}"
```

驗證 regex 同步改成固定 16 個小寫 hex。只有當本次 `CREATE DATABASE` 成功後，finally 才能刪除該名稱。

- [ ] **Step 4: 限制 backup path**

```python
def resolve_backup_path(backup_dir: Path, stored_path: str) -> Path:
    root = backup_dir.resolve()
    candidate = (root / stored_path).resolve()
    candidate.relative_to(root)
    return candidate
```

另驗證 `candidate.name == f"{backup_id}.sql"`，拒絕絕對路徑、`..` 與逃逸 symlink。

- [ ] **Step 5: 以 chunk 計算 checksum 並串流輸入 mysql**

```python
def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

調整 `ProcessRunner`，restore import 接受 `stdin_path: Path | None`；`RealRunner` 以 binary file handle 傳給 `subprocess.run(stdin=stream)`，不得使用 `read_bytes()`、`read_text()` 或 `input=<完整 dump>`。

- [ ] **Step 6: cleanup 失敗必須 fail closed**

新增：

```python
class RestoreCleanupFailed(RuntimeError):
    def __init__(self, database_name: str) -> None:
        super().__init__(f"restore cleanup failed: {database_name}")
        self.database_name = database_name
```

DROP nonzero 時 repository 記錄 `cleanup_failed` 並 raise；CLI 捕捉後輸出固定 `restore_cleanup_failed` code 與隔離 DB 名稱，return 1。

- [ ] **Step 7: 執行 tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_backups.py tests/test_backup_repository.py tests/test_cli.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/backups.py src/qingpu_insight/backup_repository.py tests/test_backups.py
```

Expected: 所有 restore safety tests PASS；測試不得建立真實 production database。

- [ ] **Step 8: 提交**

```powershell
git add database/005_m43_health_backup_schema.sql src/qingpu_insight/backups.py src/qingpu_insight/backup_repository.py tests/test_backups.py tests/test_backup_repository.py tests/test_cli.py
git commit -m "fix(m43): make restore drills safe and bounded"
```

---

### Task 4: 建立符合正式 schema 的 MySQL evidence adapter

**Files:**
- Create: `src/qingpu_insight/evidence_repository.py`
- Modify: `src/qingpu_insight/evidence.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `database/006_m44_reports_schema.sql`
- Create: `tests/test_evidence_repository.py`
- Modify: `tests/test_evidence.py`
- Modify: `tests/test_m44_release_gate.py`

**Interfaces:**
- Produces: `MySQLEvidenceRepository.current_dataset_version() -> str`
- Produces: `load_candidates(candidate_ids: Sequence[str]) -> pd.DataFrame`
- Produces: `load_market_evidence(candidate_ids: Sequence[str]) -> pd.DataFrame`

- [ ] **Step 1: 寫 schema-compatible SQL contract tests**

測試 fake cursor 必須只接受：

```sql
SELECT version
FROM published_datasets
WHERE dataset_key = %s
LIMIT 1
```

候選欄位必須明確 alias：

```sql
SELECT source_listing_id AS listing_id,
       listing_type,
       title,
       asking_price_twd AS price,
       building_area_ping,
       station_code,
       station_distance_m,
       building_age_years,
       snapshot_at AS observed_at
FROM listing_current
WHERE source_listing_id IN (...)
  AND active = TRUE
```

- [ ] **Step 2: 定義 market comparable 規則，不查不存在的 listing_id**

先取得候選的 `station_code`、`listing_type` 與面積，再查：

```sql
SELECT transaction_key,
       station_code,
       transaction_type,
       transaction_date,
       total_price_twd AS transaction_price,
       unit_price_per_ping_twd,
       building_area_ping
FROM market_transactions
WHERE station_code IN (...)
  AND analysis_eligible = TRUE
ORDER BY transaction_date DESC
LIMIT 500
```

EvidenceBuilder 以站點、交易類型、面積區間建立 comparable facts；不得假造 listing-to-transaction foreign key。

- [ ] **Step 3: 驗證 unknown candidate IDs**

在 `EvidenceBuilder.build()` 加入：

```python
requested = set(request.candidate_ids)
found = set(candidate_rows["listing_id"].astype(str))
missing = sorted(requested - found)
if missing:
    raise UnknownCandidateError(tuple(missing))
```

Web 回固定 404 `candidate_not_found`，CLI 回固定 code，不輸出 SQL 或 traceback。

- [ ] **Step 4: 移除 cli.py 內嵌 repository class**

`_create_report_service()` 改為 import `MySQLEvidenceRepository`；`cli.py` 不再含 SQL 字串或 pandas adapter 實作。

- [ ] **Step 5: 測試並提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evidence_repository.py tests/test_evidence.py tests/test_m44_release_gate.py tests/test_cli.py -q
git add src/qingpu_insight/evidence_repository.py src/qingpu_insight/evidence.py src/qingpu_insight/cli.py database/006_m44_reports_schema.sql tests/test_evidence_repository.py tests/test_evidence.py tests/test_m44_release_gate.py tests/test_cli.py
git commit -m "fix(m44): align report evidence with mysql schema"
```

---

### Task 5: 封住 LLM evidence validation 繞過

**Files:**
- Modify: `src/qingpu_insight/report_contracts.py`
- Modify: `src/qingpu_insight/report_validation.py`
- Modify: `src/qingpu_insight/report_repository.py`
- Modify: `tests/test_report_contracts.py`
- Modify: `tests/test_report_validation.py`
- Modify: `tests/test_report_repository.py`
- Modify: `tests/test_m44_release_gate.py`

**Interfaces:**
- Produces: 任一 numeric token 都必須對應 `numeric_fact_ids`
- Produces: repository GET 重新驗證 `BuyerReportDraft`

- [ ] **Step 1: 寫已重現漏洞的 regression test**

```python
def test_number_without_numeric_fact_id_is_rejected(pack: EvidencePack) -> None:
    claim = ReportClaim(
        text="總價99999999元",
        fact_ids=(pack.facts[0].fact_id,),
        numeric_fact_ids=(),
    )
    draft = make_valid_draft(summary=claim)
    result = validate_report(draft, pack)
    assert not result.valid
    assert "missing_numeric_fact_reference" in {i.code for i in result.issues}
```

- [ ] **Step 2: 先執行並確認漏洞測試失敗**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_validation.py::test_number_without_numeric_fact_id_is_rejected -q
```

Expected: FAIL，因目前 validator 回 valid。

- [ ] **Step 3: 永遠掃描數字**

```python
text_numbers = _normalize_number(claim.text)
if text_numbers and not claim.numeric_fact_ids:
    issues.append(
        ValidationIssue(code="missing_numeric_fact_reference", path=path)
    )
    return
```

另要求 `set(numeric_fact_ids).issubset(fact_ids)`；每個文字數字至少命中一個 numeric fact 的 normalized value。

- [ ] **Step 4: 擴充敏感 URL pattern**

接受 driver suffix：

```python
r"(?:mysql(?:\+pymysql)?|postgres(?:ql)?|sqlite|mongodb|redis)://\S+"
```

- [ ] **Step 5: GET persisted report 時重新驗證 contract**

`MySQLReportRepository.get()` 使用：

```python
draft = BuyerReportDraft.model_validate(json.loads(row["content"]))
content = draft.model_dump(mode="json")
```

格式錯誤時 raise `CorruptReportError`；Web 回固定 503 `report_corrupt`，不得直接回傳任意 JSON。

- [ ] **Step 6: 測試並提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_contracts.py tests/test_report_validation.py tests/test_report_repository.py tests/test_m44_release_gate.py -q
git add src/qingpu_insight/report_contracts.py src/qingpu_insight/report_validation.py src/qingpu_insight/report_repository.py tests/test_report_contracts.py tests/test_report_validation.py tests/test_report_repository.py tests/test_m44_release_gate.py
git commit -m "fix(m44): enforce numeric evidence boundaries"
```

---

### Task 6: 建立 CLI/Web 共用 production composition

**Files:**
- Create: `src/qingpu_insight/report_composition.py`
- Modify: `src/qingpu_insight/ollama_report_provider.py`
- Modify: `src/qingpu_insight/gemini_report_provider.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/web.py`
- Create: `tests/test_report_composition.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Produces: `create_provider_registry(env: Mapping[str, str]) -> dict[str, ReportProvider]`
- Produces: `create_report_service(root: Path, env: Mapping[str, str]) -> ReportService`
- Produces: `create_ops_services(root: Path) -> OpsServices`

- [ ] **Step 1: 寫 provider registry tests**

測試固定行為：

```python
def test_registry_always_contains_rule() -> None: ...
def test_registry_adds_ollama_only_with_explicit_model() -> None: ...
def test_registry_adds_gemini_only_with_key_and_model() -> None: ...
def test_registry_never_includes_api_key_in_repr() -> None: ...
```

環境名稱固定為：

```text
QINGPU_OLLAMA_BASE_URL
QINGPU_OLLAMA_MODEL
QINGPU_GEMINI_API_KEY
QINGPU_GEMINI_MODEL
```

- [ ] **Step 2: 建立共用 factory**

```python
def create_provider_registry(env: Mapping[str, str]) -> dict[str, ReportProvider]:
    providers: dict[str, ReportProvider] = {"rule": RuleReportProvider()}
    if model := env.get("QINGPU_OLLAMA_MODEL"):
        providers["ollama"] = OllamaReportProvider(
            base_url=env.get("QINGPU_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            model=model,
        )
    if key := env.get("QINGPU_GEMINI_API_KEY"):
        model = env.get("QINGPU_GEMINI_MODEL")
        if model:
            providers["gemini"] = GeminiReportProvider(api_key=key, model=model)
    return providers
```

`create_report_service()` 組合 Task 4 repository、MySQLReportRepository、EvidenceBuilder、validator 與 Rule fallback。

- [ ] **Step 3: Gemini key 改由 header 傳遞**

請求使用：

```python
headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": self._api_key,
}
response = self._session.post(url, headers=headers, json=payload, timeout=self._timeout)
```

URL 不得含 `?key=`。

- [ ] **Step 4: Web 啟動時建立完整 services**

`create_app(root=...)` 在 DB 設定有效時呼叫共用 factory；`OpsServices` 必須同時包含：

```python
OpsServices(
    health_service=HealthService(ProductionHealthProbes(...)),
    health_repository=MySQLHealthRepository(factory),
    backup_repository=MySQLBackupRepository(factory),
)
```

保留明確 dependency injection 供單元測試使用。

- [ ] **Step 5: 加 production composition integration tests**

```python
def test_root_app_composes_health_service(monkeypatch, tmp_path) -> None: ...
def test_root_app_composes_report_service(monkeypatch, tmp_path) -> None: ...
def test_ollama_request_reaches_ollama_adapter(monkeypatch, tmp_path) -> None: ...
def test_gemini_request_reaches_gemini_adapter(monkeypatch, tmp_path) -> None: ...
```

測試不得連外；使用 recording HTTP session，斷言 provider/model identity。

- [ ] **Step 6: 測試並提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_composition.py tests/test_cli.py tests/test_web.py tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py -q
git add src/qingpu_insight/report_composition.py src/qingpu_insight/ollama_report_provider.py src/qingpu_insight/gemini_report_provider.py src/qingpu_insight/cli.py src/qingpu_insight/web.py tests/test_report_composition.py tests/test_cli.py tests/test_web.py tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py
git commit -m "fix(m44): wire production report services"
```

---

### Task 7: 修復 Report API 邊界與 bounded execution

**Files:**
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `tests/test_web.py`

**Interfaces:**
- Produces: CSRF-protected `POST /api/reports`
- Produces: 同一 app process 最多一個 active local report generation

- [ ] **Step 1: 寫 CSRF regression tests**

```python
def test_report_post_rejects_missing_csrf(report_client) -> None: ...
def test_report_post_rejects_wrong_csrf(report_client) -> None: ...
def test_report_post_accepts_matching_csrf(report_client) -> None: ...
```

錯誤固定為 HTTP 403：

```json
{"error":{"code":"csrf_mismatch","message":"CSRF 驗證失敗。"}}
```

- [ ] **Step 2: 共用 POST boundary helper**

```python
def _require_trusted_local_post():
    if not _is_trusted_local_request():
        return jsonify({"error": {"code": "forbidden", "message": "僅限本機。"}}), 403
    if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
        return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403
    return None
```

管理更新與 report POST 都必須呼叫同一 helper。

- [ ] **Step 3: 加入 report concurrency guard**

本機作品集不新增完整 job subsystem；使用 app-owned `BoundedSemaphore(1)`：

```python
if not report_semaphore.acquire(blocking=False):
    return jsonify(
        {"error": {"code": "report_busy", "message": "已有報告正在產生。"}}
    ), 429
try:
    saved = report_services.service.generate(report_request)
finally:
    report_semaphore.release()
```

provider timeout維持有界；Ollama/Gemini payload 必須設定最大輸出 token。

- [ ] **Step 4: UI 防重送**

submit 開始即：

```javascript
reportSubmit.disabled = true;
```

在 `finally` 恢復；收到 429 顯示固定「已有報告正在產生」訊息，不再發第二個 fetch。

- [ ] **Step 5: 避免 health GET 每分鐘寫入歷史**

Web GET 回 repository 最新結果；只有 CLI `health-run` 執行 probes 並保存。前端可每 60 秒讀取最新狀態，但 `document.hidden === true` 時不 polling。

- [ ] **Step 6: 測試並提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py tests/test_health.py tests/test_health_repository.py -q
git add src/qingpu_insight/web.py src/qingpu_insight/static/app.js tests/test_web.py
git commit -m "fix(m44): bound local report API execution"
```

---

### Task 8: 讓 benchmark 與 smoke 真正測試指定 provider

**Files:**
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/llm_benchmark.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_llm_benchmark.py`
- Modify: `tests/test_m44_release_gate.py`
- Create: `benchmarks/m44_cases.json`

**Interfaces:**
- Produces: `llm-benchmark` 每個 case/model 都有 success 或 failure result
- Produces: `llm-smoke --provider rule|ollama|gemini --model NAME`

- [ ] **Step 1: 寫 parser contract tests**

```python
def test_benchmark_accepts_models_env_without_models() -> None: ...
def test_benchmark_accepts_explicit_models_without_models_env() -> None: ...
def test_benchmark_rejects_both_model_sources() -> None: ...
def test_benchmark_rejects_missing_model_source() -> None: ...
```

用 mutually exclusive group：

```python
models = parser.add_mutually_exclusive_group(required=True)
models.add_argument("--models", nargs="+")
models.add_argument("--models-env")
```

- [ ] **Step 2: benchmark 使用真實 Ollama model identity**

每個 model 建立：

```python
providers[model_name] = OllamaReportProvider(
    base_url=ollama_base_url,
    model=model_name,
)
```

Gemini benchmark 只有在明確 `provider=gemini` 且 key/model 存在時使用；Rule 只以 model `rule` 出現。

- [ ] **Step 3: 失敗不得消失**

`run_benchmark()` 捕捉 provider exception 時寫入：

```python
BenchmarkResult(
    case_id=case.pack_id,
    provider=provider_name,
    model=model_name,
    success=False,
    latency_ms=elapsed_ms,
    error_code="provider_failed",
    validation_codes=(),
)
```

若所有結果都失敗，CLI return 1；不得印出成功完成。

- [ ] **Step 4: smoke 使用 CLI 選項並建立有效最小 evidence**

無本機資料時 fixture 至少包含一個 `source_type="smoke"` 的非敏感 fact；Rule fallback 必須可產生合法 ReportClaim。結果 JSON 必須保留 requested provider、actual provider、model、fallback_reason。

- [ ] **Step 5: 建立固定匿名 cases**

`benchmarks/m44_cases.json` 包含：

- 一筆 sale 自住比較；
- 一筆 newhouse 預售比較；
- 一筆 rental_reference；
- 不含 URL、電話、email、姓名或 raw HTML；
- 每個 numeric fact 有明確 unit 與 source version。

- [ ] **Step 6: 測試並提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_llm_benchmark.py tests/test_cli.py tests/test_m44_release_gate.py -q
git add src/qingpu_insight/cli.py src/qingpu_insight/llm_benchmark.py tests/test_cli.py tests/test_llm_benchmark.py tests/test_m44_release_gate.py benchmarks/m44_cases.json
git commit -m "fix(m44): benchmark requested report providers"
```

---

### Task 9: 更新 runbook 並完成真實 M4.3/M4.4 release gates

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-22-qingpu-insight-m4-3-observability-backup.md`
- Modify: `docs/superpowers/plans/2026-07-22-qingpu-insight-m4-4-intelligent-reports.md`
- Create: `outputs/m43-acceptance/acceptance-YYYYMMDD.md`
- Create: `outputs/m44-benchmark/benchmark-rtx3080-YYYYMMDD.json`
- Create: `outputs/m44-benchmark/benchmark-rtx3080-YYYYMMDD.md`
- Create: `outputs/m44-benchmark/smoke-gtx1050ti-YYYYMMDD.json`

**Interfaces:**
- Produces: 可由面試官依 README 重現的本機操作與去識別驗收證據

- [ ] **Step 1: 修正 migration runbook**

README 明確依序列出 001、002、003、兩個 004、005、006；說明 005 可重跑且保留 backup history。不得在命令範例寫入真實密碼。

- [ ] **Step 2: 記錄 M4.3 真實 backup/restore**

使用本機環境變數執行：

```powershell
.\.venv\Scripts\qingpu-data.exe health-run
.\.venv\Scripts\qingpu-data.exe backup-create
.\.venv\Scripts\qingpu-data.exe backup-restore-drill --backup-id <returned-uuid>
```

artifact 只保存日期、backup ID、SHA-256、size、restore DB、四個核心 table row count、published pointer、cleanup status；不得保存 DB URL、使用者名稱、密碼或 dump 內容。

- [ ] **Step 3: 在 RTX 3080 執行一次真實 benchmark**

```powershell
$env:M44_BENCHMARK_MODELS = "<installed-ollama-model-1>,<installed-ollama-model-2>"
.\.venv\Scripts\qingpu-data.exe llm-benchmark --cases benchmarks/m44_cases.json --models-env M44_BENCHMARK_MODELS --output-dir outputs/m44-benchmark
```

Expected: 每個 case/model 都有一筆結果；artifact 的 `actual_provider` 是 `ollama`，不是 `rule`；列出 success rate、validation rate、fallback rate、p50/p95 latency。

- [ ] **Step 4: 在 GTX 1050 Ti 執行 smoke 或明確 Rule fallback**

若選定小模型可用：

```powershell
.\.venv\Scripts\qingpu-data.exe llm-smoke --provider ollama --model <installed-small-model> --output-dir outputs/m44-benchmark
```

若小模型不可接受，執行：

```powershell
.\.venv\Scripts\qingpu-data.exe llm-smoke --provider rule --model rule --output-dir outputs/m44-benchmark
```

artifact 必須如實標示 actual provider 與 fallback reason。

- [ ] **Step 5: 執行完整 deterministic gate**

```powershell
$env:PYTHONPATH = (Resolve-Path "src").Path
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
git status --short
```

Expected: 全部 PASS；除本 task 的 README、plan status 與去識別 acceptance artifacts 外沒有未提交檔案。

- [ ] **Step 6: 更新兩份舊 plan checkbox 與封版聲明**

只勾選已有 artifact 證明的 manual gate，並加上：

```markdown
M4.4 portfolio release complete. The project is feature-frozen after this
milestone; future work is limited to defect fixes and data refreshes.
```

- [ ] **Step 7: 提交 release evidence**

```powershell
git add README.md docs/superpowers/plans/2026-07-22-qingpu-insight-m4-3-observability-backup.md docs/superpowers/plans/2026-07-22-qingpu-insight-m4-4-intelligent-reports.md
git add -f outputs/m43-acceptance/acceptance-YYYYMMDD.md outputs/m44-benchmark/benchmark-rtx3080-YYYYMMDD.json outputs/m44-benchmark/benchmark-rtx3080-YYYYMMDD.md outputs/m44-benchmark/smoke-gtx1050ti-YYYYMMDD.json
git commit -m "docs(m44): record portfolio release acceptance"
```

---

## Final Acceptance Matrix

| Gate | Required result |
|---|---|
| Migration safety | 005 可重跑，既有 `backup_records` row 不遺失 |
| Backup restore | 真實 UUID 可 restore；checksum 正確；隔離 DB 成功清除 |
| Failure safety | cleanup 失敗 return nonzero 並標記 `cleanup_failed` |
| Memory bound | checksum/import 不讀取完整 SQL dump 到記憶體 |
| MySQL evidence | 所有 SQL 欄位存在於 001–006 schema |
| Candidate validation | 任一 unknown ID 產生穩定 404/CLI error |
| Numeric grounding | 任一無 numeric fact 的數字 claim 被拒絕 |
| Production Web | 正常 `qingpu-web` 可使用 health 與 reports，不依賴 test injection |
| API boundary | report POST 缺少或錯誤 CSRF 時為 403 |
| Provider identity | Ollama/Gemini 請求實際到達對應 adapter |
| Benchmark integrity | requested model 不得被 Rule 冒名；失敗也有 result row |
| Hardware evidence | RTX 3080 benchmark 與 GTX 1050 Ti smoke/fallback artifact 存在 |
| Regression | 完整 pytest、Ruff、`git diff --check` 全部通過 |
| Scope | M4.4 後 feature freeze；不建立 M4.5/M4.6 |

## Self-Review Result

- Review 中的 destructive migration、restore UUID、未提交 repository contract、path confinement、cleanup false-success、dump memory、MySQL schema mismatch、unknown candidate、numeric bypass、provider wiring、Web composition、CSRF、bounded execution、health write amplification、benchmark/smoke、README 與 manual artifacts 均有對應 task。
- 各 production interface 在產生端與消費端使用相同名稱。
- 計畫沒有要求公開部署、登入、收藏、聊天、通知或新里程碑功能。
