# M3 Browser-Backed Listing Acquisition Design

## Objective

Make the M3 listing pipeline reliably produce project-usable sale, new-house, and rental data from the authorized 591 browsing flow. The design prioritizes stable identifiers, reproducible raw evidence, A17–A19 analysis eligibility, and clear failure behavior over allegiance to either HTML or JSON.

## Confirmed constraints

- The operator is authorized to browse all three listing categories.
- Normal browser navigation is the acquisition boundary. The implementation may consume rendered DOM, embedded structured state, or structured responses observed inside that browser session.
- The pipeline must not bypass verification, defeat access controls, invent private credentials, or silently switch to unrelated data sources.
- M1 and M2 training data remain official transaction data only. Listing data is comparison and monitoring input, never training input.
- Raw captures remain untracked and may contain all Taoyuan results. Only listings within two kilometres of A17, A18, or A19 are analysis-eligible.

## Chosen approach

Use a browser-backed hybrid acquisition layer with one adapter per listing type.

Each adapter may select the most reliable representation exposed by the authorized browser session:

1. Prefer a structured response or embedded state when it contains the required fields and passes region and schema validation.
2. Otherwise parse the fully rendered DOM.
3. Never accept a response merely because it is valid JSON. It must identify the requested listing type and Taoyuan region, contain stable listing IDs, and satisfy the normalized contract.

Direct unauthenticated `requests` calls to undocumented endpoints are excluded. They would create a second session model, duplicate cookie handling, and weaken the portfolio explanation.

## Architecture

### Browser session

`BrowserSession` owns Chrome configuration, normal navigation, page readiness, optional profile reuse, and diagnostic capture. Visible Chrome is the default for manual M3 acquisition because live verification showed that the sale result list is absent in headless mode. Headless remains opt-in and must fail clearly when only navigation or recommendation content appears.

The browser session exposes:

- final URL, title, rendered HTML, and capture timestamp;
- browser-observed structured responses when available;
- one total readiness deadline rather than a separate full timeout per selector;
- diagnostic HTML and metadata when extraction fails.

### Per-type adapters

`Sale591Adapter`, `Newhouse591Adapter`, and `Rental591Adapter` implement a common interface:

```python
extract(browser_capture) -> ExtractionResult
```

`ExtractionResult` contains accepted `SourceListing` records, rejected-card diagnostics, representation type (`dom`, `embedded_json`, or `browser_response`), and schema version.

Known live behavior establishes the first adapter versions:

- Sale: rendered cards currently use `div.ware-item[data-id]` and detail links under the card header.
- Rental: rendered cards currently use `div.item[data-id]` with `item-info-*` descendants.
- New house: the existing `/home/housing/search` route returns JSON and did not prove that the requested Taoyuan region was honored. The adapter must start from the human-facing public listing page, observe both DOM and browser responses, and accept only data that passes region validation.

### Normalized contract

An accepted record requires:

- non-empty stable source listing ID;
- canonical HTTPS URL on a `591.com.tw` host;
- listing type;
- non-empty title;
- positive asking price for sale/new-house or positive monthly rent for rental;
- positive building area;
- capture timestamp and acquisition representation;
- raw evidence hash.

Layout, floor, coordinates, building age, parking, and building type remain nullable. Missing optional fields must not be represented as numeric zero.

### Location and storage

Every accepted Taoyuan record may be stored as raw or normalized evidence. `assign_listing_life_circle` remains the only authority for A17–A19 eligibility. Metrics, valuation comparisons, and public dashboard results must require `location_eligible=True`.

Snapshot identity remains `(source, listing_type, source_listing_id, batch_id)`. Current-state identity remains `(source, listing_type, source_listing_id)`.

## Data flow

1. Open the human-facing list page in Chrome.
2. Wait once for a recognized result, verified empty state, or total timeout.
3. Collect rendered DOM and any browser-observed structured candidates.
4. Let the type adapter select and validate a representation.
5. Write raw evidence and extraction diagnostics atomically.
6. Normalize accepted records and reject invalid records individually.
7. Assign A17–A19 distance and eligibility.
8. Persist snapshots and current state.
9. Run event detection only when the capture proves completeness.
10. Run M2 comparisons only for eligible sale/new-house records.

## Completeness and pagination

A configured page limit is a safety cap, not proof of completeness. A batch is complete only when the adapter observes a verified terminal state after successfully processing prior pages. Click failures, timeouts, structure changes, verification pages, headless-empty results, and operator-imposed caps all produce incomplete batches and can never advance delisting counters.

Pagination controls are type-specific. The adapter must verify that navigation changed either the canonical page number or the set of stable listing IDs. A click with no data change is an error, not a terminal page.

## Error handling and diagnostics

- Reject malformed cards individually and report counts plus reason codes.
- Fail the page when no valid card remains after parsing.
- Save diagnostic HTML for schema failures under the ignored raw batch directory.
- Do not log contact data, cookies, authorization headers, browser profiles, or credentials.
- Report verification/login pages explicitly so the operator can resume with a browser profile.
- Keep retries bounded by one total page deadline and a small retry count.
- Preserve incomplete manifests even when extraction fails.

## CLI behavior

- Add an explicit `--headless` option; visible Chrome is the default for listing commands.
- Add `--profile-dir` for operator-owned Chrome profile reuse.
- Keep `--max-pages`, delay, and timeout controls.
- Print one concise result per type: captured pages, accepted listings, rejected cards, representation, completeness, and raw batch path.
- Return non-zero when a requested type produces no accepted records.

## Testing strategy

### Automated fixtures

- Create minimal anonymized fixtures from live sale, rental, and new-house structures.
- Store only fields required to prove parsing; exclude names, phone numbers, free-form descriptions, images, cookies, and tokens.
- Test stable ID, canonical URL, price/rent, area, layout, floor, optional coordinates, and type isolation.
- Test structured-candidate rejection when the region or schema is wrong.
- Test DOM fallback when structured data is absent or invalid.

### Capture and lifecycle tests

- One total timeout for multiple selector alternatives.
- Headless-empty result becomes an explicit incomplete error.
- Navigation failure never marks a batch complete.
- Repeated batches remain idempotent.
- Two complete missing batches are required for delisting.
- Incomplete or capped batches never increment absence counters.

### Live acceptance

Run one visible-browser page for each type. Acceptance requires:

- exit code zero;
- at least one accepted record per requested type;
- non-empty stable IDs and canonical URLs;
- positive price/rent and area;
- requested Taoyuan provenance;
- no credentials or contact fields in logs or tracked files;
- successful normalization and location assignment;
- an intentionally incomplete manifest when stopped by `--max-pages 1`.

Headless acceptance is separate. If a type cannot load real result cards headlessly, that limitation is documented and the command must fail with a specific message rather than emitting partial data.

## Portfolio presentation

The project will describe the acquisition boundary as an authorized browser-backed adapter layer. The portfolio can demonstrate raw evidence, schema validation, representation fallback, geospatial eligibility, lifecycle safety, and the strict separation between asking-price monitoring and official transaction-model training.

## Out of scope

- CAPTCHA solving or verification bypass.
- Credential storage or automatic login.
- Scheduled unattended crawling; this remains M4 work.
- Using listing data to train M2 valuation models.
- Community, amenity, or agent enrichment unrelated to the three listing types.
