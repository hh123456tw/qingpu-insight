"""Valuation evidence for a 591 listing imported into the conversation assistant.

Maps the listing's free-text fields (格局, 樓層, 建物型態, 車位, 公設比) onto the model's
:class:`~qingpu_insight.model_features.ValuationInput`, runs the official valuation and
picks nearby comparable transactions. Used by the conversation evidence builder.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

from qingpu_insight.geo import station_from_coords, wgs84_to_twd97
from qingpu_insight.market_cleaning import MAX_COMMON_AREA_RATIO, common_area_ratio
from qingpu_insight.market_metrics import MarketFilters
from qingpu_insight.market_repository import MarketDataSource
from qingpu_insight.model_features import ValuationInput, build_model_frame
from qingpu_insight.valuation import ModelRegistry, valuate


def listing_market_comparables(
    data_source: MarketDataSource,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    transaction_type = "presale" if payload.get("listing_type") == "newhouse" else "resale"
    area = pd.to_numeric(payload.get("area_ping"), errors="coerce")
    filters = MarketFilters(
        transaction_type=transaction_type,
        area_ping_min=float(area * 0.7) if pd.notna(area) else None,
        area_ping_max=float(area * 1.3) if pd.notna(area) else None,
    )
    frame = data_source.load(filters).copy()
    if frame.empty:
        return []

    latitude = pd.to_numeric(payload.get("latitude"), errors="coerce")
    longitude = pd.to_numeric(payload.get("longitude"), errors="coerce")
    frame["_distance_m"] = np.nan
    if pd.notna(latitude) and pd.notna(longitude):
        target_lat = np.radians(float(latitude))
        target_lon = np.radians(float(longitude))
        row_lat = np.radians(pd.to_numeric(frame["latitude"], errors="coerce"))
        row_lon = np.radians(pd.to_numeric(frame["longitude"], errors="coerce"))
        delta_lat = row_lat - target_lat
        delta_lon = row_lon - target_lon
        haversine = (
            np.sin(delta_lat / 2) ** 2
            + np.cos(target_lat) * np.cos(row_lat) * np.sin(delta_lon / 2) ** 2
        )
        frame["_distance_m"] = (
            6_371_000
            * 2
            * np.arctan2(
                np.sqrt(haversine),
                np.sqrt(1 - haversine),
            )
        )

    frame["_transaction_date"] = pd.to_datetime(
        frame["transaction_date"],
        errors="coerce",
    )
    frame = frame.sort_values(
        ["_distance_m", "_transaction_date"],
        ascending=[True, False],
        na_position="last",
    ).head(10)

    comparables: list[dict[str, Any]] = []
    for rank, (_, row) in enumerate(frame.iterrows(), start=1):
        date = row.get("_transaction_date")
        distance = row.get("_distance_m")
        comparables.append(
            {
                "rank": rank,
                "price_twd": (
                    int(row["total_price_twd"]) if pd.notna(row.get("total_price_twd")) else None
                ),
                "unit_price_per_ping_twd": (
                    int(row["unit_price_per_ping_twd"])
                    if pd.notna(row.get("unit_price_per_ping_twd"))
                    else None
                ),
                "distance_m": (int(round(float(distance))) if pd.notna(distance) else None),
                "transaction_date": (date.date().isoformat() if pd.notna(date) else None),
                "station_code": row.get("station_code"),
                "station_distance_m": (
                    float(row["station_distance_m"])
                    if pd.notna(row.get("station_distance_m"))
                    else None
                ),
                "selection_reason": "面積相近，依距離與日期排序",
            }
        )
    return comparables


_LAYOUT_RE = re.compile(
    r"(?P<bedrooms>\d+)\s*房.*?"
    r"(?P<living_rooms>\d+)\s*廳.*?"
    r"(?P<bathrooms>\d+)\s*衛"
)
_FLOOR_RE = re.compile(r"(?P<floor>\d+)\s*[Ff]")
# 591 sometimes renders 8.516坪 as "8.5 16坪" or "10. 32坪"; only a number that already
# has a decimal point may absorb a following space-split digit run, so "B2 16坪" stays 16.
_PARKING_AREA_RE = re.compile(
    r"(?P<area>\d+\.\s*\d+(?:\s+\d+)?|\d+)\s*坪"
)


def model_building_type(
    raw_building_type: object,
    total_floors: int,
) -> str:
    value = str(raw_building_type or "").strip()
    if not value:
        raise ValueError("building type unavailable")
    if "公寓" in value:
        return "公寓(5樓含以下無電梯)"
    if "華廈" in value:
        return "華廈(10層含以下有電梯)"
    if "住宅大樓" in value:
        return "住宅大樓(11層含以上有電梯)"
    if "電梯大樓" in value:
        if total_floors <= 10:
            return "華廈(10層含以下有電梯)"
        return "住宅大樓(11層含以上有電梯)"
    return value


def parse_listing_parking(
    raw_parking: object,
) -> tuple[str, float]:
    value = str(raw_parking or "").strip()
    if not value or "無車位" in value:
        return "", 0
    area_match = _PARKING_AREA_RE.search(value)
    if area_match is None:
        return "", 0
    area = float(re.sub(r"\s+", "", area_match.group("area")))
    if area <= 0:
        return "", 0
    if "機械" in value:
        parking_type = "坡道機械"
    elif "平面" in value:
        parking_type = "坡道平面"
    else:
        parking_type = "其他"
    return parking_type, area


def _positive_listing_number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def listing_common_area(
    payload: dict[str, Any],
    *,
    total_area: float,
    net_area: float,
    parking_area: float,
    parking_unverified: bool,
) -> tuple[float | None, dict[str, Any], str]:
    """公設比 net of parking from a 591 listing, or None when it cannot match the model.

    Uses the deed breakdown (主建物 + 附屬建物, where 591's 附屬建物 includes balconies)
    first. A listed 公設比 is taken as is only without parking; with a known parking area
    it is converted assuming 591 counts parking as common area. Never guesses otherwise.
    """
    unused = {"provided": False, "source": None, "ratio": None}
    skipped = "591 未提供可換算的主建物／附屬建物坪數，本次未使用公設比，估價區間較寬"
    if parking_unverified:
        return (
            None,
            unused,
            "591 車位坪數無法確認，公設比無法扣除車位，本次未使用公設比，估價區間較寬",
        )

    main = _positive_listing_number(payload.get("main_building_area_ping"))
    auxiliary = _positive_listing_number(payload.get("auxiliary_building_area_ping"))
    if main is not None and main > 0 and auxiliary is not None:
        ratio = common_area_ratio(main, auxiliary, 0.0, net_area)
        if ratio is None:
            return (
                None,
                unused,
                "591 主建物／附屬建物坪數換算出的公設比不合理，本次未使用公設比，估價區間較寬",
            )
        return (
            ratio,
            {"provided": True, "source": "591_areas", "ratio": round(ratio, 4)},
            f"公設比（不含車位）由 591 主建物 {main:g} 坪＋附屬建物 {auxiliary:g} 坪"
            f"換算為 {ratio:.1%}，已納入估價",
        )

    percent = _positive_listing_number(payload.get("listed_common_area_percent"))
    if percent is not None:
        listed = percent / 100
        if parking_area > 0:
            ratio = 1 - (1 - listed) * total_area / net_area
            source = "591_listed_converted"
            note = (
                f"591 標示公設比 {percent:g}%（假設含車位），扣除車位 {parking_area:g} 坪後"
                f"換算為不含車位的 {ratio:.1%}，已納入估價"
            )
        else:
            ratio = listed
            source = "591_listed"
            note = f"採用 591 標示的公設比 {percent:g}%（無車位，與模型算法相同），已納入估價"
        if 0 <= ratio <= MAX_COMMON_AREA_RATIO:
            return ratio, {"provided": True, "source": source, "ratio": round(ratio, 4)}, note
    return None, unused, skipped


def valuate_listing(
    data_source: MarketDataSource,
    registry: ModelRegistry,
    payload: dict[str, Any],
) -> dict[str, Any]:
    transaction_type = "presale" if payload.get("listing_type") == "newhouse" else "resale"
    layout = _LAYOUT_RE.search(str(payload.get("layout") or ""))
    floor_match = _FLOOR_RE.search(str(payload.get("floor") or ""))
    if layout is None or floor_match is None:
        raise ValueError("listing lacks required valuation features")

    longitude = payload.get("longitude")
    latitude = payload.get("latitude")
    if longitude is None or latitude is None:
        raise ValueError("listing lacks coordinates")
    station_code, station_distance_m = station_from_coords(
        float(longitude), float(latitude)
    )

    total_floors = int(payload["total_floors"])
    floor = int(floor_match.group("floor"))
    total_area = float(payload["area_ping"])
    parking_type, parking_area = parse_listing_parking(
        payload.get("parking_type")
    )
    area = total_area - parking_area
    if area <= 0:
        raise ValueError("parking area must be smaller than total area")
    building_type = model_building_type(
        payload.get("building_type"),
        total_floors,
    )
    age = None if transaction_type == "presale" else float(payload["age_years"])
    raw_parking = str(payload.get("parking_type") or "").strip()
    ratio, common_area, common_area_note = listing_common_area(
        payload,
        total_area=total_area,
        net_area=area,
        parking_area=parking_area,
        parking_unverified=bool(raw_parking) and "無車位" not in raw_parking and not parking_type,
    )
    longitude = payload.get("longitude")
    latitude = payload.get("latitude")
    coordinates = (
        wgs84_to_twd97(float(longitude), float(latitude))
        if longitude is not None and latitude is not None
        else (None, None)
    )
    valuation_input = ValuationInput(
        transaction_type=transaction_type,
        station_code=station_code,
        station_distance_m=station_distance_m,
        building_area_ping=area,
        building_type=building_type,
        bedrooms=int(layout.group("bedrooms")),
        living_rooms=int(layout.group("living_rooms")),
        bathrooms=int(layout.group("bathrooms")),
        building_age_years=age,
        floor=floor,
        total_floors=total_floors,
        parking_type=parking_type,
        parking_area_ping=parking_area,
        asking_total_price_twd=(
            int(payload["total_price_twd"]) if payload.get("total_price_twd") is not None else None
        ),
        twd97_x=coordinates[0],
        twd97_y=coordinates[1],
        common_area_ratio=ratio,
    )
    market = data_source.load(MarketFilters(transaction_type=transaction_type))
    if market.empty:
        raise ValueError("market data unavailable")
    latest_data_date = pd.Timestamp(market["transaction_date"].max())
    model_frame = build_model_frame(market, transaction_type)
    result = valuate(
        valuation_input,
        registry,
        model_frame,
        latest_data_date=latest_data_date,
    )
    low, high = result["interval_total_price_twd"]
    model = result.get("model") or {}
    limitations = [
        f"捷運生活圈與距離由物件座標直接計算（距捷運站約 {station_distance_m:g} 公尺）"
    ]
    if payload.get("parking_type") and not parking_type:
        limitations.append(
            "591 未提供可驗證車位坪數，本次未另加車位估值"
        )
    elif parking_type:
        limitations.append(
            f"房屋坪數已從建物總坪數扣除車位 {parking_area:g} 坪"
        )
    limitations.append(common_area_note)
    return {
        "common_area": common_area,
        "point_estimate_twd": result["estimated_total_price_twd"],
        "estimated_building_price_twd": result.get("estimated_building_price_twd"),
        "estimated_parking_price_twd": result.get("estimated_parking_price_twd"),
        "low_estimate_twd": low,
        "high_estimate_twd": high,
        "confidence": result["confidence"],
        "confidence_reasons": result.get("confidence_reasons", []),
        "asking_price_assessment": result.get("asking_price_assessment"),
        "model_version": model.get("version", "unknown"),
        "dataset_version": result.get("data_date", "unknown"),
        "comparables": result.get("comparables", []),
        "limitations": limitations,
    }
