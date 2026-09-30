"""Public market and listing APIs: ``/api/market/*``, ``/api/transactions``, listings."""

from __future__ import annotations

import pandas as pd
from flask import Blueprint, jsonify, request
from werkzeug.datastructures import MultiDict

from qingpu_insight.api_errors import ApiInputError
from qingpu_insight.listing_metrics import (
    ListingFilters,
    listing_summary,
    public_events,
    public_listings,
)
from qingpu_insight.listing_repository import ListingRepository
from qingpu_insight.market_metrics import (
    MapBounds,
    MarketFilters,
    market_map_points,
    market_summary,
    market_trends,
    recent_transactions,
)
from qingpu_insight.market_repository import MarketDataSource
from qingpu_insight.web_routes.guards import guarded_blueprint


def parse_filters(args: MultiDict[str, str]) -> MarketFilters:
    transaction_type = args.get("transaction_type", "resale")
    if transaction_type == "presale":
        raise ApiInputError("目前僅支援中古屋市場。", {"transaction_type": "resale_only"})
    stations = tuple(args.getlist("station")) or ("A17", "A18", "A19")
    try:
        date_from = (
            pd.to_datetime(args.get("date_from"), errors="raise") if args.get("date_from") else None
        )
    except (TypeError, ValueError):
        raise ApiInputError("日期格式不正確。", {"date_from": "invalid"}) from None
    try:
        date_to = (
            pd.to_datetime(args.get("date_to"), errors="raise") if args.get("date_to") else None
        )
    except (TypeError, ValueError):
        raise ApiInputError("日期格式不正確。", {"date_to": "invalid"}) from None
    try:
        area_ping_min = float(args["area_ping_min"]) if args.get("area_ping_min") else None
        area_ping_max = float(args["area_ping_max"]) if args.get("area_ping_max") else None
        bedrooms = tuple(int(value) for value in args.getlist("bedrooms"))
    except (TypeError, ValueError):
        raise ApiInputError("篩選條件格式不正確。", {"filters": "invalid"}) from None
    try:
        return MarketFilters(
            transaction_type=transaction_type,
            station_codes=stations,
            date_from=date_from,
            date_to=date_to,
            area_ping_min=area_ping_min,
            area_ping_max=area_ping_max,
            building_types=tuple(args.getlist("building_type")),
            bedrooms=bedrooms,
        )
    except ValueError:
        fields: dict[str, str] = {}
        if transaction_type != "resale":
            fields["transaction_type"] = "resale_only"
        if not stations or not set(stations) <= {"A17", "A18", "A19"}:
            fields["station"] = "A17_A18_or_A19"
        if area_ping_min is not None and area_ping_min < 0:
            fields["area_ping_min"] = "non_negative"
        if area_ping_max is not None and area_ping_max < 0:
            fields["area_ping_max"] = "non_negative"
        if (
            area_ping_min is not None
            and area_ping_max is not None
            and area_ping_min >= 0
            and area_ping_max >= 0
            and area_ping_min > area_ping_max
        ):
            fields["area_ping_min"] = "must_not_exceed_area_ping_max"
            fields["area_ping_max"] = "must_not_be_less_than_area_ping_min"
        raise ApiInputError("篩選條件無效。", fields or {"filters": "invalid"}) from None


def _parse_map_view(args: MultiDict[str, str]) -> tuple[int, MapBounds | None]:
    raw_zoom = args.get("zoom", "14")
    try:
        zoom = int(raw_zoom)
    except (TypeError, ValueError):
        raise ApiInputError("地圖縮放層級格式不正確。", {"zoom": "integer_10_to_19"}) from None
    if str(zoom) != raw_zoom or not 10 <= zoom <= 19:
        raise ApiInputError("地圖縮放層級無效。", {"zoom": "integer_10_to_19"})

    names = ("south", "west", "north", "east")
    values = [args.get(name) for name in names]
    if not any(value not in (None, "") for value in values):
        return zoom, None
    if any(value in (None, "") for value in values):
        raise ApiInputError("地圖邊界不完整。", {"bounds": "all_or_none"})
    try:
        bounds = MapBounds(*(float(value) for value in values if value is not None))
    except (TypeError, ValueError):
        raise ApiInputError("地圖邊界無效。", {"bounds": "ordered_finite_numbers"}) from None
    return zoom, bounds



def _listing_filters_from_args() -> ListingFilters:
    listing_type = request.args.get("listing_type", "sale")
    if listing_type != "sale":
        raise ApiInputError("僅支援中古屋刊登資料。", {"listing_type": "supported_value"})
    stations = tuple(request.args.getlist("station")) or ("A17", "A18", "A19")
    try:
        limit = min(max(int(request.args.get("limit", "100")), 1), 100)
    except (TypeError, ValueError):
        raise ApiInputError("筆數格式不正確。", {"limit": "integer_1_to_100"}) from None
    return ListingFilters(
        listing_type=listing_type,
        station_codes=stations,
        limit=limit,
    )


def _publicly_visible_listings(df: pd.DataFrame) -> pd.DataFrame:
    required = {"location_eligible", "active"}
    if not required.issubset(df.columns):
        return df.iloc[0:0]
    return df[df["location_eligible"].eq(True) & df["active"].eq(True)]


def _listing_data_unavailable():
    err = {"code": "listing_data_unavailable", "message": "刊登資料未啟用。"}
    return jsonify({"error": err}), 503


def create_market_blueprint(
    data_source: MarketDataSource | None,
    listing_repo: ListingRepository | None,
) -> Blueprint:
    bp = guarded_blueprint("market", __name__)
    ds = data_source
    lr = listing_repo

    @bp.get("/api/market/summary")
    def summary_api():
        filters = parse_filters(request.args)
        return jsonify(market_summary(ds.load(filters), filters))

    @bp.get("/api/market/trends")
    def trends_api():
        filters = parse_filters(request.args)
        return jsonify({"items": market_trends(ds.load(filters), filters)})

    @bp.get("/api/market/map-points")
    def map_points_api():
        filters = parse_filters(request.args)
        zoom, bounds = _parse_map_view(request.args)
        return jsonify(
            market_map_points(
                ds.load(filters),
                filters,
                zoom=zoom,
                bounds=bounds,
            )
        )

    @bp.get("/api/transactions")
    def transactions_api():
        filters = parse_filters(request.args)
        try:
            limit = min(max(int(request.args.get("limit", "20")), 1), 100)
        except (TypeError, ValueError):
            raise ApiInputError("筆數格式不正確。", {"limit": "integer_1_to_100"}) from None
        return jsonify(
            {
                "items": recent_transactions(ds.load(filters), filters, limit),
                "limit": limit,
            }
        )

    @bp.get("/api/listings/summary")
    def listing_summary_api():
        filters = _listing_filters_from_args()
        if lr is None:
            return _listing_data_unavailable()
        df = _publicly_visible_listings(lr.load_current(filters.listing_type))
        return jsonify(listing_summary(df, filters))

    @bp.get("/api/listings")
    def listings_api():
        filters = _listing_filters_from_args()
        if lr is None:
            return _listing_data_unavailable()
        df = _publicly_visible_listings(lr.load_current(filters.listing_type))
        items = public_listings(df, filters)
        return jsonify({"items": items, "limit": filters.limit})

    @bp.get("/api/listing-events")
    def listing_events_api():
        filters = _listing_filters_from_args()
        if lr is None:
            return _listing_data_unavailable()
        df = lr.load_events(filters.listing_type)
        events = public_events(df, filters)
        return jsonify({"items": events, "limit": filters.limit})

    return bp
