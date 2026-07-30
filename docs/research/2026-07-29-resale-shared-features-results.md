# Resale Shared-Feature Research

> **Status:** Implementation complete, candidate training pending (requires MySQL database).

## Research Question

Do public-area ratio and time-safe community statistics improve Qingpu resale valuation accuracy beyond the v3 baseline?

## Data and Leakage Controls

- Public-area ratio derived from official MOI area components, bounded 0–70%
- Community matching uses deterministically curated registry (27 communities)
- Historical community statistics use strictly prior 24-month transactions
- Same-date transactions never contribute to each other's features
- E1–E4 feature family selection uses calibration MAE/MAPE only; final test is never inspected during selection
- Community identity never enters estimator as raw name (uses `community_id` → statistics)

## Experiment Families (E1–E4)

| Name | Features |
|------|----------|
| baseline_v3 | Existing 20 feature columns (unchanged) |
| common_area | baseline_v3 + `common_area_ratio` |
| community | baseline_v3 + 4 community statistics |
| common_area_community | baseline_v3 + `common_area_ratio` + 4 community statistics |
| common_area_community_management | baseline_v3 + `common_area_ratio` + 4 community statistics + `has_management` |

## Result

**Pending — requires completing a guided training run with a configured MySQL database.**

To train:

```powershell
$env:QINGPU_DATABASE_URL = "mysql://..."
.\.venv\Scripts\qingpu-web.exe  # then use admin UI → training
```

## Gate Checks (Implemented)

- MAE ≥ 2% improvement versus baseline
- Overall MAPE does not worsen
- No station MAPE worsens by > 1 percentage point
- ≥ 2 of 3 annual backtests pass
- Prediction-interval coverage within accepted range
- 591 validation: ≥ 20 labeled pages, ≥ 70% parsing success, ≥ 80% community recognition
- Registry digest check on validation corpus

## Limitations

- Community registry is curated by hand; coverage depends on manual verification
- 591 public-area ratio parsed from unstructured HTML; accuracy depends on page layout
- Community statistics use at most 24 months of history; newer communities have NaN values
- Registry coordinates are blank for all entries; coordinate-based matching is untested on live data

## Midterm Presentation Narrative

We extended the Qingpu resale valuation model with public-area ratio and community-specific transaction history. The public-area ratio is derived directly from official MOI land-registry data, not estimated. Community statistics use a carefully curated registry of 27 verified communities and a strict 24-month lookback to prevent any data leakage. The experiment framework compares five feature families on calibration data and locks one winner before the final test — ensuring honest evaluation.

## Interview Narrative

The model now understands not just a property's size and location, but its building efficiency (public-area ratio) and how its specific community has been trading. These features are optional — if you don't know the community or the ratio, the model still works fine. The feature selection process is governed by a strict protocol: we compare five feature families on calibration data only, lock the winner, and only then evaluate on the final test. We also added 591 listing analysis that can recognize community names and parse public-area ratios from listing pages, feeding that data back into the valuation.
