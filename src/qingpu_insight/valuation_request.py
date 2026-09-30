"""Parse and validate the valuation form / ``POST /api/valuations`` payload.

Turns the JSON body into a :class:`~qingpu_insight.model_features.ValuationInput`,
resolving an optional address or coordinates into station features and an optional
公設比 (from deed areas or a stated ratio). Invalid input raises
:class:`~qingpu_insight.api_errors.ApiInputError`.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from qingpu_insight.address_location import (
    AddressLocator,
    AddressLocatorUnavailable,
    ValuationLocation,
    location_from_twd97,
    location_from_wgs84,
)
from qingpu_insight.api_errors import ApiInputError
from qingpu_insight.market_cleaning import MAX_COMMON_AREA_RATIO, common_area_ratio
from qingpu_insight.model_features import ValuationInput


def valuation_transaction_type(payload: dict[str, Any]) -> str:
    transaction_type = str(payload.get("transaction_type", "resale"))
    if transaction_type == "presale":
        raise ApiInputError(
            "目前已停止預售屋估價。",
            {"transaction_type": "resale_only"},
            code="presale_valuation_disabled",
        )
    return transaction_type


_ADDRESS_MAX_LENGTH = 120
_OUTSIDE_SERVICE_AREA = "不在 A17／A18／A19 捷運站 2 公里服務範圍內。"
_FORM_LOCATION_FIELDS = ("station_code", "station_distance_m")


def _coordinate_pair(
    payload: dict[str, Any], first: str, second: str
) -> tuple[float, float] | None:
    values = (payload.get(first), payload.get(second))
    provided = [value not in (None, "") for value in values]
    if not any(provided):
        return None
    if not all(provided):
        raise ApiInputError(
            f"{first} 與 {second} 必須成對提供。", {"location": "coordinates_incomplete"}
        )
    try:
        pair = (float(values[0]), float(values[1]))
    except (TypeError, ValueError):
        pair = (float("nan"), float("nan"))
    if not all(np.isfinite(pair)):
        raise ApiInputError("座標格式不正確。", {"location": "coordinates_invalid"})
    return pair


def resolve_valuation_location(
    payload: dict[str, Any], locator: AddressLocator
) -> tuple[ValuationLocation | None, dict[str, Any]]:
    """Resolve the optional address or coordinates into a location and its public summary.

    The address is only passed to the locator; it is never returned, stored or logged.
    """
    raw_address = payload.get("address")
    if raw_address is not None and not isinstance(raw_address, str):
        raise ApiInputError("門牌地址格式不正確。", {"address": "invalid"})
    address = (raw_address or "").strip()
    twd97 = _coordinate_pair(payload, "twd97_x", "twd97_y")
    wgs84 = _coordinate_pair(payload, "longitude", "latitude")
    if sum(item is not None for item in (address or None, twd97, wgs84)) > 1:
        raise ApiInputError(
            "門牌地址、TWD97 座標與經緯度請擇一提供。", {"location": "multiple_sources"}
        )

    if twd97 is not None or wgs84 is not None:
        try:
            location = (
                location_from_twd97(*twd97) if twd97 is not None else location_from_wgs84(*wgs84)
            )
        except ValueError:
            raise ApiInputError(
                "座標" + _OUTSIDE_SERVICE_AREA,
                {"location": "outside_service_area"},
                code="outside_service_area",
            ) from None
        return location, _location_summary("coordinates", location)

    if not address:
        return None, {"source": "form", "precise": False}
    if len(address) > _ADDRESS_MAX_LENGTH:
        raise ApiInputError("門牌地址過長。", {"address": "too_long"})
    try:
        match = locator.resolve(address)
    except AddressLocatorUnavailable:
        if all(payload.get(name) not in (None, "") for name in _FORM_LOCATION_FIELDS):
            return None, {
                "source": "form",
                "precise": False,
                "note": "門牌定位資料暫時無法使用，本次僅依生活圈與距捷運距離估價，"
                "無法比對同棟成交紀錄。",
            }
        raise ApiInputError(
            "門牌定位資料暫時無法使用，請改填生活圈與距捷運距離。",
            {"address": "geocoder_unavailable"},
            code="geocoder_unavailable",
        ) from None
    if match is None:
        raise ApiInputError(
            "找不到這個門牌地址。請確認路名與門牌號碼（可加上中壢區或大園區），"
            "或清空地址欄後改填距捷運距離。",
            {"address": "not_found"},
            code="address_not_found",
        )
    try:
        location = location_from_twd97(match.twd97_x, match.twd97_y)
    except ValueError:
        raise ApiInputError(
            "這個門牌" + _OUTSIDE_SERVICE_AREA,
            {"address": "outside_service_area"},
            code="outside_service_area",
        ) from None
    return location, _location_summary("address", location, match.match_quality)


def _location_summary(
    source: str, location: ValuationLocation, match_quality: str | None = None
) -> dict[str, Any]:
    summary: dict[str, Any] = {"source": source, "precise": True}
    if match_quality is not None:
        summary["match_quality"] = match_quality
    summary["station_code"] = location.station_code
    summary["station_distance_m"] = location.station_distance_m
    return summary


DEED_AREA_FIELDS = (
    "main_building_area_ping",
    "auxiliary_building_area_ping",
    "balcony_area_ping",
)
# A stated ratio may differ from the one computed from deed areas by rounding only.
COMMON_AREA_RATIO_TOLERANCE = 0.01
_COMMON_AREA_OUT_OF_RANGE = (
    "主建物＋附屬建物＋陽台換算出的公設比（不含車位）不在 0%～70% 之間，"
    "請確認權狀面積與房屋坪數（不含車位）。"
)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _optional_area(payload: dict[str, Any], name: str) -> float | None:
    value = payload.get(name)
    if _blank(value):
        return None
    try:
        area = float(value)
    except (TypeError, ValueError):
        area = math.nan
    if not math.isfinite(area) or area < 0 or area > 200:
        raise ApiInputError("權狀面積必須是 0～200 之間的坪數。", {name: "non_negative_number"})
    return area


def _has_deed_areas(payload: dict[str, Any]) -> bool:
    return any(not _blank(payload.get(name)) for name in DEED_AREA_FIELDS)


def parse_common_area_ratio(payload: dict[str, Any], building_area_ping: float) -> float | None:
    """公設比 net of parking from either the deed areas or a stated ratio (0-0.70).

    Deed areas (主建物／附屬建物／陽台, in ping) use the training definition with
    building_area_ping, which already excludes parking. A stated ratio must agree with them.
    """
    stated: float | None = None
    raw_ratio = payload.get("common_area_ratio")
    if not _blank(raw_ratio):
        try:
            stated = float(raw_ratio)
        except (TypeError, ValueError):
            stated = math.nan
        if not (math.isfinite(stated) and 0 <= stated <= MAX_COMMON_AREA_RATIO):
            raise ApiInputError(
                "公設比（不含車位）必須介於 0%～70%。",
                {"common_area_ratio": "between_0_and_0.70"},
            )
    if not _has_deed_areas(payload):
        return stated

    main = payload.get("main_building_area_ping")
    if _blank(main):
        raise ApiInputError(
            "填寫附屬建物或陽台坪數時，請一併填寫主建物坪數。",
            {"main_building_area_ping": "required_with_areas"},
        )
    try:
        main_area = float(main)
    except (TypeError, ValueError):
        main_area = math.nan
    if not (math.isfinite(main_area) and 0 < main_area <= 200):
        raise ApiInputError(
            "主建物坪數必須大於 0。", {"main_building_area_ping": "positive_number"}
        )
    auxiliary = _optional_area(payload, "auxiliary_building_area_ping") or 0.0
    balcony = _optional_area(payload, "balcony_area_ping") or 0.0
    computed = common_area_ratio(main_area, auxiliary, balcony, building_area_ping)
    if computed is None:
        raise ApiInputError(
            _COMMON_AREA_OUT_OF_RANGE, {"main_building_area_ping": "ratio_out_of_range"}
        )
    if stated is not None and abs(stated - computed) > COMMON_AREA_RATIO_TOLERANCE:
        raise ApiInputError(
            f"填寫的公設比與權狀面積換算結果（約 {computed:.1%}）不一致，請擇一填寫或確認數字。",
            {"common_area_ratio": "conflicts_with_areas"},
        )
    return computed


def common_area_summary(payload: dict[str, Any], input_: ValuationInput) -> dict[str, Any]:
    """Whether and how the 公設比 entered this valuation, for the result page."""
    ratio = input_.common_area_ratio
    if ratio is None:
        return {"provided": False, "source": None, "ratio": None}
    source = "areas" if _has_deed_areas(payload) else "ratio"
    return {"provided": True, "source": source, "ratio": round(ratio, 4)}


def parse_valuation_payload(
    payload: dict[str, Any], location: ValuationLocation | None = None
) -> ValuationInput:
    transaction_type = valuation_transaction_type(payload)
    if location is not None:
        # Station features always come from the coordinates when a location is known.
        payload = {
            **payload,
            "station_code": location.station_code,
            "station_distance_m": location.station_distance_m,
        }
    required = (
        "station_code",
        "building_area_ping",
        "station_distance_m",
        "building_type",
        "bedrooms",
        "living_rooms",
        "bathrooms",
        "floor",
        "total_floors",
    )
    missing = {name: "required" for name in required if payload.get(name) in (None, "")}
    if missing:
        raise ApiInputError("請完整填寫估價條件。", missing)
    parking_type = payload.get("parking_type", "")
    parking_area = float(payload.get("parking_area_ping", 0))
    if not parking_type:
        parking_area = 0
    elif parking_area <= 0:
        raise ApiInputError(
            "有車位時請填寫大於 0 的車位面積。",
            {"parking_area_ping": "positive_when_parking_selected"},
        )
    try:
        building_area = float(payload["building_area_ping"])
    except (TypeError, ValueError):
        raise ApiInputError("估價條件格式不正確。", {"valuation": "invalid"}) from None
    ratio = parse_common_area_ratio(payload, building_area)
    try:
        return ValuationInput(
            transaction_type=transaction_type,
            station_code=str(payload["station_code"]),
            building_area_ping=building_area,
            station_distance_m=float(payload["station_distance_m"]),
            building_type=str(payload["building_type"]),
            bedrooms=int(payload["bedrooms"]),
            living_rooms=int(payload["living_rooms"]),
            bathrooms=int(payload["bathrooms"]),
            building_age_years=float(payload["building_age_years"])
            if payload.get("building_age_years") is not None
            else None,
            floor=int(payload["floor"]),
            total_floors=int(payload["total_floors"]),
            parking_type=parking_type,
            parking_area_ping=parking_area,
            asking_total_price_twd=int(payload["asking_total_price_twd"])
            if payload.get("asking_total_price_twd")
            else None,
            twd97_x=location.twd97_x if location is not None else None,
            twd97_y=location.twd97_y if location is not None else None,
            common_area_ratio=ratio,
        )
    except (KeyError, TypeError, ValueError):
        raise ApiInputError("估價條件格式不正確。", {"valuation": "invalid"}) from None
