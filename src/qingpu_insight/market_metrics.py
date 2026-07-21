from dataclasses import dataclass
from typing import Any

import pandas as pd

PUBLIC_TRANSACTION_COLUMNS: tuple[str, ...] = (
    "transaction_type",
    "record_id",
    "station_code",
    "transaction_date",
    "building_area_ping",
    "unit_price_per_ping_twd",
    "total_price_twd",
    "building_type",
    "bedrooms",
    "living_rooms",
    "bathrooms",
    "building_age_years",
    "station_distance_m",
    "longitude",
    "latitude",
    "match_quality",
)


@dataclass(frozen=True)
class MarketFilters:
    transaction_type: str
    station_codes: tuple[str, ...] = ("A17", "A18", "A19")
    date_from: pd.Timestamp | None = None
    date_to: pd.Timestamp | None = None
    area_ping_min: float | None = None
    area_ping_max: float | None = None
    building_types: tuple[str, ...] = ()
    bedrooms: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.transaction_type not in {"resale", "presale"}:
            raise ValueError("transaction_type must be resale or presale")
        if not self.station_codes or not set(self.station_codes) <= {"A17", "A18", "A19"}:
            raise ValueError("station_codes must contain A17, A18, or A19")
        if self.area_ping_min is not None and self.area_ping_min < 0:
            raise ValueError("area_ping_min must be non-negative")
        if (
            self.area_ping_min is not None
            and self.area_ping_max is not None
            and self.area_ping_min > self.area_ping_max
        ):
            raise ValueError("area_ping_min must not exceed area_ping_max")


def filter_market(frame: pd.DataFrame, filters: MarketFilters) -> pd.DataFrame:
    result = frame.copy()
    result = result[result["transaction_type"] == filters.transaction_type]
    if len(filters.station_codes) < 3:
        result = result[result["station_code"].isin(filters.station_codes)]
    if filters.date_from is not None:
        result = result[result["transaction_date"] >= filters.date_from]
    if filters.date_to is not None:
        result = result[result["transaction_date"] <= filters.date_to]
    if filters.area_ping_min is not None:
        result = result[result["building_area_ping"] >= filters.area_ping_min]
    if filters.area_ping_max is not None:
        result = result[result["building_area_ping"] <= filters.area_ping_max]
    if filters.building_types:
        result = result[result["building_type"].isin(filters.building_types)]
    if filters.bedrooms:
        result = result[result["bedrooms"].isin(filters.bedrooms)]
    return result


def market_summary(frame: pd.DataFrame, filters: MarketFilters) -> dict[str, Any]:
    subset = filter_market(frame, filters)
    count = len(subset)
    median_price = subset["unit_price_per_ping_twd"].median()
    median_total = subset["total_price_twd"].median()
    latest_date = subset["transaction_date"].max()
    latest_date_str = str(latest_date.date()) if pd.notna(latest_date) else None
    return {
        "transaction_type": filters.transaction_type,
        "station_codes": list(filters.station_codes),
        "record_count": int(count),
        "median_unit_price_per_ping_twd": (float(median_price) if pd.notna(median_price) else None),
        "median_total_price_twd": float(median_total) if pd.notna(median_total) else None,
        "latest_transaction_date": latest_date_str,
    }


def market_trends(frame: pd.DataFrame, filters: MarketFilters) -> list[dict[str, Any]]:
    subset = filter_market(frame, filters)
    if subset.empty:
        return []
    working = subset.copy()
    working["month"] = working["transaction_date"].dt.to_period("M").astype(str)
    grouped = (
        working.groupby("month", sort=True)
        .agg(
            median_unit_price_per_ping_twd=("unit_price_per_ping_twd", "median"),
            record_count=("transaction_type", "count"),
        )
        .reset_index()
    )
    return grouped.to_dict(orient="records")


def recent_transactions(
    frame: pd.DataFrame, filters: MarketFilters, limit: int = 20
) -> list[dict[str, Any]]:
    subset = filter_market(frame, filters)
    sorted_subset = subset.sort_values("transaction_date", ascending=False)
    cap = min(limit, 100)
    public_columns = [
        column for column in PUBLIC_TRANSACTION_COLUMNS if column in sorted_subset.columns
    ]
    rows = sorted_subset.head(cap)[public_columns].to_dict(orient="records")
    for row in rows:
        for key, value in row.items():
            try:
                if pd.isna(value):
                    row[key] = None
            except (TypeError, ValueError):
                pass
        if "latitude" in row and row["latitude"] is not None and pd.notna(row["latitude"]):
            row["latitude"] = round(float(row["latitude"]), 4)
        if "longitude" in row and row["longitude"] is not None and pd.notna(row["longitude"]):
            row["longitude"] = round(float(row["longitude"]), 4)
    return rows
