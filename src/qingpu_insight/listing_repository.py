"""Snapshot repositories for M3 listing intelligence."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd

from qingpu_insight.listing_sources import CaptureBatch

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ListingRepository(Protocol):
    """Contract for persisting listing snapshots and events."""

    def save_batch(self, batch: CaptureBatch, rows: pd.DataFrame) -> None:
        """Persist a captured batch and its normalized listings."""

    def load_current(
        self, listing_type: str | None = None
    ) -> pd.DataFrame:
        """Return the current snapshot for every listing.

        When *listing_type* is provided only rows of that type are returned.
        """

    def load_snapshots(
        self, batch_id: str | None = None
    ) -> pd.DataFrame:
        """Return snapshot history, optionally filtered by *batch_id*."""

    def load_events(
        self, listing_type: str | None = None
    ) -> pd.DataFrame:
        """Return stored listing events, optionally filtered by *listing_type*."""

    def merge_state(self, state: pd.DataFrame) -> None:
        """Merge state columns into the current snapshot."""

    def append_events(self, events: pd.DataFrame) -> None:
        """Append new event records (duplicate keys are ignored)."""


# ---------------------------------------------------------------------------
# Parquet adapter
# ---------------------------------------------------------------------------

_SNAPSHOT_DIR = "snapshots"
_CURRENT_FILE = "current.parquet"
_EVENT_FILE = "events.parquet"


class ParquetListingRepository:
    """File-system repository backed by Apache Parquet.

    All writes are performed to a ``.tmp`` sibling then atomically renamed
    via ``Path.replace()`` so that partial writes never produce a readable
    file.
    """

    def __init__(self, base_path: str | Path = "data/processed") -> None:
        self.base_path = Path(base_path)

    # ------------------------------------------------------------------
    # save_batch
    # ------------------------------------------------------------------

    def save_batch(self, batch: CaptureBatch, rows: pd.DataFrame) -> None:
        if rows.empty:
            return

        rows = rows.copy()
        rows["batch_id"] = batch.batch_id
        rows["source"] = batch.source

        self._write_snapshots(batch.batch_id, rows)
        self._merge_current(rows)

    # ------------------------------------------------------------------
    # load_current
    # ------------------------------------------------------------------

    def load_current(
        self, listing_type: str | None = None
    ) -> pd.DataFrame:
        path = self.base_path / _CURRENT_FILE
        try:
            df = pd.read_parquet(path)
        except (FileNotFoundError, ValueError):
            return pd.DataFrame()
        if listing_type is not None:
            df = df[df["listing_type"] == listing_type]
        return df

    # ------------------------------------------------------------------
    # load_snapshots
    # ------------------------------------------------------------------

    def load_snapshots(
        self, batch_id: str | None = None
    ) -> pd.DataFrame:
        snapshots_dir = self.base_path / _SNAPSHOT_DIR
        if batch_id is not None:
            path = snapshots_dir / f"{batch_id}.parquet"
            try:
                return pd.read_parquet(path)
            except (FileNotFoundError, ValueError):
                return pd.DataFrame()

        files = sorted(snapshots_dir.glob("*.parquet"))
        if not files:
            return pd.DataFrame()
        return pd.concat(
            [pd.read_parquet(f) for f in files], ignore_index=True
        )

    # ------------------------------------------------------------------
    # load_events
    # ------------------------------------------------------------------

    def load_events(
        self, listing_type: str | None = None
    ) -> pd.DataFrame:
        path = self.base_path / _EVENT_FILE
        try:
            df = pd.read_parquet(path)
        except (FileNotFoundError, ValueError):
            return pd.DataFrame()
        if listing_type is not None:
            df = df[df["listing_type"] == listing_type]
        return df

    # ------------------------------------------------------------------
    # merge_state
    # ------------------------------------------------------------------

    def merge_state(self, state: pd.DataFrame) -> None:
        if state.empty:
            return
        path = self.base_path / _CURRENT_FILE
        try:
            existing = pd.read_parquet(path)
        except (FileNotFoundError, ValueError):
            return

        merge_cols = ["source", "listing_type", "source_listing_id"]
        state_cols = merge_cols + [
            "active", "consecutive_absences", "last_seen_batch_id",
        ]
        state_subset = state[state_cols].drop_duplicates(subset=merge_cols)

        for col in ("active", "consecutive_absences", "last_seen_batch_id"):
            if col in existing.columns:
                existing = existing.drop(columns=[col])

        existing = existing.merge(state_subset, on=merge_cols, how="left")
        existing["active"] = existing["active"].fillna(True)
        existing["consecutive_absences"] = (
            existing["consecutive_absences"].fillna(0).astype(int)
        )
        existing["last_seen_batch_id"] = existing["last_seen_batch_id"].fillna("")

        self._atomic_write(path, existing)

    # ------------------------------------------------------------------
    # append_events
    # ------------------------------------------------------------------

    def append_events(self, events: pd.DataFrame) -> None:
        path = self.base_path / _EVENT_FILE
        try:
            existing = pd.read_parquet(path)
            combined = pd.concat(
                [existing, events], ignore_index=True
            ).drop_duplicates(subset=["event_key"], keep="last")
        except (FileNotFoundError, ValueError):
            combined = events
        self._atomic_write(path, combined)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_snapshots(self, batch_id: str, rows: pd.DataFrame) -> None:
        snapshots_dir = self.base_path / _SNAPSHOT_DIR
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        target = snapshots_dir / f"{batch_id}.parquet"
        self._atomic_write(target, rows)

    def _merge_current(self, rows: pd.DataFrame) -> None:
        path = self.base_path / _CURRENT_FILE
        try:
            existing = pd.read_parquet(path)
        except (FileNotFoundError, ValueError):
            existing = pd.DataFrame()

        keys = ["source", "listing_type", "source_listing_id"]
        if not existing.empty:
            merge_keys = rows[keys].drop_duplicates()
            existing = existing.merge(
                merge_keys, on=keys, how="left", indicator=True
            )
            existing = existing[existing["_merge"] == "left_only"].drop(
                columns=["_merge"]
            )

        updated = (
            rows.copy()
            if existing.empty
            else pd.concat([existing, rows], ignore_index=True)
        )
        self._atomic_write(path, updated)

    def _atomic_write(self, path: Path, df: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(path)


# ---------------------------------------------------------------------------
# MySQL adapter
# ---------------------------------------------------------------------------

_CREATE_SNAPSHOTS_SQL = """
CREATE TABLE IF NOT EXISTS listing_snapshots (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  batch_id VARCHAR(64) NOT NULL,
  source VARCHAR(32) NOT NULL,
  listing_type VARCHAR(16) NOT NULL,
  source_listing_id VARCHAR(64) NOT NULL,
  snapshot_at DATETIME NOT NULL,
  source_url VARCHAR(512) NOT NULL,
  title VARCHAR(256) NOT NULL DEFAULT '',
  asking_price_twd BIGINT UNSIGNED NULL,
  monthly_rent_twd BIGINT UNSIGNED NULL,
  building_area_ping DECIMAL(10,2) NULL,
  building_type VARCHAR(80) NULL,
  bedrooms TINYINT UNSIGNED NULL,
  living_rooms TINYINT UNSIGNED NULL,
  bathrooms TINYINT UNSIGNED NULL,
  building_age_years DECIMAL(6,2) NULL,
  floor TINYINT UNSIGNED NULL,
  total_floors TINYINT UNSIGNED NULL,
  parking_type VARCHAR(80) NULL,
  latitude DECIMAL(10,7) NULL,
  longitude DECIMAL(10,7) NULL,
  station_code VARCHAR(16) NULL,
  station_distance_m DECIMAL(10,2) NULL,
  location_eligible BOOLEAN NOT NULL DEFAULT FALSE,
  model_evidence JSON NULL,
  raw_hash CHAR(64) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_snapshot (batch_id, source, listing_type, source_listing_id)
) ENGINE=InnoDB
"""

_CREATE_CURRENT_SQL = """
CREATE TABLE IF NOT EXISTS listing_current (
  source VARCHAR(32) NOT NULL,
  listing_type VARCHAR(16) NOT NULL,
  source_listing_id VARCHAR(64) NOT NULL,
  snapshot_at DATETIME NOT NULL,
  source_url VARCHAR(512) NOT NULL,
  title VARCHAR(256) NOT NULL DEFAULT '',
  asking_price_twd BIGINT UNSIGNED NULL,
  monthly_rent_twd BIGINT UNSIGNED NULL,
  building_area_ping DECIMAL(10,2) NULL,
  building_type VARCHAR(80) NULL,
  bedrooms TINYINT UNSIGNED NULL,
  living_rooms TINYINT UNSIGNED NULL,
  bathrooms TINYINT UNSIGNED NULL,
  building_age_years DECIMAL(6,2) NULL,
  floor TINYINT UNSIGNED NULL,
  total_floors TINYINT UNSIGNED NULL,
  parking_type VARCHAR(80) NULL,
  latitude DECIMAL(10,7) NULL,
  longitude DECIMAL(10,7) NULL,
  station_code VARCHAR(16) NULL,
  station_distance_m DECIMAL(10,2) NULL,
  location_eligible BOOLEAN NOT NULL DEFAULT FALSE,
  model_evidence JSON NULL,
  raw_hash CHAR(64) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  consecutive_absences TINYINT UNSIGNED NOT NULL DEFAULT 0,
  last_seen_batch_id VARCHAR(64) NOT NULL DEFAULT '',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (source, listing_type, source_listing_id)
) ENGINE=InnoDB
"""

_CREATE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS listing_events (
  event_key VARCHAR(64) NOT NULL PRIMARY KEY,
  source VARCHAR(32) NOT NULL,
  listing_type VARCHAR(16) NOT NULL,
  source_listing_id VARCHAR(64) NOT NULL,
  event_type VARCHAR(32) NOT NULL,
  event_data JSON NULL,
  occurred_at DATETIME NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB
"""

_CREATE_BATCHES_SQL = """
CREATE TABLE IF NOT EXISTS listing_batches (
  batch_id VARCHAR(64) NOT NULL PRIMARY KEY,
  source VARCHAR(32) NOT NULL,
  listing_type VARCHAR(16) NOT NULL,
  started_at DATETIME NOT NULL,
  reached_terminal_page BOOLEAN NOT NULL DEFAULT FALSE,
  error_count SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB
"""

_INSERT_SNAPSHOT_SQL = """
INSERT IGNORE INTO listing_snapshots
    (batch_id, source, listing_type, source_listing_id, snapshot_at,
     source_url, title,
     asking_price_twd, monthly_rent_twd, building_area_ping,
     building_type, bedrooms, living_rooms, bathrooms,
     building_age_years, floor, total_floors, parking_type,
     latitude, longitude, station_code, station_distance_m,
     location_eligible, model_evidence, raw_hash)
VALUES
    (%(batch_id)s, %(source)s, %(listing_type)s, %(source_listing_id)s, %(snapshot_at)s,
     %(source_url)s, %(title)s,
     %(asking_price_twd)s, %(monthly_rent_twd)s, %(building_area_ping)s,
     %(building_type)s, %(bedrooms)s, %(living_rooms)s, %(bathrooms)s,
     %(building_age_years)s, %(floor)s, %(total_floors)s, %(parking_type)s,
     %(latitude)s, %(longitude)s, %(station_code)s, %(station_distance_m)s,
     %(location_eligible)s, %(model_evidence)s, %(raw_hash)s)
"""

_INSERT_CURRENT_SQL = """
INSERT INTO listing_current
    (source, listing_type, source_listing_id, snapshot_at,
     source_url, title,
     asking_price_twd, monthly_rent_twd, building_area_ping,
     building_type, bedrooms, living_rooms, bathrooms,
     building_age_years, floor, total_floors, parking_type,
     latitude, longitude, station_code, station_distance_m,
     location_eligible, model_evidence, raw_hash, active,
     consecutive_absences, last_seen_batch_id)
VALUES
    (%(source)s, %(listing_type)s, %(source_listing_id)s, %(snapshot_at)s,
     %(source_url)s, %(title)s,
     %(asking_price_twd)s, %(monthly_rent_twd)s, %(building_area_ping)s,
     %(building_type)s, %(bedrooms)s, %(living_rooms)s, %(bathrooms)s,
     %(building_age_years)s, %(floor)s, %(total_floors)s, %(parking_type)s,
     %(latitude)s, %(longitude)s, %(station_code)s, %(station_distance_m)s,
     %(location_eligible)s, %(model_evidence)s, %(raw_hash)s, %(active)s,
     %(consecutive_absences)s, %(last_seen_batch_id)s)
ON DUPLICATE KEY UPDATE
    snapshot_at = VALUES(snapshot_at),
    source_url = VALUES(source_url),
    title = VALUES(title),
    asking_price_twd = VALUES(asking_price_twd),
    monthly_rent_twd = VALUES(monthly_rent_twd),
    building_area_ping = VALUES(building_area_ping),
    building_type = VALUES(building_type),
    bedrooms = VALUES(bedrooms),
    living_rooms = VALUES(living_rooms),
    bathrooms = VALUES(bathrooms),
    building_age_years = VALUES(building_age_years),
    floor = VALUES(floor),
    total_floors = VALUES(total_floors),
    parking_type = VALUES(parking_type),
    latitude = VALUES(latitude),
    longitude = VALUES(longitude),
    station_code = VALUES(station_code),
    station_distance_m = VALUES(station_distance_m),
    location_eligible = VALUES(location_eligible),
    model_evidence = VALUES(model_evidence),
    active = VALUES(active),
    consecutive_absences = VALUES(consecutive_absences),
    last_seen_batch_id = VALUES(last_seen_batch_id),
    raw_hash = VALUES(raw_hash)
"""

_INSERT_BATCH_SQL = """
INSERT IGNORE INTO listing_batches
    (batch_id, source, listing_type, started_at, reached_terminal_page, error_count)
VALUES
    (%(batch_id)s, %(source)s, %(listing_type)s,
     %(started_at)s, %(reached_terminal_page)s, %(error_count)s)
"""

_INSERT_EVENT_SQL = """
INSERT IGNORE INTO listing_events
    (event_key, source, listing_type, source_listing_id,
     event_type, event_data, occurred_at)
VALUES
    (%(event_key)s, %(source)s, %(listing_type)s, %(source_listing_id)s,
     %(event_type)s, %(event_data)s, %(occurred_at)s)
"""


class MySQLListingRepository:
    """MySQL-backed repository using PyMySQL.

    Every ``save_batch`` call runs inside a single transaction; if any step
    fails the entire transaction is rolled back.
    """

    def __init__(self, connection):
        self._conn = connection
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        with self._conn.cursor() as cur:
            for ddl in (
                _CREATE_BATCHES_SQL,
                _CREATE_SNAPSHOTS_SQL,
                _CREATE_CURRENT_SQL,
                _CREATE_EVENTS_SQL,
            ):
                cur.execute(ddl)
        self._conn.commit()

    # ------------------------------------------------------------------
    # save_batch
    # ------------------------------------------------------------------

    def save_batch(self, batch: CaptureBatch, rows: pd.DataFrame) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    _INSERT_BATCH_SQL,
                    {
                        "batch_id": batch.batch_id,
                        "source": batch.source,
                        "listing_type": batch.listing_type,
                        "started_at": batch.started_at,
                        "reached_terminal_page": int(batch.reached_terminal_page),
                        "error_count": len(batch.errors),
                    },
                )
                if rows.empty:
                    self._conn.commit()
                    return

                for _, row in rows.iterrows():
                    params = {
                        "batch_id": batch.batch_id,
                        "source": batch.source,
                        "listing_type": row["listing_type"],
                        "source_listing_id": row["source_listing_id"],
                        "snapshot_at": row["snapshot_at"],
                        "source_url": row["source_url"],
                        "title": str(row.get("title", "")),
                        "asking_price_twd": _safe_int(row, "asking_price_twd"),
                        "monthly_rent_twd": _safe_int(row, "monthly_rent_twd"),
                        "building_area_ping": _safe_float(row, "building_area_ping"),
                        "building_type": _safe_str(row, "building_type"),
                        "bedrooms": _safe_int(row, "bedrooms"),
                        "living_rooms": _safe_int(row, "living_rooms"),
                        "bathrooms": _safe_int(row, "bathrooms"),
                        "building_age_years": _safe_float(row, "building_age_years"),
                        "floor": _safe_int(row, "floor"),
                        "total_floors": _safe_int(row, "total_floors"),
                        "parking_type": _safe_str(row, "parking_type"),
                        "latitude": _safe_float(row, "latitude"),
                        "longitude": _safe_float(row, "longitude"),
                        "station_code": _safe_str(row, "station_code"),
                        "station_distance_m": _safe_float(row, "station_distance_m"),
                        "location_eligible": _safe_bool(row, "location_eligible"),
                        "model_evidence": _safe_str(row, "model_evidence"),
                        "active": _safe_bool(row, "active", default=True),
                        "consecutive_absences": _safe_int(
                            row, "consecutive_absences", default=0
                        ),
                        "last_seen_batch_id": _safe_str(
                            row, "last_seen_batch_id"
                        ) or batch.batch_id,
                        "raw_hash": str(row.get("raw_hash", "")),
                    }
                    cur.execute(_INSERT_SNAPSHOT_SQL, params)
                    cur.execute(_INSERT_CURRENT_SQL, params)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------
    # load_current
    # ------------------------------------------------------------------

    def load_current(
        self, listing_type: str | None = None
    ) -> pd.DataFrame:
        where = "WHERE listing_type = %(lt)s" if listing_type else ""
        sql = f"SELECT * FROM listing_current {where}"

        with self._conn.cursor() as cur:
            if listing_type:
                cur.execute(sql, {"lt": listing_type})
            else:
                cur.execute(sql)
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=cols)

    # ------------------------------------------------------------------
    # load_snapshots
    # ------------------------------------------------------------------

    def load_snapshots(
        self, batch_id: str | None = None
    ) -> pd.DataFrame:
        where = "WHERE batch_id = %(bid)s" if batch_id else ""
        sql = f"SELECT * FROM listing_snapshots {where}"

        with self._conn.cursor() as cur:
            if batch_id:
                cur.execute(sql, {"bid": batch_id})
            else:
                cur.execute(sql)
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=cols)

    # ------------------------------------------------------------------
    # append_events
    # ------------------------------------------------------------------

    def append_events(self, events: pd.DataFrame) -> None:
        try:
            with self._conn.cursor() as cur:
                for _, row in events.iterrows():
                    cur.execute(
                        _INSERT_EVENT_SQL,
                        {
                            "event_key": row["event_key"],
                            "source": row["source"],
                            "listing_type": row["listing_type"],
                            "source_listing_id": row["source_listing_id"],
                            "event_type": row["event_type"],
                            "event_data": row.get("event_data"),
                            "occurred_at": row["occurred_at"],
                        },
                    )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------
    # load_events
    # ------------------------------------------------------------------

    def load_events(
        self, listing_type: str | None = None
    ) -> pd.DataFrame:
        where = "WHERE listing_type = %(lt)s" if listing_type else ""
        sql = f"SELECT * FROM listing_events {where}"

        with self._conn.cursor() as cur:
            if listing_type:
                cur.execute(sql, {"lt": listing_type})
            else:
                cur.execute(sql)
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=cols)

    # ------------------------------------------------------------------
    # merge_state
    # ------------------------------------------------------------------

    def merge_state(self, state: pd.DataFrame) -> None:
        if state.empty:
            return
        sql = """
UPDATE listing_current
SET active = %(active)s,
    consecutive_absences = %(consecutive_absences)s,
    last_seen_batch_id = %(last_seen_batch_id)s
WHERE source = %(source)s
  AND listing_type = %(listing_type)s
  AND source_listing_id = %(source_listing_id)s
"""
        try:
            with self._conn.cursor() as cur:
                for _, row in state.iterrows():
                    cur.execute(
                        sql,
                        {
                            "source": row["source"],
                            "listing_type": row["listing_type"],
                            "source_listing_id": row["source_listing_id"],
                            "active": _safe_bool(row, "active", default=True),
                            "consecutive_absences": _safe_int(
                                row, "consecutive_absences", default=0
                            ),
                            "last_seen_batch_id": _safe_str(
                                row, "last_seen_batch_id"
                            ) or "",
                        },
                    )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_int(
    row: pd.Series, col: str, default: int | None = None
) -> int | None:
    v = row.get(col)
    return default if v is None or pd.isna(v) else int(v)


def _safe_float(row: pd.Series, col: str) -> float | None:
    v = row.get(col)
    return None if pd.isna(v) else float(v)


def _safe_str(row: pd.Series, col: str) -> str | None:
    v = row.get(col)
    return None if v is None or pd.isna(v) else str(v)


def _safe_bool(row: pd.Series, col: str, default: bool = False) -> int:
    v = row.get(col)
    return int(default if v is None or pd.isna(v) else bool(v))
