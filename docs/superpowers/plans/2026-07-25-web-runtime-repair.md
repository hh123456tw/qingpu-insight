# Web Runtime Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The user explicitly requested a simple plan and no TDD; tests are added after implementation as regression coverage.

**Goal:** Make the normal `qingpu-web.exe` startup load local configuration, expose listing-backed features, and render Leaflet markers without broken images.

**Architecture:** Keep dependency injection in `create_app()` unchanged for tests. Add a small production startup composer that loads `.env`, creates the configured listing repository, and passes it into `create_app()`. Configure Leaflet's default icon URLs explicitly because its CDN path heuristic currently generates invalid URLs.

**Tech Stack:** Python 3.12, Flask, python-dotenv, MySQL/Parquet repositories, Leaflet 1.9.4, pytest, Ruff.

## Global Constraints

- Do not use TDD; implement first, then add regression tests.
- Preserve all pre-existing uncommitted changes.
- Existing process environment variables must override values in `.env`.
- Do not expose database credentials or secrets in logs or API errors.

---

### Task 1: Production startup composition

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/qingpu_insight/web.py`
- Test: `tests/test_web.py`

- [x] Add `python-dotenv>=1,<2`.
- [x] Add a startup helper that loads `<root>/.env` with `override=False`.
- [x] Build the listing repository with `create_listing_repository(root)`.
- [x] Pass the repository to `create_app(root=root, listing_repo=...)`.
- [x] Serialize access to the shared PyMySQL listing connection for concurrent Flask requests.
- [x] Verify a normal startup exposes `/api/listings/summary` instead of `listing_data_unavailable`.

### Task 2: Leaflet marker assets

**Files:**
- Modify: `src/qingpu_insight/static/app.js`
- Test: `tests/test_web.py`

- [x] Configure Leaflet's marker, retina marker, and shadow path explicitly under `https://unpkg.com/leaflet@1.9.4/dist/images/`.
- [x] Verify marker images load with non-zero natural dimensions.

### Task 3: Verification and record

**Files:**
- Modify: `docs/superpowers/plans/2026-07-25-web-runtime-repair.md`

- [x] Run targeted web and repository tests.
- [x] Run the full pytest suite and Ruff.
- [x] Start the real executable and verify market, listing, ops, and static endpoints.
- [x] Inspect the page in a browser and record final results below.

## Verification Record

- Status: completed
- Targeted tests: 3 passed
- Full test suite: reached 100% with no failures; existing deprecation/future warnings remain
- Ruff: `All checks passed!`
- JavaScript syntax: `node --check` passed
- Dependencies: `pip check` reported no broken requirements
- Real executable/API smoke:
  - `/`: 200
  - `/static/app.js`: 200
  - `/api/market/summary`: 200
  - `/api/listings/summary`: 200 under concurrent browser load
  - `/api/ops/health`: 200 after the first health run
  - `/api/ops/backups`: 200
- Browser/map smoke:
  - resale: 16,293 records, median unit price 38.6 萬
  - presale filter: 2,907 records, median unit price 45.2 萬
  - marker icon: 50×82 pixels from the expected Leaflet `/dist/images/` URL
  - marker shadow: 41×41 pixels from the expected Leaflet `/dist/images/` URL
  - browser console: no errors or warnings

## Operational Data Record

- Ran `health-run` once. Recorded status: `critical`.
- Critical health reasons are data readiness, not endpoint failures:
  - no backup record
  - no active newhouse listings
  - no active rental listings
  - no published market dataset version
- Ran the documented idempotent `mysql-load` workflow.
- Upserted 19,200 existing Parquet market rows into local MySQL.
- The listing API is operational, but the public sale count is currently 0 because the existing active rows have `location_eligible=0`; the API intentionally hides them.

## Follow-up: listing artifact failure and broken map

### Incident

- Failed job: `28c63d2d-f9c3-4b9f-808a-df84eb592a1f`
- Public error: `artifact_failed` / `listing artifact validation failed`
- Capture evidence:
  - sale: 31 accepted, 0 rejected, complete
  - newhouse: 7 accepted, 7 rejected, complete
  - rental: 30 accepted, 0 rejected, complete

### Root causes and repairs

- The crawler completed successfully. Artifact creation failed after MySQL rows were
  combined with fresh rows:
  - PyMySQL returned `DECIMAL` columns as `decimal.Decimal`, while fresh normalized
    rows used `float`.
  - MySQL boolean columns used `0`/`1`, while fresh rows used `bool`.
  - MySQL `DATETIME` values were naive, while fresh capture timestamps were UTC-aware.
- `MySQLListingRepository` now normalizes these values at its read boundary to the
  listing DataFrame contract: numeric floats, booleans, and UTC timestamps.
- Leaflet image files and OpenStreetMap tiles were healthy. The map broke because the
  browser rejected the CDN Leaflet stylesheet at the SRI boundary, leaving panes and
  tiles with static positioning. The stylesheet link no longer applies the failing
  integrity constraint; marker asset paths remain explicitly pinned to Leaflet 1.9.4.

### Follow-up verification

- Replayed all three saved 2026-07-25 captures without network access.
- Prepared 86 artifact rows:
  - sale: 49 rows / 18 events
  - newhouse: 7 rows / 7 events
  - rental: 30 rows / 30 events
- Parquet write, read-back, artifact hash, and canonical row hash all passed.
- Fresh server browser check:
  - Leaflet CSS loaded
  - pane/tile positioning: `absolute`
  - 12 tiles loaded, 0 broken
  - 20 markers loaded, 0 broken
  - retina marker natural size: 50×82
- Targeted repository/update/web tests: 209 passed.
- Full pytest suite: reached 100%, exit code 0; warnings only.
- Ruff and JavaScript syntax checks: passed.
