# 青埔智價 M4.4 Core 可驗證智慧購屋報告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 從 MySQL 已發布資料建立匿名 Evidence Pack，在無 LLM 時以規則式 provider 產生報告，並選配 Ollama／Gemini 強化文字，同時拒絕無證據數值與敏感資料。

**Architecture:** Deterministic `EvidenceBuilder` 只讀 M4.2 published dataset、官方成交與估價證據，建立帶穩定 `fact_id` 的 Pydantic Evidence Pack。Rule、Ollama、Gemini 共用窄 `ReportProvider` contract；`ReportValidator` 驗證 schema、fact ID、數值與隱私，`ReportService` 對 AI provider 最多修復一次後回退 Rule，報告 metadata/content 存 MySQL。

**Tech Stack:** Python 3.11、Pydantic 2、requests、PyMySQL、Flask、Ollama HTTP API、Gemini REST API、pytest、Ruff

## Global Constraints

- M4.3 Lite 必須先通過真實 MySQL health、backup 與隔離 restore gate。
- Rule provider 永遠可用；Ollama、Gemini、網路與 API key 都不得成為基本報告的必要條件。
- Provider 只能接收匿名 Evidence Pack，不得查詢 MySQL、讀取 raw 591 HTML、使用工具或上網查價。
- 報告中的每個數值 claim 必須引用存在的 `fact_id`，顯示值以 Evidence Pack 為準。
- 不保存姓名、電話、Email、Cookie、token、591 聯絡資訊或不必要的完整地址。
- M4.4 Core 不建立 profile、收藏、比較、通知、多人功能或 provider 管理後台。
- Gemini 只有明確設定 API key 與 model 後才啟用；CI 不呼叫 Ollama 或 Gemini。
- RTX 3080 只跑一次固定案例 benchmark；GTX 1050 Ti 只做選定小模型或 Rule fallback smoke。
- 所有 production code 先新增能因正確原因失敗的 regression test，再寫最小實作。

## File Map

| File | Responsibility |
|---|---|
| `src/qingpu_insight/report_contracts.py` | Evidence、claim、report、request Pydantic schemas |
| `src/qingpu_insight/evidence.py` | 從已發布資料建立匿名 fact registry |
| `src/qingpu_insight/report_providers.py` | Provider protocol、Rule provider 與測試 Mock |
| `src/qingpu_insight/ollama_report_provider.py` | Ollama structured JSON adapter |
| `src/qingpu_insight/gemini_report_provider.py` | Gemini REST structured JSON adapter |
| `src/qingpu_insight/report_validation.py` | fact、數值、schema 與隱私驗證 |
| `src/qingpu_insight/report_service.py` | provider 選擇、一次 repair、Rule fallback |
| `src/qingpu_insight/report_repository.py` | MySQL report persistence |
| `src/qingpu_insight/llm_benchmark.py` | 一次性固定案例 benchmark／smoke |
| `database/006_m44_reports_schema.sql` | report metadata/content schema |
| `tests/test_m44_release_gate.py` | 無 LLM、provider failure 與 hallucination gate |

---

### Task 1: Pydantic contracts 與 deterministic Evidence Pack

**Files:**
- Modify: `pyproject.toml`
- Create: `src/qingpu_insight/report_contracts.py`
- Create: `src/qingpu_insight/evidence.py`
- Create: `tests/test_report_contracts.py`
- Create: `tests/test_evidence.py`

**Interfaces:**
- Produces: `ReportRequest(candidate_ids: tuple[str, ...], budget_twd: int | None, intended_use: Literal["self_use", "rental_reference"], provider: Literal["rule", "ollama", "gemini"])`；這是單次請求，不是保存 profile。
- Produces: `EvidenceCandidate(candidate_id: str, listing_type: Literal["sale", "newhouse", "rental"])`。
- Produces: `EvidenceFact(fact_id, kind, label, value, unit, source_type, source_version, observed_at)`。
- Produces: `EvidencePack(pack_id, dataset_version, generated_at, candidates, facts, limitations)`。
- Produces: `ReportClaim(text, fact_ids, numeric_fact_ids)`。
- Produces: `BuyerReportDraft(summary: ReportClaim, advantages: tuple[ReportClaim, ...], risks: tuple[ReportClaim, ...], negotiation: tuple[ReportClaim, ...], limitations: tuple[ReportClaim, ...])`。
- Produces: `EvidenceBuilder.build(request: ReportRequest) -> EvidencePack`。

- [ ] **Step 1: 建立 dependency 與 schema RED**

在 `pyproject.toml` 加入 `"pydantic>=2.10,<3"`，並先寫：

```python
def test_report_request_rejects_profile_scope_and_too_many_candidates() -> None:
    with pytest.raises(ValidationError):
        ReportRequest(candidate_ids=tuple(f"id-{i}" for i in range(6)))


def test_claim_requires_evidence_reference() -> None:
    with pytest.raises(ValidationError):
        ReportClaim(text="價格便宜 10%", fact_ids=(), numeric_fact_ids=())
```

`candidate_ids` 限制 1–5 個；`intended_use` 只允許 `self_use`、`rental_reference`；provider 只允許 `rule`、`ollama`、`gemini`。

- [ ] **Step 2: 執行 RED**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
.\.venv\Scripts\python.exe -m pytest tests/test_report_contracts.py tests/test_evidence.py -q
```

Expected: collection fails because contracts/evidence modules do not exist.

- [ ] **Step 3: 實作 contracts**

所有 model 使用 `ConfigDict(extra="forbid", frozen=True)`。文字欄位有明確上限；`BuyerReportDraft` 限制優點與風險各 1–3 項，議價 1–3 項，不接受 provider 額外欄位。

- [ ] **Step 4: 建立 EvidenceBuilder RED**

stateful fake repository 提供：

```python
repository.current_dataset_version() -> str
repository.load_candidates(candidate_ids: Sequence[str]) -> pd.DataFrame
repository.load_market_evidence(candidate_ids: Sequence[str]) -> pd.DataFrame
```

測試穩定排序、相同輸入相同 fact IDs、缺資料產生 limitation、不符合 published version 的 row 被拒絕，以及輸出不含電話／聯絡人／完整地址。

- [ ] **Step 5: 實作 allowlisted fact registry**

fact ID 使用 `sha256(f"{dataset_version}|{candidate_id}|{kind}|{unit}".encode()).hexdigest()[:20]`。只允許開價、單價、面積、屋齡、站點／距離、模型區間、附近官方成交摘要、資料時間與 location evidence；忽略未 allowlist 欄位。

- [ ] **Step 6: 執行 GREEN、lint 與提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_contracts.py tests/test_evidence.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/report_contracts.py src/qingpu_insight/evidence.py tests/test_report_contracts.py tests/test_evidence.py
git add pyproject.toml src/qingpu_insight/report_contracts.py src/qingpu_insight/evidence.py tests/test_report_contracts.py tests/test_evidence.py
git commit -m "feat(m44): build anonymous evidence packs"
```

### Task 2: Rule provider 與嚴格 fact validator

**Files:**
- Create: `src/qingpu_insight/report_providers.py`
- Create: `src/qingpu_insight/report_validation.py`
- Create: `tests/test_report_providers.py`
- Create: `tests/test_report_validation.py`

**Interfaces:**
- Consumes: Task 1 `EvidencePack`、`BuyerReportDraft`。
- Produces: `ProviderResult(provider, model, draft, latency_ms, raw_usage)`。
- Produces: `ReportProvider.generate(pack, repair_codes=()) -> ProviderResult`。
- Produces: `RuleReportProvider` 與 test-only `MockReportProvider`。
- Produces: `ValidationIssue(code, path, fact_id)` 與 `ValidationResult(valid, issues)`。
- Produces: `validate_report(draft, pack) -> ValidationResult`。

- [ ] **Step 1: 建立 Rule provider RED**

```python
def test_rule_provider_generates_complete_report_without_network() -> None:
    result = RuleReportProvider().generate(PACK)
    assert result.provider == "rule"
    assert result.draft.advantages
    assert result.draft.risks
    assert all(claim.fact_ids for claim in all_claims(result.draft))
```

Rule 只使用明確 facts：asking/model interval、附近成交、location eligibility、資料新鮮度與缺資料 limitation。不得宣稱未來漲跌或保證成交。

- [ ] **Step 2: 建立 validator RED**

涵蓋：

- unknown fact ID；
- claim 文字包含 Evidence Pack 外數字；
- fact 的 unit 與文字單位不符；
- phone、Email、token、HTML、DB URL；
- 合法數字僅出現在引用 fact；
- 無數值的限制文字可通過。

- [ ] **Step 3: 執行 RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_providers.py tests/test_report_validation.py -q
```

- [ ] **Step 4: 實作 Rule 與 validator**

validator 從 draft model dump 遍歷所有 `ReportClaim`。所有數字 token 必須能對應 `numeric_fact_ids` 指向的 normalized fact 值；百分比、TWD、萬、坪、公尺及年份使用固定 unit formatter，不讓 LLM 自行決定顯示值。

- [ ] **Step 5: 執行 mutation-style regression**

在測試中依序把合法報告的 fact ID、數值、unit、敏感字串改壞，每一種 mutation 都必須得到固定 issue code。

- [ ] **Step 6: 執行 GREEN、lint 與提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_providers.py tests/test_report_validation.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/report_providers.py src/qingpu_insight/report_validation.py tests/test_report_providers.py tests/test_report_validation.py
git add src/qingpu_insight/report_providers.py src/qingpu_insight/report_validation.py tests/test_report_providers.py tests/test_report_validation.py
git commit -m "feat(m44): validate rule based buyer reports"
```

### Task 3: Ollama／Gemini adapters、fallback service 與 MySQL 保存

**Files:**
- Create: `src/qingpu_insight/ollama_report_provider.py`
- Create: `src/qingpu_insight/gemini_report_provider.py`
- Create: `src/qingpu_insight/report_service.py`
- Create: `src/qingpu_insight/report_repository.py`
- Create: `database/006_m44_reports_schema.sql`
- Create: `tests/test_ollama_report_provider.py`
- Create: `tests/test_gemini_report_provider.py`
- Create: `tests/test_report_service.py`
- Create: `tests/test_report_repository.py`

**Interfaces:**
- Consumes: Tasks 1–2 contracts/provider/validator。
- Produces: `OllamaReportProvider(base_url, model, timeout_seconds, session)`。
- Produces: `GeminiReportProvider(api_key, model, timeout_seconds, session)`。
- Produces: `SavedBuyerReport(report_id, request_hash, dataset_version, evidence_pack_id, provider, model, content, fallback_reason, validation_codes, latency_ms, created_at)`。
- Produces: `ReportService.generate(request) -> SavedBuyerReport`。
- Produces: `MySQLReportRepository.create(report)`、`get(report_id)`。

- [ ] **Step 1: 建立共用 HTTP contract RED**

Ollama 與 Gemini tests 使用 `responses`，涵蓋成功 structured JSON、timeout、連線失敗、429、5xx、非 JSON、額外 schema 欄位與 provider error body。例外只含固定 code，不保存 raw body、prompt、API key 或 URL。

- [ ] **Step 2: 實作窄 adapters**

兩個 adapters 都只送 `EvidencePack.model_dump(mode="json")`、固定 system instruction 與 `BuyerReportDraft.model_json_schema()`。Gemini API key 只放 request header/query 的 client boundary，不進 log/repr；model 必須由 `GEMINI_MODEL` 明確設定，沒有 key/model 時 provider 不建立。

- [ ] **Step 3: 建立 fallback service RED**

```python
def test_invalid_ai_report_repairs_once_then_falls_back_to_rule() -> None:
    ai = SequenceProvider([INVALID_RESULT, INVALID_RESULT])
    saved = service(ai=ai, rule=RuleReportProvider()).generate(REQUEST)
    assert ai.calls == [(), ("unknown_fact",)]
    assert saved.provider == "rule"
    assert saved.fallback_reason == "validation_failed"
```

另測 provider unavailable、timeout、429、schema error、repository failure。Rule 失敗才讓整個請求失敗；AI 失敗不得影響 published dataset。

- [ ] **Step 4: 建立 migration/repository RED**

`buyer_reports` 保存 report ID、request hash、dataset/evidence version、provider/model、validated content JSON、fallback reason、latency 與 timestamps。request hash 相同不強制 dedupe；每次報告是獨立版本。repository 每次操作使用新 connection、單 transaction、關閉連線。

- [ ] **Step 5: 實作 service 與 repository**

順序固定：

1. build Evidence Pack；
2. 選擇 request provider；
3. AI generate；
4. validate；
5. 最多一次帶 issue codes repair；
6. 仍失敗則 Rule；
7. validate Rule；
8. 保存 validated report。

- [ ] **Step 6: 執行 GREEN、lint 與提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py tests/test_report_service.py tests/test_report_repository.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/ollama_report_provider.py src/qingpu_insight/gemini_report_provider.py src/qingpu_insight/report_service.py src/qingpu_insight/report_repository.py tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py tests/test_report_service.py tests/test_report_repository.py
git add database/006_m44_reports_schema.sql src/qingpu_insight/ollama_report_provider.py src/qingpu_insight/gemini_report_provider.py src/qingpu_insight/report_service.py src/qingpu_insight/report_repository.py tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py tests/test_report_service.py tests/test_report_repository.py
git commit -m "feat(m44): add validated llm report fallback"
```

### Task 4: Report API、CLI 與最小 UI

**Files:**
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `README.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: Task 3 `ReportService`、`MySQLReportRepository`。
- Produces: CLI `report-generate --candidate ID [--provider rule|ollama|gemini]`。
- Produces: trusted-local POST `/api/reports`、GET `/api/reports/{report_id}`。
- Produces: 一個候選物件、provider 與報告結果區；不建立 profile／收藏／比較。

- [ ] **Step 1: 建立 API input/security RED**

驗證非 JSON、未知 candidate、超過五筆、無 provider、Gemini 未設定、非本機 Host、secret-bearing service error。POST 只接受 candidate IDs、可選 budget/intended use/provider，不接受任意 prompt。

- [ ] **Step 2: 建立 Rule fallback integration RED**

不設定 Ollama/Gemini，POST provider=`rule` 必須成功；POST provider=`ollama` 且 provider unavailable 時也必須回成功 Rule 報告及 `fallback_reason`，而不是 503。

- [ ] **Step 3: 實作 API 與 CLI**

GET 只回 validated/saved report。POST 回 `201`，內容包含 report ID、provider used、model、dataset/evidence version、fallback reason 與 report sections；不回 Evidence Pack 中未用 fact、raw provider response 或 prompt。

- [ ] **Step 4: 實作最小 UI**

沿用本機 CSRF／Host boundary。UI 只提供候選 ID、provider 選擇與產生按鈕；Gemini 選項旁顯示外部資料傳送提示。禁止新增聊天介面、任意 prompt、profile editor 或收藏功能。

- [ ] **Step 5: 執行 GREEN、lint 與提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_web.py tests/test_report_service.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/cli.py src/qingpu_insight/web.py tests/test_cli.py tests/test_web.py
git add README.md src/qingpu_insight/cli.py src/qingpu_insight/web.py src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js tests/test_cli.py tests/test_web.py
git commit -m "feat(m44): expose verified buyer reports"
```

### Task 5: 一次性 benchmark、弱機 smoke 與 M4.4 gate

**Files:**
- Create: `src/qingpu_insight/llm_benchmark.py`
- Create: `tests/test_llm_benchmark.py`
- Create: `tests/test_m44_release_gate.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 1–4 services/providers。
- Produces: CLI `llm-benchmark --cases PATH --models MODEL... --output-dir PATH`。
- Produces: CLI `llm-smoke --provider ollama --model MODEL`。
- Produces: JSON／Markdown artifact；不建立常駐 benchmark service。

- [ ] **Step 1: 建立 deterministic scorer RED**

固定案例檔只保存匿名 Evidence Packs 與 expected rule categories。每個 result 計算：

```python
{
    "schema_success": bool,
    "fact_accuracy": float,
    "required_section_coverage": float,
    "fallback_used": bool,
    "latency_ms": int,
}
```

總分只作模型比較，不自動修改 production model 設定。

- [ ] **Step 2: 實作 artifact writer**

先寫 temp，再 `os.replace`。JSON 保存完整 machine-readable 結果；Markdown 只顯示模型、案例數、成功率、fact accuracy、coverage、p50/p95 latency、失敗碼。不得保存 API key、prompt raw body 或敏感 facts。

- [ ] **Step 3: 建立 M4.4 deterministic release gate**

`tests/test_m44_release_gate.py` 必須證明：

- 無網路、無 Ollama、無 Gemini 時 Rule 報告成功；
- AI provider 合法輸出通過並保存；
- unknown fact、竄改數值、敏感資訊、invalid JSON 觸發一次 repair；
- repair 仍失敗時 Rule fallback；
- report failure 不改變 M4.2 published pointer；
- MySQL report metadata 使用 evidence/dataset version；
- API 不回 raw provider body 或 secrets。

- [ ] **Step 4: 執行自動 gate**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
.\.venv\Scripts\python.exe -m pytest tests/test_report_contracts.py tests/test_evidence.py tests/test_report_providers.py tests/test_report_validation.py tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py tests/test_report_service.py tests/test_report_repository.py tests/test_llm_benchmark.py tests/test_m44_release_gate.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

- [ ] **Step 5: RTX 3080 手動 benchmark**

使用 5–10 個固定匿名案例與少量能在 12GB VRAM 執行的候選模型。實際 model IDs 在執行當日由已安裝 Ollama 清單選定並記入 artifact；計畫不硬編尚未下載的模型名稱。

```powershell
if (-not $env:M44_BENCHMARK_MODELS) { throw "M44_BENCHMARK_MODELS must contain comma-separated installed Ollama model IDs" }
.\.venv\Scripts\qingpu-data.exe llm-benchmark --cases benchmarks/m44_cases.json --models-env M44_BENCHMARK_MODELS --output-dir outputs/m44-benchmark
```

Gemini 只有設定 `GEMINI_API_KEY`、`GEMINI_MODEL` 且使用者明確同意外部傳送時才作選配基準。

- [ ] **Step 6: GTX 1050 Ti smoke**

```powershell
if (-not $env:M44_SMOKE_MODEL) { throw "M44_SMOKE_MODEL must name an installed Ollama model" }
.\.venv\Scripts\qingpu-data.exe llm-smoke --provider ollama --model $env:M44_SMOKE_MODEL
```

若模型無法載入，必須執行並保存 Rule fallback smoke；弱機不跑完整模型比較。

- [ ] **Step 7: 提交**

```powershell
git add README.md src/qingpu_insight/llm_benchmark.py src/qingpu_insight/cli.py tests/test_llm_benchmark.py tests/test_m44_release_gate.py
git commit -m "feat(m44): benchmark verified report providers"
```

## M4.4 Core 停止條件

完成 Task 5、RTX 3080 benchmark、GTX 1050 Ti smoke/fallback 與獨立 review 後停止。不要新增 profile、收藏、比較、通知、聊天介面、provider 後台或公開部署；這些仍屬 M4.5／M4.6 或未來里程碑。
