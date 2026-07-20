# M1 Market Methodology

## 1. Data Range and M0 GO Evidence

The M0 feasibility check validated **20 consecutive seasons** (110S3 through 115S2) of Ministry of the Interior (MOI) real-price registration data. At the M0 checkpoint:

- **62.6% eligible coordinate coverage** across all A17–A19 transactions
- Each station × type cell meets the 50-record minimum
- Decision: **GO** — sufficient data volume and geolocation quality to proceed to M1 market analysis

## 2. Residential Eligibility and Price/Area Bounds

Only residential transactions within the A17–A19 life circle are eligible for market analysis.

### Eligibility criteria

| Criterion | Rule |
|-----------|------|
| Residential | `main_use` contains "住家" |
| Life circle | `coordinate_eligible` is `True` and `station_code` is A17, A18, or A19 |
| Price per ping | `unit_price_per_ping_twd` between **100,000** and **2,000,000** |
| Area | `building_area_ping` between **5** and **200** |
| Date | `transaction_date` is not null |

### Derived constants

```python
SQM_PER_PING = 3.305785
PRICE_PER_PING_MIN = 100_000
PRICE_PER_PING_MAX = 2_000_000
```

All bounds are applied inclusively.

## 3. Doorplate Matching and Nearest-Station Rule

Addresses are geocoded against Taoyuan City's official doorplate dataset. Match quality is one of:

- **exact** — literal doorplate match
- **nearest_number** — same street, closest doorplate number
- **nearest_street** — same village/district, closest street intersection
- **unmatched** — no geographic coordinate assigned

Transactions within **2 km** of their nearest station (A17/A18/A19) are assigned to that station's life circle. The 2 km radius covers the walkable catchment for each station.

## 4. Resale and Presale Separation

Resale (中古屋) and presale (預售屋) are **never aggregated** into a single price KPI. Reasons:

1. **Price formation** — Presale prices reflect future delivery expectations; resale prices reflect current market conditions.
2. **Unit-price calculation** — Presale prices often include builder financing terms and staged payments, making per-ping comparisons misleading.
3. **Building age** — Presale has zero building age at signing; resale age can span decades.
4. **Market behavior** — These segments respond differently to interest rates, supply pipeline, and regulatory changes.

All analysis views (station comparison, trend, map, recent cases) separate the two types. The `transaction_type` filter is required in every query.

## 5. Known Limitation: Presale Coordinates

Presale exact coordinates are **substantially less complete** than resale coordinates. Many presale transactions are recorded at the development-plot level rather than the individual-building level, resulting in:

- More `nearest_street` and `unmatched` matches
- Lower coordinate coverage in the M0 report for type `presale` vs `resale`
- Wider station-distance distributions

## 6. Demo Order (Five-Minute Walkthrough)

1. **Source traceability** — Show the data provenance chain from MOI CSV → M0 located Parquet → M1 market Parquet
2. **Market switch** — Toggle between resale and presale views
3. **Station comparison** — Compare A17, A18, A19 pricing and volume
4. **Trend** — Price-per-ping trend over the dataset's 20-season window
5. **Map** — Transaction distribution with station-life-circle overlay
6. **Recent cases** — Latest matched transactions with doorplate quality badges
7. **Architecture and limitations** — Parquet/MySQL dual source, coordinate coverage gaps, presale limitations
