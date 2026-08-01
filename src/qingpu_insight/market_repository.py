from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib import parse as urllib_parse
from urllib.parse import ParseResult, urlparse

import pandas as pd

from qingpu_insight.market_metrics import MarketFilters

ALLOWLISTED_COLUMNS: tuple[str, ...] = (
    "transaction_key",
    "transaction_type",
    "record_id",
    "station_code",
    "transaction_date",
    "building_area_sqm",
    "building_area_ping",
    "unit_price_sqm_twd",
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
    "source_file",
    "floor",
    "total_floors",
    "parking_type",
    "parking_area_sqm",
    "parking_price_twd",
    "analysis_eligible",
)

COLUMNS_SQL = ", ".join(ALLOWLISTED_COLUMNS)

NUMERIC_COLUMNS: tuple[str, ...] = (
    "building_area_sqm",
    "building_area_ping",
    "unit_price_sqm_twd",
    "unit_price_per_ping_twd",
    "total_price_twd",
    "bedrooms",
    "living_rooms",
    "bathrooms",
    "building_age_years",
    "station_distance_m",
    "longitude",
    "latitude",
    "parking_area_sqm",
    "parking_price_twd",
)


class MarketDataSource(ABC):
    @abstractmethod
    def load(self, filters: MarketFilters) -> pd.DataFrame: ...


class ParquetMarketDataSource(MarketDataSource):
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, filters: MarketFilters) -> pd.DataFrame:
        frame = pd.read_parquet(self._path)
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


def _build_filter_sql(filters: MarketFilters) -> tuple[str, dict[str, Any]]:
    conditions: list[str] = []
    params: dict[str, Any] = {}

    conditions.append("transaction_type = %(transaction_type)s")
    params["transaction_type"] = filters.transaction_type

    if len(filters.station_codes) < 3:
        placeholders = ", ".join(f"%(sc_{i})s" for i in range(len(filters.station_codes)))
        conditions.append(f"station_code IN ({placeholders})")
        for i, code in enumerate(filters.station_codes):
            params[f"sc_{i}"] = code

    if filters.date_from is not None:
        conditions.append("transaction_date >= %(date_from)s")
        params["date_from"] = filters.date_from

    if filters.date_to is not None:
        conditions.append("transaction_date <= %(date_to)s")
        params["date_to"] = filters.date_to

    if filters.area_ping_min is not None:
        conditions.append("building_area_ping >= %(area_ping_min)s")
        params["area_ping_min"] = filters.area_ping_min

    if filters.area_ping_max is not None:
        conditions.append("building_area_ping <= %(area_ping_max)s")
        params["area_ping_max"] = filters.area_ping_max

    if filters.building_types:
        placeholders = ", ".join(f"%(bt_{i})s" for i in range(len(filters.building_types)))
        conditions.append(f"building_type IN ({placeholders})")
        for i, bt in enumerate(filters.building_types):
            params[f"bt_{i}"] = bt

    if filters.bedrooms:
        placeholders = ", ".join(f"%(br_{i})s" for i in range(len(filters.bedrooms)))
        conditions.append(f"bedrooms IN ({placeholders})")
        for i, br in enumerate(filters.bedrooms):
            params[f"br_{i}"] = br

    where_clause = " AND ".join(conditions)
    return where_clause, params


class MySQLMarketDataSource(MarketDataSource):
    def __init__(self, parsed_url: ParseResult, _test_connection: Any = None) -> None:
        self._parsed_url = parsed_url
        self._test_connection = _test_connection

    def _get_connection(self) -> Any:
        if self._test_connection is not None:
            return self._test_connection
        import pymysql

        return pymysql.connect(
            host=self._parsed_url.hostname or "localhost",
            port=self._parsed_url.port or 3306,
            user=urllib_parse.unquote(self._parsed_url.username or ""),
            password=urllib_parse.unquote(self._parsed_url.password or ""),
            database=self._parsed_url.path.lstrip("/"),
            charset="utf8mb4",
        )

    def load(self, filters: MarketFilters) -> pd.DataFrame:
        where_clause, params = _build_filter_sql(filters)
        sql = f"SELECT {COLUMNS_SQL} FROM market_transactions WHERE {where_clause}"
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
        finally:
            if self._test_connection is None:
                conn.close()
        frame = pd.DataFrame(rows, columns=list(ALLOWLISTED_COLUMNS))
        frame["transaction_date"] = pd.to_datetime(frame["transaction_date"])
        for column in NUMERIC_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["analysis_eligible"] = frame["analysis_eligible"].astype(bool)
        return frame


def repository_from_env(root: Path) -> MarketDataSource:
    import os

    url = os.environ.get("QINGPU_DATABASE_URL")
    if url is not None:
        parsed = urlparse(url)
        if parsed.scheme not in ("mysql", "mysql+pymysql"):
            raise ValueError(
                f"Unsupported scheme: {parsed.scheme!r}; expected 'mysql' or 'mysql+pymysql'"
            )
        return MySQLMarketDataSource(parsed)
    return ParquetMarketDataSource(root / "data" / "processed" / "market_transactions.parquet")
