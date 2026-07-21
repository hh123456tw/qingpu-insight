# M3 Live 591 Listing Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `listing-scrape`, `listing-build`, and `listing-sync` produce validated, project-usable sale, rental, and new-house records from the authorized public 591 browser flow.

**Architecture:** Keep Chrome as the acquisition boundary and dispatch captured HTML to a type-specific parser in `listing_591.py`. Each parse returns accepted records plus per-card rejection diagnostics and representation metadata; normalization preserves exact values and advertised ranges without manufacturing new-house totals. Capture uses one page-level readiness deadline, visible Chrome by default, atomic evidence, and incomplete-batch semantics that cannot generate false delistings.

**Tech Stack:** Python 3.12, Selenium, BeautifulSoup, pandas/Parquet, PyMySQL/MySQL 8, pytest, Ruff.

## Global Constraints

- Normal browser navigation is the acquisition boundary; do not call undocumented endpoints with a second HTTP client.
- Do not bypass verification, solve CAPTCHA, persist credentials, or log contact data, cookies, headers, or Chrome profiles.
- M1/M2 training data remains official transaction data only; 591 data is monitoring and comparison input.
- Only `location_eligible=True` records may enter A17–A19 metrics and model comparison.
- New-house unit-price and area ranges are stored as ranges; never synthesize total asking price or range midpoint.
- A page cap, timeout, click failure, verification page, or schema failure leaves the batch incomplete.
- Raw captures remain gitignored; test fixtures must be minimal and anonymized.

## File map

- `src/qingpu_insight/listing_591.py`: extraction contracts, type dispatch, validation, live DOM/JSON-LD parsers.
- `src/qingpu_insight/listing_capture.py`: Chrome configuration, routes, readiness, pagination, and atomic diagnostics.
- `src/qingpu_insight/listing_sources.py`: capture metadata carried from extraction to manifests.
- `src/qingpu_insight/listing_normalization.py`: normalized exact/range fields and stable hashes.
- `src/qingpu_insight/listing_repository.py`: Parquet/MySQL persistence for the expanded contract.
- `src/qingpu_insight/listing_metrics.py`: public exact/range price and area output.
- `src/qingpu_insight/listing_valuation.py`: explicit no-total-price handling for new houses.
- `src/qingpu_insight/cli.py`: visible-browser flags, profile reuse, extraction summaries, and non-zero failure behavior.
- `database/003_listing_intelligence_schema.sql`: additive snapshot/current columns.
- `tests/fixtures/listings/591_*_live_page.html`: minimal anonymized representations of observed live structures.
- `tests/test_listing_591.py`, `tests/test_listing_capture.py`, `tests/test_listing_normalization.py`, `tests/test_listing_repository.py`, `tests/test_listing_metrics.py`, `tests/test_listing_valuation.py`, `tests/test_cli.py`: regression and contract tests.
- `README.md`, `docs/m3-listing-methodology.md`: operator workflow and acceptance evidence.

---

### Task 1: Parse the three observed live representations

**Files:**
- Modify: `src/qingpu_insight/listing_591.py`
- Create: `tests/fixtures/listings/591_sale_live_page.html`
- Create: `tests/fixtures/listings/591_rental_live_page.html`
- Create: `tests/fixtures/listings/591_newhouse_live_page.html`
- Modify: `tests/test_listing_591.py`

**Interfaces:**
- Consumes: rendered HTML and `ListingType`.
- Produces: `extract_rendered_page(html: str, listing_type: ListingType) -> ExtractionResult` and the backward-compatible `parse_rendered_page(...) -> list[SourceListing]`.

- [ ] **Step 1: Add minimal anonymized fixtures and failing adapter tests**

The sale fixture contains one `div.ware-item[data-id]` with `.ware-item__header a`, `.ware-item__attrs`, `.ware-item__price-value`, `.ware-item__section`, and `.ware-item__address`. The rental fixture contains one `div.item[data-id]` with `.item-info-title a`, two `.item-info-txt` blocks, and `.item-info-price strong`. Neither fixture includes `.user-info__name`, `.role-name`, phone, image, or free-form contact text. The new-house fixture contains only this standardized state:

```html
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","itemListElement":[
  {"@type":"ListItem","position":1,"item":{"@type":"Product",
   "name":"青埔測試建案","url":"https://newhouse.591.com.tw/138379",
   "description":"位於桃園市中壢區，二房(21~23坪)，坪數19~30坪",
   "offers":{"@type":"AggregateOffer","priceCurrency":"TWD",
   "lowPrice":500000,"highPrice":560000,"offerCount":1}}}
]}
</script>
```

Add tests that assert:

```python
def test_live_sale_dom_extracts_required_fields():
    result = extract_rendered_page(live_fixture("591_sale_live_page.html"), "sale")
    row = result.listings[0]
    assert result.representation == "dom"
    assert result.schema_version == "591-sale-dom-v1"
    assert row.source_listing_id == "20215131"
    assert row.source_url == "https://sale.591.com.tw/home/house/detail/2/20215131.html"
    assert row.payload["asking_price_twd"] == 8_500_000
    assert row.payload["area_ping"] == 30.66
    assert row.payload["layout_rooms"] == 3
    assert row.payload["floor"] == 10

def test_live_rental_dom_extracts_required_fields():
    result = extract_rendered_page(live_fixture("591_rental_live_page.html"), "rental")
    row = result.listings[0]
    assert result.representation == "dom"
    assert row.source_listing_id == "21649547"
    assert row.source_url == "https://rent.591.com.tw/21649547"
    assert row.payload["monthly_rent_twd"] == 14_500
    assert row.payload["area_ping"] == 24.5
    assert row.payload["layout_rooms"] == 3

def test_live_newhouse_jsonld_preserves_advertised_ranges():
    result = extract_rendered_page(live_fixture("591_newhouse_live_page.html"), "newhouse")
    row = result.listings[0]
    assert result.representation == "jsonld"
    assert result.schema_version == "591-newhouse-jsonld-v1"
    assert row.payload["asking_price_twd"] is None
    assert row.payload["asking_unit_price_low_twd_per_ping"] == 500_000
    assert row.payload["asking_unit_price_high_twd_per_ping"] == 560_000
    assert row.payload["area_min_ping"] == 19.0
    assert row.payload["area_max_ping"] == 30.0
```

Also assert the payload key set is disjoint from `{"phone", "name", "contact", "agent", "role_name"}` and that malformed cards appear in `result.rejected` without discarding valid siblings.

- [ ] **Step 2: Run the focused tests and confirm the contract does not exist**

Run: `python -m pytest tests/test_listing_591.py -q`

Expected: FAIL because `ExtractionResult` and `extract_rendered_page` are not defined and current selectors do not parse the live fixtures.

- [ ] **Step 3: Implement extraction result types, dispatch, canonicalization, and per-type parsing**

Add these exact public types:

```python
@dataclass(frozen=True)
class RejectedListing:
    source_ref: str
    reason_code: str
    message: str

@dataclass(frozen=True)
class ExtractionResult:
    listings: list[SourceListing]
    rejected: list[RejectedListing]
    representation: Literal["dom", "jsonld"]
    schema_version: str
```

Use selectors `div.ware-item[data-id]` for sale and `div.item[data-id]` for rental. Canonicalize URLs by removing query and fragment with `urlsplit`/`urlunsplit`, require HTTPS and a hostname equal to or ending in `.591.com.tw`, and validate title, stable ID, price/rent, and exact/ranged area before accepting a record. Parse sale attributes and rental text using regexes for `房`, `廳`, `衛`, `坪`, and `F/F`. Parse new-house `ItemList` JSON-LD, require `Product`, `priceCurrency == "TWD"`, a positive low or high price, and description text containing `桃園市`; derive the stable ID from the final numeric URL segment. Return reason codes `missing_id`, `invalid_url`, `missing_title`, `missing_price`, `missing_area`, `wrong_region`, and `malformed_jsonld`.

Keep compatibility as:

```python
def parse_rendered_page(html: str, listing_type: ListingType) -> list[SourceListing]:
    return extract_rendered_page(html, listing_type).listings
```

If no valid listings remain, raise `ListingSchemaError` with accepted/rejected counts and the distinct reason codes.

- [ ] **Step 4: Run parser tests and lint**

Run: `python -m pytest tests/test_listing_591.py -q && python -m ruff check src/qingpu_insight/listing_591.py tests/test_listing_591.py`

Expected: all parser tests PASS and Ruff reports no errors.

- [ ] **Step 5: Commit the live adapters**

```bash
git add src/qingpu_insight/listing_591.py tests/test_listing_591.py tests/fixtures/listings/591_sale_live_page.html tests/fixtures/listings/591_rental_live_page.html tests/fixtures/listings/591_newhouse_live_page.html
git commit -m "feat: parse live 591 listing structures"
```

---

### Task 2: Make browser capture visible-first, bounded, and diagnosable

**Files:**
- Modify: `src/qingpu_insight/listing_capture.py`
- Modify: `src/qingpu_insight/listing_sources.py`
- Modify: `tests/fake_browser.py`
- Modify: `tests/test_listing_capture.py`

**Interfaces:**
- Consumes: `CARD_SELECTORS`, `extract_rendered_page`, operator Chrome configuration.
- Produces: `ChromeConfig(headless=False, profile_dir=None, ...)`, type-specific public routes, manifest extraction summaries, and `diagnostic.html` on failure.

- [ ] **Step 1: Write failing tests for real routes, one deadline, diagnostics, and batch identity**

Add assertions for:

```python
def test_newhouse_uses_human_facing_taoyuan_route():
    assert ROUTES["newhouse"] == "https://newhouse.591.com.tw/housing-list.html?regionid=6"

def test_visible_browser_is_default():
    assert ChromeConfig().headless is False

def test_writer_batch_dir_contains_type_and_matches_manifest(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    batch = Selenium591Source(browser=FakeBrowser(pages=[SALE_HTML]), writer=writer,
                              config=ChromeConfig(max_retries=0)).capture("sale", 1)
    manifest = json.loads((writer.batch_dir / "manifest.json").read_text("utf-8"))
    assert writer.batch_dir.name == batch.batch_id
    assert writer.batch_dir.name.startswith("591-sale-")
    assert manifest["batch_id"] == batch.batch_id

def test_schema_failure_writes_diagnostic_html(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    source = Selenium591Source(browser=FakeBrowser(pages=["<html>changed</html>"]),
                              writer=writer,
                              config=ChromeConfig(page_timeout_seconds=1, max_retries=0))
    batch = source.capture("sale", 1)
    assert batch.errors[0].code == "page_failed"
    assert (writer.batch_dir / "diagnostic-page-0001.html").exists()
```

Instrument the fake wait clock and assert all selector alternatives share one `page_timeout_seconds` budget instead of multiplying the timeout by selector count.

- [ ] **Step 2: Run capture tests and verify failure**

Run: `python -m pytest tests/test_listing_capture.py -q`

Expected: FAIL on the old new-house route, headless default, writer constructor, and missing diagnostic file.

- [ ] **Step 3: Implement bounded readiness and atomic diagnostics**

Change `RawBatchWriter(base_dir: Path, listing_type: ListingType)` so one UTC timestamp creates both directory and batch ID as `591-{listing_type}-{timestamp}`. Add `write_diagnostic(page_number: int, html: str) -> Path` using the same `.tmp` then `Path.replace()` pattern.

Set recognized selectors to:

```python
CARD_SELECTORS = {
    "sale": ("div.ware-item[data-id]",),
    "rental": ("div.item[data-id]",),
    "newhouse": ('script[type="application/ld+json"]', "#__NUXT_DATA__"),
}
```

Use one `WebDriverWait(browser, timeout).until(predicate)` where `predicate` checks every card and empty selector on the current DOM. After readiness, call `extract_rendered_page` before writing a normal page so navigation-only/recommendation content fails capture. Save the current HTML as a diagnostic on the final failed attempt. Record `accepted_count`, `rejected_count`, `representation`, and `schema_version` in `CapturedPage` and manifest page entries.

Detect likely verification with title/body tokens `驗證`, `captcha`, and `verify`; emit `verification_required` without storing those page contents as accepted evidence. Keep `reached_terminal_page=False` when the first page is empty, when capped, or after any error.

- [ ] **Step 4: Verify capture behavior**

Run: `python -m pytest tests/test_listing_capture.py tests/test_listing_events.py -q && python -m ruff check src/qingpu_insight/listing_capture.py src/qingpu_insight/listing_sources.py tests/fake_browser.py tests/test_listing_capture.py`

Expected: all tests PASS; capped and failed batches remain incomplete.

- [ ] **Step 5: Commit capture safety**

```bash
git add src/qingpu_insight/listing_capture.py src/qingpu_insight/listing_sources.py tests/fake_browser.py tests/test_listing_capture.py
git commit -m "feat: harden browser-backed listing capture"
```

---

### Task 3: Preserve advertised ranges through normalization and storage

**Files:**
- Modify: `src/qingpu_insight/listing_normalization.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/listing_repository.py`
- Modify: `database/003_listing_intelligence_schema.sql`
- Modify: `tests/test_listing_normalization.py`
- Modify: `tests/test_listing_repository.py`

**Interfaces:**
- Consumes: `SourceListing.payload` exact/range fields and acquisition metadata.
- Produces: expanded `NormalizedListing` and identical Parquet/MySQL columns.

- [ ] **Step 1: Add failing exact/range normalization and repository tests**

Extend expected rows with these fields:

```python
assert normalized.asking_unit_price_low_twd_per_ping == 500_000
assert normalized.asking_unit_price_high_twd_per_ping == 560_000
assert normalized.building_area_min_ping == 19.0
assert normalized.building_area_max_ping == 30.0
assert normalized.acquisition_representation == "jsonld"
assert normalized.acquisition_schema_version == "591-newhouse-jsonld-v1"
```

Assert `asking_price_twd is None`, changing any range endpoint changes `raw_hash`, and Parquet/MySQL round trips preserve all six new columns. Add a schema assertion that both `listing_snapshots` and `listing_current` define the columns.

- [ ] **Step 2: Run focused tests and verify missing fields**

Run: `python -m pytest tests/test_listing_normalization.py tests/test_listing_repository.py -q`

Expected: FAIL because the dataclass, SQL parameters, and table columns do not contain the range/acquisition fields.

- [ ] **Step 3: Expand the normalized and persisted contracts**

Add to `NormalizedListing`:

```python
asking_unit_price_low_twd_per_ping: int | None
asking_unit_price_high_twd_per_ping: int | None
building_area_min_ping: float | None
building_area_max_ping: float | None
acquisition_representation: str
acquisition_schema_version: str
```

Validate all monetary and area values as positive when present, require low `<=` high, set rental unit-price fields to `None`, and include every field in `_stable_dict`. In `_normalized_to_rows`, copy every field verbatim.

Add nullable MySQL columns to both snapshot and current tables:

```sql
asking_unit_price_low_twd_per_ping  BIGINT UNSIGNED NULL,
asking_unit_price_high_twd_per_ping BIGINT UNSIGNED NULL,
building_area_min_ping              DECIMAL(10,2) NULL,
building_area_max_ping              DECIMAL(10,2) NULL,
acquisition_representation          VARCHAR(24) NOT NULL,
acquisition_schema_version          VARCHAR(64) NOT NULL,
```

Add the same named parameters to `_INSERT_SNAPSHOT_SQL`, `_INSERT_CURRENT_SQL`, update clauses, and `save_batch`. Existing Parquet persistence needs no column declaration, only populated DataFrame columns.

- [ ] **Step 4: Run normalization, repository, and SQL integration tests**

Run: `python -m pytest tests/test_listing_normalization.py tests/test_listing_repository.py tests/test_cli.py -q && python -m ruff check src/qingpu_insight/listing_normalization.py src/qingpu_insight/listing_repository.py src/qingpu_insight/cli.py`

Expected: all tests PASS, including rollback/idempotency coverage.

- [ ] **Step 5: Commit the expanded contract**

```bash
git add src/qingpu_insight/listing_normalization.py src/qingpu_insight/listing_repository.py src/qingpu_insight/cli.py database/003_listing_intelligence_schema.sql tests/test_listing_normalization.py tests/test_listing_repository.py tests/test_cli.py
git commit -m "feat: persist listing price and area ranges"
```

---

### Task 4: Expose honest metrics and valuation semantics

**Files:**
- Modify: `src/qingpu_insight/listing_metrics.py`
- Modify: `src/qingpu_insight/listing_valuation.py`
- Modify: `tests/test_listing_metrics.py`
- Modify: `tests/test_listing_valuation.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: persisted exact/range columns and `location_eligible` filtering performed by the repository/web layer.
- Produces: range-aware public JSON and explicit `no_total_asking_price` model evidence.

- [ ] **Step 1: Write failing metrics and valuation tests**

Add a new-house row with no total price and assert:

```python
item = public_listings(frame, ListingFilters(listing_type="newhouse"))[0]
assert item["price"] is None
assert item["unit_price_range_twd_per_ping"] == {"low": 500_000, "high": 560_000}
assert item["area_range_ping"] == {"low": 19.0, "high": 30.0}

result = compare_listing_to_model(newhouse_without_total, registry, market,
                                  station_code="A18", station_distance_m=500,
                                  location_eligible=True)
assert result == {
    "valuation_eligible": False,
    "reason": "no_total_asking_price",
    "advertised_unit_price_range_twd_per_ping": (500_000, 560_000),
}
```

Assert the web endpoint omits records with `location_eligible=False` and never replaces missing total price with a midpoint-derived estimate.

- [ ] **Step 2: Run tests and verify old total-price assumptions fail**

Run: `python -m pytest tests/test_listing_metrics.py tests/test_listing_valuation.py tests/test_web.py -q`

Expected: FAIL because public output lacks range fields and valuation currently calls `asking_status(None, ...)`.

- [ ] **Step 3: Implement range-aware output and early valuation return**

In `public_listings`, emit `unit_price_range_twd_per_ping` and `area_range_ping` with `None` endpoints preserved only when the complete range is unavailable. In `listing_summary`, keep total-price KPIs for sale; for new house add `median_unit_price_low` and `median_unit_price_high` and leave total-price KPIs `None` when no total prices exist.

In `compare_listing_to_model`, after location/type checks and before feature validation, return `no_total_asking_price` for `newhouse` when `asking_price_twd is None`, carrying the advertised low/high tuple. Do not call `valuate` in this branch.

- [ ] **Step 4: Verify public and model behavior**

Run: `python -m pytest tests/test_listing_metrics.py tests/test_listing_valuation.py tests/test_web.py -q && python -m ruff check src/qingpu_insight/listing_metrics.py src/qingpu_insight/listing_valuation.py`

Expected: all tests PASS and JSON contains no NaN/Infinity values.

- [ ] **Step 5: Commit honest comparison behavior**

```bash
git add src/qingpu_insight/listing_metrics.py src/qingpu_insight/listing_valuation.py tests/test_listing_metrics.py tests/test_listing_valuation.py tests/test_web.py
git commit -m "feat: expose range-aware listing insights"
```

---

### Task 5: Make CLI acquisition usable and self-verifying

**Files:**
- Modify: `src/qingpu_insight/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: extraction counts stored in `CapturedPage`, `ChromeConfig`, capture manifests.
- Produces: visible-first `listing-scrape`/`listing-sync`, `--headless`, `--profile-dir`, concise summaries, and non-zero exit on zero accepted records.

- [ ] **Step 1: Add failing CLI argument and result tests**

Assert:

```python
args = build_parser().parse_args(["listing-scrape", "--types", "sale",
                                  "--headless", "--profile-dir", "C:/ChromeProfile"])
assert args.headless is True
assert args.profile_dir == "C:/ChromeProfile"

def test_listing_scrape_returns_nonzero_when_capture_has_no_accepted_records(...):
    assert listing_scrape(tmp_path, args) == 1
```

Capture stdout for a successful fake page and require the fields `captured_pages=1`, `accepted=1`, `rejected=0`, `representation=dom`, `complete=false`, and `batch_path=`.

- [ ] **Step 2: Run CLI tests and verify flags/output are absent**

Run: `python -m pytest tests/test_cli.py -q`

Expected: FAIL because the parser still exposes `--no-headless`, no profile option, and no accepted-record summary.

- [ ] **Step 3: Implement CLI flags and failure semantics**

Replace `--no-headless` with `--headless` and add `--profile-dir` to both commands. Construct:

```python
ChromeConfig(
    headless=args.headless,
    profile_dir=args.profile_dir,
    page_timeout_seconds=args.page_timeout,
    delay_seconds=(args.delay_min, args.delay_max),
)
```

Sum accepted/rejected counts from captured pages, print one line per type, and return `1` if errors exist or accepted count is zero. During `listing-build` and `listing-sync`, call `extract_rendered_page`, aggregate rejection reasons, and return non-zero on any page schema failure. Keep an intentionally capped batch processable only through `listing-sync`; `listing-build` continues to reject incomplete evidence so lifecycle state cannot be advanced from it.

- [ ] **Step 4: Run CLI and lifecycle tests**

Run: `python -m pytest tests/test_cli.py tests/test_listing_events.py tests/test_listing_capture.py -q && python -m ruff check src/qingpu_insight/cli.py tests/test_cli.py`

Expected: all tests PASS and incomplete batches never increment absence counters.

- [ ] **Step 5: Commit the operator workflow**

```bash
git add src/qingpu_insight/cli.py tests/test_cli.py
git commit -m "feat: add visible-first listing CLI workflow"
```

---

### Task 6: Run full regression and live acceptance, then document evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/m3-listing-methodology.md`
- Modify: `docs/superpowers/plans/2026-07-21-qingpu-insight-m3-listing-intelligence.md`

**Interfaces:**
- Consumes: completed tasks 1–5 and authorized visible browsing.
- Produces: reproducible commands, limitations, raw batch evidence, and a clean verified worktree.

- [ ] **Step 1: Run the complete automated suite**

Run: `python -m pytest -q`

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Run static and repository hygiene checks**

Run: `python -m ruff check . && git diff --check && git status --short`

Expected: Ruff and `git diff --check` exit zero; status contains only intended M3 files and ignored raw captures do not appear.

- [ ] **Step 3: Run one-page visible acceptance for all three types**

Run:

```powershell
python -m qingpu_insight.cli listing-scrape --types sale newhouse rental --max-pages 1 --page-timeout 45 --delay-min 2 --delay-max 4
```

Expected: each type reports `accepted` greater than zero, canonical 591 URLs, a representation, `complete=false`, and an isolated `591-{type}-{timestamp}` batch path. No password is passed on the command line.

- [ ] **Step 4: Inspect manifests and build each raw batch against isolated persistence**

For each reported batch path, verify `manifest.json` has requested type, Taoyuan route, accepted count greater than zero, no contact keys, and `is_complete=false`. Copy each batch to a temporary acceptance directory, set only the copied manifest `reached_terminal_page=true` and `is_complete=true`, then run `listing-build --batch-dir <copied-path>` with the default Parquet repository. This proves parsing/normalization/storage without misrepresenting the original capped capture.

Expected: build exits zero for all three copied batches; `data/processed/listing_snapshots.parquet` contains stable IDs, exact/range price, exact/range area, raw hash, and acquisition metadata. New-house rows with no coordinates remain `location_eligible=False`, which is reported rather than guessed.

- [ ] **Step 5: Document commands, observed representations, and limits**

Update README and methodology with visible Chrome as the default, `--headless` as best-effort, `--profile-dir` behavior, the three public routes, new-house JSON-LD range semantics, raw evidence paths, and the rule that unlocated new houses do not enter A17–A19 metrics. Mark the original M3 plan acceptance items complete only when supported by test or live evidence.

- [ ] **Step 6: Re-run targeted docs-adjacent smoke tests and commit**

Run: `python -m pytest tests/test_cli.py tests/test_web.py -q && git diff --check`

Expected: all tests PASS and no whitespace errors.

```bash
git add README.md docs/m3-listing-methodology.md docs/superpowers/plans/2026-07-21-qingpu-insight-m3-listing-intelligence.md
git commit -m "docs: record M3 live acquisition workflow"
```

---

## Final acceptance checklist

- [ ] Sale, rental, and new-house live fixtures parse through separate validated representations.
- [ ] One malformed card does not discard valid siblings; zero valid cards fails the page.
- [ ] Visible Chrome is default and headless failure is explicit.
- [ ] All selector alternatives share one readiness deadline.
- [ ] Batch directory, batch ID, type, manifest, and page evidence agree.
- [ ] Exact and ranged monetary/area values survive normalization, hashes, Parquet, MySQL, and public JSON.
- [ ] No midpoint-derived total asking price exists.
- [ ] Incomplete evidence cannot advance delisting counters.
- [ ] No credential, contact field, cookie, token, or browser profile path is persisted in project data.
- [ ] Full pytest, Ruff, diff check, and visible one-page acceptance pass.
