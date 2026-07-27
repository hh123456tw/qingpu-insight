# Google Model Selection and Local Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-form conversation provider/model entry with a fixed four-model catalog and automatically fall back from the selected Google model to local Ollama and finally Rule.

**Architecture:** A single server-side model catalog owns every public model ID and resolves it to a provider. A focused fallback executor performs provider calls, validation, one Google retry, and downgrade routing, while the conversation service persists requested selection at conversation level and actual execution metadata at message level. The browser consumes a read-only model catalog and never receives the Gemini API key.

**Tech Stack:** Python 3.11, Flask, Pydantic v2, MySQL 8, `requests`, vanilla JavaScript, Node contract tests, pytest.

## Global Constraints

- Follow strict Red-Green-Refactor: no production change before its focused test has failed for the expected reason.
- Public model IDs are exactly `gemini-3.5-flash-lite`, `gemma-4-31b-it`, `gemma4:e2b`, and `rule`.
- The default public model is exactly `gemini-3.5-flash-lite`.
- A Google conversation executes selected Google model attempt 1, selected Google model attempt 2, local `gemma4:e2b`, then `rule`.
- An Ollama conversation executes local `gemma4:e2b`, then `rule`; a Rule conversation does not call an LLM.
- The selected model is fixed when the conversation is created; reply requests cannot override provider or model.
- The Gemini API key is read only from `instance/secrets.env` or `QINGPU_GEMINI_API_KEY`.
- Never return, render, log, persist in MySQL, or commit the Gemini API key.
- Do not add dynamic Google model discovery, usage billing, multi-tenant secrets, or parallel provider racing.
- Preserve existing untracked `candidates/` directories and `未命名.jpg`; do not clean, reset, move, or commit them.
- Do not put a real Google API key in tests, fixtures, docs, commands, screenshots, or Git history.

---

## File Structure

**Create**

- `src/qingpu_insight/conversation_models.py`: immutable public model catalog and lookup functions.
- `src/qingpu_insight/conversation_fallback.py`: retry, validation, fallback routing, and safe reason mapping.
- `database/009_conversation_fallback_metadata.sql`: idempotent message metadata migration.
- `tests/test_conversation_models.py`: model catalog domain tests.
- `tests/test_conversation_fallback.py`: fallback route and retry tests.
- `tests/test_conversation_fallback_migration.py`: SQL migration contract.

**Modify**

- `src/qingpu_insight/conversation_providers.py`: one network request per provider attempt, dynamic Gemini key resolution, and status-specific safe errors.
- `src/qingpu_insight/conversation_contracts.py`: model-only conversation create request and fixed-model reply request.
- `src/qingpu_insight/conversation_repository.py`: persist and read `fallback_reason`.
- `src/qingpu_insight/conversation_service.py`: resolve model at conversation creation and delegate replies to fallback executor.
- `src/qingpu_insight/conversation_web.py`: expose model catalog, remove per-reply provider/model override, and project execution metadata.
- `src/qingpu_insight/web.py`: apply migration 009, wire the live secret getter, providers, catalog, and fallback executor.
- `src/qingpu_insight/templates/index.html`: replace provider/free-text controls with one fixed model selector and status copy.
- `src/qingpu_insight/static/home_assistant.js`: load model catalog and submit only model ID.
- `src/qingpu_insight/templates/assistant.html`: expose requested model clearly.
- `src/qingpu_insight/static/assistant.js`: stop sending overrides and render actual model/fallback badges.
- `src/qingpu_insight/static/assistant.css`: model and fallback presentation.
- `tests/test_conversation_providers.py`, `tests/test_conversation_repository.py`, `tests/test_conversation_service.py`, `tests/test_conversation_web.py`, `tests/test_web.py`: focused Python behavior.
- `tests/js/home_assistant_contract.cjs`, `tests/js/assistant_contract.cjs`: browser contract behavior.
- `docs/operations/listing-conversation-assistant.md`, `README.md`: operator and portfolio documentation.

---

### Task 1: Fixed Conversation Model Catalog

**Files:**

- Create: `src/qingpu_insight/conversation_models.py`
- Create: `tests/test_conversation_models.py`

**Interfaces:**

- Produces: `ModelDefinition`, `PUBLIC_CONVERSATION_MODELS`, `DEFAULT_CONVERSATION_MODEL`, `resolve_conversation_model(model_id: str) -> ModelDefinition`, and `public_model_catalog(*, gemini_configured: bool) -> dict[str, object]`.
- Consumers: Tasks 3, 5, 6, 7, and 8.

- [ ] **Step 1: Write failing catalog tests**

```python
import pytest

from qingpu_insight.conversation_models import (
    DEFAULT_CONVERSATION_MODEL,
    public_model_catalog,
    resolve_conversation_model,
)


def test_catalog_has_exact_public_models_in_display_order():
    catalog = public_model_catalog(gemini_configured=True)
    assert catalog["default_model"] == "gemini-3.5-flash-lite"
    assert [item["id"] for item in catalog["items"]] == [
        "gemini-3.5-flash-lite",
        "gemma-4-31b-it",
        "gemma4:e2b",
        "rule",
    ]
    assert catalog["gemini_configured"] is True


def test_catalog_resolves_provider_server_side():
    assert resolve_conversation_model("gemini-3.5-flash-lite").provider == "gemini"
    assert resolve_conversation_model("gemma-4-31b-it").provider == "gemini"
    assert resolve_conversation_model("gemma4:e2b").provider == "ollama"
    assert resolve_conversation_model("rule").provider == "rule"
    assert DEFAULT_CONVERSATION_MODEL == "gemini-3.5-flash-lite"


def test_catalog_rejects_unknown_model():
    with pytest.raises(ValueError, match="unknown conversation model"):
        resolve_conversation_model("gemini-pro")
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_models.py -q
```

Expected: collection fails because `qingpu_insight.conversation_models` does not exist.

- [ ] **Step 3: Implement the immutable catalog**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    label: str
    provider: str
    cloud: bool
    description: str


PUBLIC_CONVERSATION_MODELS = (
    ModelDefinition(
        id="gemini-3.5-flash-lite",
        label="Google Gemini 3.5 Flash-Lite",
        provider="gemini",
        cloud=True,
        description="快速、穩定；雲端失敗時自動改用本機",
    ),
    ModelDefinition(
        id="gemma-4-31b-it",
        label="Google Gemma 4 31B",
        provider="gemini",
        cloud=True,
        description="Google 託管的大型開放模型",
    ),
    ModelDefinition(
        id="gemma4:e2b",
        label="本機 Ollama Gemma 4",
        provider="ollama",
        cloud=False,
        description="不使用雲端 API",
    ),
    ModelDefinition(
        id="rule",
        label="Rule 摘要模式",
        provider="rule",
        cloud=False,
        description="完全離線備援",
    ),
)
DEFAULT_CONVERSATION_MODEL = "gemini-3.5-flash-lite"
_BY_ID = {item.id: item for item in PUBLIC_CONVERSATION_MODELS}


def resolve_conversation_model(model_id: str) -> ModelDefinition:
    try:
        return _BY_ID[model_id]
    except KeyError:
        raise ValueError(f"unknown conversation model: {model_id}") from None


def public_model_catalog(*, gemini_configured: bool) -> dict[str, object]:
    return {
        "default_model": DEFAULT_CONVERSATION_MODEL,
        "gemini_configured": gemini_configured,
        "items": [asdict(item) for item in PUBLIC_CONVERSATION_MODELS],
    }
```

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_models.py -q
```

Expected: all catalog tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/conversation_models.py tests/test_conversation_models.py
git commit -m "feat(assistant): add fixed conversation model catalog"
```

---

### Task 2: One-Call Providers and Safe Gemini Errors

**Files:**

- Modify: `src/qingpu_insight/conversation_providers.py:27-30,129-319`
- Modify: `tests/test_conversation_providers.py`

**Interfaces:**

- Produces: `ConversationProvider.reply(..., repair_hint: str | None = None) -> ChatAnswerDraft` with exactly one HTTP request per invocation.
- Produces: `GeminiConversationProvider(api_key_getter: Callable[[], str | None], ...)`.
- Produces provider error codes: `gemini_auth_missing`, `gemini_auth_failed`, `gemini_rate_limited`, `gemini_unavailable`, `gemini_http_error`, `gemini_timeout`, `gemini_connection_error`, and `gemini_validation_error`.
- Consumer: Task 3.

- [ ] **Step 1: Add failing provider behavior tests**

```python
def test_gemini_resolves_key_for_each_request(fake_session):
    keys = iter(["first-key", "second-key"])
    provider = GeminiConversationProvider(
        api_key_getter=lambda: next(keys),
        session=fake_session,
    )
    provider.reply(model="gemini-3.5-flash-lite", question="q", context=_EMPTY_CONTEXT)
    provider.reply(model="gemini-3.5-flash-lite", question="q", context=_EMPTY_CONTEXT)
    assert fake_session.calls[0]["headers"]["x-goog-api-key"] == "first-key"
    assert fake_session.calls[1]["headers"]["x-goog-api-key"] == "second-key"


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "gemini_auth_failed"),
        (403, "gemini_auth_failed"),
        (429, "gemini_rate_limited"),
        (500, "gemini_unavailable"),
        (503, "gemini_unavailable"),
        (404, "gemini_http_error"),
    ],
)
def test_gemini_maps_http_status_without_exposing_body(
    status_code, expected_code, fake_session
):
    fake_session.response.status_code = status_code
    fake_session.response.text = "secret upstream body"
    provider = GeminiConversationProvider(
        api_key_getter=lambda: "test-key",
        session=fake_session,
    )
    with pytest.raises(ProviderError) as error:
        provider.reply(
            model="gemini-3.5-flash-lite",
            question="q",
            context=_EMPTY_CONTEXT,
        )
    assert error.value.code == expected_code
    assert "secret upstream body" not in str(error.value)


def test_gemini_invalid_draft_makes_one_http_call(fake_session):
    fake_session.response.json_data = {
        "candidates": [{"content": {"parts": [{"text": "not-json"}]}}]
    }
    provider = GeminiConversationProvider(
        api_key_getter=lambda: "test-key",
        session=fake_session,
    )
    with pytest.raises(ProviderError, match="gemini_validation_error"):
        provider.reply(
            model="gemini-3.5-flash-lite",
            question="q",
            context=_EMPTY_CONTEXT,
        )
    assert len(fake_session.calls) == 1
```

Add the equivalent one-call assertion for an invalid Ollama draft.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_providers.py -q
```

Expected: failures show the old static `api_key` constructor, generic HTTP code, and internal second request.

- [ ] **Step 3: Implement one-call provider attempts**

Update the protocol to include the optional repair hint:

```python
class ConversationProvider(Protocol):
    def reply(
        self,
        *,
        model: str,
        question: str,
        context: ConversationContext,
        repair_hint: str | None = None,
    ) -> ChatAnswerDraft: ...
```

Resolve the Gemini key immediately before the request:

```python
api_key = self._api_key_getter()
if not api_key:
    raise ProviderError("gemini_auth_missing")
headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": api_key,
}
```

Map status codes without reading or logging response bodies:

```python
if resp.status_code in {401, 403}:
    raise ProviderError("gemini_auth_failed")
if resp.status_code == 429:
    raise ProviderError("gemini_rate_limited")
if resp.status_code >= 500:
    raise ProviderError("gemini_unavailable")
if resp.status_code >= 400:
    raise ProviderError("gemini_http_error")
```

For both Gemini and Ollama, perform one transport call, strip a code fence when needed,
and convert response-envelope, JSON, or Pydantic failure directly to
`ProviderError("*_validation_error")`.
Append a fixed repair instruction only when `repair_hint` is provided; never embed a raw
exception or upstream response in the prompt.

Update `RuleConversationProvider.reply` to accept `repair_hint: str | None = None` and
ignore it, so all three registered providers satisfy the same protocol.

- [ ] **Step 4: Verify GREEN and provider regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_providers.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_validation.py -q
```

Expected: both suites pass and each provider invocation makes one HTTP request.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/conversation_providers.py tests/test_conversation_providers.py
git commit -m "refactor(assistant): make provider attempts single-call"
```

---

### Task 3: Retry and Fallback Executor

**Files:**

- Create: `src/qingpu_insight/conversation_fallback.py`
- Create: `tests/test_conversation_fallback.py`

**Interfaces:**

- Consumes: `resolve_conversation_model`, `ConversationProviderRegistry`, `ConversationContext`, `ValidatedChatAnswer`, and the existing validator callable.
- Produces:

```python
@dataclass(frozen=True)
class ReplyExecution:
    validated: ValidatedChatAnswer
    actual_provider: str
    actual_model: str
    fallback_reason: str | None


class ConversationFallbackExecutor:
    def execute(
        self,
        *,
        requested_model: str,
        question: str,
        context: ConversationContext,
        available_fact_ids: set[str],
        evidence_revision: int,
    ) -> ReplyExecution:
        ...
```

- Consumer: Task 5.

- [ ] **Step 1: Write failing route tests with counting fake providers**

```python
class CountingProvider:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def reply(self, *, model, question, context, repair_hint=None):
        self.calls.append((model, repair_hint))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def build_executor(google, ollama, rule, validator):
    registry = ConversationProviderRegistry()
    registry.register("gemini", google)
    registry.register("ollama", ollama)
    registry.register("rule", rule)
    return ConversationFallbackExecutor(
        provider_registry=registry,
        validator=validator,
    )


def test_google_success_does_not_call_fallbacks(valid_draft, validator):
    google = CountingProvider([valid_draft])
    ollama = CountingProvider([AssertionError("ollama must not run")])
    rule = CountingProvider([AssertionError("rule must not run")])
    executor = build_executor(google, ollama, rule, validator)
    result = executor.execute(
        requested_model="gemini-3.5-flash-lite",
        question="值得買嗎",
        context=CONTEXT,
        available_fact_ids={"f1"},
        evidence_revision=1,
    )
    assert result.actual_model == "gemini-3.5-flash-lite"
    assert result.fallback_reason is None
    assert len(google.calls) == 1


def test_google_retries_once_then_uses_local(valid_draft, validator):
    google = CountingProvider([
        ProviderError("gemini_timeout"),
        ProviderError("gemini_timeout"),
    ])
    ollama = CountingProvider([valid_draft])
    rule = CountingProvider([AssertionError("rule must not run")])
    executor = build_executor(google, ollama, rule, validator)
    result = executor.execute(
        requested_model="gemma-4-31b-it",
        question="值得買嗎",
        context=CONTEXT,
        available_fact_ids={"f1"},
        evidence_revision=1,
    )
    assert len(google.calls) == 2
    assert result.actual_provider == "ollama"
    assert result.actual_model == "gemma4:e2b"
    assert result.fallback_reason == "cloud_timeout"


def test_local_failure_uses_rule(valid_draft, validator):
    google = CountingProvider([
        ProviderError("gemini_unavailable"),
        ProviderError("gemini_unavailable"),
    ])
    ollama = CountingProvider([ProviderError("ollama_connection_error")])
    rule = CountingProvider([valid_draft])
    executor = build_executor(google, ollama, rule, validator)
    result = executor.execute(
        requested_model="gemini-3.5-flash-lite",
        question="值得買嗎",
        context=CONTEXT,
        available_fact_ids={"f1"},
        evidence_revision=1,
    )
    assert result.actual_provider == "rule"
    assert result.actual_model == "rule"
    assert result.fallback_reason == "local_unavailable"
```

Also test:

- first Google failure followed by Google success returns no fallback reason;
- grounding validation failure consumes the second Google attempt;
- `gemini_rate_limited`, authentication, unavailable, and invalid response map to the exact safe codes;
- direct Ollama uses Ollama once then Rule;
- direct Rule calls only Rule.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_fallback.py -q
```

Expected: collection fails because `conversation_fallback.py` does not exist.

- [ ] **Step 3: Implement deterministic execution routes**

Use these exact routes:

```python
_ROUTES = {
    "gemini": (
        ("gemini", None, 2),
        ("ollama", "gemma4:e2b", 1),
        ("rule", "rule", 1),
    ),
    "ollama": (
        ("ollama", "gemma4:e2b", 1),
        ("rule", "rule", 1),
    ),
    "rule": (("rule", "rule", 1),),
}
```

For the Gemini route, substitute the requested Google model when the route model is
`None`. Validate each returned draft immediately. A provider exception or
`GroundingValidationError` consumes one attempt. On a repeated Google validation failure,
pass only the fixed hint `"Return valid JSON with only known evidence fact IDs."`.

Map errors with a private function:

```python
_SAFE_REASON_BY_PROVIDER_ERROR = {
    "gemini_timeout": "cloud_timeout",
    "gemini_rate_limited": "cloud_rate_limited",
    "gemini_auth_missing": "cloud_auth_failed",
    "gemini_auth_failed": "cloud_auth_failed",
    "gemini_connection_error": "cloud_unavailable",
    "gemini_unavailable": "cloud_unavailable",
    "gemini_http_error": "cloud_unavailable",
    "gemini_validation_error": "cloud_invalid_response",
    "ollama_timeout": "local_unavailable",
    "ollama_connection_error": "local_unavailable",
    "ollama_http_error": "local_unavailable",
    "ollama_validation_error": "local_unavailable",
}
```

Never include `str(exception)` in `ReplyExecution`.
Map `GroundingValidationError` to `cloud_invalid_response` during a Google attempt and
`local_unavailable` during an Ollama attempt. If Rule also raises or fails validation,
raise `ProviderError("all_conversation_providers_unavailable")` without preserving any
raw exception text.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_fallback.py -q
```

Expected: all routing, retry, validation, and safe reason tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/conversation_fallback.py tests/test_conversation_fallback.py
git commit -m "feat(assistant): add cloud-to-local fallback executor"
```

---

### Task 4: Persist Actual Execution and Fallback Reason

**Files:**

- Create: `database/009_conversation_fallback_metadata.sql`
- Create: `tests/test_conversation_fallback_migration.py`
- Modify: `database/008_conversation_assistant_schema.sql:60-70`
- Modify: `src/qingpu_insight/conversation_repository.py:75-83,129-145,587-650`
- Modify: `tests/test_conversation_repository.py`

**Interfaces:**

- Produces: `MessageRecord.fallback_reason: str | None`.
- Changes: `append_message(..., fallback_reason: str | None = None) -> MessageRecord`.
- Consumer: Tasks 5 and 6.

- [ ] **Step 1: Write failing migration and repository tests**

```python
from pathlib import Path


def test_fallback_migration_is_idempotent_and_adds_safe_column():
    sql = Path("database/009_conversation_fallback_metadata.sql").read_text("utf-8")
    assert "INFORMATION_SCHEMA.COLUMNS" in sql
    assert "fallback_reason" in sql
    assert "VARCHAR(64)" in sql
    assert "PREPARE" in sql


def test_append_message_round_trips_fallback_reason(repository):
    message = repository.append_message(
        conversation_id="conversation-id",
        role="assistant",
        content="answer",
        evidence_revision=1,
        provider="ollama",
        model="gemma4:e2b",
        citations=["f1"],
        fallback_reason="cloud_timeout",
    )
    assert message.fallback_reason == "cloud_timeout"
    loaded = repository.get_messages(conversation_id="conversation-id", limit=10)
    assert loaded[0].fallback_reason == "cloud_timeout"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_fallback_migration.py tests/test_conversation_repository.py -q
```

Expected: migration file is missing and repository rejects `fallback_reason`.

- [ ] **Step 3: Add new-schema and upgrade-schema SQL**

Add this column to the 008 create table for fresh installs:

```sql
fallback_reason VARCHAR(64) NULL,
```

Create migration 009:

```sql
SET @fallback_reason_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversation_messages'
      AND COLUMN_NAME = 'fallback_reason'
);

SET @fallback_reason_sql = IF(
    @fallback_reason_exists = 0,
    'ALTER TABLE conversation_messages ADD COLUMN fallback_reason VARCHAR(64) NULL AFTER model',
    'SELECT 1'
);

PREPARE fallback_reason_stmt FROM @fallback_reason_sql;
EXECUTE fallback_reason_stmt;
DEALLOCATE PREPARE fallback_reason_stmt;
```

Add the field to `MessageRecord`, `_row_to_message`, `INSERT`, and returned records. Keep
the method default `None` so existing call sites and old fixtures remain compatible.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_fallback_migration.py tests/test_conversation_repository.py -q
```

Expected: migration and repository suites pass.

- [ ] **Step 5: Commit**

```powershell
git add database/008_conversation_assistant_schema.sql database/009_conversation_fallback_metadata.sql src/qingpu_insight/conversation_repository.py tests/test_conversation_fallback_migration.py tests/test_conversation_repository.py
git commit -m "feat(assistant): persist fallback execution metadata"
```

---

### Task 5: Fix Model Choice at Conversation Creation

**Files:**

- Modify: `src/qingpu_insight/conversation_service.py:66-91,169-231,321-424`
- Modify: `tests/test_conversation_service.py`

**Interfaces:**

- Consumes: `resolve_conversation_model` and `ConversationFallbackExecutor`.
- Changes: `create_conversation(*, model: str) -> ConversationRecord`.
- Changes: `start_reply(*, conversation_id: str, question: str, evidence_revision: int, idempotency_key: str) -> ConversationCommand`.
- The conversation row remains the source of requested provider/model.

- [ ] **Step 1: Write failing service tests**

```python
def test_create_conversation_resolves_provider_from_catalog(service, repository):
    service.create_conversation(model="gemma-4-31b-it")
    repository.create_conversation.assert_called_once_with(
        provider="gemini",
        model="gemma-4-31b-it",
    )


def test_create_conversation_rejects_unknown_model(service):
    with pytest.raises(ValueError, match="unknown conversation model"):
        service.create_conversation(model="custom-model")


def test_reply_uses_saved_model_and_persists_actual_execution(
    service, repository, fallback_executor
):
    repository.get_conversation.return_value = conversation(
        default_provider="gemini",
        default_model="gemini-3.5-flash-lite",
        active_evidence_revision=1,
    )
    fallback_executor.execute.return_value = ReplyExecution(
        validated=validated_answer(),
        actual_provider="ollama",
        actual_model="gemma4:e2b",
        fallback_reason="cloud_timeout",
    )
    service._run_reply("run-id", "conversation-id", "值得買嗎", 1)
    fallback_executor.execute.assert_called_once()
    assistant_call = repository.append_message.call_args_list[-1].kwargs
    assert assistant_call["provider"] == "ollama"
    assert assistant_call["model"] == "gemma4:e2b"
    assert assistant_call["fallback_reason"] == "cloud_timeout"
```

Add a test proving Rule conversations still reject free-form replies using the saved
conversation provider, not client input.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_service.py -q
```

Expected: service still accepts client provider/model and calls the registry directly.

- [ ] **Step 3: Integrate catalog and fallback executor**

Inject `reply_executor: ConversationFallbackExecutor` into `ConversationService`.
Resolve the provider only in `create_conversation`. In `start_reply`, load the
conversation, validate evidence revision, and reject free-form Rule replies based on
`conv.default_provider`.

Remove provider/model parameters from queued `_run_reply`. Load the conversation again
inside the worker before execution, then call:

```python
execution = self._reply_executor.execute(
    requested_model=conversation.default_model,
    question=question,
    context=context,
    available_fact_ids=available_fact_ids,
    evidence_revision=evidence_revision,
)
```

Persist `execution.validated`, actual provider/model, and fallback reason. Keep job errors
safe: known terminal failure uses code `reply_providers_unavailable` and message
`"all conversation providers unavailable"`; do not pass provider exception text into
`JobService.fail`.

- [ ] **Step 4: Verify GREEN and release-gate compatibility**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_service.py tests/test_conversation_release_gate.py -q
```

Expected: service and release-gate tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/conversation_service.py tests/test_conversation_service.py tests/test_conversation_release_gate.py
git commit -m "feat(assistant): fix model selection per conversation"
```

---

### Task 6: Model Catalog and Fixed-Reply HTTP Contracts

**Files:**

- Modify: `src/qingpu_insight/conversation_contracts.py:8-29`
- Modify: `src/qingpu_insight/conversation_web.py:61-106,133-152,340-434`
- Modify: `tests/test_conversation_contracts.py`
- Modify: `tests/test_conversation_web.py`

**Interfaces:**

- Produces: `GET /api/conversation-models`.
- Create payload: `{"model": "<allowed-id>"}`.
- Reply payload: `{"content": "...", "evidence_revision": 1}`.
- Message JSON adds `requested_provider`, `requested_model`, and `fallback_reason`.

- [ ] **Step 1: Write failing contract and endpoint tests**

```python
def test_create_request_contains_only_model():
    request = ConversationCreateRequest(model="gemini-3.5-flash-lite")
    assert request.model_dump() == {"model": "gemini-3.5-flash-lite"}
    with pytest.raises(ValidationError):
        ConversationCreateRequest(
            provider="gemini",
            model="gemini-3.5-flash-lite",
        )


def test_reply_request_rejects_model_override():
    with pytest.raises(ValidationError):
        ReplyCreateRequest(
            content="值得買嗎",
            evidence_revision=1,
            provider="gemini",
            model="gemma-4-31b-it",
        )


def test_model_catalog_endpoint_never_returns_key(client):
    response = client.get("/api/conversation-models")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["default_model"] == "gemini-3.5-flash-lite"
    assert payload["gemini_configured"] is True
    assert "key" not in response.get_data(as_text=True).lower()


def test_message_json_projects_requested_and_actual_models(client, repository):
    response = client.get("/api/conversations/conversation-id/messages?limit=50")
    item = response.get_json()["items"][0]
    assert item["requested_model"] == "gemini-3.5-flash-lite"
    assert item["model"] == "gemma4:e2b"
    assert item["fallback_reason"] == "cloud_timeout"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_contracts.py tests/test_conversation_web.py -q
```

Expected: old requests require provider/model and catalog endpoint is absent.

- [ ] **Step 3: Implement fixed HTTP contracts**

Change contracts to:

```python
class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=120)


class ReplyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    evidence_revision: int = Field(ge=1)
```

Inject `catalog_getter: Callable[[], dict[str, object]]` into the blueprint. Add the GET
route, pass only `model` to service creation, and pass no provider/model to
`start_reply`.

For message JSON, fetch the conversation once and project:

```python
{
    "requested_provider": conversation.default_provider,
    "requested_model": conversation.default_model,
    "provider": message.provider,
    "model": message.model,
    "fallback_reason": message.fallback_reason,
}
```

User messages keep actual provider/model/fallback fields as `None`.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_contracts.py tests/test_conversation_web.py -q
```

Expected: contract and route tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/conversation_contracts.py src/qingpu_insight/conversation_web.py tests/test_conversation_contracts.py tests/test_conversation_web.py
git commit -m "feat(assistant): expose fixed model HTTP contracts"
```

---

### Task 7: Dynamic Secret and Runtime Wiring

**Files:**

- Modify: `src/qingpu_insight/web.py:885-910,985-999,1118-1155`
- Modify: `tests/test_web.py`

**Interfaces:**

- Consumes: catalog, fallback executor, and dynamic `LocalSecretsStore`.
- Produces a Gemini provider registered even when the key is initially absent.
- Produces model catalog status that reflects a key saved from Admin without restarting.

- [ ] **Step 1: Write failing application wiring tests**

```python
def test_conversation_runtime_reads_qingpu_gemini_key(monkeypatch, app_factory):
    monkeypatch.setenv("QINGPU_GEMINI_API_KEY", "runtime-test-key")
    app = app_factory()
    provider = app.extensions["conversation_provider_registry"].get("gemini")
    assert provider._api_key_getter() == "runtime-test-key"


def test_saving_local_key_updates_catalog_without_restart(app, secrets_store):
    client = app.test_client()
    before = client.get("/api/conversation-models").get_json()
    assert before["gemini_configured"] is False
    secrets_store.set_gemini_key("new-test-key")
    after = client.get("/api/conversation-models").get_json()
    assert after["gemini_configured"] is True


def test_startup_applies_008_then_009_schema(connection_factory, project_root):
    _ensure_conversation_schema(project_root, connection_factory)
    executed = connection_factory.cursor_statements
    assert any("conversation_messages" in sql for sql in executed)
    assert any("fallback_reason" in sql for sql in executed)
```

Use a fake secrets store in tests; never write a real key.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -q
```

Expected: runtime reads the wrong `GEMINI_API_KEY`, registers Gemini only at startup,
and applies only migration 008.

- [ ] **Step 3: Wire live secrets and both migrations**

Change `_ensure_conversation_schema` to execute both files in order:

```python
migration_paths = [
    root / "database" / "008_conversation_assistant_schema.sql",
    root / "database" / "009_conversation_fallback_metadata.sql",
]
```

Create a live getter:

```python
def get_gemini_key() -> str | None:
    current = secrets_store.merged_env(os.environ)
    return current.get("QINGPU_GEMINI_API_KEY")
```

Always register:

```python
providers.register(
    "gemini",
    GeminiConversationProvider(api_key_getter=get_gemini_key),
)
```

Construct `ConversationFallbackExecutor` and inject it into `ConversationService`.
Pass a catalog getter using `bool(get_gemini_key())` to the blueprint. Expose the registry
in `app.extensions` only if existing app wiring tests use extensions; otherwise inject a
capturing fake and assert through public behavior.

- [ ] **Step 4: Verify GREEN and secret tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py tests/test_local_secrets.py -q
```

Expected: both suites pass; saving a fake key changes status without restart.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/web.py tests/test_web.py
git commit -m "fix(assistant): wire live Gemini secret and fallback runtime"
```

---

### Task 8: Beginner-First Homepage Model Selector

**Files:**

- Modify: `src/qingpu_insight/templates/index.html:12-25`
- Modify: `src/qingpu_insight/static/home_assistant.js:41-44,112-155`
- Modify: `src/qingpu_insight/static/app.css`
- Modify: `tests/js/home_assistant_contract.cjs`

**Interfaces:**

- Consumes: `GET /api/conversation-models`.
- Produces: `buildCreatePayload(model) -> {model}`.
- Produces one `#assistant-model` select and `#assistant-model-help` status text.

- [ ] **Step 1: Write failing JavaScript contract tests**

```javascript
assert.deepStrictEqual(
  hooks.buildCreatePayload("gemini-3.5-flash-lite"),
  { model: "gemini-3.5-flash-lite" }
);

assert.strictEqual(
  hooks.modelStatusText(
    { provider: "gemini", cloud: true },
    false
  ),
  "尚未設定 Gemini API Key；送出後將自動使用本機模型"
);

assert.strictEqual(
  hooks.modelStatusText(
    { provider: "ollama", cloud: false },
    true
  ),
  "本機模式，不使用 Google API"
);
```

Update the fetch fixture to return the exact four catalog items and assert the create POST
body contains no `provider`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
node tests/js/home_assistant_contract.cjs
```

Expected: old payload contains provider and the model status helper is absent.

- [ ] **Step 3: Replace free-form controls**

Render:

```html
<label for="assistant-model">分析模型</label>
<select id="assistant-model" required disabled>
  <option value="">正在載入模型…</option>
</select>
<p id="assistant-model-help" class="model-help" aria-live="polite"></p>
```

On setup, fetch `/api/conversation-models`, add only returned items, select
`default_model`, enable the select, and render safe status text. Submit:

```javascript
function buildCreatePayload(model) {
  return { model: model };
}
```

If catalog loading fails, keep submit disabled and show
`"模型目錄暫時無法載入，請重新整理頁面"`; do not restore free-text entry.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
node tests/js/home_assistant_contract.cjs
```

Expected: contract prints its success line and exits zero.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/templates/index.html src/qingpu_insight/static/home_assistant.js src/qingpu_insight/static/app.css tests/js/home_assistant_contract.cjs
git commit -m "feat(assistant): add guided homepage model selector"
```

---

### Task 9: Conversation Actual-Model and Fallback Presentation

**Files:**

- Modify: `src/qingpu_insight/templates/assistant.html:12-32`
- Modify: `src/qingpu_insight/static/assistant.js:65-68,180-200,285-286,391-416,510-518`
- Modify: `src/qingpu_insight/static/assistant.css`
- Modify: `tests/js/assistant_contract.cjs`

**Interfaces:**

- Reply POST contains only `content` and `evidence_revision`.
- Requested model comes from conversation `default_model`.
- Actual model and fallback reason come from each assistant message.

- [ ] **Step 1: Write failing conversation UI contracts**

```javascript
assert.deepStrictEqual(
  hooks.buildReplyPayload("值得買嗎", 3),
  { content: "值得買嗎", evidence_revision: 3 }
);

assert.strictEqual(
  hooks.fallbackLabel({
    provider: "ollama",
    model: "gemma4:e2b",
    fallback_reason: "cloud_timeout",
  }),
  "Gemini 連線逾時，已改用本機 Gemma 4"
);

assert.strictEqual(
  hooks.actualModelLabel({ provider: "rule", model: "rule" }),
  "Rule／離線摘要"
);
```

Add exact labels for every safe fallback reason:

```javascript
{
  cloud_timeout: "Gemini 連線逾時",
  cloud_rate_limited: "Gemini 暫時達到使用限制",
  cloud_unavailable: "Gemini 暫時無法使用",
  cloud_auth_failed: "Gemini API Key 無法使用",
  cloud_invalid_response: "Gemini 回覆未通過資料驗證",
  local_unavailable: "本機 Ollama 無法使用，已改用離線摘要"
}
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
node tests/js/assistant_contract.cjs
```

Expected: payload still contains provider/model and label helpers are absent.

- [ ] **Step 3: Render requested, actual, and fallback state**

Change:

```javascript
function buildReplyPayload(content, evidenceRevision) {
  return {
    content: content,
    evidence_revision: evidenceRevision,
  };
}
```

The page header badge shows `conversation.default_model`. Assistant message headers show
the safe actual-model label. When `fallback_reason` is non-null, append:

```html
<p class="fallback-notice" role="status"></p>
```

Use only the fixed error-label map; unknown fallback codes render
`"已自動切換備援模型"` and never render the raw code. Rule messages always receive a
visible `offline-summary` class and label.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
node tests/js/assistant_contract.cjs
```

Expected: all payload and presentation contracts pass.

- [ ] **Step 5: Commit**

```powershell
git add src/qingpu_insight/templates/assistant.html src/qingpu_insight/static/assistant.js src/qingpu_insight/static/assistant.css tests/js/assistant_contract.cjs
git commit -m "feat(assistant): explain actual model and fallback state"
```

---

### Task 10: Documentation, Security Gate, and Real Smoke Test

**Files:**

- Modify: `docs/operations/listing-conversation-assistant.md`
- Modify: `README.md`
- Test: all changed Python and JavaScript suites.

**Interfaces:**

- Documents exact model choices, retry/fallback order, API key workflow, status labels,
  and smoke procedure.
- Produces no new runtime interface.

- [ ] **Step 1: Write documentation assertions before editing docs**

Run:

```powershell
@'
from pathlib import Path

readme = Path("README.md").read_text("utf-8")
ops = Path("docs/operations/listing-conversation-assistant.md").read_text("utf-8")
required = [
    "gemini-3.5-flash-lite",
    "gemma-4-31b-it",
    "gemma4:e2b",
    "Rule",
    "重試一次",
    "instance/secrets.env",
]
missing = [item for item in required if item not in readme + ops]
raise SystemExit("missing: " + ", ".join(missing) if missing else 0)
'@ | .\.venv\Scripts\python.exe -
```

Expected: non-zero with a list of missing model/fallback documentation.

- [ ] **Step 2: Update operator and portfolio documentation**

Document:

- the four visible model choices;
- `Google selected model x2 → Ollama gemma4:e2b → Rule`;
- requested versus actual model meaning;
- Admin key save/test/delete workflow;
- no-restart key activation;
- safe fallback reason meanings;
- why the project uses a fixed catalog instead of dynamic model discovery;
- the two official Google model documentation links from the approved spec;
- a warning to revoke any key pasted into chat, terminal history, or screenshots.

- [ ] **Step 3: Re-run the documentation assertion**

Run the exact PowerShell/Python block from Step 1.

Expected: exit code zero.

- [ ] **Step 4: Run focused and full automated gates**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_models.py tests/test_conversation_providers.py tests/test_conversation_fallback.py tests/test_conversation_fallback_migration.py tests/test_conversation_repository.py tests/test_conversation_service.py tests/test_conversation_contracts.py tests/test_conversation_web.py tests/test_web.py -q
node tests/js/home_assistant_contract.cjs
node tests/js/assistant_contract.cjs
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight tests
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: every command exits zero with no warnings introduced by this feature.

- [ ] **Step 5: Run a secret-leak gate**

Run:

```powershell
git diff --check
git diff --cached --check
git grep -n -E "AQ\.[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{20,}" -- . ":(exclude).env" ":(exclude)instance/secrets.env"
```

Expected: diff checks are clean and `git grep` returns no matches. Do not print or grep the
real replacement key.

- [ ] **Step 6: Perform real local smoke tests with a newly rotated key**

1. Revoke the key previously pasted into chat.
2. Start the existing application normally.
3. In Admin, save a newly created test Gemini API key; do not restart.
4. Confirm `GET /api/conversation-models` reports `gemini_configured: true` without
   returning the key.
5. Create one conversation with `gemini-3.5-flash-lite`, import a supported 591 detail
   URL, ask one evidence-grounded question, and confirm requested and actual model match.
6. Create one conversation with `gemma-4-31b-it`, ask the same question, and confirm the
   answer passes citation validation.
7. Temporarily stop Ollama only after both Google success tests. Use an intentionally
   invalid key entered through Admin, ask a question, and confirm the UI reaches Rule with
   a safe fallback message and no raw provider error.
8. Restore the new valid key and restart Ollama; confirm the catalog and local status are
   healthy.

Expected: both Google models succeed; the controlled failure follows the exact fallback
route and never exposes a key.

- [ ] **Step 7: Commit documentation and final test adjustments**

```powershell
git add README.md docs/operations/listing-conversation-assistant.md
git commit -m "docs(assistant): explain model selection and fallback"
```

- [ ] **Step 8: Final review gate**

Run:

```powershell
git status --short
git log --oneline --max-count=10
```

Expected: only known pre-existing local artifacts remain untracked; all feature files are
committed in small task commits. Request `superpowers:requesting-code-review` before
declaring the implementation complete.
