from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request
from werkzeug.datastructures import MultiDict

from qingpu_insight.market_metrics import (
    MarketFilters,
    market_summary,
    market_trends,
    recent_transactions,
)
from qingpu_insight.market_repository import MarketDataSource, repository_from_env


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


def create_app(
    data_source: MarketDataSource | None = None,
    root: Path | None = None,
) -> Flask:
    app = Flask(__name__)
    app.json.default = _json_default

    if data_source is None and root is not None:
        data_source = repository_from_env(root)

    ds = data_source

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

    return app


def main() -> None:
    port = int(os.environ.get("QINGPU_PORT", "5000"))
    debug = os.environ.get("QINGPU_DEBUG", "") == "1"
    app = create_app(root=Path.cwd())
    app.run(host="127.0.0.1", port=port, debug=debug)


if __name__ == "__main__":
    main()
