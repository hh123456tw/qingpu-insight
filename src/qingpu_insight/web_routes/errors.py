"""JSON error responses and serialisation shared by the web blueprints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from qingpu_insight.api_errors import ApiInputError


def json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
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


def error_response(code: str, message: str, status: int, fields: dict | None = None):
    return jsonify({"error": {"code": code, "message": message, "fields": fields}}), status


def api_input_error_response(error: ApiInputError):
    return error_response(error.code, error.message, 400, error.fields)


def invalid_request(fields: dict[str, str]):
    return error_response("invalid_request", "Request validation failed.", 400, fields)


def parse_limit(raw_limit: str) -> int | None:
    """A strict ``limit`` query value in 1..100, or None when it is not one."""
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return None
    if str(limit) != raw_limit or not 1 <= limit <= 100:
        return None
    return limit


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiInputError)
    def handle_api_input_error(error: ApiInputError):
        return api_input_error_response(error)

    @app.errorhandler(Exception)
    def handle_unhandled(error: Exception):
        if isinstance(error, HTTPException):
            return error
        app.logger.exception("unhandled error serving request")
        return error_response(
            "market_data_unavailable", "無法取得市場資料，請稍後再試。", 503
        )
