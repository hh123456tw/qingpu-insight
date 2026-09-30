import hashlib
import re
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

SQM_PER_PING = 3.305785
PRICE_PER_PING_MIN = 100_000
PRICE_PER_PING_MAX = 2_000_000
MARKET_TRANSACTION_SUBJECTS = frozenset(
    {"房地(土地+建物)", "房地(土地+建物)+車位"}
)
SPECIAL_RELATIONSHIP_PATTERN = re.compile(
    r"親友|員工|共有人|特殊關係|二等親"
)

REQUIRED_COLUMNS = frozenset(
    {
        "transaction_type",
        "record_id",
        "transaction_date",
        "transaction_subject",
        "source_file",
        "building_area_sqm",
        "unit_price_sqm_twd",
        "completion_date",
        "main_use",
        "coordinate_eligible",
        "station_code",
    }
)


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


AREA_COMPONENT_COLUMNS = (
    "main_building_area_sqm",
    "auxiliary_building_area_sqm",
    "balcony_area_sqm",
)
MAX_COMMON_AREA_RATIO = 0.70


def add_area_share_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Share of the non-parking building area that is common (公設), from official areas.

    Parking area is removed from the denominator because registered parking spaces sit in
    the common area; balconies are private area, not common area. Ratios outside
    [0, MAX_COMMON_AREA_RATIO] or with missing components are left empty.
    """
    result = frame.copy()
    for column in AREA_COMPONENT_COLUMNS:
        if column not in result:
            result[column] = pd.NA
        result[column] = pd.to_numeric(result[column], errors="coerce")
    parking = (
        pd.to_numeric(result["parking_area_sqm"], errors="coerce").fillna(0)
        if "parking_area_sqm" in result
        else 0.0
    )
    non_parking = pd.to_numeric(result["building_area_sqm"], errors="coerce") - parking
    result["common_area_ratio"] = _common_area_ratio_values(
        *(result[column] for column in AREA_COMPONENT_COLUMNS), non_parking
    )
    return result


def _common_area_ratio_values(main, auxiliary, balcony, non_parking_area) -> np.ndarray:
    values = [
        pd.Series(value, dtype=object).pipe(pd.to_numeric, errors="coerce")
        .to_numpy(dtype=float, na_value=np.nan)
        if isinstance(value, pd.Series)
        else np.asarray(pd.to_numeric(value, errors="coerce"), dtype=float)
        for value in (main, auxiliary, balcony, non_parking_area)
    ]
    private = values[0] + values[1] + values[2]
    denominator = np.where(values[3] > 0, values[3], np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = 1 - private / denominator
    return np.where((ratio >= 0) & (ratio <= MAX_COMMON_AREA_RATIO), ratio, np.nan)


def common_area_ratio(
    main: float, auxiliary: float, balcony: float, non_parking_area: float
) -> float | None:
    """公設比 net of parking for one unit: 1 - (main + auxiliary + balcony) / non-parking area.

    The training definition of add_area_share_features applied to a single valuation; any
    unit works as long as all four areas share it. None when outside [0, 0.70] or unusable.
    """
    value = float(_common_area_ratio_values(main, auxiliary, balcony, non_parking_area))
    return value if np.isfinite(value) else None


def _key(row: pd.Series) -> str:
    payload = "|".join(
        str(row.get(name, ""))
        for name in ("transaction_type", "record_id", "transaction_date", "source_file")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


PRECOMPLETION_TRANSFERS_FILE = "precompletion_transfers.parquet"


def _annotate_market_rows(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    invalid = set(frame["transaction_type"].unique()) - {"resale", "presale"}
    if invalid:
        raise ValueError(f"Invalid transaction_type values: {sorted(invalid)}")

    output = add_area_share_features(frame)
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
    market_subject = output["transaction_subject"].isin(
        MARKET_TRANSACTION_SUBJECTS
    )
    remarks = (
        output["remarks"]
        if "remarks" in output
        else pd.Series("", index=output.index, dtype="object")
    )
    special_relationship = remarks.fillna("").str.contains(
        SPECIAL_RELATIONSHIP_PATTERN
    )
    base_eligible = residential & in_circle & valid_price & valid_area & valid_date
    eligible_before_completion = base_eligible & market_subject & ~special_relationship
    resale = output["transaction_type"].eq("resale")
    missing_completion_date = (
        resale & eligible_before_completion & output["completion_date"].isna()
    )
    future_completion_transfer = (
        resale
        & eligible_before_completion
        & output["completion_date"].gt(output["transaction_date"])
    )
    output["analysis_eligible"] = eligible_before_completion & ~(
        missing_completion_date | future_completion_transfer
    )

    masks = {
        "residential": residential,
        "in_circle": in_circle,
        "valid_price": valid_price,
        "valid_area": valid_area,
        "valid_date": valid_date,
        "base_eligible": base_eligible,
        "market_subject": market_subject,
        "special_relationship": special_relationship,
        "missing_completion_date": missing_completion_date,
        "future_completion_transfer": future_completion_transfer,
    }
    return output, masks


def build_precompletion_transfers(frame: pd.DataFrame) -> pd.DataFrame:
    """Market-eligible resale-labelled transfers recorded before the building was completed.

    They are excluded from resale targets (they are presale deals) but remain the price
    history of their building, which the anchor model uses for new projects.
    """
    output, masks = _annotate_market_rows(frame)
    transfers = output.loc[masks["future_completion_transfer"]].copy()
    transfers["building_age_years"] = pd.NA
    return transfers.drop_duplicates("transaction_key").reset_index(drop=True)


def build_market_dataset(frame: pd.DataFrame) -> tuple[pd.DataFrame, MarketQuality]:
    output, masks = _annotate_market_rows(frame)
    residential = masks["residential"]
    in_circle = masks["in_circle"]
    valid_price = masks["valid_price"]
    valid_area = masks["valid_area"]
    valid_date = masks["valid_date"]
    base_eligible = masks["base_eligible"]
    market_subject = masks["market_subject"]
    special_relationship = masks["special_relationship"]
    missing_completion_date = masks["missing_completion_date"]
    future_completion_transfer = masks["future_completion_transfer"]

    reasons = {
        "non_residential": int((~residential).sum()),
        "outside_life_circle": int((residential & ~in_circle).sum()),
        "invalid_price": int((residential & in_circle & ~valid_price).sum()),
        "invalid_area": int((residential & in_circle & valid_price & ~valid_area).sum()),
        "invalid_date": int(
            (residential & in_circle & valid_price & valid_area & ~valid_date).sum()
        ),
        "non_market_subject": int((base_eligible & ~market_subject).sum()),
        "special_relationship": int(
            (base_eligible & market_subject & special_relationship).sum()
        ),
        "missing_completion_date": int(missing_completion_date.sum()),
        "future_completion_transfer": int(future_completion_transfer.sum()),
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
