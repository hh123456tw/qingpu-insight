from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import HTTPException

from qingpu_insight.listing_metrics import (
    ListingFilters,
    listing_summary,
    public_events,
    public_listings,
)
from qingpu_insight.listing_repository import ListingRepository
from qingpu_insight.market_metrics import (
    MarketFilters,
    market_summary,
    market_trends,
    recent_transactions,
)
from qingpu_insight.market_repository import MarketDataSource, repository_from_env
from qingpu_insight.model_features import ValuationInput, build_model_frame
from qingpu_insight.valuation import ModelRegistry, valuate
from qingpu_insight.valuation_store import FileValuationStore


class ApiInputError(Exception):
    def __init__(self, message: str, fields: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.fields = fields or {}


def parse_filters(args: MultiDict[str, str]) -> MarketFilters:
    transaction_type = args.get("transaction_type", "")
    if not transaction_type:
        raise ApiInputError("請選擇中古屋或預售屋。", {"transaction_type": "required"})
    stations = tuple(args.getlist("station")) or ("A17", "A18", "A19")
    return MarketFilters(
        transaction_type=transaction_type,
        station_codes=stations,
        date_from=pd.to_datetime(args.get("date_from"), errors="raise")
        if args.get("date_from")
        else None,
        date_to=pd.to_datetime(args.get("date_to"), errors="raise")
        if args.get("date_to")
        else None,
        area_ping_min=float(args["area_ping_min"]) if args.get("area_ping_min") else None,
        area_ping_max=float(args["area_ping_max"]) if args.get("area_ping_max") else None,
        building_types=tuple(args.getlist("building_type")),
        bedrooms=tuple(int(v) for v in args.getlist("bedrooms")),
    )


def _json_default(obj: Any) -> Any:
    if isinstance(obj, pd.Timestamp | pd.Timedelta):
        return None if pd.isna(obj) else obj.isoformat()
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def parse_valuation_payload(payload: dict[str, Any]) -> ValuationInput:
    required = (
        "transaction_type",
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
    return ValuationInput(
        transaction_type=str(payload["transaction_type"]),
        station_code=str(payload["station_code"]),
        building_area_ping=float(payload["building_area_ping"]),
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
        parking_type=payload.get("parking_type"),
        parking_area_ping=float(payload.get("parking_area_ping", 0)),
        asking_total_price_twd=int(payload["asking_total_price_twd"])
        if payload.get("asking_total_price_twd")
        else None,
    )


def create_app(
    data_source: MarketDataSource | None = None,
    root: Path | None = None,
    valuation_store: FileValuationStore | None = None,
    model_registry: ModelRegistry | None = None,
    listing_repo: ListingRepository | None = None,
) -> Flask:
    app = Flask(__name__)
    app.json.default = _json_default

    if data_source is None and root is not None:
        data_source = repository_from_env(root)

    ds = data_source
    store = valuation_store or FileValuationStore(Path.cwd() / "outputs" / "valuations")
    registry = model_registry or ModelRegistry(Path.cwd() / "artifacts")
    lr = listing_repo

    @app.errorhandler(ApiInputError)
    def handle_api_input_error(error: ApiInputError):
        return jsonify(
            {
                "error": {
                    "code": "invalid_request",
                    "message": error.message,
                    "fields": error.fields,
                }
            }
        ), 400

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    @app.errorhandler(TypeError)
    def handle_parse_error(error: Exception):
        return jsonify(
            {
                "error": {
                    "code": "invalid_request",
                    "message": str(error),
                    "fields": None,
                }
            }
        ), 400

    @app.errorhandler(Exception)
    def handle_unhandled(error: Exception):
        if isinstance(error, HTTPException):
            return error
        app.logger.exception("unhandled error serving request")
        return jsonify(
            {
                "error": {
                    "code": "market_data_unavailable",
                    "message": "無法取得市場資料，請稍後再試。",
                    "fields": None,
                }
            }
        ), 503

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/market/summary")
    def summary_api():
        filters = parse_filters(request.args)
        return jsonify(market_summary(ds.load(filters), filters))

    @app.get("/api/market/trends")
    def trends_api():
        filters = parse_filters(request.args)
        return jsonify({"items": market_trends(ds.load(filters), filters)})

    @app.get("/api/transactions")
    def transactions_api():
        filters = parse_filters(request.args)
        limit = min(max(int(request.args.get("limit", "20")), 1), 100)
        return jsonify(
            {
                "items": recent_transactions(ds.load(filters), filters, limit),
                "limit": limit,
            }
        )

    # ------------------------------------------------------------------
    # Listing intelligence (M3)
    # ------------------------------------------------------------------

    def _listing_filters_from_args() -> ListingFilters:
        listing_type = request.args.get("listing_type", "")
        if not listing_type:
            raise ApiInputError("請選擇刊登類型。", {"listing_type": "required"})
        stations = tuple(request.args.getlist("station")) or ("A17", "A18", "A19")
        limit = min(max(int(request.args.get("limit", "100")), 1), 100)
        return ListingFilters(
            listing_type=listing_type,
            station_codes=stations,
            limit=limit,
        )

    def _location_eligible_listings(df: pd.DataFrame) -> pd.DataFrame:
        if "location_eligible" not in df.columns:
            return df.iloc[0:0]
        return df[df["location_eligible"].eq(True)]

    @app.get("/api/listings/summary")
    def listing_summary_api():
        filters = _listing_filters_from_args()
        if lr is None:
            err = {"code": "listing_data_unavailable", "message": "刊登資料未啟用。"}
            return jsonify({"error": err}), 503
        df = _location_eligible_listings(lr.load_current(filters.listing_type))
        return jsonify(listing_summary(df, filters))

    @app.get("/api/listings")
    def listings_api():
        filters = _listing_filters_from_args()
        if lr is None:
            err = {"code": "listing_data_unavailable", "message": "刊登資料未啟用。"}
            return jsonify({"error": err}), 503
        df = _location_eligible_listings(lr.load_current(filters.listing_type))
        items = public_listings(df, filters)
        return jsonify({"items": items, "limit": filters.limit})

    @app.get("/api/listing-events")
    def listing_events_api():
        filters = _listing_filters_from_args()
        if lr is None:
            err = {"code": "listing_data_unavailable", "message": "刊登資料未啟用。"}
            return jsonify({"error": err}), 503
        df = lr.load_events(filters.listing_type)
        events = public_events(df, filters)
        return jsonify({"items": events, "limit": filters.limit})

    @app.post("/api/valuations")
    def create_valuation():
        try:
            input_ = parse_valuation_payload(request.get_json(force=True))
        except ApiInputError as error:
            return jsonify(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": error.message,
                        "fields": error.fields,
                    }
                }
            ), 400

        market = ds.load(MarketFilters(transaction_type=input_.transaction_type))
        market_model = build_model_frame(market, input_.transaction_type)

        result = valuate(input_, registry, market_model)
        result["valuation_id"] = str(uuid.uuid4())
        store.save_with_id(result["valuation_id"], result)
        return jsonify(result), 201

    @app.get("/api/valuations/<valuation_id>")
    def get_valuation(valuation_id: str):
        record = store.get(valuation_id)
        if record is None:
            return jsonify(
                {"error": {"code": "not_found", "message": "估價記錄不存在。", "fields": None}}
            ), 404
        return jsonify(record)

    return app


def main() -> None:
    port = int(os.environ.get("QINGPU_PORT", "5000"))
    debug = os.environ.get("QINGPU_DEBUG", "") == "1"
    app = create_app(root=Path.cwd())
    app.run(host="127.0.0.1", port=port, debug=debug)


if __name__ == "__main__":
    main()
