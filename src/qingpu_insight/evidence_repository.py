from __future__ import annotations

from collections.abc import Callable, Sequence

import pandas as pd
import pymysql


class MySQLEvidenceRepository:
    def __init__(self, connection_factory: Callable[[], pymysql.Connection]) -> None:
        self._factory = connection_factory

    def current_dataset_version(self) -> str:
        conn = self._factory()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT version"
                    " FROM published_datasets"
                    " WHERE dataset_key = %s"
                    " LIMIT 1",
                    ("market",),
                )
                row = cursor.fetchone()
            if row:
                val = row.get("version") if isinstance(row, dict) else row[0]
                return str(val)
            return "unknown"
        finally:
            conn.close()

    def load_candidates(self, candidate_ids: Sequence[str]) -> pd.DataFrame:
        conn = self._factory()
        try:
            query = (
                "SELECT source_listing_id AS listing_id,"
                "       listing_type,"
                "       title,"
                "       asking_price_twd AS price,"
                "       building_area_ping,"
                "       station_code,"
                "       station_distance_m,"
                "       building_age_years,"
                "       snapshot_at AS observed_at"
                " FROM listing_current"
                " WHERE source_listing_id IN ({})"
                "   AND active = TRUE"
            ).format(",".join("%s" for _ in candidate_ids))
            df = pd.read_sql(query, conn, params=list(candidate_ids))
            return df
        finally:
            conn.close()

    def load_market_evidence(self, candidate_ids: Sequence[str]) -> pd.DataFrame:
        conn = self._factory()
        try:
            # Get station codes for the requested candidates
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT station_code FROM listing_current"
                " WHERE source_listing_id IN ({})"
                "   AND station_code IS NOT NULL".format(
                    ",".join("%s" for _ in candidate_ids)
                ),
                list(candidate_ids),
            )
            station_codes = [row[0] for row in cursor.fetchall()]
            if not station_codes:
                return pd.DataFrame()
            query = (
                "SELECT transaction_key,"
                "       station_code,"
                "       transaction_type,"
                "       transaction_date,"
                "       total_price_twd AS transaction_price,"
                "       unit_price_per_ping_twd,"
                "       building_area_ping"
                " FROM market_transactions"
                " WHERE station_code IN ({})"
                "   AND analysis_eligible = TRUE"
                " ORDER BY transaction_date DESC"
                " LIMIT 500"
            ).format(",".join("%s" for _ in station_codes))
            df = pd.read_sql(query, conn, params=list(station_codes))
            return df
        finally:
            conn.close()
