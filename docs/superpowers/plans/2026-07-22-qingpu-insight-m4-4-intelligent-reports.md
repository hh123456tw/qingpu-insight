# 青埔智價 M4.4 智慧購屋報告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以可驗證 Evidence Pack 產生版本化購屋報告，支援 Mock、Ollama、Gemini 與規則式 fallback，並用固定案例選出本機模型。

**Architecture:** Deterministic `EvidenceBuilder` 從 MySQL published data 建立 fact registry；所有 provider 只接受匿名 Evidence Pack 並回傳同一 Pydantic schema。`ReportValidator` 驗證 fact ID、數值與禁用資料，`ReportService` 最多重試一次後 fallback；報告與 metadata 存 MySQL，benchmark artifacts 存檔案。

**Tech Stack:** Python 3.11、Pydantic 2、requests、Google Gen AI SDK、Ollama HTTP API、PyMySQL、Flask、pytest

## Global Constraints

- LLM 不得查詢資料庫、上網查價、修改 Evidence Pack 或產生未引用數值結論。
- Evidence Pack 不含姓名、電話、Email、Cookie、token、591 聯絡資料或不必要完整地址。
- CI 只使用 Mock／Rule provider，不連線 Ollama 或 Gemini。
- provider 不可用、429、timeout、schema error 或 fact validation failure 必須安全 fallback。
- 完整 benchmark 只在 RTX 3080 12GB 主機執行；GTX 1050 Ti 只做選定小模型 smoke。
- 免費 Gemini 畫面必須提示 submitted content 可能依 Google 條款用於產品改善。

## File Map

| File | Responsibility |
|---|---|
| `report_contracts.py` | Pydantic Evidence/Report/Claim schemas |
| `evidence.py` | 從 allowlisted published rows 建立 fact registry |
| `report_providers.py` | 共用 provider protocol、Mock 與 Rule provider |
| `ollama_report_provider.py` | Ollama structured-output adapter |
| `gemini_report_provider.py` | Gemini structured-output adapter |
| `report_validation.py` | fact IDs、數值、schema 與隱私驗證 |
| `report_service.py` | retry、provider fallback 與生成流程 |
| `report_repository.py` | MySQL report/content/version persistence |
| `llm_benchmark.py` | 固定案例評分與 JSON/Markdown artifacts |
| `web.py` / `cli.py` | 已保存 profile 的 report API 與 benchmark/smoke commands |

---

### Task 1: Evidence Pack 與報告 Pydantic contracts

**Files:**
- Create: `src/qingpu_insight/report_contracts.py`
- Create: `src/qingpu_insight/evidence.py`
- Create: `tests/test_report_contracts.py`
- Create: `tests/test_evidence.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `EvidenceFact`, `EvidencePack`, `ReportClaim`, `BuyerReportDraft`, `EvidenceBuilder.build(profile, candidates)`。

- [ ] **Step 1: 寫入 fact registry 與匿名化測試**

```python
def test_evidence_pack_contains_versioned_facts_without_contact_data() -> None:
    pack = EvidenceBuilder().build(profile(), candidates_with_contact_fields())
    assert pack.dataset_version == "listings-v3"
    assert pack.facts["listing:591:123:asking_price"].value == 18_800_000
    serialized = pack.model_dump_json()
    assert "0912" not in serialized
    assert "agent_name" not in serialized


def test_report_claim_requires_fact_ids() -> None:
    with pytest.raises(ValidationError):
        ReportClaim(text="總價 1880 萬", fact_ids=[])
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_report_contracts.py tests/test_evidence.py -q`
Expected: FAIL，Pydantic contracts 尚不存在。

- [ ] **Step 3: 實作 immutable contracts 與 allowlist builder**

在 `pyproject.toml` 的 production dependencies 加入 `pydantic>=2.10,<3`。

```python
class EvidenceFact(BaseModel):
    model_config = ConfigDict(frozen=True)
    fact_id: str
    label: str
    value: str | int | float | bool | None
    unit: str | None = None
    source: str
    observed_at: datetime


class EvidencePack(BaseModel):
    model_config = ConfigDict(frozen=True)
    pack_id: str
    dataset_version: str
    valuation_model_versions: tuple[str, ...]
    generated_at: datetime
    profile: dict[str, str | int | float | bool | None]
    candidate_keys: tuple[str, ...]
    facts: dict[str, EvidenceFact]


class ReportClaim(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    fact_ids: list[str] = Field(min_length=1, max_length=10)
```

`EvidenceBuilder` 使用明確 allowlist，不把來源 row 整包 `dict()`；fact ID 格式固定為
`<entity-type>:<stable-key>:<field>`。候選排名、開價、估值區間、價差、樣本數、資料日期與模型版本
皆各自成 fact。

- [ ] **Step 4: 執行測試與 lint**

Run: `python -m pytest tests/test_report_contracts.py tests/test_evidence.py -q && python -m ruff check src/qingpu_insight/report_contracts.py src/qingpu_insight/evidence.py`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml src/qingpu_insight/report_contracts.py src/qingpu_insight/evidence.py tests/test_report_contracts.py tests/test_evidence.py
git commit -m "feat(m4): define buyer report evidence contract"
```

### Task 2: 共用 provider contract、Mock 與 Rule provider

**Files:**
- Create: `src/qingpu_insight/report_providers.py`
- Create: `tests/test_report_providers.py`

**Interfaces:**
- Produces: `ReportProvider.generate(pack, repair_codes=()) -> ProviderResult`、`MockReportProvider`、`RuleReportProvider`、`ProviderUnavailable`、`ProviderInvalidResponse`。

- [ ] **Step 1: 寫入 deterministic provider contract tests**

```python
@pytest.mark.parametrize("provider", [MockReportProvider(), RuleReportProvider()])
def test_offline_providers_return_schema_valid_report(provider, evidence_pack) -> None:
    result = provider.generate(evidence_pack)
    assert result.provider in {"mock", "rule"}
    assert isinstance(result.report, BuyerReportDraft)
    assert result.report.recommendations[0].claims[0].fact_ids


def test_rule_provider_mentions_insufficient_evidence(sparse_evidence_pack) -> None:
    result = RuleReportProvider().generate(sparse_evidence_pack)
    assert any("資料不足" in risk.title for risk in result.report.risks)
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_report_providers.py -q`
Expected: FAIL，provider module 尚不存在。

- [ ] **Step 3: 實作 provider protocol 與兩個離線 provider**

```python
class ReportProvider(Protocol):
    name: str
    model: str
    def generate(
        self, pack: EvidencePack, repair_codes: tuple[str, ...] = ()
    ) -> ProviderResult: ...


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model: str
    report: BuyerReportDraft
    latency_ms: int
    usage: dict[str, int]
```

Mock 使用 fixture-like 固定句型但從 facts 取值；Rule provider 依候選 rank、asking gap、confidence 與
missing facts 產生固定繁中段落。兩者不得在 pack 外自行產生價格或樣本數。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_report_providers.py -q`
Expected: PASS，重跑輸出一致。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/report_providers.py tests/test_report_providers.py
git commit -m "feat(m4): add offline buyer report providers"
```

### Task 3: Ollama 與 Gemini providers

**Files:**
- Create: `src/qingpu_insight/ollama_report_provider.py`
- Create: `src/qingpu_insight/gemini_report_provider.py`
- Create: `tests/test_ollama_report_provider.py`
- Create: `tests/test_gemini_report_provider.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `EvidencePack`, `BuyerReportDraft.model_json_schema()`。
- Produces: `OllamaReportProvider`、`GeminiReportProvider`，都回傳 `ProviderResult`。

- [ ] **Step 1: 寫入 HTTP／SDK contract、timeout 與 429 測試**

```python
def test_ollama_sends_schema_and_non_streaming(responses, evidence_pack) -> None:
    responses.post("http://127.0.0.1:11434/api/chat", json=ollama_success())
    OllamaReportProvider(model="gemma4:12b").generate(evidence_pack)
    body = responses.calls[0].request.json()
    assert body["stream"] is False
    assert body["format"] == BuyerReportDraft.model_json_schema()


def test_gemini_429_is_provider_unavailable(fake_genai_client, evidence_pack) -> None:
    fake_genai_client.raise_resource_exhausted = True
    with pytest.raises(ProviderUnavailable, match="rate_limited"):
        GeminiReportProvider(fake_genai_client, "gemini-3.5-flash").generate(evidence_pack)
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py -q`
Expected: FAIL，providers 尚不存在。

- [ ] **Step 3: 實作 structured output 與安全錯誤映射**

在 `pyproject.toml` 的 production dependencies 加入 `google-genai>=1,<2`；Ollama 繼續使用既有
`requests`，不增加第二個 HTTP client。

Ollama POST `/api/chat`，connect timeout 3 秒、read timeout 180 秒、`stream=false`、`format` 為 schema、
context 預設 8192 並由 `OLLAMA_NUM_CTX` 覆寫。Gemini 使用官方 `google-genai` client 與
`response_mime_type="application/json"`、`response_schema=BuyerReportDraft`；API key 與 model 只從
環境注入 factory，不存在時不建立 provider。兩者 prompt 只包含 system rules、schema 與
`pack.model_dump(mode="json")`，不記錄原始 prompt／response。

所有 timeout、connection、404 model missing、429 與 5xx 映射為無 secret 的 reason code；JSON／
Pydantic error 映射 `ProviderInvalidResponse("schema_invalid")`。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py -q`
Expected: PASS，無真實網路呼叫。

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml src/qingpu_insight/ollama_report_provider.py src/qingpu_insight/gemini_report_provider.py tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py
git commit -m "feat(m4): integrate Ollama and Gemini reports"
```

### Task 4: 事實驗證、fallback service 與 MySQL 保存

**Files:**
- Create: `src/qingpu_insight/report_validation.py`
- Create: `src/qingpu_insight/report_service.py`
- Create: `src/qingpu_insight/report_repository.py`
- Create: `tests/test_report_validation.py`
- Create: `tests/test_report_service.py`
- Create: `tests/test_report_repository.py`

**Interfaces:**
- Produces: `validate_report(report, pack) -> ValidationResult`、`ReportService.generate()`、`MySQLReportRepository`。

- [ ] **Step 1: 寫入 hallucinated fact、竄改數字與 fallback 測試**

```python
def test_validator_rejects_unknown_fact_and_changed_number(evidence_pack) -> None:
    report = draft_with_claim("總價 1999 萬", ["listing:591:123:asking_price"])
    result = validate_report(report, evidence_pack)
    assert result.valid is False
    assert "numeric_value_not_in_facts" in result.codes


def test_service_retries_once_then_uses_rule(evidence_pack, failing_provider, repository) -> None:
    record = ReportService(repository, failing_provider, RuleReportProvider()).generate(evidence_pack)
    assert failing_provider.calls == 2
    assert record.provider == "rule"
    assert record.fallback_reason == "schema_invalid"
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_report_validation.py tests/test_report_service.py tests/test_report_repository.py -q`
Expected: FAIL，validator/service/repository 尚不存在。

- [ ] **Step 3: 實作 validator、fallback 與 MySQL table**

Validator 檢查所有 fact IDs 存在、claim 中的數字正規化後屬於被引用 facts 的允許表示、不得出現
電話／Email／credential pattern、必要 sections 非空、provider/model metadata 完整。百分比與萬元
顯示透過同一 `format_fact_value()` 產生 allowlist，不以任意 rounding 放行。

`ReportService` 流程固定為 selected provider → validation → 一次 repair request → rule provider；
selected provider unavailable 可直接進 local configured provider，再 rule。`buyer_reports` 保存 report_id、
pack_id、profile_id nullable、dataset/model/provider versions、status、fallback_reason、JSON report、
created_at；同一 pack/provider/model 的 unique key 防止重複生成。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_report_validation.py tests/test_report_service.py tests/test_report_repository.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/report_validation.py src/qingpu_insight/report_service.py src/qingpu_insight/report_repository.py tests/test_report_validation.py tests/test_report_service.py tests/test_report_repository.py
git commit -m "feat(m4): validate and persist grounded reports"
```

### Task 5: 報告 API、benchmark 與弱機 smoke

**Files:**
- Create: `src/qingpu_insight/llm_benchmark.py`
- Create: `tests/fixtures/reports/benchmark_cases.json`
- Create: `tests/test_llm_benchmark.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/web.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_web.py`
- Create: `docs/m4-llm-methodology.md`

**Interfaces:**
- Produces: `llm-benchmark`、`llm-smoke` CLI；`POST /api/reports`、`GET /api/reports/<report_id>`。

- [ ] **Step 1: 寫入 weighted score 與 API privacy tests**

```python
def test_benchmark_score_uses_40_20_20_20_weights() -> None:
    score = score_case(FactScore(1.0), LanguageScore(0.5), UtilityScore(0.75), SchemaScore(1.0))
    assert score.total == pytest.approx(0.85)


def test_report_api_never_returns_evidence_contact_fields(client) -> None:
    response = client.post("/api/reports", json={"profile_id": "profile-1"})
    assert response.status_code == 201
    assert "prompt" not in response.json
    assert "api_key" not in response.json
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_llm_benchmark.py tests/test_cli.py tests/test_web.py -q`
Expected: FAIL，benchmark/commands/routes 尚不存在。

- [ ] **Step 3: 實作可重現 benchmark 與 API**

20 cases fixture 每案固定 profile、candidate rows、expected fact IDs 與人工 rubric；CLI 要求明確
`--models`、`--output-dir`、`--machine-label`，保存 config、Ollama version、model digest、每案分數、
latency、RAM、VRAM、failure/retry 及彙總 JSON／Markdown。3080 指令：

```powershell
qingpu-data llm-benchmark --models gemma4:12b qwen3.5:9b `
  --cases tests/fixtures/reports/benchmark_cases.json `
  --machine-label rtx3080-12gb --output-dir outputs/benchmarks
```

弱機只執行：

```powershell
qingpu-data llm-smoke --models gemma4:e2b qwen3.5:4b --machine-label gtx1050ti-4gb
```

Web factory 注入 `ReportService`；POST 只接受已保存 profile ID 與 provider preference，不接受任意
prompt。免費 Gemini 回應 metadata 包含 `data_use_notice=true` 供前端顯示。

- [ ] **Step 4: 執行 M4.4 gate**

Run: `python -m pytest tests/test_report_contracts.py tests/test_evidence.py tests/test_report_providers.py tests/test_ollama_report_provider.py tests/test_gemini_report_provider.py tests/test_report_validation.py tests/test_report_service.py tests/test_report_repository.py tests/test_llm_benchmark.py tests/test_cli.py tests/test_web.py -q`
Expected: PASS，無外部網路。

Run: `python -m pytest -q && python -m ruff check . && git diff --check`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/llm_benchmark.py src/qingpu_insight/cli.py src/qingpu_insight/web.py tests/fixtures/reports/benchmark_cases.json tests/test_llm_benchmark.py tests/test_cli.py tests/test_web.py docs/m4-llm-methodology.md
git commit -m "feat(m4): benchmark and serve grounded reports"
```
