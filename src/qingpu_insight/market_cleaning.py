import hashlib
from dataclasses import asdict, dataclass

import pandas as pd

SQM_PER_PING = 3.305785
PRICE_PER_PING_MIN = 100_000
PRICE_PER_PING_MAX = 2_000_000

REQUIRED_COLUMNS = frozenset({
    "transaction_type",
    "record_id",
    "transaction_date",
    "source_file",
    "building_area_sqm",
    "unit_price_sqm_twd",
    "completion_date",
    "main_use",
    "coordinate_eligible",
    "station_code",
})


@dataclass(frozen=True)
class MarketQuality:
    input_records: int
    output_records: int
    output_by_type: dict[str, int]
    exclusion_reasons: dict[str, int]
    minimum_date: str | None
    maximum_date: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _key(row: pd.Series) -> str:
    payload = "|".join(
        str(row.get(name, ""))
        for name in ("transaction_type", "record_id", "transaction_date", "source_file")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_market_dataset(frame: pd.DataFrame) -> tuple[pd.DataFrame, MarketQuality]:
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    invalid = set(frame["transaction_type"].unique()) - {"resale", "presale"}
    if invalid:
        raise ValueError(f"Invalid transaction_type values: {sorted(invalid)}")

    output = frame.copy()
    output["building_area_ping"] = output["building_area_sqm"] / SQM_PER_PING
    output["unit_price_per_ping_twd"] = output["unit_price_sqm_twd"] * SQM_PER_PING
    output["building_age_years"] = (
        output["transaction_date"] - output["completion_date"]
    ).dt.days / 365.2425
    output.loc[output["transaction_type"].eq("presale"), "building_age_years"] = pd.NA
    output["transaction_key"] = output.apply(_key, axis=1)

    residential = output["main_use"].fillna("").str.contains("住家")
    in_circle = output["coordinate_eligible"].fillna(False) & output["station_code"].isin(
        ("A17", "A18", "A19")
    )
    valid_price = output["unit_price_per_ping_twd"].between(
        PRICE_PER_PING_MIN, PRICE_PER_PING_MAX, inclusive="both"
    )
    valid_area = output["building_area_ping"].between(5, 200, inclusive="both")
    valid_date = output["transaction_date"].notna()
    output["analysis_eligible"] = residential & in_circle & valid_price & valid_area & valid_date

    reasons = {
        "non_residential": int((~residential).sum()),
        "outside_life_circle": int((residential & ~in_circle).sum()),
        "invalid_price": int((residential & in_circle & ~valid_price).sum()),
        "invalid_area": int((residential & in_circle & valid_price & ~valid_area).sum()),
        "invalid_date": int(
            (residential & in_circle & valid_price & valid_area & ~valid_date).sum()
        ),
    }
    reasons = {name: count for name, count in reasons.items() if count}
    clean = output.loc[output["analysis_eligible"]].drop_duplicates("transaction_key").copy()
    quality = MarketQuality(
        input_records=len(output),
        output_records=len(clean),
        output_by_type={
            str(kind): int(count)
            for kind, count in clean["transaction_type"].value_counts().items()
        },
        exclusion_reasons=reasons,
        minimum_date=clean["transaction_date"].min().date().isoformat() if len(clean) else None,
        maximum_date=clean["transaction_date"].max().date().isoformat() if len(clean) else None,
    )
    return clean.reset_index(drop=True), quality
