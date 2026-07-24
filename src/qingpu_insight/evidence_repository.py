from __future__ import annotations

from collections.abc import Callable, Sequence

import pandas as pd
import pymysql


def empty_market_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "listing_id", "transaction_key", "station_code", "transaction_type",
        "transaction_date", "transaction_price", "unit_price_per_ping_twd",
        "building_area_ping",
    ])


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
                "       asking_price_twd AS price,"
                "       COALESCE("
                "           asking_unit_price_low_twd_per_ping,"
                "           asking_unit_price_high_twd_per_ping,"
                "           asking_price_twd / NULLIF(building_area_ping, 0)"
                "       ) AS price_per_ping,"
                "       building_area_ping,"
                "       station_code,"
                "       station_distance_m,"
                "       building_age_years,"
                "       snapshot_at,"
                "       acquisition_representation AS location_method,"
                "       model_evidence"
                " FROM listing_current"
                " WHERE source_listing_id IN ({})"
                "   AND active = TRUE"
            ).format(",".join("%s" for _ in candidate_ids))
            df = pd.read_sql(query, conn, params=list(candidate_ids))
            df = df.rename(columns={
                "source_listing_id": "listing_id",
                "asking_price_twd": "price",
                "asking_unit_price_low_twd_per_ping": "price_per_ping",
                "acquisition_representation": "location_method",
            })
            return df
        finally:
            conn.close()

    def load_market_evidence(self, candidate_ids: Sequence[str]) -> pd.DataFrame:
        conn = self._factory()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source_listing_id, station_code, building_area_ping"
                " FROM listing_current"
                " WHERE source_listing_id IN ({})"
                "   AND station_code IS NOT NULL"
                "   AND active = TRUE".format(
                    ",".join("%s" for _ in candidate_ids)
                ),
                list(candidate_ids),
            )
            rows = cursor.fetchall()
            if not rows:
                return empty_market_frame()
            candidate_rows = pd.DataFrame(
                rows,
                columns=["listing_id", "station_code", "building_area_ping"],
            )
            station_codes = candidate_rows["station_code"].unique().tolist()
            market_query = (
                "SELECT transaction_key,"
                "       station_code,"
                "       transaction_type,"
                "       transaction_date,"
                "       total_price_twd,"
                "       unit_price_per_ping_twd,"
                "       building_area_ping"
                " FROM market_transactions"
                " WHERE station_code IN ({})"
                "   AND analysis_eligible = TRUE"
                " ORDER BY transaction_date DESC"
                " LIMIT 500"
            ).format(",".join("%s" for _ in station_codes))
            market_df = pd.read_sql(market_query, conn, params=list(station_codes))
            _RENAME = {"total_price_twd": "transaction_price"}
            market_df = market_df.rename(columns=_RENAME)

            if market_df.empty:
                return empty_market_frame()

            frames: list[pd.DataFrame] = []
            for _, candidate in candidate_rows.iterrows():
                comparable = market_df[
                    market_df["station_code"] == candidate["station_code"]
                ]
                area = candidate["building_area_ping"]
                if area is not None and not (isinstance(area, float) and pd.isna(area)):
                    comparable = comparable[
                        comparable["building_area_ping"].between(
                            area * 0.8, area * 1.2
                        )
                    ]
                comparable = comparable.copy()
                comparable["listing_id"] = str(candidate["listing_id"])
                frames.append(comparable.head(100))

            if frames:
                return pd.concat(frames, ignore_index=True)
            return empty_market_frame()
        finally:
            conn.close()
