# Qingpu Insight M3 Listing Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an authorized Selenium-based 591 listing pipeline for sale, new-house, and rental pages, then add A17–A19 snapshots, lifecycle events, M2 asking-price comparisons, APIs, and a dashboard.

**Architecture:** Selenium captures rendered pages through normal authorized browsing flows and writes immutable HTML batches. Focused modules parse, normalize, locate, persist, compare, and expose listings; incomplete batches never trigger delisting. Listing data remains isolated from the official M1/M2 training dataset.

**Tech Stack:** Python 3.11, Selenium 4, Beautiful Soup 4, pandas/Parquet, MySQL 8, Flask, vanilla JavaScript, pytest, Ruff.

## Global Constraints

- The authorized source has no supported API; use rendered DOM only and do not call private JSON endpoints.
- Support `sale`, `newhouse`, and `rental` as isolated listing types; `sale` is the default product view.
- Only reliably located listings within 2,000 metres of A17, A18, or A19 enter official M3 metrics.
- Do not collect or log names, phone numbers, messages, full addresses, cookies, tokens, credentials, or the authorization document.
- Default page delay is a random 2–5 seconds; use explicit waits, three retries, checkpoints, and a 30-second page timeout.
- Never treat an incomplete batch as evidence that a listing was delisted.
- `sale` maps to the resale model, `newhouse` maps to the presale model, and `rental` never calls M2 valuation.
- Listing prices must never enter M2 training, calibration, or test frames.
- Unit tests use anonymized rendered HTML fixtures and never connect to 591.

---

## File Structure

### Create

- `src/qingpu_insight/listing_sources.py` — source contracts and batch/result types.
- `src/qingpu_insight/listing_591.py` — 591 routes, rendered-DOM parsers, and Selenium adapter.
- `src/qingpu_insight/listing_capture.py` — Chrome creation, explicit waits, retries, checkpoints, and atomic raw batch writes.
- `src/qingpu_insight/listing_normalization.py` — fixed listing contract and type-specific normalization.
- `src/qingpu_insight/listing_location.py` — coordinate validation and A17–A19 assignment.
- `src/qingpu_insight/listing_repository.py` — Parquet and MySQL snapshot/event repositories.
- `src/qingpu_insight/listing_events.py` — idempotent lifecycle and price event detection.
- `src/qingpu_insight/listing_valuation.py` — safe mapping to M2 valuation.
- `src/qingpu_insight/listing_metrics.py` — listing filters, summaries, and public rows.
- `database/003_listing_intelligence_schema.sql` — MySQL batches, snapshots, state, events, and valuation tables.
- `tests/fixtures/listings/591_sale_page.html` — anonymized rendered sale cards.
- `tests/fixtures/listings/591_newhouse_page.html` — anonymized rendered new-house cards.
- `tests/fixtures/listings/591_rental_page.html` — anonymized rendered rental cards.
- `tests/test_listing_sources.py`
- `tests/test_listing_591.py`
- `tests/test_listing_capture.py`
- `tests/test_listing_normalization.py`
- `tests/test_listing_location.py`
- `tests/test_listing_repository.py`
- `tests/test_listing_events.py`
- `tests/test_listing_valuation.py`
- `tests/test_listing_metrics.py`

### Modify

- `pyproject.toml` — add Selenium and retain Beautiful Soup.
- `src/qingpu_insight/cli.py` — replace the draft `scrape591` command with `listing-scrape`, `listing-build`, and `listing-sync`.
- `src/qingpu_insight/web.py` — inject the listing repository and expose three M3 APIs.
- `src/qingpu_insight/templates/index.html` — add the listing intelligence panel.
- `src/qingpu_insight/static/app.js` — fetch and render M3 evidence.
- `src/qingpu_insight/static/app.css` — style listing controls, metrics, and event rows.
- `README.md` — document authorized browser setup, commands, output paths, and privacy boundaries.
- `docs/m3-listing-methodology.md` — publish source, scope, event, and valuation rules.
- `tests/test_cli.py`, `tests/test_web.py` — cover commands and APIs.

### Remove after replacement tests pass

- `src/qingpu_insight/scraper591.py` — untested draft with direct private-endpoint requests and bypass language.

---

### Task 1: Source contracts and dependency boundary

**Files:**
- Create: `src/qingpu_insight/listing_sources.py`
- Create: `tests/test_listing_sources.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `ListingType`, `CapturedPage`, `CaptureError`, `CaptureBatch`, and `ListingSource.capture()`.
- Consumed by: Tasks 2, 3, and 8.

- [ ] **Step 1: Write the failing source-contract test**

```python
from datetime import UTC, datetime

from qingpu_insight.listing_sources import CaptureBatch, CapturedPage


def test_capture_batch_is_complete_only_without_errors_and_with_terminal_page():
    page = CapturedPage(page_number=1, url="https://sale.591.com.tw/", html="<html/>")
    batch = CaptureBatch(
        batch_id="591-sale-20260721T120000Z",
        source="591",
        listing_type="sale",
        started_at=datetime(2026, 7, 21, 12, tzinfo=UTC),
        pages=[page],
        errors=[],
        reached_terminal_page=True,
    )
    assert batch.is_complete is True
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_sources.py -q`

Expected: FAIL with `ModuleNotFoundError: qingpu_insight.listing_sources`.

- [ ] **Step 3: Implement the source contract**

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

ListingType = Literal["sale", "newhouse", "rental"]


@dataclass(frozen=True)
class CapturedPage:
    page_number: int
    url: str
    html: str


@dataclass(frozen=True)
class CaptureError:
    page_number: int
    code: str
    message: str


@dataclass
class CaptureBatch:
    batch_id: str
    source: str
    listing_type: ListingType
    started_at: datetime
    pages: list[CapturedPage] = field(default_factory=list)
    errors: list[CaptureError] = field(default_factory=list)
    reached_terminal_page: bool = False

    @property
    def is_complete(self) -> bool:
        return self.reached_terminal_page and not self.errors


class ListingSource(Protocol):
    def capture(self, listing_type: ListingType, max_pages: int) -> CaptureBatch: ...
```

Add `selenium>=4.28,<5` and keep `beautifulsoup4>=4.12,<5` in `dependencies`. Do not add `webdriver-manager`; Selenium Manager resolves the local driver without downloading an executable from application code.

- [ ] **Step 4: Verify GREEN and lint**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_sources.py -q`

Run: `.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/listing_sources.py tests/test_listing_sources.py`

Expected: PASS and `All checks passed!`.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml src/qingpu_insight/listing_sources.py tests/test_listing_sources.py
git commit -m "feat: define M3 listing source contract"
```

---

### Task 2: Rendered HTML parsers for all three listing types

**Files:**
- Create: `src/qingpu_insight/listing_591.py`
- Create: `tests/test_listing_591.py`
- Create: `tests/fixtures/listings/591_sale_page.html`
- Create: `tests/fixtures/listings/591_newhouse_page.html`
- Create: `tests/fixtures/listings/591_rental_page.html`
- Remove after tests pass: `src/qingpu_insight/scraper591.py`

**Interfaces:**
- Consumes: `ListingType` from Task 1.
- Produces: `SourceListing` and `parse_rendered_page(html, listing_type)`.

- [ ] **Step 1: Save anonymized rendered fixtures**

Each fixture contains two realistic cards and no personal data. Preserve DOM attributes needed for parsing, but replace title, URL ID, price, coordinates, and address text with synthetic values. A sale card must exercise this contract:

```html
<article data-houseid="S-1001" data-lat="25.0100" data-lng="121.2150">
  <a class="listing-link" href="https://sale.591.com.tw/home/house/detail/2/S-1001.html">
    <h2 class="listing-title">A18 範例住宅</h2>
  </a>
  <span class="price">1,880 萬</span>
  <span class="area">32.5坪</span>
  <span class="layout">3房2廳2衛</span>
  <span class="floor">10F/20F</span>
</article>
```

- [ ] **Step 2: Write one failing contract test per type**

```python
@pytest.mark.parametrize(
    ("listing_type", "expected_price_field"),
    [("sale", "asking_price_twd"), ("newhouse", "asking_price_twd"),
     ("rental", "monthly_rent_twd")],
)
def test_parse_rendered_page_keeps_types_isolated(listing_type, expected_price_field):
    html = fixture_path(f"591_{listing_type}_page.html").read_text(encoding="utf-8")
    rows = parse_rendered_page(html, listing_type)
    assert len(rows) == 2
    assert all(row.listing_type == listing_type for row in rows)
    assert all(row.payload[expected_price_field] > 0 for row in rows)
```

Also assert stable IDs, canonical HTTPS 591 URLs, numeric area/layout/floor fields, coordinates, and absence of phone/name keys.

- [ ] **Step 3: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_591.py -q`

Expected: FAIL because `listing_591` does not exist.

- [ ] **Step 4: Implement the DOM parser**

```python
@dataclass(frozen=True)
class SourceListing:
    source_listing_id: str
    listing_type: ListingType
    source_url: str
    payload: dict[str, object]


CARD_SELECTORS = {
    "sale": ("article[data-houseid]", "[data-id][class*='house']"),
    "newhouse": ("article[data-housingid]", "[data-id][class*='housing']"),
    "rental": ("article[data-houseid]", "[data-id][class*='item']"),
}


def parse_rendered_page(html: str, listing_type: ListingType) -> list[SourceListing]:
    soup = BeautifulSoup(html, "html.parser")
    cards = _first_nonempty_selector(soup, CARD_SELECTORS[listing_type])
    return [_parse_card(card, listing_type) for card in cards]
```

Keep price, layout, floor, area, coordinate, URL, and ID parsers as separate pure functions. Raise `ListingSchemaError` when a non-empty page has no recognized cards; do not silently return an empty successful page.

- [ ] **Step 5: Verify GREEN, remove the draft, and lint**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_591.py -q`

After PASS, remove `src/qingpu_insight/scraper591.py`. Remove all imports of `create_scraper`, private endpoint URLs, `requests.Session`, anti-bot bypass text, `webdriver-manager`, and generic exception swallowing.

Run: `.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/listing_591.py tests/test_listing_591.py`

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/listing_591.py tests/test_listing_591.py tests/fixtures/listings
git add -u src/qingpu_insight/scraper591.py
git commit -m "feat: parse authorized 591 listing pages"
```

---

### Task 3: Selenium capture, explicit waits, retries, and raw batches

**Files:**
- Create: `src/qingpu_insight/listing_capture.py`
- Create: `tests/test_listing_capture.py`
- Modify: `src/qingpu_insight/listing_591.py`

**Interfaces:**
- Consumes: Task 1 contracts and Task 2 parser/schema error.
- Produces: `ChromeConfig`, `RawBatchWriter`, `Selenium591Source.capture()`.

- [ ] **Step 1: Write failing tests with an injected fake browser**

```python
def test_incomplete_navigation_writes_manifest_but_never_complete(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML], fail_on_next=True)
    source = Selenium591Source(browser=browser, writer=RawBatchWriter(tmp_path))
    batch = source.capture("sale", max_pages=3)
    assert batch.is_complete is False
    manifest = json.loads((tmp_path / batch.batch_id / "manifest.json").read_text())
    assert manifest["is_complete"] is False
    assert manifest["errors"][0]["code"] == "navigation_failed"
```

Add tests for terminal empty state, explicit wait timeout, three retries, checkpoint resume, allowed host validation, and atomic `.tmp` to final rename.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_capture.py -q`

Expected: FAIL because capture classes do not exist.

- [ ] **Step 3: Implement capture infrastructure**

```python
@dataclass(frozen=True)
class ChromeConfig:
    binary: str | None = None
    profile_dir: str | None = None
    headless: bool = True
    page_timeout_seconds: int = 30
    delay_seconds: tuple[float, float] = (2.0, 5.0)
    max_retries: int = 3


def create_chrome(config: ChromeConfig) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if config.headless:
        options.add_argument("--headless=new")
    if config.binary:
        options.binary_location = config.binary
    if config.profile_dir:
        options.add_argument(f"--user-data-dir={config.profile_dir}")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(config.page_timeout_seconds)
    return driver
```

Do not add `--no-sandbox`, disable TLS, handle CAPTCHA, or inspect performance/network logs. `RawBatchWriter` writes `page-0001.html`, checkpoint JSON, and manifest JSON using temporary files followed by `Path.replace()`.

- [ ] **Step 4: Implement normal route navigation**

Use explicit route definitions for normal listing pages:

```python
ROUTES = {
    "sale": "https://sale.591.com.tw/?shType=list&regionid=6",
    "newhouse": "https://newhouse.591.com.tw/home/housing/search?regionid=6",
    "rental": "https://rent.591.com.tw/list?region=6",
}
```

Wait for either a recognized card selector or a verified empty-result selector. Click the visible enabled next-page control, verify the page marker changes, save the rendered HTML, then delay. Mark terminal only for a verified empty result or disabled/missing next control after at least one successfully parsed page.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_capture.py tests/test_listing_591.py -q`

Run: `.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/listing_capture.py src/qingpu_insight/listing_591.py tests/test_listing_capture.py`

```powershell
git add src/qingpu_insight/listing_capture.py src/qingpu_insight/listing_591.py tests/test_listing_capture.py
git commit -m "feat: capture authorized listing snapshots"
```

---

### Task 4: Normalize listings and enforce the A17–A19 boundary

**Files:**
- Create: `src/qingpu_insight/listing_normalization.py`
- Create: `src/qingpu_insight/listing_location.py`
- Create: `tests/test_listing_normalization.py`
- Create: `tests/test_listing_location.py`

**Interfaces:**
- Consumes: `SourceListing`, existing `get_settings()`, `build_doorplate_frame()`, and `station_points()`.
- Produces: `NormalizedListing`, `normalize_listing()`, and `assign_listing_life_circle()`.

- [ ] **Step 1: Write failing normalization and location tests**

```python
def test_sale_normalization_never_places_price_in_rent_field(source_sale):
    row = normalize_listing(source_sale, SNAPSHOT_AT)
    assert row.asking_price_twd == 18_800_000
    assert row.monthly_rent_twd is None


def test_missing_coordinates_are_retained_but_not_eligible(station_frame):
    result = assign_listing_life_circle(frame_without_coordinates(), station_frame, 2_000)
    assert result.loc[0, "location_eligible"] is False
    assert pd.isna(result.loc[0, "station_code"])
```

Add tests for valid nearest station, overlap choosing nearest, exactly 2,000 metres, outside radius, invalid coordinate range, URL host rejection, and all three type contracts.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_normalization.py tests/test_listing_location.py -q`

- [ ] **Step 3: Implement the fixed normalized contract**

```python
@dataclass(frozen=True)
class NormalizedListing:
    source: str
    source_listing_id: str
    listing_type: ListingType
    snapshot_at: datetime
    source_url: str
    title: str
    asking_price_twd: int | None
    monthly_rent_twd: int | None
    building_area_ping: float | None
    building_type: str | None
    bedrooms: int | None
    living_rooms: int | None
    bathrooms: int | None
    building_age_years: float | None
    floor: int | None
    total_floors: int | None
    parking_type: str | None
    latitude: float | None
    longitude: float | None
    raw_hash: str
```

Build `raw_hash` from stable normalized source fields only; exclude `snapshot_at`, browser-generated attributes, and ordering noise. Reject non-HTTPS or non-591 URLs.

- [ ] **Step 4: Implement WGS84 life-circle assignment**

Load station coordinates by reusing M0 `data/raw/doorplates.csv`, `build_doorplate_frame()`, and `station_points()`, then transform station TWD97 coordinates to WGS84 once. Compute vectorized Haversine distance and return `station_code`, `station_distance_m`, and `location_eligible`. Never infer coordinates from title text.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_normalization.py tests/test_listing_location.py -q`

```powershell
git add src/qingpu_insight/listing_normalization.py src/qingpu_insight/listing_location.py tests/test_listing_normalization.py tests/test_listing_location.py
git commit -m "feat: normalize and locate M3 listings"
```

---

### Task 5: Snapshot repositories and MySQL schema

**Files:**
- Create: `src/qingpu_insight/listing_repository.py`
- Create: `database/003_listing_intelligence_schema.sql`
- Create: `tests/test_listing_repository.py`

**Interfaces:**
- Produces: `ListingRepository`, `ParquetListingRepository`, `MySQLListingRepository`, `save_batch()`, `load_current()`, `append_events()`.
- Consumed by: Tasks 6–9.

- [ ] **Step 1: Write failing repository contract tests**

```python
@pytest.mark.parametrize("repository_factory", [parquet_repository, mysql_fake_repository])
def test_save_batch_is_idempotent(repository_factory, complete_batch, normalized_rows):
    repo = repository_factory()
    repo.save_batch(complete_batch, normalized_rows)
    repo.save_batch(complete_batch, normalized_rows)
    assert len(repo.load_snapshots(batch_id=complete_batch.batch_id)) == len(normalized_rows)
```

Also test type isolation, batch completeness persistence, public-field allowlist, atomic Parquet writes, and transaction rollback for MySQL.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_repository.py -q`

- [ ] **Step 3: Create the schema**

Create tables `listing_batches`, `listing_snapshots`, `listing_current`, `listing_events`, and `listing_valuations`. Use `(source, listing_type, source_listing_id)` as the current-state key, `(batch_id, source, listing_type, source_listing_id)` as the snapshot unique key, and `(event_key)` as the event unique key. Add indexes for `(listing_type, station_code, snapshot_at)`, price status, and event time.

The schema must omit contact names, phone numbers, free-form messages, credentials, and full addresses.

- [ ] **Step 4: Implement repository adapters**

```python
class ListingRepository(Protocol):
    def save_batch(self, batch: CaptureBatch, rows: pd.DataFrame) -> None: ...
    def load_current(self, listing_type: ListingType | None = None) -> pd.DataFrame: ...
    def load_snapshots(self, batch_id: str | None = None) -> pd.DataFrame: ...
    def append_events(self, events: pd.DataFrame) -> None: ...
```

Parquet writes use `.tmp` and `Path.replace()`. MySQL uses one transaction per batch with rollback on any failure. Duplicate unique keys update nothing except explicitly mutable current-state fields.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_repository.py -q`

```powershell
git add database/003_listing_intelligence_schema.sql src/qingpu_insight/listing_repository.py tests/test_listing_repository.py
git commit -m "feat: persist M3 listing snapshots"
```

---

### Task 6: Idempotent lifecycle and price events

**Files:**
- Create: `src/qingpu_insight/listing_events.py`
- Create: `tests/test_listing_events.py`

**Interfaces:**
- Consumes: current state, new normalized batch, and `CaptureBatch.is_complete`.
- Produces: `detect_listing_events(previous, current, batch)` and updated state.

- [ ] **Step 1: Write the event state-machine tests**

```python
def test_two_complete_absences_are_required_for_delisting(active_state):
    first = detect_listing_events(active_state, empty_rows(), complete_batch("B2"))
    assert first.events.empty
    assert first.state.loc[0, "consecutive_absences"] == 1
    second = detect_listing_events(first.state, empty_rows(), complete_batch("B3"))
    assert second.events["event_type"].tolist() == ["delisted"]


def test_incomplete_batch_does_not_increment_absence(active_state):
    result = detect_listing_events(active_state, empty_rows(), incomplete_batch("B2"))
    assert result.state.loc[0, "consecutive_absences"] == 0
    assert result.events.empty
```

Add listed, price decrease/increase, relisted, unchanged, same-batch idempotency, and cross-type isolation tests.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_events.py -q`

- [ ] **Step 3: Implement deterministic event keys**

```python
def event_key(batch_id: str, listing_key: str, event_type: str) -> str:
    value = f"{batch_id}|{listing_key}|{event_type}".encode()
    return hashlib.sha256(value).hexdigest()
```

Compare only rows of the same `listing_type`. Price events require both old and new positive prices. Save previous price, new price, absolute change, and percentage change. A relisted item resets absence count and active status.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_events.py -q`

```powershell
git add src/qingpu_insight/listing_events.py tests/test_listing_events.py
git commit -m "feat: detect listing lifecycle events"
```

---

### Task 7: Safe M2 asking-price comparison

**Files:**
- Create: `src/qingpu_insight/listing_valuation.py`
- Create: `tests/test_listing_valuation.py`

**Interfaces:**
- Consumes: normalized eligible listing, `ModelRegistry`, market frame, `ValuationInput`, and `valuate()`.
- Produces: `compare_listing_to_model()` returning eligibility, model evidence, interval, and asking gap.

- [ ] **Step 1: Write failing mapping tests**

```python
def test_rental_never_calls_model(rental_listing, spy_registry):
    result = compare_listing_to_model(rental_listing, spy_registry, market_frame)
    assert result["valuation_eligible"] is False
    assert result["reason"] == "rental_not_supported"
    assert spy_registry.calls == []


def test_incomplete_sale_returns_reason_instead_of_imputing(sale_without_floor, registry):
    result = compare_listing_to_model(sale_without_floor, registry, market_frame)
    assert result == {"valuation_eligible": False, "reason": "missing:floor,total_floors"}
```

Add sale→resale, newhouse→presale, outside-area rejection, asking gap, model version, and range status tests.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_valuation.py -q`

- [ ] **Step 3: Implement strict mapping**

```python
TYPE_TO_MODEL = {"sale": "resale", "newhouse": "presale"}


def asking_status(asking: int, interval: tuple[int, int]) -> str:
    if asking < interval[0]:
        return "below_range"
    if asking > interval[1]:
        return "above_range"
    return "within_range"
```

Construct `ValuationInput` only when every required field is present and `location_eligible=True`. Catch validation errors and return a public reason; do not call fallback valuation for missing listing attributes. Persist `model_version`, `valued_at`, interval, estimate, `asking_gap_pct`, and status.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_valuation.py -q`

```powershell
git add src/qingpu_insight/listing_valuation.py tests/test_listing_valuation.py
git commit -m "feat: compare listing asks with M2 models"
```

---

### Task 8: Replace the draft CLI with reproducible M3 commands

**Files:**
- Modify: `src/qingpu_insight/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: `listing-scrape`, `listing-build`, and `listing-sync` commands.

- [ ] **Step 1: Write failing CLI tests using a fake source**

```python
def test_listing_sync_runs_types_independently(tmp_path, monkeypatch, fake_source):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qingpu_insight.cli.create_listing_source", lambda _: fake_source)
    assert main(["listing-sync", "--types", "sale", "newhouse", "rental", "--max-pages", "1"]) == 0
    assert (tmp_path / "data/processed/listing_snapshots.parquet").exists()
    assert set(fake_source.calls) == {"sale", "newhouse", "rental"}
```

Add invalid type/max-pages/delay tests, one-type failure isolation, browser creation failure exit code, and command help tests. Verify `--types` uses `nargs="+"`, while each individual type defaults to none unless requested.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_cli.py -q -k listing`

- [ ] **Step 3: Replace `scrape591`**

Delete the draft `scrape591()` function and `scrape591` parser. Add command handlers with dependency-injected source/repository factories. `listing-scrape` only captures raw data; `listing-build` only reads an existing batch; `listing-sync` composes capture, normalization, location, persistence, events, and eligible valuation.

Use these defaults:

```python
listing_type_choices = ("sale", "newhouse", "rental")
max_pages = 10
delay_min = 2.0
delay_max = 5.0
page_timeout = 30
```

Reject `delay_min > delay_max`, `max_pages < 1`, unknown types, and a missing `data/raw/doorplates.csv` with actionable Chinese errors.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_cli.py -q`

Run: `.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/cli.py tests/test_cli.py`

```powershell
git add src/qingpu_insight/cli.py tests/test_cli.py
git commit -m "feat: expose reproducible M3 listing commands"
```

---

### Task 9: Listing metrics, APIs, and evidence-first dashboard

**Files:**
- Create: `src/qingpu_insight/listing_metrics.py`
- Create: `tests/test_listing_metrics.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `tests/test_web.py`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `src/qingpu_insight/static/app.css`

**Interfaces:**
- Produces: `ListingFilters`, `listing_summary()`, `public_listings()`, `public_events()`, and three GET APIs.

- [ ] **Step 1: Write failing metric and API tests**

```python
def test_listing_summary_keeps_types_and_stations_isolated(listing_frame):
    result = listing_summary(listing_frame, ListingFilters("sale", ("A18",)))
    assert result["listing_type"] == "sale"
    assert result["station_codes"] == ["A18"]
    assert result["active_count"] == 2


def test_listing_api_never_exposes_private_or_raw_fields(client):
    raw = client.get("/api/listings?listing_type=sale").get_data(as_text=True)
    for field in ("raw_html", "payload", "phone", "contact_name", "full_address"):
        assert field not in raw
```

Cover missing/invalid type, station allowlist, price status, event type, pagination cap 100, empty metrics, nullable values, and source/update disclosure.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_metrics.py tests/test_web.py -q -k listing`

- [ ] **Step 3: Implement metrics and APIs**

Add:

```python
@app.get("/api/listings")
def listings_api(): ...

@app.get("/api/listings/summary")
def listing_summary_api(): ...

@app.get("/api/listing-events")
def listing_events_api(): ...
```

Inject `ListingRepository` into `create_app()` using a Parquet default under `data/processed`. Public rows may include listing ID, type, title, source URL, station, area, price, event/status, coordinates rounded to four decimals, model evidence, and snapshot time only.

- [ ] **Step 4: Add the dashboard panel**

Add a “待售情報” section with type tabs, station filters, summary cards, latest price events, active listings, source timestamp, and clear `below/within/above` labels. Build DOM nodes with `textContent`; never insert source HTML with `innerHTML`. Show M2 comparison only when `valuation_eligible=true`.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_listing_metrics.py tests/test_web.py -q`

```powershell
git add src/qingpu_insight/listing_metrics.py src/qingpu_insight/web.py src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js src/qingpu_insight/static/app.css tests/test_listing_metrics.py tests/test_web.py
git commit -m "feat: publish M3 listing intelligence dashboard"
```

---

### Task 10: Documentation, privacy audit, and release evidence

**Files:**
- Modify: `README.md`
- Create: `docs/m3-listing-methodology.md`
- Modify: `.gitignore` if raw listing batches are not already ignored.

**Interfaces:**
- Produces: reproducible operator commands and M3 release evidence.

- [ ] **Step 1: Document exact setup and operation**

Document Chrome/Selenium prerequisites, authorization assumption, environment variables, three CLI flows, raw/processed paths, MySQL migration order, API routes, event definitions, M2 separation, privacy rules, and recovery from incomplete batches. State that scheduled execution remains M4.

- [ ] **Step 2: Run the automated release gate**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: all tests pass, Ruff reports `All checks passed!`, and `git diff --check` has no output.

- [ ] **Step 3: Run fixture-based end-to-end verification**

Run `listing-sync` with a fake/fixture source and assert:

- three isolated batches are saved;
- normalized rows contain no private fields;
- only A17–A19 rows within 2 km are eligible;
- repeating the same batch emits no duplicate events;
- an incomplete batch emits no delisting;
- sale/newhouse valuation results include model versions;
- rental results never contain sale valuation fields.

- [ ] **Step 4: Run authorized one-page smoke tests manually**

```powershell
.\.venv\Scripts\qingpu-data.exe listing-scrape --type sale --max-pages 1
.\.venv\Scripts\qingpu-data.exe listing-scrape --type newhouse --max-pages 1
.\.venv\Scripts\qingpu-data.exe listing-scrape --type rental --max-pages 1
```

For each command, verify exit code 0, one rendered HTML page, a complete manifest, a non-zero parsed count, no contact fields, and no credentials in logs. If a selector changed, update the anonymized fixture and parser test before changing production selectors.

- [ ] **Step 5: Audit the repository for prohibited data**

Run searches for phone-number patterns, cookie names, authorization text, raw HTML under tracked paths, `.env`, Chrome profiles, tokens, and credentials. Remove any generated secrets from the staging set and rotate them if they were ever committed.

- [ ] **Step 6: Commit documentation and release evidence**

```powershell
git add README.md docs/m3-listing-methodology.md .gitignore
git commit -m "docs: publish M3 listing methodology"
```

---

## Final Review Checklist

- [ ] Existing uncommitted draft code has been replaced, not silently committed as-is.
- [ ] No private endpoint URL or anti-bot bypass behavior remains.
- [ ] All three listing types have independent parser, batch, repository, event, and API tests.
- [ ] Every downlisting decision requires two complete missing batches.
- [ ] A17–A19 two-kilometre eligibility is enforced before metrics and valuation.
- [ ] M2 model frames still load only official M1 transactions.
- [ ] Full tests, Ruff, diff check, fixture E2E, and authorized one-page smoke tests pass.
- [ ] Git status contains no raw HTML, credentials, browser profiles, or contact data.

