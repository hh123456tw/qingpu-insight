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
from qingpu_insight.market_snapshot import ModelFrameCache
from qingpu_insight.model_features import ValuationInput, build_model_frame
from qingpu_insight.valuation import ModelRegistry, quick_estimates, valuate


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
_BEDROOMS_ONLY_RE = re.compile(r"(?P<bedrooms>\d+)\s*房")
# ValuationInput accepts station distances up to 2 km (the life-circle radius).
MAX_STATION_DISTANCE_M = 2000.0


class ListingOutOfArea(ValueError):
    """The listing lies outside the A17–A19 life circles the model covers."""


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

    Live 591 sale pages (checked 2026-10) list 主建物 + 附屬建物 + 共用部分 + 車位 = 權狀坪數,
    with balconies inside 附屬建物, and a listed 公設比 = 共用部分 ÷ (權狀 − 車位). Both match
    the model's parking-free definition. Preference: the full breakdown (needs no parking
    data), then 主建物 + 附屬建物 over the parking-free area, then the listed percentage.
    """
    unused = {"provided": False, "source": None, "ratio": None}
    skipped = "591 未提供可換算的主建物／附屬建物坪數，本次未使用公設比，估價區間較寬"

    main = _positive_listing_number(payload.get("main_building_area_ping"))
    auxiliary = _positive_listing_number(payload.get("auxiliary_building_area_ping"))
    common = _positive_listing_number(payload.get("common_area_ping"))
    if main is not None and main > 0 and auxiliary is not None and common is not None:
        ratio = common / (main + auxiliary + common)
        if ratio <= MAX_COMMON_AREA_RATIO:
            return (
                ratio,
                {"provided": True, "source": "591_areas", "ratio": round(ratio, 4)},
                f"公設比（不含車位）由 591 主建物 {main:g}＋附屬建物 {auxiliary:g}＋共用部分"
                f" {common:g} 坪換算為 {ratio:.1%}，已納入估價",
            )

    if main is not None and main > 0 and auxiliary is not None:
        if parking_unverified:
            return (
                None,
                unused,
                "591 車位坪數無法確認，無法由主建物／附屬建物換算公設比，"
                "本次未使用公設比，估價區間較寬",
            )
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
        ratio = percent / 100
        if 0 <= ratio <= MAX_COMMON_AREA_RATIO:
            return (
                ratio,
                {"provided": True, "source": "591_listed", "ratio": round(ratio, 4)},
                f"採用 591 標示的公設比 {percent:g}%（不含車位，與模型算法相同），已納入估價",
            )
    return None, unused, skipped


_LISTED_STATIONS = ("A17", "A18", "A19")


def _listed_station(payload: dict[str, Any]) -> tuple[str, float]:
    code = payload.get("station_code")
    distance = _positive_listing_number(payload.get("station_distance_m"))
    if code not in _LISTED_STATIONS:
        raise ListingOutOfArea("591 did not list an A17–A19 station")
    if distance is None:
        raise ValueError("listing lacks a listed station distance")
    return str(code), float(distance)


def valuate_listing(
    data_source: MarketDataSource,
    registry: ModelRegistry,
    payload: dict[str, Any],
    *,
    snapshots: ModelFrameCache | None = None,
) -> dict[str, Any]:
    """Valuation evidence for the assistant (see :func:`valuate_listing_with_context`)."""
    result, _ = valuate_listing_with_context(
        data_source, registry, payload, snapshots=snapshots
    )
    return result


def valuate_listing_with_context(
    data_source: MarketDataSource,
    registry: ModelRegistry,
    payload: dict[str, Any],
    *,
    snapshots: ModelFrameCache | None = None,
    allow_listed_station: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The assistant's valuation plus how it was reached (anchor, station, parking, fallback).

    The listing radar ranks listings with the same numbers the assistant shows, and needs
    the context to judge how far each estimate can be trusted.

    ``allow_listed_station`` is only for the radar's list-field prescreen: without
    coordinates it takes 591's own nearest-station label and distance
    (``station_code`` / ``station_distance_m``), so there is no same-building anchor.
    """
    prepared = _prepare_listing_input(payload, allow_listed_station=allow_listed_station)
    latest_data_date, model_frame = _market_snapshot(
        data_source, prepared["transaction_type"], snapshots
    )
    result = valuate(
        prepared["input"],
        registry,
        model_frame,
        latest_data_date=latest_data_date,
    )
    return _listing_valuation_result(prepared, result, payload)


def prescreen_listings_with_context(
    data_source: MarketDataSource,
    registry: ModelRegistry,
    payloads: list[dict[str, Any]],
    *,
    snapshots: ModelFrameCache | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]] | Exception]:
    """The radar's list-field prescreen: one model call for many coordinate-less listings.

    Same inputs and price/interval as :func:`valuate_listing_with_context` with
    ``allow_listed_station=True``, but without comparables, factors or confidence.
    Per-listing problems come back as the exception in that listing's slot.
    """
    outcomes: list[tuple[dict[str, Any], dict[str, Any]] | Exception | None] = []
    prepared_items: list[tuple[int, dict[str, Any]]] = []
    for index, payload in enumerate(payloads):
        try:
            prepared = _prepare_listing_input(payload, allow_listed_station=True)
        except Exception as error:  # missing features, out of area, odd numbers
            outcomes.append(error)
            continue
        outcomes.append(None)
        prepared_items.append((index, prepared))
    if not prepared_items:
        return [outcome for outcome in outcomes if outcome is not None]
    latest_data_date, model_frame = _market_snapshot(data_source, "resale", snapshots)
    resale = [(i, p) for i, p in prepared_items if p["transaction_type"] == "resale"]
    estimates = quick_estimates(
        [p["input"] for _, p in resale], registry, latest_data_date=latest_data_date
    )
    if estimates is None:
        # Degraded model path: the per-listing valuation knows how to fall back.
        for index, prepared in prepared_items:
            try:
                result = valuate(
                    prepared["input"], registry, model_frame, latest_data_date=latest_data_date
                )
                outcomes[index] = _listing_valuation_result(prepared, result, payloads[index])
            except Exception as error:
                outcomes[index] = error
    else:
        for (index, prepared), estimate in zip(resale, estimates, strict=True):
            result = {**estimate, "confidence": None, "confidence_reasons": []}
            outcomes[index] = _listing_valuation_result(prepared, result, payloads[index])
        for index, _prepared in prepared_items:
            if outcomes[index] is None:
                outcomes[index] = ValueError("prescreen supports resale listings only")
    return [outcome for outcome in outcomes if outcome is not None]


def _market_snapshot(
    data_source: MarketDataSource,
    transaction_type: str,
    snapshots: ModelFrameCache | None,
) -> tuple[pd.Timestamp, pd.DataFrame]:
    if snapshots is not None:
        snapshot = snapshots.snapshot(transaction_type)
        if snapshot.row_count == 0:
            raise ValueError("market data unavailable")
        return snapshot.latest_data_date, snapshot.model_frame
    market = data_source.load(MarketFilters(transaction_type=transaction_type))
    if market.empty:
        raise ValueError("market data unavailable")
    return (
        pd.Timestamp(market["transaction_date"].max()),
        build_model_frame(market, transaction_type),
    )


def _prepare_listing_input(
    payload: dict[str, Any], *, allow_listed_station: bool
) -> dict[str, Any]:
    """591 listing fields -> the model's ValuationInput plus the notes the result needs."""
    transaction_type = "presale" if payload.get("listing_type") == "newhouse" else "resale"
    layout_text = str(payload.get("layout") or "")
    layout = _LAYOUT_RE.search(layout_text)
    floor_match = _FLOOR_RE.search(str(payload.get("floor") or ""))
    layout_note = None
    if layout is not None:
        bedrooms = int(layout.group("bedrooms"))
        living_rooms = int(layout.group("living_rooms"))
        bathrooms = int(layout.group("bathrooms"))
    elif (bedrooms_only := _BEDROOMS_ONLY_RE.search(layout_text)) is not None:
        # Some 591 listings show only "2房"; use the common hall/bath count for that size.
        bedrooms = int(bedrooms_only.group("bedrooms"))
        living_rooms = 2 if bedrooms >= 2 else 1
        bathrooms = 2 if bedrooms >= 3 else 1
        layout_note = (
            f"591 格局只有房數（{bedrooms} 房），廳衛以常見配置"
            f"（{living_rooms} 廳 {bathrooms} 衛）推估"
        )
    if floor_match is None or (layout is None and layout_note is None):
        raise ValueError("listing lacks required valuation features")

    longitude = payload.get("longitude")
    latitude = payload.get("latitude")
    listed_station = False
    if longitude is None or latitude is None:
        if not allow_listed_station:
            raise ValueError("listing lacks coordinates")
        station_code, station_distance_m = _listed_station(payload)
        listed_station = True
    else:
        station_code, station_distance_m = station_from_coords(
            float(longitude), float(latitude)
        )
    if station_distance_m > MAX_STATION_DISTANCE_M:
        raise ListingOutOfArea(
            f"listing is {station_distance_m:.0f} m from the nearest A17–A19 station"
        )

    if payload.get("total_floors") is None:
        raise ValueError("listing lacks total floors")
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
    if transaction_type == "presale":
        age = None
    elif payload.get("age_years") is None:
        raise ValueError("listing lacks building age")
    else:
        age = float(payload["age_years"])
    raw_parking = str(payload.get("parking_type") or "").strip()
    parking_unverified = bool(raw_parking) and "無車位" not in raw_parking and not parking_type
    ratio, common_area, common_area_note = listing_common_area(
        payload,
        total_area=total_area,
        net_area=area,
        parking_area=parking_area,
        parking_unverified=parking_unverified,
    )
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
        bedrooms=bedrooms,
        living_rooms=living_rooms,
        bathrooms=bathrooms,
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
    return {
        "input": valuation_input,
        "transaction_type": transaction_type,
        "station_code": station_code,
        "station_distance_m": station_distance_m,
        "listed_station": listed_station,
        "total_area": total_area,
        "net_area": area,
        "parking_type": parking_type,
        "parking_area": parking_area,
        "parking_unverified": parking_unverified,
        "age": age,
        "common_area": common_area,
        "common_area_note": common_area_note,
        "layout_note": layout_note,
    }


def _listing_valuation_result(
    prepared: dict[str, Any], result: dict[str, Any], payload: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    station_distance_m = prepared["station_distance_m"]
    parking_type = prepared["parking_type"]
    low, high = result["interval_total_price_twd"]
    model = result.get("model") or {}
    limitations = [
        (
            f"捷運站與距離採 591 列表標示（約 {station_distance_m:g} 公尺）；"
            "沒有座標，因此未使用同棟成交錨定"
        )
        if prepared["listed_station"]
        else f"捷運生活圈與距離由物件座標直接計算（距捷運站約 {station_distance_m:g} 公尺）"
    ]
    if payload.get("parking_type") and not parking_type:
        limitations.append(
            "591 未提供可驗證車位坪數，本次未另加車位估值"
        )
    elif parking_type:
        limitations.append(
            f"房屋坪數已從建物總坪數扣除車位 {prepared['parking_area']:g} 坪"
        )
    limitations.append(prepared["common_area_note"])
    if prepared["layout_note"] is not None:
        limitations.append(prepared["layout_note"])
    context = {
        "transaction_type": prepared["transaction_type"],
        "station_code": prepared["station_code"],
        "station_distance_m": station_distance_m,
        "total_area_ping": prepared["total_area"],
        "net_area_ping": prepared["net_area"],
        "parking_area_ping": prepared["parking_area"],
        "parking_unverified": prepared["parking_unverified"],
        "age_years": prepared["age"],
        "price_anchor": model.get("price_anchor"),
        "degraded": bool(result.get("degraded", False)),
        "degraded_reason": result.get("degraded_reason"),
        "model_name": model.get("name"),
        "location_source": (
            "591_listed_station" if prepared["listed_station"] else "coordinates"
        ),
    }
    public = {
        "common_area": prepared["common_area"],
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
    return public, context
