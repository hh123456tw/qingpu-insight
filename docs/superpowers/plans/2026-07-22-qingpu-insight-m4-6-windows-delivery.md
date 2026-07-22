# 青埔智價 M4.6 Windows 交付 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓乾淨 Windows 電腦能依文件完成環境檢查、啟停、排程、smoke、備份與作品集展示，並保留未來 Cloudflare 唯讀匯出界面。

**Architecture:** `ops/*.ps1` 只作薄 wrapper，實際邏輯由 Python CLI 執行；設定來自本機 `.env`／使用者環境，不寫入 Task Scheduler arguments。正式 runtime 使用 MySQL，Parquet 是 pipeline／ML artifact；E2E 以 fake source、Mock LLM 與隔離測試資料庫完成。

**Tech Stack:** Windows PowerShell 5.1+、Python 3.11、MySQL 8、Ollama、Chrome、Windows Task Scheduler、pytest、Ruff

## Global Constraints

- 腳本不得建立、覆寫或輸出真實 `.env`；只提交 `.env.example`。
- PowerShell process invocation 使用 argument array，不拼接含 secrets 的 shell command。
- Flask 只綁 `127.0.0.1`；背景啟動使用 `Start-Process -WindowStyle Hidden`。
- 591 排程預設不建立；只有使用者明確傳入 `-Include591` 才建立且必須設為登入時執行。
- 所有安裝／移除腳本可重複執行；只操作 `QingpuInsight-*` 命名空間內的排程。
- 清理或還原不得刪除 production database、raw evidence 或使用者未指定路徑。

## File Map

| File | Responsibility |
|---|---|
| `preflight.py` | 必要／選配依賴與安全設定檢查 |
| `ops/preflight.ps1` | 呼叫 machine-readable Python preflight |
| `ops/start-local.ps1` / `stop-local.ps1` | 驗證 PID 的本機啟停 |
| `ops/install-scheduled-tasks.ps1` / `remove-scheduled-tasks.ps1` | 固定 namespace 的 idempotent 排程管理 |
| `ops/run-scheduled-job.ps1` | Allowlisted scheduled job wrapper |
| `smoke.py` / `ops/smoke-m4.ps1` | fake、authorized、weak-llm release evidence |
| `public_export.py` | 匿名 Cloudflare-ready 唯讀匯出，不負責部署 |
| `docs/m4-operations-runbook.md` | Windows 安裝、操作、備份與故障排查 |
| `docs/m4-portfolio-case-study.md` | M0～M4 作品集敘事與限制 |

---

### Task 1: 環境契約、preflight 與啟停腳本

**Files:**
- Create: `.env.example`
- Create: `src/qingpu_insight/preflight.py`
- Modify: `src/qingpu_insight/config.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `pyproject.toml`
- Create: `tests/test_preflight.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_web.py`
- Create: `ops/preflight.ps1`
- Create: `ops/start-local.ps1`
- Create: `ops/stop-local.ps1`
- Create: `tests/powershell/preflight.Tests.ps1`

**Interfaces:**
- Produces: `qingpu-data preflight --json` 與三個 idempotent PowerShell wrappers。

- [ ] **Step 1: 寫入 required/optional dependency 測試**

```python
def test_preflight_requires_python_mysql_and_project_write_access(fake_environment) -> None:
    result = run_preflight(fake_environment.without_mysql())
    assert result.ready is False
    assert result.checks["mysql"].status == "required_missing"


def test_preflight_allows_missing_llm_with_rule_fallback(fake_environment) -> None:
    result = run_preflight(fake_environment.without_ollama_or_gemini())
    assert result.ready is True
    assert result.checks["llm"].status == "optional_missing"


def test_project_env_never_overrides_process_environment(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("QINGPU_PORT=6000\n", encoding="utf-8")
    monkeypatch.setenv("QINGPU_PORT", "7000")
    load_project_env(tmp_path)
    assert os.environ["QINGPU_PORT"] == "7000"
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_preflight.py -q`
Expected: FAIL，preflight module 尚不存在。

- [ ] **Step 3: 實作 preflight 與 scripts**

在 `pyproject.toml` production dependencies 加入 `python-dotenv>=1,<2`，並在 CLI／Web factory 建立前
呼叫 `load_dotenv(root / ".env", override=False)`；既有 process environment 永遠優先於 `.env`。

Preflight 檢查 Python >=3.11、package import、`QINGPU_DATABASE_URL` schema／連線、必要 tables、
processed/raw/artifact 目錄可寫、Chrome、磁碟空間、Ollama endpoint／model、Gemini key presence、
mysqldump/mysql executables、Task Scheduler access。只有 Python、MySQL、目錄與 project import 是 required。

`.env.example` 只含明確的範例值與空白選配欄位：

```dotenv
QINGPU_DATABASE_URL=mysql+pymysql://USER:URL_ENCODED_PASSWORD@127.0.0.1:3306/qingpu_insight
QINGPU_PORT=5000
LLM_PROVIDER=rule
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_TO=
```

`start-local.ps1` 先跑 preflight，再用 PID file 防重複啟動：

```powershell
$process = Start-Process -FilePath $PythonExe `
  -ArgumentList @('-m', 'qingpu_insight.web') `
  -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
$process.Id | Set-Content -LiteralPath $PidFile -Encoding ascii
```

Stop script 驗證 PID 對應 command path 屬於專案後才 `Stop-Process -Id`，不得依名稱批次停止 Python。

- [ ] **Step 4: 執行 Python 與 Pester 測試**

Run: `python -m pytest tests/test_preflight.py -q`
Expected: PASS。

Run: `Invoke-Pester tests/powershell/preflight.Tests.ps1`
Expected: PASS；若 CI 無 Pester，標記為 Windows release job，不在 pytest 中假裝通過。

- [ ] **Step 5: 提交**

```bash
git add .env.example pyproject.toml src/qingpu_insight/preflight.py src/qingpu_insight/config.py src/qingpu_insight/cli.py src/qingpu_insight/web.py tests/test_preflight.py tests/test_config.py tests/test_cli.py tests/test_web.py ops/preflight.ps1 ops/start-local.ps1 ops/stop-local.ps1 tests/powershell/preflight.Tests.ps1
git commit -m "feat(m4): package local Windows startup"
```

### Task 2: Windows Task Scheduler 安裝、移除與背景工作

**Files:**
- Create: `ops/run-scheduled-job.ps1`
- Create: `ops/install-scheduled-tasks.ps1`
- Create: `ops/remove-scheduled-tasks.ps1`
- Create: `tests/powershell/scheduled-tasks.Tests.ps1`
- Modify: `src/qingpu_insight/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `scheduled-run --job-type` CLI；`QingpuInsight-OfficialData`、`-Health`、`-Backup` 與選配 `-591` tasks。

- [ ] **Step 1: 寫入 task allowlist 與 default-no-591 tests**

```powershell
It 'does not install 591 task by default' {
  & $InstallScript -WhatIf
  $script:RegisteredTaskNames | Should -Not -Contain 'QingpuInsight-591'
}

It 'only removes QingpuInsight tasks declared by this project' {
  & $RemoveScript -WhatIf
  $script:RemovedTaskNames | Should -Be @(
    'QingpuInsight-OfficialData', 'QingpuInsight-Health', 'QingpuInsight-Backup'
  )
}
```

Python test 斷言 `scheduled-run --job-type arbitrary` parser 拒絕，允許值只有
`official-data`、`health`、`backup`、`listing-591`。

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_cli.py -q`
Expected: FAIL，`scheduled-run` 尚不存在。

Run: `Invoke-Pester tests/powershell/scheduled-tasks.Tests.ps1`
Expected: FAIL，scripts 尚不存在。

- [ ] **Step 3: 實作 allowlisted dispatcher 與 Task Scheduler scripts**

`run-scheduled-job.ps1` 只接受 ValidateSet，呼叫 `qingpu-data scheduled-run --job-type $JobType`，
將安全 stdout/stderr 寫 `outputs/logs/<type>-<UTC>.log`。安裝 script 使用
`Register-ScheduledTask`，官方資料每週、health 每日、backup 每週；重跑以同名 task 更新。

`-Include591` 才新增 591 task，principal 使用 `LogonType Interactive` 且 action 不含 `--headless`；
其餘 task 可背景執行。所有 action arguments 只包含 script path 與 job type，不含 DB URL/key/password。
Remove script 逐一檢查固定 task name 後 `Unregister-ScheduledTask -Confirm:$false`。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_cli.py -q`
Expected: PASS。

Run: `Invoke-Pester tests/powershell/scheduled-tasks.Tests.ps1`
Expected: PASS，WhatIf 不改變實際排程。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/cli.py tests/test_cli.py ops/run-scheduled-job.ps1 ops/install-scheduled-tasks.ps1 ops/remove-scheduled-tasks.ps1 tests/powershell/scheduled-tasks.Tests.ps1
git commit -m "feat(m4): install safe Windows schedules"
```

### Task 3: 完整本機 E2E 與 release smoke

**Files:**
- Create: `src/qingpu_insight/smoke.py`
- Create: `tests/test_m4_e2e.py`
- Create: `ops/smoke-m4.ps1`
- Modify: `src/qingpu_insight/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `m4-smoke --mode fake|authorized|weak-llm`，回傳 machine-readable gate summary。

- [ ] **Step 1: 寫入 offline happy path 與 publish rollback E2E**

```python
def test_m4_fake_e2e_updates_matches_reports_and_notifies(m4_harness) -> None:
    result = m4_harness.run(mode="fake")
    assert result.gates == {
        "listing_update": "passed",
        "published_version": "passed",
        "buyer_match": "passed",
        "mock_report": "passed",
        "notification_dedupe": "passed",
        "health": "passed",
    }


def test_failed_build_keeps_serving_previous_version(m4_harness) -> None:
    previous = m4_harness.publisher.current().version
    m4_harness.fake_source.fail_type = "newhouse"
    result = m4_harness.run(mode="fake")
    assert result.gates["listing_update"] == "failed"
    assert m4_harness.publisher.current().version == previous
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_m4_e2e.py tests/test_cli.py -q`
Expected: FAIL，smoke harness 尚不存在。

- [ ] **Step 3: 實作三種明確模式**

`fake` 使用 fixture source、Mock provider 與隔離 MySQL database；`authorized` 要求 interactive session，
只抓三類各一頁並產生 raw/quality/published evidence；`weak-llm` 不爬網，只載入指定 Ollama 小模型、
產生一份 schema-valid report、測試無模型 fallback。每次輸出
`outputs/smoke/<UTC>-<mode>/summary.json`，包含版本、gates、duration 與 artifact hashes，不含 secrets。

`ops/smoke-m4.ps1`：

```powershell
param([ValidateSet('fake','authorized','weak-llm')] [string]$Mode = 'fake')
& $PythonExe -m qingpu_insight.cli m4-smoke --mode $Mode
if ($LASTEXITCODE -ne 0) { throw "M4 smoke failed with exit code $LASTEXITCODE" }
```

- [ ] **Step 4: 執行 offline release gate**

Run: `python -m pytest tests/test_m4_e2e.py -q`
Expected: PASS，不連外。

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File ops/smoke-m4.ps1 -Mode fake`
Expected: exit 0 並產生 summary；授權與弱機模式在對應實機手動執行。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/smoke.py src/qingpu_insight/cli.py tests/test_m4_e2e.py tests/test_cli.py ops/smoke-m4.ps1
git commit -m "test(m4): add complete local release smoke"
```

### Task 4: 操作、作品集與未來 Cloudflare 匯出文件

**Files:**
- Create: `src/qingpu_insight/public_export.py`
- Create: `tests/test_public_export.py`
- Create: `docs/m4-operations-runbook.md`
- Create: `docs/m4-portfolio-case-study.md`
- Modify: `README.md`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `public-export --output PATH`，只輸出匿名唯讀資料；完整 M4 runbook 與 case study。

- [ ] **Step 1: 寫入 export allowlist 與 secret scan 測試**

```python
def test_public_export_contains_only_allowlisted_aggregates(tmp_path, mysql_repositories) -> None:
    output = export_public_snapshot(mysql_repositories, tmp_path)
    payload = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "dataset_version", "generated_at", "files"}
    combined = "".join(path.read_text(encoding="utf-8") for path in output.glob("*.json"))
    assert "source_url" not in combined
    assert "buyer_profile" not in combined
    assert "api_key" not in combined
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_public_export.py -q`
Expected: FAIL，export module 尚不存在。

- [ ] **Step 3: 實作 export 與文件**

Public export 只含 market/listing aggregate、站點、月份、資料日期、方法版本與 schema version；不含
個別 591 URL、收藏、profile、report、notification、raw rows 或精確座標。輸出到使用者指定的新目錄，
已存在非空目錄時拒絕，避免覆寫。

Runbook 必須逐步涵蓋安裝 Python/MySQL/Chrome/Ollama、建立 DB user/schema、URL encoding、
`.env`、initial load、啟停、手動 591、背景排程、health、backup/restore、benchmark、弱機 smoke、
常見錯誤與完整移除。Case study 說明 M0～M4 問題、AIPE04 技術、架構、benchmark、成果、限制、
資料倫理及未來 Cloudflare 唯讀匯出。

- [ ] **Step 4: 執行完整 M4 release gate**

Run: `python -m pytest -q`
Expected: 全部 PASS。

Run: `python -m ruff check . && git diff --check`
Expected: 全部 PASS。

Run: `git grep -n -E "(AIza[0-9A-Za-z_-]{20,}|-----BEGIN .*PRIVATE KEY-----|mysql\+pymysql://[^:]+:[^@]+@)" -- src ops tests`
Expected: 無輸出；任何命中都先人工確認並移除真實 secrets。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/public_export.py tests/test_public_export.py docs/m4-operations-runbook.md docs/m4-portfolio-case-study.md README.md .gitignore
git commit -m "docs(m4): deliver Windows operations portfolio"
```
