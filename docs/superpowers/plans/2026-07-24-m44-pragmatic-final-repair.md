# M4.4 Pragmatic Final Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓青埔房價分析專案能在 Windows 本機正常更新、查詢及產生可信報告，足以供自用、期中專題展示與求職面試說明，完成後封版。

**Architecture:** MySQL 為正式資料來源，Parquet 保留作 immutable artifact 與展示 fallback。Web 與 CLI 共用同一個 report runtime factory；EvidenceBuilder 只接收符合固定欄位契約的候選與官方交易 comparable；Rule 永遠可用，Ollama/Gemini 是明確設定後才啟用的可選 provider。

**Tech Stack:** Python 3.11、Flask、Pydantic、PyMySQL、MySQL 8、pandas、pytest、Ruff、Ollama、Gemini API。

## Global Constraints

- 完成這份計畫後 feature freeze；不開 M4.5/M4.6。
- 只支援 Windows 本機與 loopback Web，不做公開部署。
- 不新增登入、收藏、通知、聊天、profile、多人權限或常駐排程服務。
- 591 維持已授權的正常瀏覽流程，不改爬蟲策略。
- 報告、log、benchmark、Git 不得包含電話、email、資料庫密碼、API key、raw HTML 或 provider 原始回應。
- 修復以最小可用方案為準；不做微服務、分散式 queue、完整 migration framework 或過度效能調校。
- 每項修復先建立會失敗的 regression test，再實作最小修改。
- 每個 Task 必須獨立通過指定測試、Ruff 與 task review 後才進入下一項。

## 明確不納入本次

- MySQL 大資料量索引最佳化：目前本機資料量不構成展示阻擋。
- 完整中文數字轉換器：只阻擋常見中文數字加房價單位的輸出，不建立 NLP parser。
- 非同步報告 job queue：保留現有單工 semaphore，避免同時壓垮本機 GPU。
- 1050 Ti 強制跑 LLM：弱機可用 Rule smoke 作為正式 fallback 證據。
- 公開雲端、Cloudflare、手機版與多使用者功能。

---

### Task 1: 接通正式 Web、CLI 與 Provider Runtime

**Files:**
- Modify: `src/qingpu_insight/report_composition.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/web.py`
- Create: `tests/test_report_composition.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Produces: `ReportRuntime(service: ReportService, repository: MySQLReportRepository)`
- Produces: `create_report_runtime(connection_factory, root, env) -> ReportRuntime`
- Consumes: `create_mysql_connection_factory()`、`ReportServices`

- [ ] **Step 1: 寫 production composition 的 failing tests**

```python
def test_runtime_contains_configured_ollama_and_rule(monkeypatch, tmp_path):
    env = {
        "QINGPU_OLLAMA_MODEL": "gemma3:4b",
        "QINGPU_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
    }
    runtime = create_report_runtime(fake_connection_factory, tmp_path, env)
    assert runtime.service is not None
    assert runtime.repository is not None
    assert set(runtime.providers) == {"rule", "ollama"}


def test_root_web_app_composes_report_services(monkeypatch, tmp_path):
    monkeypatch.setenv("QINGPU_DATABASE_URL", SAFE_TEST_DATABASE_URL)
    monkeypatch.setattr(
        "qingpu_insight.web.create_report_runtime",
        lambda *args, **kwargs: fake_report_runtime(),
    )
    app = create_app(root=tmp_path)
    assert app.extensions["qingpu_report_services"] is not None


def test_cli_report_uses_configured_provider_registry(monkeypatch, tmp_path):
    runtime = recording_report_runtime()
    monkeypatch.setattr(
        "qingpu_insight.cli.create_report_runtime",
        lambda *args, **kwargs: runtime,
    )
    assert report_generate(tmp_path, ollama_args()) == 0
    assert runtime.requested_provider == "ollama"
```

- [ ] **Step 2: 執行並確認目前失敗**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_composition.py tests/test_web.py tests/test_cli.py -q
```

Expected: production runtime tests FAIL，因 factory 沒有正式呼叫者且 CLI 仍使用空 providers。

- [ ] **Step 3: 建立單一 runtime factory**

在 `report_composition.py` 加入：

```python
@dataclass(frozen=True)
class ReportRuntime:
    service: ReportService
    repository: MySQLReportRepository
    providers: Mapping[str, ReportProvider]


def create_report_runtime(
    connection_factory,
    root: Path,
    env: Mapping[str, str],
) -> ReportRuntime:
    repository = MySQLReportRepository(connection_factory)
    providers = create_provider_registry(env)
    service = ReportService(
        evidence_builder=EvidenceBuilder(MySQLEvidenceRepository(connection_factory)),
        providers=dict(providers),
        rule_provider=providers["rule"],
        validator=validate_report,
        repository=repository,
    )
    return ReportRuntime(service=service, repository=repository, providers=providers)
```

刪除或改寫舊 `create_report_service()`，避免存在兩套 composition。

- [ ] **Step 4: Web 與 CLI 使用同一 runtime**

Web 在 `root`、`QINGPU_DATABASE_URL` 存在且沒有 test injection 時：

```python
factory = create_mysql_connection_factory()
runtime = create_report_runtime(factory, root, os.environ)
report_services = ReportServices(
    service=runtime.service,
    repository=runtime.repository,
)
app.extensions["qingpu_report_services"] = report_services
```

CLI 的正式 MySQL 路徑直接回傳 `runtime.service`；沒有 DB 時仍保留本機 Parquet＋Rule fallback，但不得宣稱 Ollama/Gemini 已使用正式 MySQL evidence。

- [ ] **Step 5: Unknown candidate 使用固定錯誤契約**

Web 捕捉 `evidence.UnknownCandidateError`：

```python
except UnknownCandidateError:
    return jsonify(
        {"error": {"code": "candidate_not_found", "message": "找不到指定物件。"}}
    ), 404
```

CLI 回 `candidate_not_found` 並 return 1；刪除 `evidence_repository.py` 內重複的 exception class。

- [ ] **Step 6: 驗證並提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_composition.py tests/test_web.py tests/test_cli.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/report_composition.py src/qingpu_insight/cli.py src/qingpu_insight/web.py tests/test_report_composition.py
git add src/qingpu_insight/report_composition.py src/qingpu_insight/cli.py src/qingpu_insight/web.py src/qingpu_insight/evidence_repository.py tests/test_report_composition.py tests/test_cli.py tests/test_web.py
git commit -m "fix(m44): connect production report runtime"
```

Expected: 正常 entrypoint 不依賴 fake injection；Ollama/Gemini 設定會到達對應 adapter。

---

### Task 2: 修正 MySQL Evidence 與 Comparable Contract

**Files:**
- Modify: `src/qingpu_insight/evidence_repository.py`
- Modify: `src/qingpu_insight/evidence.py`
- Modify: `tests/test_evidence_repository.py`
- Modify: `tests/test_evidence.py`
- Modify: `tests/test_m44_release_gate.py`

**Interfaces:**
- Candidate columns: `listing_id, listing_type, price, price_per_ping, building_area_ping, station_code, station_distance_m, building_age_years, snapshot_at, location_method, model_evidence`
- Market columns: `listing_id, transaction_key, station_code, transaction_type, transaction_date, transaction_price, unit_price_per_ping_twd, building_area_ping`

- [ ] **Step 1: 寫 adapter＋builder 整合 failing test**

```python
def test_mysql_repository_output_builds_evidence_pack(fake_mysql):
    repository = MySQLEvidenceRepository(fake_mysql.factory)
    pack = EvidenceBuilder(repository).build(
        ReportRequest(
            candidate_ids=("sale-001",),
            intended_use="self_use",
            provider="rule",
        )
    )
    assert pack.candidates[0].candidate_id == "sale-001"
    assert {fact.kind for fact in pack.facts} >= {
        "asking_price",
        "unit_price",
        "data_freshness",
        "nearby_transactions_summary",
    }
```

Fake MySQL rows必須使用真實 001、003、004 schema 的欄位，不得先手動加工成 builder 想要的格式。

- [ ] **Step 2: 執行並確認 `KeyError: listing_id`**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evidence_repository.py::test_mysql_repository_output_builds_evidence_pack -q
```

Expected: FAIL，重現目前 market frame 缺少 `listing_id`。

- [ ] **Step 3: Candidate query 對齊固定欄位**

SQL 使用：

```sql
SELECT source_listing_id AS listing_id,
       listing_type,
       asking_price_twd AS price,
       COALESCE(
           asking_unit_price_low_twd_per_ping,
           asking_unit_price_high_twd_per_ping,
           asking_price_twd / NULLIF(building_area_ping, 0)
       ) AS price_per_ping,
       building_area_ping,
       station_code,
       station_distance_m,
       building_age_years,
       snapshot_at,
       acquisition_representation AS location_method,
       model_evidence
FROM listing_current
WHERE source_listing_id IN (...)
  AND active = TRUE
```

Builder 統一讀 `snapshot_at`，不再混用 `observed_at`。

- [ ] **Step 4: 以站點與面積產生 per-candidate comparable rows**

Repository 先取得每個候選的 `listing_id, station_code, building_area_ping`，再查最多 500 筆官方交易。使用：

```python
frames: list[pd.DataFrame] = []
for candidate in candidate_rows:
    comparable = market_df[market_df["station_code"] == candidate["station_code"]]
    area = candidate["building_area_ping"]
    if area is not None:
        comparable = comparable[
            comparable["building_area_ping"].between(area * 0.8, area * 1.2)
        ]
    comparable = comparable.copy()
    comparable["listing_id"] = str(candidate["listing_id"])
    frames.append(comparable.head(100))
return pd.concat(frames, ignore_index=True) if frames else empty_market_frame()
```

不建立不存在的 listing-to-transaction foreign key；`listing_id` 只代表這批 comparables 是為哪個候選產生。

- [ ] **Step 5: 驗證空交易與欄位缺失**

```python
def test_candidate_without_market_comparables_keeps_listing_facts(): ...
def test_unknown_candidate_raises_domain_error(): ...
def test_market_comparables_are_station_and_area_scoped(): ...
```

- [ ] **Step 6: 驗證並提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evidence_repository.py tests/test_evidence.py tests/test_m44_release_gate.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/evidence_repository.py src/qingpu_insight/evidence.py tests/test_evidence_repository.py
git add src/qingpu_insight/evidence_repository.py src/qingpu_insight/evidence.py tests/test_evidence_repository.py tests/test_evidence.py tests/test_m44_release_gate.py
git commit -m "fix(m44): build schema compatible evidence"
```

---

### Task 3: 修復 Restore Cleanup 與有界執行

**Files:**
- Modify: `src/qingpu_insight/backups.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `RealRunner(timeout_seconds: int = 300)`
- Produces: `restore_cleanup_failed` CLI code
- 保證只 DROP 本次成功 CREATE 的 database

- [ ] **Step 1: 寫三個 failure-path regression tests**

```python
def test_create_failure_never_drops_database():
    runner = RecordingRunner(returncodes=[1])
    with pytest.raises(RuntimeError, match="Failed to create database"):
        service(runner).restore_drill(valid_backup_id)
    assert len(runner.calls) == 1


def test_import_and_drop_failure_records_cleanup_failed():
    runner = RecordingRunner(returncodes=[0, 1, 1])
    with pytest.raises(RestoreCleanupFailed):
        service(runner).restore_drill(valid_backup_id)
    assert repository.restore_status == "cleanup_failed"


def test_metadata_failure_does_not_skip_drop():
    repository.fail_mark_restore = True
    with pytest.raises(RepositoryError):
        service(successful_runner).restore_drill(valid_backup_id)
    assert successful_runner.calls[-1][-1].startswith("DROP DATABASE")
```

- [ ] **Step 2: 執行並確認目前失敗**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_backups.py -q
```

Expected: 新增的三個 tests FAIL，證明目前 exception cleanup 次序不安全。

- [ ] **Step 3: 使用 `created` flag 與單一 cleanup helper**

```python
created = False
primary_error: Exception | None = None
try:
    create_result = self._runner.run(create_args, env)
    if create_result.returncode != 0:
        raise RuntimeError("Failed to create database")
    created = True
    evidence = self._run_restore_checks(...)
except Exception as exc:
    primary_error = exc
finally:
    if created:
        cleanup = self._runner.run(drop_args, env)
        if cleanup.returncode != 0:
            self._safe_mark_restore(backup_id, "cleanup_failed", checked_at)
            raise RestoreCleanupFailed(drill_db) from primary_error
```

成功 cleanup 後才記錄 `succeeded` 或原始 `failed`。`_safe_mark_restore()` 不得阻止 DROP，但 cleanup 成功後若 metadata 寫入失敗仍要向 CLI 回報失敗。

- [ ] **Step 4: MySQL child process 加 300 秒上限**

```python
class RealRunner:
    def __init__(self, timeout_seconds: int = 300) -> None:
        self._timeout_seconds = timeout_seconds

    def run(...):
        try:
            result = subprocess.run(
                args,
                env=env,
                stdin=stream,
                capture_output=stream is None,
                text=stream is None,
                shell=False,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ProcessResult(124, "", "process_timeout")
```

CLI 捕捉 `RestoreCleanupFailed` 後輸出固定 `restore_cleanup_failed` 與 database name，return 1。

- [ ] **Step 5: 驗證並提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_backups.py tests/test_backup_repository.py tests/test_cli.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/backups.py src/qingpu_insight/cli.py tests/test_backups.py
git add src/qingpu_insight/backups.py src/qingpu_insight/cli.py tests/test_backups.py tests/test_cli.py
git commit -m "fix(m43): fail closed during restore cleanup"
```

---

### Task 4: 讓 LLM 驗證、Benchmark 與 Smoke 可被信任

**Files:**
- Modify: `src/qingpu_insight/report_contracts.py`
- Modify: `src/qingpu_insight/report_validation.py`
- Modify: `src/qingpu_insight/ollama_report_provider.py`
- Modify: `src/qingpu_insight/gemini_report_provider.py`
- Modify: `src/qingpu_insight/llm_benchmark.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `tests/test_report_contracts.py`
- Modify: `tests/test_report_validation.py`
- Modify: `tests/test_ollama_report_provider.py`
- Modify: `tests/test_gemini_report_provider.py`
- Modify: `tests/test_llm_benchmark.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- `numeric_fact_ids ⊆ fact_ids`
- Ollama `num_predict=1200`
- Gemini `maxOutputTokens=1200`
- Benchmark 缺少 provider config 時 return 1，不代跑 Rule
- Smoke 保存 requested/actual provider 與 model

- [ ] **Step 1: 寫 trust-boundary failing tests**

```python
def test_numeric_fact_ids_must_be_subset_of_fact_ids():
    with pytest.raises(ValidationError):
        ReportClaim(
            text="總價100元",
            fact_ids=(),
            numeric_fact_ids=("price-fact",),
        )


@pytest.mark.parametrize(
    "text",
    ["api_key=supersecret", "password=hunter2", "Bearer abcdefghijklmnop"],
)
def test_secret_shaped_claim_is_rejected(text, valid_pack):
    assert not validate_report(make_draft(text), valid_pack).valid


def test_common_chinese_price_expression_is_rejected(valid_pack):
    result = validate_report(make_draft("總價一千五百萬"), valid_pack)
    assert not result.valid
```

- [ ] **Step 2: 執行並確認失敗**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_contracts.py tests/test_report_validation.py -q
```

Expected: 新增 tests FAIL。

- [ ] **Step 3: 加最小驗證，不建立中文數字 parser**

`ReportClaim` model validator 加入：

```python
if not set(self.numeric_fact_ids).issubset(self.fact_ids):
    raise ValueError("numeric_fact_ids must be a subset of fact_ids")
```

Sensitive patterns加入大小寫不敏感的 credential assignment 與 Bearer token。若 claim 同時含 `零一二三四五六七八九十百千萬億` 與 `元、萬、坪、年、公尺` 任一單位，回 `unsupported_chinese_number`，要求 provider repair 成阿拉伯數字。

- [ ] **Step 4: Provider payload 限制輸出**

Ollama：

```python
"options": {"num_predict": 1200}
```

Gemini：

```python
"generationConfig": {
    "responseMimeType": "application/json",
    "maxOutputTokens": 1200,
}
```

API key 繼續只放 `x-goog-api-key` header。

- [ ] **Step 5: Benchmark 不得靜默替換 provider**

Gemini 缺 key/model 時直接印出 `gemini_not_configured` 並 return 1。JSON result 必須包含：

```python
{
    "requested_provider": requested_provider,
    "requested_model": requested_model,
    "actual_provider": result.provider,
    "actual_model": result.model,
    "success": benchmark_result.success,
    "error_code": benchmark_result.error_code,
    "fallback_used": benchmark_result.fallback_used,
}
```

只要結果為空或所有 `success=False`，CLI return 1。

- [ ] **Step 6: Smoke 支援 artifact**

Parser 增加：

```python
smoke_parser.add_argument(
    "--output-dir",
    default="outputs/m44-benchmark",
)
```

輸出 `smoke_result.json`，requested identity 不得因 Rule fallback 被覆蓋；另存 actual identity 與 `fallback_reason`。

- [ ] **Step 7: 驗證並提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_contracts.py tests/test_report_validation.py tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py tests/test_llm_benchmark.py tests/test_cli.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/report_contracts.py src/qingpu_insight/report_validation.py src/qingpu_insight/ollama_report_provider.py src/qingpu_insight/gemini_report_provider.py src/qingpu_insight/llm_benchmark.py src/qingpu_insight/cli.py
git add src/qingpu_insight/report_contracts.py src/qingpu_insight/report_validation.py src/qingpu_insight/ollama_report_provider.py src/qingpu_insight/gemini_report_provider.py src/qingpu_insight/llm_benchmark.py src/qingpu_insight/cli.py tests/test_report_contracts.py tests/test_report_validation.py tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py tests/test_llm_benchmark.py tests/test_cli.py
git commit -m "fix(m44): make report benchmarks trustworthy"
```

---

### Task 5: 文件、真實驗收與作品集封版

**Files:**
- Modify: `README.md`
- Modify: `tests/test_m43_release_gate.py`
- Create: `outputs/m43-acceptance/acceptance-20260724.md`
- Create: `outputs/m44-benchmark/benchmark_results.json`
- Create: `outputs/m44-benchmark/benchmark_results.md`
- Create: `outputs/m44-benchmark/smoke_result.json`

**Interfaces:**
- Produces: 一套可以照 README 重現的本機操作流程
- Produces: 不含 secrets 的 M4.3/M4.4 acceptance evidence

- [ ] **Step 1: 修正 Ruff**

刪除 `tests/test_m43_release_gate.py` 未使用及重複的 `uuid`、`build_restore_database` imports。

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 2: 更新 README migration 與操作順序**

README 必須依序列出：

```text
001_market_schema.sql
002_add_valuation_columns.sql
003_listing_intelligence_schema.sql
004_listing_range_fields.sql
004_m4_jobs_publishing_schema.sql
005_m43_health_backup_schema.sql
006_m44_reports_schema.sql
```

另加入四個最短操作流程：

```powershell
.\.venv\Scripts\qingpu-data.exe listing-update --types sale newhouse rental --max-pages 1
.\.venv\Scripts\qingpu-data.exe health-run
$listingId = mysql -h 127.0.0.1 -u root -p --batch --skip-column-names qingpu_insight -e "SELECT source_listing_id FROM listing_current WHERE active = TRUE LIMIT 1"
.\.venv\Scripts\qingpu-data.exe report-generate --candidate $listingId --provider rule --intended-use self_use
.\.venv\Scripts\qingpu-web.exe
```

文件說明 Ollama/Gemini 未設定時 UI 可選 Rule，不把 fallback 描述成模型成功。

- [ ] **Step 3: 執行一次真實 M4.3 backup/restore**

使用現有本機 MySQL 環境，不把密碼寫入指令或文件：

```powershell
.\.venv\Scripts\qingpu-data.exe health-run
$backup = .\.venv\Scripts\qingpu-data.exe backup-create | ConvertFrom-Json
.\.venv\Scripts\qingpu-data.exe backup-restore-drill --backup-id $backup.backup_id
```

`outputs/m43-acceptance/acceptance-20260724.md` 只保存日期、backup ID、SHA-256、size、四個核心 table row count、published pointer 與 cleanup status。

- [ ] **Step 4: 在目前 RTX 3080 執行一次 benchmark**

```powershell
ollama list
$env:M44_BENCHMARK_MODELS = ((ollama list | Select-Object -Skip 1 -First 1) -split '\s+')[0]
.\.venv\Scripts\qingpu-data.exe llm-benchmark --cases benchmarks/m44_cases.json --models-env M44_BENCHMARK_MODELS --provider ollama --output-dir outputs/m44-benchmark
```

只需要一個實際可跑的模型；不為了比較而下載多個大模型。若本機 Ollama 尚未安裝模型，Rule smoke 可先完成自用與期中展示，但 README 與 artifact 必須明確標示 LLM benchmark 尚未執行，不能假裝完成。

- [ ] **Step 5: 低階機路徑以 Rule smoke 驗收**

```powershell
.\.venv\Scripts\qingpu-data.exe llm-smoke --provider rule --model rule --output-dir outputs/m44-benchmark
```

Expected: `smoke_result.json` 的 requested/actual provider 都是 `rule`，exit 0。這是 GTX 1050 Ti 的正式 fallback，不要求它載入 LLM。

- [ ] **Step 6: 完整 release gate**

```powershell
$env:PYTHONPATH = (Resolve-Path "src").Path
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
git status --short
```

Expected: 全部測試通過、Ruff 0 errors、diff check 0 errors；status 只包含預期文件與去識別 artifact。

- [ ] **Step 7: 最終人工展示 smoke**

依 README 實際完成：

1. 啟動 `qingpu-web`。
2. 首頁可載入市場摘要與最近 health。
3. 使用 DB 中存在的 listing ID 產生一份 Rule 報告。
4. 若 Ollama 已設定，再產生一份 Ollama 報告；失敗時 UI 明確顯示 Rule fallback。
5. 關閉 Web 後確認沒有殘留 report worker 或 restore database。

- [ ] **Step 8: 提交封版文件與證據**

```powershell
git add README.md tests/test_m43_release_gate.py
git add -f outputs/m43-acceptance outputs/m44-benchmark
git commit -m "docs(m44): record pragmatic portfolio acceptance"
```

---

## Final Acceptance Checklist

- [ ] `qingpu-web` 正常啟動且 `/api/reports` 不再固定 503。
- [ ] CLI 的 Rule、Ollama、Gemini provider identity 不會被冒名替換。
- [ ] 真實 MySQL candidate 可以建立 EvidencePack，不發生欄位 KeyError。
- [ ] 不存在的 candidate 回固定 404／CLI error。
- [ ] Restore 只清理本次建立的 database；DROP 失敗會明確回報。
- [ ] Provider 有 timeout 與 1200 token 上限。
- [ ] Benchmark 全失敗或 provider 未設定時 return nonzero。
- [ ] Smoke 會寫出 requested/actual identity artifact。
- [ ] README 可由使用者從 migration、啟動一路操作到報告。
- [ ] 真實 M4.3 backup/restore evidence 存在且不含秘密。
- [ ] RTX 3080 一模型 benchmark 或明確「尚未執行」狀態。
- [ ] GTX 1050 Ti 使用 Rule smoke 即可驗收。
- [ ] 完整 pytest、Ruff、`git diff --check` 全部通過。
- [ ] 完成後 feature freeze，只再做資料更新與 defect fix。

## Self-Review Result

- 原 review 的正式組裝、MySQL evidence、unknown candidate、restore cleanup、process timeout、numeric fact、secret-shaped output、token cap、benchmark false green、smoke artifact、Ruff、README 與人工驗收都有對應 Task。
- 已排除不影響本機自用及作品展示的索引微調、公開部署、多人功能、完整中文 NLP 與非同步 job queue。
- Web、CLI、benchmark 共用同一 provider identity 規則；requested 與 actual identity 在所有 artifact 中使用相同欄位名稱。
