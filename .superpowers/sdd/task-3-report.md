# Task 3 report: structured-address geocoding cache

## Summary

Added a cache-backed structured-address geocoding service.  It canonicalizes
addresses with the existing address normalizer, returns provenance-rich
`LocationEvidence`, and only caches successful Taiwan-coordinate resolutions.
The official doorplate adapter performs exact normalized-address lookups only.

## Files

- `src/qingpu_insight/listing_geocoding.py`
- `tests/test_listing_geocoding.py`

## RED

1. The initial cache/provider tests failed at collection because
   `qingpu_insight.listing_geocoding` did not exist.
2. After the service was added, the normalized-cache test failed with two
   provider calls; canonicalization was corrected to remove spacing before
   calling `addresses.normalize_address`.
3. The doorplate/MySQL tests then failed at collection because those classes
   were not yet defined.

## GREEN and verification

- Focused geocoding tests: `7 passed`.
- Related location/normalization regressions: `69 passed`.
- Full suite: passed. It emitted five pre-existing pandas `FutureWarning`s in
  `listing_repository.py`; no test failures.
- `ruff check src/qingpu_insight/listing_geocoding.py tests/test_listing_geocoding.py`: passed.
- `git diff --check`: passed.

## Commit SHA

`dab9b6871ea652f59c9f392c9a00285fc126f364`

## Self-review

- Cache keys use a canonical normalized address, and cache hits bypass the
  provider.
- Provider unavailable, no-result, and invalid-coordinate cases return
  explicit unknown evidence and never cache a failure.
- Doorplate matching is exact-only; conflicting duplicate coordinates are
  unresolved rather than chosen arbitrarily.
- The MySQL schema is idempotent and uses UTC `DATETIME(6)`, parameterized SQL,
  a single upsert transaction, commit on success, and rollback/re-raise on a
  write failure.
- Cache reads restore UTC-aware timestamps and reject corrupt, incomplete,
  invalid-coordinate, or unknown-enum rows.

## Risks

- No real MySQL server is contacted; DB behavior is covered with fake
  connection/cursor tests. Production connection creation remains intentionally
  injected so it can follow the repository's environment-specific setup.
- TWD97 doorplate conversion uses the repository's existing `pyproj`
  dependency.
