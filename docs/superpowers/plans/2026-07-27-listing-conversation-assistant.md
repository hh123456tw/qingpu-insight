# Listing Conversation Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an AI-first 591 listing assistant that captures one supported sale or newhouse detail URL into an immutable conversation snapshot, grounds every property-specific answer in versioned evidence, persists and resumes conversations, and keeps the existing market dashboard available as secondary content.

**Architecture:** Add a conversation domain beside the existing report and valuation domains. A dedicated Flask blueprint accepts commands, a service layer coordinates visible-browser capture, immutable snapshots, evidence generation, providers, validation, and existing tracked jobs, and a MySQL repository owns conversation history. The homepage starts the workflow; a separate two-column workbench displays evidence and chat. Provider output is never returned until deterministic citation validation passes.

**Tech Stack:** Python 3.11, Flask, PyMySQL/MySQL, Pydantic, Selenium/visible Chrome, BeautifulSoup, existing valuation and market services, Ollama/Gemini adapters, vanilla JavaScript, Jinja, CSS, pytest, Node contract tests, Ruff.

## Global Constraints

- Implement every task test-first. Observe the named test fail before writing production code.
- Do not write captured 591 detail data into `listing_current`, official snapshots, or model-training tables.
- Support exactly one active listing per conversation in this release. Keep foreign keys and service interfaces capable of supporting multiple listings later.
- Accept only HTTPS 591 sale and newhouse detail URLs:
  - Sale final host: `sale.591.com.tw`; path: `^/home/house/detail/[1-9][0-9]*/[1-9][0-9]*\.html$`
  - Newhouse final host: `newhouse.591.com.tw`; path: `^/[1-9][0-9]*(?:/detail)?/?$`
  - Initial short host: `591.to`; token path contains 2–64 ASCII letters, digits, `_`, or `-`
  - Follow at most three HTTPS redirects; validate every redirect target and the final URL
  - Remove query strings and fragments before persistence
- Reject rentals, result/search pages, non-HTTPS URLs, embedded credentials, non-default ports, IP literals, and unsupported subdomains.
- Selenium capture must remain visible. Verification/CAPTCHA pages end in `needs_attention`; do not bypass them.
- Persist sanitized structured fields only. Never persist full raw HTML, browser cookies, authorization headers, or provider secrets.
- Every property-specific factual claim or number in a provider answer must cite one or more current evidence fact IDs. General advice must appear in a separately labeled `一般建議` section.
- Provider replies are non-streaming in this release. The UI may show stages, but it reveals the answer only after validation succeeds.
- A refresh appends a snapshot and evidence revision. Existing messages remain bound to the evidence revision used when they were generated.
- Reply context is the rolling summary, the latest 12 messages, and the selected current evidence pack. Do not send the entire history to the provider.
- Rule mode is not free-form chat. It returns a fixed evidence summary and suggested questions.
- Use the existing job state machine. When verification is required, transition `running -> failed -> needs_attention`; do not add an illegal direct transition.
- Command endpoints must retain the project’s loopback, Host, JSON, and CSRF protections. GET endpoints must not mutate state.
- Use focused modules and JavaScript files. Do not continue growing `web.py` or `static/app.js` with the whole feature.
- Preserve existing admin, model observatory, map, report, and valuation behavior.
- Run the focused test after each red/green step. Run the full release gate in Task 12.

---

## Task 1: Define the Conversation Contracts and Strict 591 URL Policy

**Files:**

- Create: `src/qingpu_insight/conversation_contracts.py`
- Create: `src/qingpu_insight/conversation_urls.py`
- Create: `tests/test_conversation_contracts.py`
- Create: `tests/test_conversation_urls.py`

**Interfaces:**

```python
# src/qingpu_insight/conversation_urls.py
from dataclasses import dataclass
from typing import Literal

ListingType = Literal["sale", "newhouse"]


@dataclass(frozen=True)
class Initial591Url:
    request_url: str
    kind: Literal["direct", "short"]


@dataclass(frozen=True)
class Validated591DetailUrl:
    canonical_url: str
    listing_type: ListingType
    source_listing_id: str


class Unsupported591Url(ValueError):
    pass


def parse_initial_591_url(raw_url: str) -> Initial591Url: ...
def validate_redirect_target(raw_url: str) -> None: ...
def validate_final_591_url(raw_url: str) -> Validated591DetailUrl: ...
```

```python
# src/qingpu_insight/conversation_contracts.py
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

ProviderName = Literal["ollama", "gemini", "rule"]
ConversationStatus = Literal["empty", "importing", "ready", "needs_attention"]
MessageRole = Literal["user", "assistant", "system"]


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: ProviderName = "ollama"
    model: str = Field(min_length=1, max_length=120)


class ListingImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)


class ReplyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    provider: ProviderName
    model: str = Field(min_length=1, max_length=120)
    evidence_revision: int = Field(ge=1)


class ConversationView(BaseModel):
    id: str
    title: str
    status: ConversationStatus
    default_provider: ProviderName
    default_model: str
    active_evidence_revision: int | None
    created_at: datetime
    updated_at: datetime
```

- [ ] Write `tests/test_conversation_urls.py` with table-driven tests for accepted sale URLs, both accepted newhouse forms, query/fragment removal, and accepted `591.to` initial URLs.
- [ ] Add rejection cases for HTTP, credentials, ports, IP literals, rental hosts, search/list pages, Unicode lookalike hosts, unsupported subdomains, malformed IDs, encoded slashes, and short tokens outside 2–64 allowed characters.
- [ ] Write `tests/test_conversation_contracts.py` proving extra JSON fields are rejected, message length limits apply, `rule` is a valid provider, and `evidence_revision` must be positive.
- [ ] Run the tests and confirm they fail because the modules do not exist:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_urls.py tests/test_conversation_contracts.py -q
```

- [ ] Implement URL parsing with `urllib.parse.urlsplit`, ASCII hostname comparison, explicit scheme/port/user-info checks, compiled full-match path expressions, and canonical URL reconstruction.
- [ ] Implement the Pydantic request and view contracts exactly as declared above.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Run Ruff on the new modules:

```powershell
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/conversation_contracts.py src/qingpu_insight/conversation_urls.py tests/test_conversation_contracts.py tests/test_conversation_urls.py
```

- [ ] Commit:

```powershell
git add src/qingpu_insight/conversation_contracts.py src/qingpu_insight/conversation_urls.py tests/test_conversation_contracts.py tests/test_conversation_urls.py
git commit -m "feat(assistant): define conversation and 591 URL contracts"
```

## Task 2: Add the Immutable Conversation Schema and Repository

**Files:**

- Create: `database/008_conversation_assistant_schema.sql`
- Create: `src/qingpu_insight/conversation_repository.py`
- Create: `tests/test_conversation_repository.py`

**Schema:**

Create these tables using the project’s existing charset, timestamp, JSON, index, and foreign-key conventions:

```sql
CREATE TABLE conversations (
    id CHAR(36) PRIMARY KEY,
    title VARCHAR(160) NOT NULL,
    status VARCHAR(32) NOT NULL,
    default_provider VARCHAR(32) NOT NULL,
    default_model VARCHAR(120) NOT NULL,
    active_listing_id CHAR(36) NULL,
    active_evidence_revision INT NULL,
    rolling_summary TEXT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    deleted_at DATETIME(6) NULL
);

CREATE TABLE conversation_listings (
    id CHAR(36) PRIMARY KEY,
    conversation_id CHAR(36) NOT NULL,
    position SMALLINT UNSIGNED NOT NULL,
    listing_type VARCHAR(16) NULL,
    source_listing_id VARCHAR(64) NULL,
    canonical_url VARCHAR(2048) NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_conversation_listing_position (conversation_id, position),
    CONSTRAINT fk_conversation_listing_conversation
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE conversation_listing_snapshots (
    id CHAR(36) PRIMARY KEY,
    conversation_listing_id CHAR(36) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    captured_at DATETIME(6) NOT NULL,
    source_url VARCHAR(2048) NOT NULL,
    structured_payload JSON NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    UNIQUE KEY uq_conversation_snapshot_revision (conversation_listing_id, revision),
    CONSTRAINT fk_conversation_snapshot_listing
        FOREIGN KEY (conversation_listing_id) REFERENCES conversation_listings(id) ON DELETE CASCADE
);

CREATE TABLE conversation_evidence_packs (
    id CHAR(36) PRIMARY KEY,
    conversation_id CHAR(36) NOT NULL,
    conversation_listing_snapshot_id CHAR(36) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    generated_at DATETIME(6) NOT NULL,
    facts JSON NOT NULL,
    valuation JSON NULL,
    comparables JSON NOT NULL,
    limitations JSON NOT NULL,
    UNIQUE KEY uq_conversation_evidence_revision (conversation_id, revision),
    CONSTRAINT fk_conversation_evidence_conversation
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    CONSTRAINT fk_conversation_evidence_snapshot
        FOREIGN KEY (conversation_listing_snapshot_id)
        REFERENCES conversation_listing_snapshots(id) ON DELETE RESTRICT
);

CREATE TABLE conversation_messages (
    id CHAR(36) PRIMARY KEY,
    conversation_id CHAR(36) NOT NULL,
    sequence_no BIGINT UNSIGNED NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    evidence_revision INT NULL,
    provider VARCHAR(32) NULL,
    model VARCHAR(120) NULL,
    citations JSON NOT NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_conversation_message_sequence (conversation_id, sequence_no),
    CONSTRAINT fk_conversation_message_conversation
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
```

Add the `conversations.active_listing_id` foreign key after both parent tables exist. Use an index on `(updated_at, id)` for history pagination and `(conversation_id, sequence_no)` for message pagination.

**Repository interface:**

```python
class ConversationRepository:
    def create_conversation(
        self, *, provider: str, model: str, title: str = "新的物件分析"
    ) -> ConversationRecord: ...

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None: ...
    def list_conversations(
        self, *, limit: int, before: tuple[datetime, str] | None = None
    ) -> list[ConversationRecord]: ...
    def add_initial_listing(self, *, conversation_id: str) -> ConversationListingRecord: ...
    def append_snapshot(self, *, listing_id: str, source_url: str, payload: dict) -> SnapshotRecord: ...
    def append_evidence_pack(
        self, *, conversation_id: str, snapshot_id: str, pack: dict
    ) -> EvidencePackRecord: ...
    def activate_evidence(
        self, *, conversation_id: str, listing_id: str, revision: int
    ) -> None: ...
    def append_message(
        self, *, conversation_id: str, role: str, content: str,
        evidence_revision: int | None, provider: str | None,
        model: str | None, citations: list[str]
    ) -> MessageRecord: ...
    def get_messages(
        self, *, conversation_id: str, limit: int = 50, before_sequence: int | None = None
    ) -> list[MessageRecord]: ...
    def set_status(self, *, conversation_id: str, status: str) -> None: ...
    def set_rolling_summary(self, *, conversation_id: str, summary: str) -> None: ...
    def delete_conversation(self, conversation_id: str) -> bool: ...
```

- [ ] Write repository tests using the project’s MySQL test fixture. Cover creation, one initial listing, append-only snapshot/evidence revision increments, deterministic SHA256 of canonical JSON, message sequence allocation, 50-message default pagination, history cursor ordering, active evidence switching, cascade delete, and not-found behavior.
- [ ] Add a concurrency test proving two message inserts receive distinct sequence numbers within transactions.
- [ ] Add a test proving `add_initial_listing` rejects a second position while the first-version service invariant is active.
- [ ] Run and confirm the migration/repository tests fail:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_repository.py -q
```

- [ ] Add the migration and implement immutable record dataclasses plus the repository using one connection per method, parameterized SQL, commit/rollback, `SELECT ... FOR UPDATE` for counters, and JSON serialization with stable key ordering.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Run Ruff on the repository and test.
- [ ] Commit:

```powershell
git add database/008_conversation_assistant_schema.sql src/qingpu_insight/conversation_repository.py tests/test_conversation_repository.py
git commit -m "feat(assistant): persist conversation snapshots and messages"
```

## Task 3: Define and Enforce the Grounded Chat Answer Contract

**Files:**

- Create: `src/qingpu_insight/conversation_validation.py`
- Create: `tests/test_conversation_validation.py`

**Interfaces:**

```python
from pydantic import BaseModel, ConfigDict, Field


class PropertyClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)
    fact_ids: list[str] = Field(min_length=1, max_length=12)


class ChatAnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=8000)
    property_claims: list[PropertyClaim] = Field(default_factory=list, max_length=30)
    general_guidance: list[str] = Field(default_factory=list, max_length=12)
    suggested_questions: list[str] = Field(default_factory=list, max_length=6)


class ValidatedChatAnswer(BaseModel):
    answer: str
    citations: list[str]
    evidence_revision: int
    general_guidance: list[str]
    suggested_questions: list[str]


class GroundingValidationError(ValueError):
    pass


def validate_chat_answer(
    draft: ChatAnswerDraft,
    *,
    available_fact_ids: set[str],
    evidence_revision: int,
) -> ValidatedChatAnswer: ...
```

Validation rules:

- Reject unknown, empty, or duplicate fact IDs within a claim.
- Reject property claims whose cited fact IDs are not in the selected evidence revision.
- Reject drafts that place property-specific numbers in `general_guidance`.
- Render `一般建議` only when the list is non-empty.
- Return a stable, de-duplicated citation list in first-use order.

- [ ] Write failing tests for valid multi-fact claims, unknown facts, missing facts, duplicate facts, citation ordering, empty guidance, numeric property assertions disguised as general guidance, and the correct evidence revision.
- [ ] Add tests for malformed provider JSON and Pydantic extra-field rejection.
- [ ] Run and confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_validation.py -q
```

- [ ] Implement the models and validator. Use a conservative numeric-token check for general guidance; guidance containing a digit must be rejected and regenerated or replaced with a fixed safe message.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Commit:

```powershell
git add src/qingpu_insight/conversation_validation.py tests/test_conversation_validation.py
git commit -m "feat(assistant): validate grounded chat answers"
```

## Task 4: Parse Sanitized 591 Sale and Newhouse Detail Pages

**Files:**

- Create: `src/qingpu_insight/conversation_listing_parser.py`
- Create: `tests/test_conversation_listing_parser.py`
- Create: `tests/fixtures/591_sale_detail.html`
- Create: `tests/fixtures/591_newhouse_detail.html`
- Create: `tests/fixtures/591_verification.html`

**Interfaces:**

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ParsedListingDetail:
    listing_type: Literal["sale", "newhouse"]
    source_listing_id: str
    title: str
    total_price_twd: int | None
    unit_price_twd_per_ping: int | None
    area_ping: Decimal | None
    layout: str | None
    address: str | None
    community_name: str | None
    builder_name: str | None
    building_type: str | None
    floor: str | None
    total_floors: int | None
    age_years: Decimal | None
    parking_type: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    source_updated_text: str | None


class ListingPageVerificationRequired(RuntimeError):
    pass


class ListingDetailParseError(ValueError):
    pass


def parse_listing_detail(
    html: str, *, canonical_url: str, listing_type: Literal["sale", "newhouse"]
) -> ParsedListingDetail: ...
```

- [ ] Create small sanitized fixtures containing only the page fragments and structured data needed by the parser. Remove seller names, phone numbers, cookies, and unrelated copyrighted page content.
- [ ] Write failing tests proving the parser extracts IDs, title, price, unit price, area, layout, address, community/builder, building/floor/age/parking, coordinates, and source update text.
- [ ] Add tests proving missing optional fields become `None`, inconsistent listing IDs fail, implausible negative values fail, and verification fixture raises `ListingPageVerificationRequired`.
- [ ] Run and confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_listing_parser.py -q
```

- [ ] Implement structured-data-first parsing, then explicit DOM fallbacks. Reuse numeric normalization helpers from `listing_591.py` only where their semantics match detail pages.
- [ ] Ensure exception messages name the missing/invalid field without including raw HTML.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Commit:

```powershell
git add src/qingpu_insight/conversation_listing_parser.py tests/test_conversation_listing_parser.py tests/fixtures/591_sale_detail.html tests/fixtures/591_newhouse_detail.html tests/fixtures/591_verification.html
git commit -m "feat(assistant): parse supported 591 detail pages"
```

## Task 5: Capture a Detail Page with Visible Chrome and Safe Redirects

**Files:**

- Create: `src/qingpu_insight/conversation_listing_capture.py`
- Create: `tests/test_conversation_listing_capture.py`

**Interfaces:**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CapturedListing:
    final_url: str
    detail: ParsedListingDetail


class DetailPageBrowser:
    def capture(self, initial_url: Initial591Url) -> CapturedListing: ...
```

The browser adapter must:

- Start Chrome without `--headless`.
- Apply existing local Chrome/driver discovery.
- Navigate only to the prevalidated initial URL.
- Observe at most three redirects.
- Reject any non-HTTPS, unsupported host, credentials, port, or IP redirect before accepting content.
- Wait for a supported detail-page marker or verification marker with a bounded timeout.
- Parse the final page, return structured data, and close the driver in `finally`.

- [ ] Write failing unit tests with a fake WebDriver for direct navigation, allowed short redirect, excessive redirects, redirect to private IP, redirect to an unrelated domain, verification page, timeout, parser failure, and driver cleanup on every outcome.
- [ ] Add a test asserting Chrome options do not contain `--headless`.
- [ ] Run and confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_listing_capture.py -q
```

- [ ] Implement the adapter using injected driver factory, clock, and parser so tests never open a real browser.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Commit:

```powershell
git add src/qingpu_insight/conversation_listing_capture.py tests/test_conversation_listing_capture.py
git commit -m "feat(assistant): capture 591 details with visible Chrome"
```

## Task 6: Import and Refresh Immutable Listing Snapshots

**Files:**

- Create: `src/qingpu_insight/conversation_import.py`
- Create: `tests/test_conversation_import.py`

**Interfaces:**

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ListingImportResult:
    conversation_id: str
    listing_id: str
    snapshot_id: str
    snapshot_revision: int
    evidence_revision: int
    outcome: Literal["ready", "needs_attention"]


class ConversationImportService:
    def import_initial_listing(
        self, *, conversation_id: str, raw_url: str
    ) -> ListingImportResult: ...

    def refresh_listing(self, *, conversation_id: str) -> ListingImportResult: ...
```

- [ ] Write failing service tests with repository, browser, and evidence-builder fakes. Cover initial import stages, URL rejection before browser launch, snapshot sanitization, evidence activation, title update, and status transitions.
- [ ] Add refresh tests proving a new snapshot/evidence revision is appended even when the page changed only slightly, older revisions remain readable, and old messages are untouched.
- [ ] Add failure tests proving verification maps to `needs_attention`, parse failure maps to `failed`, and no write reaches official listing repositories.
- [ ] Run and confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_import.py -q
```

- [ ] Implement the service with stage callbacks:

```python
ImportStage = Literal[
    "validating_url",
    "opening_browser",
    "capturing_listing",
    "building_evidence",
    "ready",
]
```

- [ ] Serialize only fields from `ParsedListingDetail`, add capture metadata, and calculate snapshot hashes in the repository.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Commit:

```powershell
git add src/qingpu_insight/conversation_import.py tests/test_conversation_import.py
git commit -m "feat(assistant): import immutable conversation listings"
```

## Task 7: Build Versioned Evidence from Listing, Market, and Valuation Data

**Files:**

- Create: `src/qingpu_insight/conversation_evidence.py`
- Create: `tests/test_conversation_evidence.py`

**Interfaces:**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceFact:
    id: str
    label: str
    value: str
    source: str
    observed_at: str | None


@dataclass(frozen=True)
class ConversationEvidence:
    facts: tuple[EvidenceFact, ...]
    valuation: dict | None
    comparables: tuple[dict, ...]
    limitations: tuple[str, ...]


class ConversationEvidenceBuilder:
    def build(self, *, snapshot: SnapshotRecord) -> ConversationEvidence: ...
```

Stable fact-ID namespaces:

- `listing.title`, `listing.price`, `listing.unit_price`, `listing.area`, `listing.layout`
- `listing.address`, `listing.community`, `listing.builder`, `listing.building_type`
- `listing.floor`, `listing.age`, `listing.parking`, `listing.location`
- `valuation.point`, `valuation.low`, `valuation.high`, `valuation.confidence`
- `market.median_unit_price`, `market.sample_size`, `market.period`
- Comparable IDs: `comparable.<rank>.price`, `.unit_price`, `.distance`, `.date`

- [ ] Write failing tests for sale evidence, newhouse evidence, missing coordinates, unavailable model, stale model, low confidence, fewer than three comparables, deterministic fact ordering, and stable fact IDs across refreshes.
- [ ] Add a test proving the valuation request cannot use impossible floor data such as floor 20 of 10; it must add a limitation and omit valuation rather than silently correcting the listing.
- [ ] Add a test proving evidence records the official data/model version used for reproducibility.
- [ ] Run and confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_evidence.py -q
```

- [ ] Implement adapters around the existing valuation and market services. Keep provider-independent text formatting in this module.
- [ ] Limit comparables to the best 10 and include the selection reason, distance, and transaction date.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Commit:

```powershell
git add src/qingpu_insight/conversation_evidence.py tests/test_conversation_evidence.py
git commit -m "feat(assistant): build versioned listing evidence"
```

## Task 8: Add Ollama, Gemini, and Rule Conversation Providers

**Files:**

- Create: `src/qingpu_insight/conversation_providers.py`
- Create: `tests/test_conversation_providers.py`

**Interfaces:**

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ConversationContext:
    rolling_summary: str | None
    recent_messages: tuple[dict, ...]
    evidence_revision: int
    evidence_facts: tuple[EvidenceFact, ...]
    limitations: tuple[str, ...]


class ConversationProvider(Protocol):
    def reply(
        self, *, model: str, question: str, context: ConversationContext
    ) -> ChatAnswerDraft: ...


class ConversationProviderRegistry:
    def get(self, provider: str) -> ConversationProvider: ...
```

- [ ] Write failing tests proving prompt/context construction includes only the rolling summary, latest 12 messages, current revision facts, limitations, fact-ID citation rules, and the user’s current question.
- [ ] Add Ollama and Gemini adapter tests for valid JSON, malformed JSON, timeouts, secret redaction, and one repair attempt after validation failure.
- [ ] Add Rule provider tests proving it ignores arbitrary free-form generation and returns only a fixed evidence summary plus suggested questions.
- [ ] Run and confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_providers.py -q
```

- [ ] Implement provider adapters by reusing existing HTTP clients/configuration from report providers while using the new `ChatAnswerDraft` schema.
- [ ] Ensure logs contain provider/model, duration, and result class, but never the Gemini key, entire prompt, raw HTML, or cookies.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Commit:

```powershell
git add src/qingpu_insight/conversation_providers.py tests/test_conversation_providers.py
git commit -m "feat(assistant): add grounded conversation providers"
```

## Task 9: Orchestrate Replies and Existing Tracked Jobs

**Files:**

- Create: `src/qingpu_insight/conversation_service.py`
- Create: `tests/test_conversation_service.py`
- Modify: `src/qingpu_insight/jobs.py`

**Interfaces:**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationCommand:
    run_id: str
    conversation_id: str


class ConversationService:
    def create_conversation(self, *, provider: str, model: str) -> ConversationRecord: ...
    def start_import(
        self, *, conversation_id: str, raw_url: str, idempotency_key: str
    ) -> ConversationCommand: ...
    def start_refresh(
        self, *, conversation_id: str, idempotency_key: str
    ) -> ConversationCommand: ...
    def start_reply(
        self, *, conversation_id: str, question: str, provider: str,
        model: str, evidence_revision: int, idempotency_key: str
    ) -> ConversationCommand: ...
    def delete_conversation(self, *, conversation_id: str) -> bool: ...
```

Job types:

```python
CONVERSATION_IMPORT = "conversation_import"
CONVERSATION_REFRESH = "conversation_refresh"
CONVERSATION_REPLY = "conversation_reply"
```

- [ ] Write failing tests for job creation, executor submission, idempotent retries, duplicate active reply rejection, selected-revision mismatch, missing evidence, provider lookup, user-message persistence, assistant-message persistence, and citation validation.
- [ ] Add tests proving provider or validation failure does not append a partial assistant message.
- [ ] Add a one-repair test: first draft fails grounding, repaired draft passes. Add a terminal test where both attempts fail and the job ends failed with a safe public error.
- [ ] Add a verification test proving the import worker calls job `fail` before `needs_attention`.
- [ ] Add context tests proving only the latest 12 messages plus rolling summary are sent and older assistant messages retain their original evidence revision.
- [ ] Run and confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_service.py -q
```

- [ ] Implement the service with injected repository, import service, provider registry, validator, `JobService`, and `LocalJobExecutor`.
- [ ] Generate/update the rolling summary only after a successful assistant reply and keep it under 4,000 characters.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Commit:

```powershell
git add src/qingpu_insight/conversation_service.py src/qingpu_insight/jobs.py tests/test_conversation_service.py
git commit -m "feat(assistant): orchestrate imports and grounded replies"
```

## Task 10: Expose a Focused Conversation API Blueprint

**Files:**

- Create: `src/qingpu_insight/conversation_web.py`
- Create: `tests/test_conversation_web.py`
- Modify: `src/qingpu_insight/web.py`

**Routes:**

```text
POST   /api/conversations
GET    /api/conversations?limit=20&before=<cursor>
GET    /api/conversations/<conversation_id>
DELETE /api/conversations/<conversation_id>
POST   /api/conversations/<conversation_id>/listing
POST   /api/conversations/<conversation_id>/refresh
GET    /api/conversations/<conversation_id>/messages?limit=50&before=<sequence>
POST   /api/conversations/<conversation_id>/replies
GET    /assistant/<conversation_id>
```

Success status rules:

- Conversation creation: `201`
- Import, refresh, reply accepted: `202` with `{run_id, conversation_id}`
- Reads: `200`
- Delete: `204`
- Validation: `400`
- Missing conversation: `404`
- Duplicate active command or stale evidence revision: `409`
- Verification-required state is observed through the existing job endpoint, not returned synchronously.

- [ ] Write failing Flask tests for every route, JSON contract, pagination cursor, 50-message default, unsupported URL, stale revision, not found, and delete.
- [ ] Add security tests for non-loopback requests, hostile Host headers, missing/wrong CSRF, non-JSON commands, and GET requests remaining side-effect free.
- [ ] Add a wiring test proving `create_app` registers the blueprint with real application dependencies without opening Chrome or contacting a provider.
- [ ] Run and confirm failure:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_web.py -q
```

- [ ] Implement `create_conversation_blueprint(service, repository)` and register it from `create_app`. Reuse the existing JSON error envelope, CSRF validator, and `/api/jobs/<run_id>` polling contract.
- [ ] Keep route handlers thin: parse request, call service/repository, serialize response.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Commit:

```powershell
git add src/qingpu_insight/conversation_web.py src/qingpu_insight/web.py tests/test_conversation_web.py
git commit -m "feat(assistant): expose conversation API"
```

## Task 11: Make the Homepage AI-First and Build the Conversation Workbench

**Files:**

- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.css`
- Create: `src/qingpu_insight/static/home_assistant.js`
- Create: `src/qingpu_insight/templates/assistant.html`
- Create: `src/qingpu_insight/static/assistant.css`
- Create: `src/qingpu_insight/static/assistant.js`
- Create: `tests/js/home_assistant_contract.cjs`
- Create: `tests/js/assistant_contract.cjs`

**Homepage behavior:**

- The first content block is “貼上 591 物件，開始分析”.
- It contains URL input, provider/model selectors, primary button, concise supported-URL text, and link to recent conversations.
- Clicking the primary button creates a conversation, starts import, and navigates to `/assistant/<id>`.
- Existing market overview, transaction map, recent transactions, valuation, reports, and admin links remain below as secondary sections.

**Workbench behavior:**

- Desktop: left evidence/listing column and right chat column.
- Mobile: compact listing summary first, then chat; full evidence opens on demand.
- Header: conversation title, provider/model, refresh, delete, and back-home actions.
- Evidence: snapshot timestamp/revision, listing facts, valuation, comparables, limitations, and data/model version.
- Chat: paginated history, revision badge on assistant messages, composer, send button, and suggested questions.
- Import/refresh stages: validating URL, opening visible browser, capturing listing, building evidence, ready.
- Reply stages: preparing evidence, asking provider, validating citations, ready.
- `needs_attention` tells the user to complete verification in visible Chrome and provides a retry button.
- A failed answer never appears in the transcript.

- [ ] Write failing `home_assistant_contract.cjs` tests for DOM hooks, request order, CSRF header, provider/model payload, import job polling, navigation, and readable error rendering.
- [ ] Write failing `assistant_contract.cjs` tests for conversation loading, 50-message pagination, safe text rendering, evidence revision display, reply payload, disabled composer during active reply, job stages, verification retry, refresh, and confirmed delete.
- [ ] Run and confirm failure:

```powershell
node tests/js/home_assistant_contract.cjs
node tests/js/assistant_contract.cjs
```

- [ ] Update `index.html` so the assistant starter is the primary hero and load `home_assistant.js` with `defer`.
- [ ] Implement the workbench template and focused JavaScript modules using `textContent`, DOM creation, and existing job polling helpers. Do not use `innerHTML` for listing/provider content.
- [ ] Add responsive CSS at 960px and 640px breakpoints. Preserve keyboard focus, visible labels, contrast, and button disabled states.
- [ ] Re-run both focused Node contracts and confirm they pass.
- [ ] Run existing frontend contracts to catch regressions:

```powershell
node tests/js/job_polling_contract.cjs
node tests/js/market_map_contract.mjs
node tests/js/admin_contract.cjs
node tests/js/model_admin_contract.cjs
```

- [ ] Commit:

```powershell
git add src/qingpu_insight/templates/index.html src/qingpu_insight/templates/assistant.html src/qingpu_insight/static/app.css src/qingpu_insight/static/home_assistant.js src/qingpu_insight/static/assistant.css src/qingpu_insight/static/assistant.js tests/js/home_assistant_contract.cjs tests/js/assistant_contract.cjs
git commit -m "feat(assistant): add AI-first listing workbench"
```

## Task 12: Add the Deterministic Release Gate, Documentation, and Manual Smoke Record

**Files:**

- Create: `tests/test_conversation_release_gate.py`
- Modify: `README.md`
- Modify: `docs/m2-valuation-methodology.md`
- Create: `docs/operations/listing-conversation-assistant.md`

**Release-gate scenarios:**

1. Create a conversation and import a supported sale fixture.
2. Persist snapshot revision 1 and evidence revision 1.
3. Ask a question; receive a grounded answer citing only revision-1 facts.
4. Refresh to snapshot/evidence revision 2.
5. Confirm the earlier answer still references revision 1.
6. Ask another question using revision 2.
7. Resume the conversation and page older messages.
8. Delete the conversation and verify cascade cleanup.
9. Simulate verification and confirm the job reaches `needs_attention`.
10. Simulate invalid provider output twice and confirm no assistant message is stored.

- [ ] Write the release-gate test first using fake browser/provider adapters and the real repository/service/API boundaries available in the test environment.
- [ ] Run it and confirm failure before adjusting integration wiring:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_release_gate.py -q
```

- [ ] Make only the minimum integration corrections required for the release gate to pass.
- [ ] Document:
  - supported and rejected URL forms
  - visible Chrome and verification behavior
  - Ollama, Gemini, and Rule capabilities
  - evidence revisions and citations
  - conversation persistence/deletion
  - MySQL migration `008`
  - privacy boundary: no raw HTML/cookies/secrets and no official listing-table writes
  - future multi-listing extension point
- [ ] Add a short beginner-friendly README workflow: paste URL → visible capture → evidence → grounded chat → refresh/resume.
- [ ] Run the complete Python suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

- [ ] Run the complete lint gate:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
```

- [ ] Run all JavaScript contracts:

```powershell
Get-ChildItem tests\js\*_contract.cjs | ForEach-Object { node $_.FullName }
node tests/js/market_map_contract.mjs
```

- [ ] Start the application and manually record these checks in the operations document:
  - Homepage assistant starter is first and existing map/dashboard still render.
  - Sale direct URL imports through visible Chrome.
  - Newhouse direct URL imports through visible Chrome.
  - Rental and search URLs are rejected before Chrome opens.
  - A `591.to` URL cannot escape the allowlist through redirects.
  - Verification produces actionable `needs_attention`.
  - Workbench displays listing facts, valuation, comparables, limitations, and revision.
  - Ollama or Gemini answer citations open/highlight the corresponding evidence facts.
  - Rule mode shows fixed summary/suggestions and does not pretend to answer arbitrary chat.
  - Refresh creates a new revision while old messages retain their old revision badges.
  - Browser reload resumes the conversation.
  - Delete removes the conversation after confirmation.
  - Admin, model observatory, valuation, report generation, and the 100-point map still work.
- [ ] Inspect the browser console and application log; record zero uncaught frontend exceptions and zero leaked secrets/raw HTML.
- [ ] Commit:

```powershell
git add tests/test_conversation_release_gate.py README.md docs/m2-valuation-methodology.md docs/operations/listing-conversation-assistant.md
git commit -m "docs(assistant): add release gate and operations guide"
```

## Final Self-Review

- [ ] Compare every approved requirement in `docs/superpowers/specs/2026-07-27-listing-conversation-assistant-design.md` against this implementation and list each requirement with its test or manual check.
- [ ] Search changed files for unfinished markers:

```powershell
rg -n "TODO|TBD|FIXME|NotImplemented|pass\s*$|placeholder" src tests database docs README.md
```

- [ ] Confirm URL policy is shared by the synchronous request validator and browser redirect validator; there must not be two divergent allowlists.
- [ ] Confirm repository, service, Pydantic, JSON, and JavaScript names agree for `provider`, `model`, `evidence_revision`, `run_id`, and pagination cursors.
- [ ] Confirm no capture path imports or writes through official listing repositories.
- [ ] Confirm every assistant property claim is validated against the selected immutable evidence revision.
- [ ] Confirm no command endpoint is callable without the project’s loopback, Host, JSON, and CSRF protections.
- [ ] Confirm no raw HTML, cookie, API key, full prompt, or authorization header is persisted or logged.
- [ ] Review the complete branch diff:

```powershell
git diff --check
git status --short
git log --oneline --decorate -15
```

- [ ] Request a final code review with `superpowers:requesting-code-review`, resolve all blocking findings, and repeat the full Python, Ruff, and JavaScript gates before declaring completion.
