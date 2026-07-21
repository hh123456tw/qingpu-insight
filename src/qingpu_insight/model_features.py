import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

FEATURE_COLUMNS = (
    "station_code",
    "station_distance_m",
    "building_area_ping",
    "building_type",
    "bedrooms",
    "living_rooms",
    "bathrooms",
    "building_age_years",
    "floor",
    "total_floors",
    "floor_ratio",
    "parking_type",
    "parking_area_ping",
    "transaction_year",
    "transaction_month",
)

_CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _chinese_to_int(text: str) -> int:
    total = 0
    for ch in text:
        if ch == "十":
            total = 10 if total == 0 else total * 10
        else:
            total += _CHINESE_DIGITS.get(ch, 0)
    return total


def parse_floor(value) -> int | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        if pd.isna(value) or not float(value).is_integer():
            return None
        number = int(value)
        return number if 0 < abs(number) <= 200 else None
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned == "全":
        return None
    cleaned = cleaned.split("，", 1)[0]
    negative = False
    if cleaned.startswith("地下"):
        negative = True
        cleaned = cleaned[2:]
    if cleaned.endswith("層"):
        cleaned = cleaned[:-1]
    if not cleaned:
        return None
    try:
        num = int(cleaned)
    except ValueError:
        num = _chinese_to_int(cleaned)
    if negative:
        num = -num
    if num == 0 or abs(num) > 200:
        return None
    return num


def parking_adjusted_target(row: pd.Series) -> tuple[float, str]:
    area = float(row["building_area_ping"])
    parking_ping = max(float(row.get("parking_area_sqm", 0) or 0) / 3.305785, 0)
    parking_price = max(float(row.get("parking_price_twd", 0) or 0), 0)
    if parking_price > 0 and 0 < parking_ping < area:
        return (float(row["total_price_twd"]) - parking_price) / (area - parking_ping), "split"
    return float(row["unit_price_per_ping_twd"]), "official_unit_price"


def build_model_frame(frame: pd.DataFrame, transaction_type: str) -> pd.DataFrame:
    result = frame.loc[
        frame["analysis_eligible"].fillna(False) & frame["transaction_type"].eq(transaction_type)
    ].copy()
    result["floor"] = result["floor"].map(parse_floor)
    result["total_floors"] = result["total_floors"].map(parse_floor)
    result.loc[result["total_floors"].le(0), "total_floors"] = pd.NA
    result["floor_ratio"] = result["floor"] / result["total_floors"]
    result["parking_area_ping"] = result["parking_area_sqm"].fillna(0) / 3.305785
    result["transaction_year"] = result["transaction_date"].dt.year
    result["transaction_month"] = result["transaction_date"].dt.month
    targets = result.apply(parking_adjusted_target, axis=1)
    result[["target_unit_price_twd", "target_policy"]] = pd.DataFrame(
        targets.tolist(), index=result.index
    )
    return result


@dataclass(frozen=True)
class ValuationInput:
    transaction_type: Literal["resale", "presale"]
    station_code: str
    station_distance_m: float
    building_area_ping: float
    building_type: str
    bedrooms: int
    living_rooms: int
    bathrooms: int
    building_age_years: float | None = None
    floor: int = 1
    total_floors: int = 1
    parking_type: str = ""
    parking_area_ping: float = 0
    asking_total_price_twd: int | None = None

    def __post_init__(self):
        if self.transaction_type not in {"resale", "presale"}:
            raise ValueError("transaction_type must be resale or presale")
        if self.station_code not in {"A17", "A18", "A19"}:
            raise ValueError("station_code must be A17, A18, or A19")
        numeric_values = (
            self.station_distance_m,
            self.building_area_ping,
            self.bedrooms,
            self.living_rooms,
            self.bathrooms,
            self.floor,
            self.total_floors,
            self.parking_area_ping,
        )
        if not all(math.isfinite(float(value)) for value in numeric_values):
            raise ValueError("numeric inputs must be finite")
        if not (5 <= self.building_area_ping <= 200):
            raise ValueError("building_area_ping must be between 5 and 200")
        if not (0 <= self.station_distance_m <= 2000):
            raise ValueError("station_distance_m must be between 0 and 2000")
        if not (0 <= self.bedrooms <= 10):
            raise ValueError("bedrooms must be between 0 and 10")
        if not (0 <= self.living_rooms <= 10):
            raise ValueError("living_rooms must be between 0 and 10")
        if not (0 <= self.bathrooms <= 10):
            raise ValueError("bathrooms must be between 0 and 10")
        if self.total_floors < 1:
            raise ValueError("total_floors must be at least 1")
        if self.floor < 1:
            raise ValueError("floor must be at least 1")
        if self.floor > self.total_floors:
            raise ValueError("floor must not exceed total_floors")
        if not (0 <= self.parking_area_ping <= 60):
            raise ValueError("parking_area_ping must be between 0 and 60")
        if self.transaction_type == "presale" and self.building_age_years is not None:
            raise ValueError("building_age_years must be omitted for presale")
        if self.transaction_type == "resale" and self.building_age_years is None:
            raise ValueError("building_age_years is required for resale")
        if self.building_age_years is not None and not math.isfinite(self.building_age_years):
            raise ValueError("building_age_years must be finite")
        if self.building_age_years is not None and not (0 <= self.building_age_years <= 100):
            raise ValueError("building_age_years must be between 0 and 100")
        if self.asking_total_price_twd is not None and self.asking_total_price_twd <= 0:
            raise ValueError("asking_total_price_twd must be > 0")


def input_frame(value: ValuationInput, data_date: pd.Timestamp) -> pd.DataFrame:
    data = {
        "station_code": [value.station_code],
        "station_distance_m": [value.station_distance_m],
        "building_area_ping": [value.building_area_ping],
        "building_type": [value.building_type],
        "bedrooms": [value.bedrooms],
        "living_rooms": [value.living_rooms],
        "bathrooms": [value.bathrooms],
        "building_age_years": [
            value.building_age_years if value.building_age_years is not None else None
        ],
        "floor": [value.floor],
        "total_floors": [value.total_floors],
        "floor_ratio": [value.floor / value.total_floors],
        "parking_type": [value.parking_type],
        "parking_area_ping": [value.parking_area_ping],
        "transaction_year": [data_date.year],
        "transaction_month": [data_date.month],
    }
    return pd.DataFrame(data)
