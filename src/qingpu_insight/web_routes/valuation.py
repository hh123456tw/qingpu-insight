"""Public valuation API: ``POST /api/valuations`` and ``GET /api/valuations/<id>``."""

from __future__ import annotations

import uuid

import pandas as pd
from flask import Blueprint, jsonify, request

from qingpu_insight.address_location import AddressLocator
from qingpu_insight.api_errors import ApiInputError
from qingpu_insight.market_metrics import MarketFilters
from qingpu_insight.market_repository import MarketDataSource
from qingpu_insight.model_features import build_model_frame
from qingpu_insight.valuation import ModelRegistry, valuate
from qingpu_insight.valuation_request import (
    common_area_summary,
    parse_valuation_payload,
    resolve_valuation_location,
    valuation_transaction_type,
)
from qingpu_insight.valuation_store import FileValuationStore
from qingpu_insight.web_routes.errors import (
    api_input_error_response,
    error_response,
    read_market_data,
)
from qingpu_insight.web_routes.guards import guarded_blueprint


def create_valuation_blueprint(
    data_source: MarketDataSource | None,
    registry: ModelRegistry,
    store: FileValuationStore,
    address_locator: AddressLocator,
) -> Blueprint:
    bp = guarded_blueprint("valuation", __name__)
    ds = data_source

    @bp.post("/api/valuations")
    def create_valuation():
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                raise ApiInputError("Request body must be a JSON object.", {"body": "object"})
            valuation_transaction_type(payload)
            location, location_summary = resolve_valuation_location(payload, address_locator)
            input_ = parse_valuation_payload(payload, location)
        except ApiInputError as error:
            return api_input_error_response(error)

        market = read_market_data(
            lambda: ds.load(MarketFilters(transaction_type=input_.transaction_type))
        )
        latest_data_date = (
            pd.Timestamp(market["transaction_date"].max()) if not market.empty else None
        )
        market_model = build_model_frame(market, input_.transaction_type)

        result = valuate(input_, registry, market_model, latest_data_date=latest_data_date)
        result["location"] = location_summary
        result["common_area"] = common_area_summary(payload, input_)
        result["valuation_id"] = str(uuid.uuid4())
        store.save_with_id(result["valuation_id"], result)
        return jsonify(result), 201

    @bp.get("/api/valuations/<valuation_id>")
    def get_valuation(valuation_id: str):
        record = store.get(valuation_id)
        if record is None:
            return error_response("not_found", "估價記錄不存在。", 404)
        return jsonify(record)

    return bp
