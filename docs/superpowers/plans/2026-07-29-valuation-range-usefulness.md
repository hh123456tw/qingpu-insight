# 估值範圍可用性 Implementation Plan

**Goal:** Replace the oversized 90% model interval as the primary decision aid with a model point estimate and a high-similarity comparable transaction band.

**Architecture:** Reuse the valuation engine's comparable selection, project a deterministic IQR market band from immutable evidence, and demote the unchanged 90% interval to an explanatory disclosure.

### Task 1: Comparable evidence

- [ ] Return dwelling unit price and similarity from model comparables.
- [ ] Prefer model comparables in conversation evidence; retain the old market fallback.
- [ ] Preserve parking estimate and model point data required by presentation.

### Task 2: Price-band projection

- [ ] Compute the 25th–75th percentile band from at least 3 cases with similarity `>= 0.60`.
- [ ] Add parking exactly once and compare the asking price with the market band.
- [ ] Flag 90% model ranges wider than 40% of the point estimate.

### Task 3: Reply-card presentation

- [ ] Show model estimate, comparable band, asking price and confidence as primary data.
- [ ] Show an explicit no-band state when comparable evidence is insufficient.
- [ ] Move the 90% model range into a closed explanatory disclosure.

### Task 4: Verification and delivery

- [ ] Run focused Python and JavaScript coverage, then the full project gates.
- [ ] Restart the app and verify a real conversation in the browser.
- [ ] Commit and push `main` without adding local candidate artifacts.

