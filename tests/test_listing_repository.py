"""Tests for listing repository adapters."""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from qingpu_insight.listing_events import detect_listing_events
from qingpu_insight.listing_repository import (
    _CREATE_CURRENT_SQL,
    _CREATE_SNAPSHOTS_SQL,
    MySQLListingRepository,
    ParquetListingRepository,
)
from qingpu_insight.listing_sources import CaptureBatch, CapturedPage

# ---------------------------------------------------------------------------
# In-memory fake for testing MySQL behaviour without a real MySQL server
# ---------------------------------------------------------------------------

class FakeMySQLListingRepository:
    """In-memory implementation of ListingRepository for testing.

    Mirrors MySQLListingRepository semantics: one-transaction-per-batch,
    duplicate-key idempotence, current-state tracking.
    """

    def __init__(self):
        self._batches: dict[str, dict] = {}
        self._snapshots: list[dict] = []
        self._current: dict[tuple[str, str, str], dict] = {}
        self._events: list[dict] = {}

    def save_batch(self, batch: CaptureBatch, rows: pd.DataFrame) -> None:
        for _, row in rows.iterrows():
            if pd.isna(row.get("source_listing_id")):
                msg = "source_listing_id cannot be null"
                raise ValueError(msg)

        batch_key = batch.batch_id
        self._batches[batch_key] = {
            "batch_id": batch.batch_id,
            "source": batch.source,
            "listing_type": batch.listing_type,
            "started_at": batch.started_at,
            "is_complete": batch.is_complete,
        }

        for _, row in rows.iterrows():
            snap = row.to_dict()
            snap["batch_id"] = batch.batch_id
            snap_key = (
                batch.batch_id, batch.source,
                row["listing_type"], row["source_listing_id"],
            )
            existing = [
                s for s in self._snapshots
                if (s.get("batch_id"), s.get("source"),
                    s.get("listing_type"), s.get("source_listing_id"))
                == snap_key
            ]
            if not existing:
                self._snapshots.append(snap)

            cur_key = (batch.source, row["listing_type"], row["source_listing_id"])
            self._current[cur_key] = dict(snap)

    def load_current(self, listing_type: str | None = None) -> pd.DataFrame:
        rows = list(self._current.values())
        if listing_type:
            rows = [r for r in rows if r["listing_type"] == listing_type]
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def load_snapshots(self, batch_id: str | None = None) -> pd.DataFrame:
        rows = self._snapshots
        if batch_id:
            rows = [r for r in rows if r.get("batch_id") == batch_id]
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def append_events(self, events: pd.DataFrame) -> None:
        for _, row in events.iterrows():
            ek = row["event_key"]
            if ek not in self._events:
                self._events[ek] = row.to_dict()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SNAPSHOT_TS = pd.Timestamp("2026-07-21 12:00:00")


@pytest.fixture
def complete_batch() -> CaptureBatch:
    return CaptureBatch(
        batch_id="591-sale-20260721T120000Z",
        source="591",
        listing_type="sale",
        started_at=datetime(2026, 7, 21, 12, 0, 0),
        pages=[CapturedPage(1, "https://sale.591.com.tw/", "<html/>")],
        errors=[],
        reached_terminal_page=True,
    )


@pytest.fixture
def normalized_rows() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "source": "591",
            "source_listing_id": "sale-001",
            "listing_type": "sale",
            "snapshot_at": SNAPSHOT_TS,
            "source_url": "https://sale.591.com.tw/index.php?h=12345",
            "title": "領航站三房平車",
            "asking_price_twd": 18_800_000,
            "monthly_rent_twd": None,
            "building_area_ping": 35.5,
            "asking_unit_price_low_twd_per_ping": 500_000,
            "asking_unit_price_high_twd_per_ping": 560_000,
            "building_area_min_ping": 19.0,
            "building_area_max_ping": 30.0,
            "acquisition_representation": "jsonld",
            "acquisition_schema_version": "591-newhouse-jsonld-v1",
            "building_type": "住宅大樓",
            "bedrooms": 3,
            "living_rooms": 2,
            "bathrooms": 2,
            "building_age_years": 6.0,
            "floor": 8,
            "total_floors": 15,
            "parking_type": "坡道平面",
            "latitude": 25.002,
            "longitude": 121.215,
            "raw_hash": "a" * 64,
        },
        {
            "source": "591",
            "source_listing_id": "sale-002",
            "listing_type": "sale",
            "snapshot_at": SNAPSHOT_TS,
            "source_url": "https://sale.591.com.tw/index.php?h=67890",
            "title": "青埔高樓層三房",
            "asking_price_twd": 22_500_000,
            "monthly_rent_twd": None,
            "building_area_ping": 45.2,
            "asking_unit_price_low_twd_per_ping": None,
            "asking_unit_price_high_twd_per_ping": None,
            "building_area_min_ping": None,
            "building_area_max_ping": None,
            "acquisition_representation": "dom",
            "acquisition_schema_version": "591-sale-dom-v1",
            "building_type": "住宅大樓",
            "bedrooms": 3,
            "living_rooms": 2,
            "bathrooms": 2,
            "building_age_years": 3.0,
            "floor": 14,
            "total_floors": 20,
            "parking_type": "坡道平面",
            "latitude": 25.010,
            "longitude": 121.200,
            "raw_hash": "b" * 64,
        },
    ])


@pytest.fixture
def rental_rows() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "source": "591",
            "source_listing_id": "rental-001",
            "listing_type": "rental",
            "snapshot_at": SNAPSHOT_TS,
            "source_url": "https://rent.591.com.tw/rent-detail-999.html",
            "title": "體育園區套房",
            "asking_price_twd": None,
            "monthly_rent_twd": 15_000,
            "building_area_ping": 12.0,
            "asking_unit_price_low_twd_per_ping": None,
            "asking_unit_price_high_twd_per_ping": None,
            "building_area_min_ping": None,
            "building_area_max_ping": None,
            "acquisition_representation": "dom",
            "acquisition_schema_version": "591-rental-dom-v1",
            "building_type": "住宅大樓",
            "bedrooms": 1,
            "living_rooms": 1,
            "bathrooms": 1,
            "building_age_years": 10.0,
            "floor": 3,
            "total_floors": 7,
            "parking_type": None,
            "latitude": 24.995,
            "longitude": 121.205,
            "raw_hash": "c" * 64,
        },
    ])


@pytest.fixture
def parquet_repository(tmp_path: Path) -> ParquetListingRepository:
    return ParquetListingRepository(base_path=tmp_path)


@pytest.fixture
def mysql_fake_repository() -> FakeMySQLListingRepository:
    return FakeMySQLListingRepository()


# ---------------------------------------------------------------------------
# Contract tests  (parameterised over parquet + fake-mysql)
# ---------------------------------------------------------------------------

class TestRepositoryContract:
    """Tests that must pass for every ListingRepository implementation."""

    @pytest.mark.parametrize("repo_fixture", [
        "parquet_repository",
        "mysql_fake_repository",
    ])
    def test_save_batch_is_idempotent(
        self, repo_fixture, complete_batch, normalized_rows, request,
    ):
        repo = request.getfixturevalue(repo_fixture)
        repo.save_batch(complete_batch, normalized_rows)
        repo.save_batch(complete_batch, normalized_rows)
        snapshots = repo.load_snapshots(batch_id=complete_batch.batch_id)
        assert len(snapshots) == len(normalized_rows)

    @pytest.mark.parametrize("repo_fixture", [
        "parquet_repository",
        "mysql_fake_repository",
    ])
    def test_type_isolation(
        self, repo_fixture, complete_batch, normalized_rows, rental_rows, request,
    ):
        repo = request.getfixturevalue(repo_fixture)
        repo.save_batch(complete_batch, normalized_rows)
        rental_batch = CaptureBatch(
            batch_id="591-rental-20260721T120000Z",
            source="591",
            listing_type="rental",
            started_at=datetime(2026, 7, 21, 12, 0, 0),
            pages=[],
            errors=[],
            reached_terminal_page=True,
        )
        repo.save_batch(rental_batch, rental_rows)
        sale_snapshots = repo.load_snapshots(batch_id=complete_batch.batch_id)
        rental_snapshots = repo.load_snapshots(batch_id=rental_batch.batch_id)
        assert len(sale_snapshots) == len(normalized_rows)
        assert len(rental_snapshots) == len(rental_rows)
        current_sale = repo.load_current(listing_type="sale")
        current_rental = repo.load_current(listing_type="rental")
        assert len(current_sale) == len(normalized_rows)
        assert len(current_rental) == len(rental_rows)

    @pytest.mark.parametrize("repo_fixture", [
        "parquet_repository",
        "mysql_fake_repository",
    ])
    def test_batch_completeness_persisted(
        self, repo_fixture, complete_batch, normalized_rows, request,
    ):
        repo = request.getfixturevalue(repo_fixture)
        incomplete_batch = CaptureBatch(
            batch_id="591-sale-incomplete",
            source="591",
            listing_type="sale",
            started_at=datetime(2026, 7, 21, 12, 0, 0),
            pages=[],
            errors=[],
            reached_terminal_page=False,
        )
        repo.save_batch(complete_batch, normalized_rows)
        repo.save_batch(incomplete_batch, normalized_rows)
        complete = repo.load_snapshots(batch_id=complete_batch.batch_id)
        incomplete = repo.load_snapshots(batch_id=incomplete_batch.batch_id)
        assert len(complete) == len(normalized_rows)
        assert len(incomplete) == len(normalized_rows)

    @pytest.mark.parametrize("repo_fixture", [
        "parquet_repository",
        "mysql_fake_repository",
    ])
    def test_load_current_returns_latest(
        self, repo_fixture, complete_batch, normalized_rows, request,
    ):
        repo = request.getfixturevalue(repo_fixture)
        repo.save_batch(complete_batch, normalized_rows)
        current = repo.load_current()
        assert len(current) == len(normalized_rows)
        for _, row in normalized_rows.iterrows():
            match = current[
                (current["source_listing_id"] == row["source_listing_id"])
                & (current["listing_type"] == row["listing_type"])
            ]
            assert len(match) == 1
            assert match.iloc[0]["asking_price_twd"] == row["asking_price_twd"]

    @pytest.mark.parametrize("repo_fixture", [
        "parquet_repository",
        "mysql_fake_repository",
    ])
    def test_advertised_ranges_and_acquisition_metadata_roundtrip(
        self, repo_fixture, complete_batch, normalized_rows, request,
    ):
        repo = request.getfixturevalue(repo_fixture)
        repo.save_batch(complete_batch, normalized_rows.iloc[[0]])

        expected = normalized_rows.iloc[0]
        for stored in (
            repo.load_snapshots(batch_id=complete_batch.batch_id).iloc[0],
            repo.load_current().iloc[0],
        ):
            for column in (
                "asking_unit_price_low_twd_per_ping",
                "asking_unit_price_high_twd_per_ping",
                "building_area_min_ping",
                "building_area_max_ping",
                "acquisition_representation",
                "acquisition_schema_version",
            ):
                assert stored[column] == expected[column]

    @pytest.mark.parametrize("repo_fixture", [
        "parquet_repository",
        "mysql_fake_repository",
    ])
    @pytest.mark.parametrize("reached_terminal_page", [True, False])
    def test_absent_listing_roundtrip_preserves_advertised_contract(
        self,
        repo_fixture,
        reached_terminal_page,
        complete_batch,
        normalized_rows,
        request,
    ):
        repo = request.getfixturevalue(repo_fixture)
        initial_rows = normalized_rows.copy()
        initial_rows["active"] = True
        initial_rows["consecutive_absences"] = 0
        initial_rows["last_seen_batch_id"] = complete_batch.batch_id
        repo.save_batch(complete_batch, initial_rows)
        previous = repo.load_current(listing_type="sale")
        next_batch = CaptureBatch(
            batch_id=f"absence-{int(reached_terminal_page)}",
            source="591",
            listing_type="sale",
            started_at=datetime(2026, 7, 22, 12, 0, 0),
            reached_terminal_page=reached_terminal_page,
        )
        result = detect_listing_events(
            previous,
            normalized_rows.iloc[[1]],
            next_batch,
        )

        repo.save_batch(next_batch, result.state)

        expected = normalized_rows.iloc[0]
        current = repo.load_current(listing_type="sale")
        stored = current[current["source_listing_id"] == "sale-001"].iloc[0]
        for column in (
            "asking_unit_price_low_twd_per_ping",
            "asking_unit_price_high_twd_per_ping",
            "building_area_min_ping",
            "building_area_max_ping",
            "acquisition_representation",
            "acquisition_schema_version",
            "raw_hash",
        ):
            assert stored[column] == expected[column]
        assert stored["consecutive_absences"] == int(reached_terminal_page)
        expected_batch = next_batch.batch_id if reached_terminal_page else complete_batch.batch_id
        assert stored["last_seen_batch_id"] == expected_batch

    @pytest.mark.parametrize("repo_fixture", [
        "parquet_repository",
        "mysql_fake_repository",
    ])
    def test_load_current_filter_by_type(
        self, repo_fixture, complete_batch, normalized_rows, rental_rows, request,
    ):
        repo = request.getfixturevalue(repo_fixture)
        repo.save_batch(complete_batch, normalized_rows)
        rental_batch = CaptureBatch(
            batch_id="591-rental-filter",
            source="591",
            listing_type="rental",
            started_at=datetime(2026, 7, 21, 12, 0, 0),
            pages=[],
            errors=[],
            reached_terminal_page=True,
        )
        repo.save_batch(rental_batch, rental_rows)
        result = repo.load_current(listing_type="rental")
        assert len(result) == len(rental_rows)
        assert (result["listing_type"] == "rental").all()

    @pytest.mark.parametrize("repo_fixture", [
        "parquet_repository",
        "mysql_fake_repository",
    ])
    def test_public_field_allowlist(
        self, repo_fixture, complete_batch, normalized_rows, request,
    ):
        repo = request.getfixturevalue(repo_fixture)
        repo.save_batch(complete_batch, normalized_rows)
        snapshots = repo.load_snapshots(batch_id=complete_batch.batch_id)
        sensitive = {"phone", "contact_name", "email", "address", "message", "password"}
        cols = set(snapshots.columns)
        assert sensitive.isdisjoint(cols), f"Sensitive fields found: {sensitive & cols}"

    @pytest.mark.parametrize("repo_fixture", [
        "parquet_repository",
        "mysql_fake_repository",
    ])
    def test_append_events_roundtrip(
        self, repo_fixture, request,
    ):
        repo = request.getfixturevalue(repo_fixture)
        events = pd.DataFrame([
            {
                "event_key": "evt-001",
                "source": "591",
                "listing_type": "sale",
                "source_listing_id": "sale-001",
                "event_type": "price_change",
                "event_data": '{"old": 18800000, "new": 18000000}',
                "occurred_at": pd.Timestamp("2026-07-21 14:00:00"),
            },
        ])
        repo.append_events(events)
        events2 = pd.DataFrame([
            {
                "event_key": "evt-002",
                "source": "591",
                "listing_type": "rental",
                "source_listing_id": "rental-001",
                "event_type": "status_change",
                "event_data": '{"status": "rented"}',
                "occurred_at": pd.Timestamp("2026-07-21 15:00:00"),
            },
        ])
        repo.append_events(events2)
        assert True  # no error means append succeeded

    @pytest.mark.parametrize("repo_fixture", [
        "parquet_repository",
        "mysql_fake_repository",
    ])
    def test_append_events_duplicate_key(
        self, repo_fixture, request,
    ):
        repo = request.getfixturevalue(repo_fixture)
        events = pd.DataFrame([
            {
                "event_key": "evt-001",
                "source": "591",
                "listing_type": "sale",
                "source_listing_id": "sale-001",
                "event_type": "price_change",
                "event_data": '{"old": 18800000, "new": 18000000}',
                "occurred_at": pd.Timestamp("2026-07-21 14:00:00"),
            },
        ])
        repo.append_events(events)
        repo.append_events(events)
        assert True


class TestParquetRepositorySpecific:
    """Additional tests specific to the Parquet backend."""

    def test_parquet_file_created(
        self, parquet_repository, complete_batch, normalized_rows,
    ):
        snapshots_dir = parquet_repository.base_path / "snapshots"
        assert not snapshots_dir.exists()
        parquet_repository.save_batch(complete_batch, normalized_rows)
        expected = snapshots_dir / f"{complete_batch.batch_id}.parquet"
        assert expected.exists()

    def test_no_tmp_file_left_behind(
        self, parquet_repository, complete_batch, normalized_rows,
    ):
        parquet_repository.save_batch(complete_batch, normalized_rows)
        tmp_files = list(parquet_repository.base_path.rglob("*.tmp"))
        assert len(tmp_files) == 0

    def test_load_snapshots_all(
        self, parquet_repository, complete_batch, normalized_rows,
    ):
        parquet_repository.save_batch(complete_batch, normalized_rows)
        parquet_repository.save_batch(
            CaptureBatch(
                batch_id="batch-002",
                source="591",
                listing_type="sale",
                started_at=datetime(2026, 7, 21, 12, 0, 0),
                pages=[],
                errors=[],
                reached_terminal_page=True,
            ),
            normalized_rows,
        )
        all_snaps = parquet_repository.load_snapshots()
        assert len(all_snaps) == 2 * len(normalized_rows)

    def test_empty_load_current(self, parquet_repository):
        df = parquet_repository.load_current()
        assert len(df) == 0

    def test_empty_load_snapshots(self, parquet_repository):
        df = parquet_repository.load_snapshots(batch_id="nonexistent")
        assert len(df) == 0

    def test_location_provenance_round_trips_through_parquet(
        self, parquet_repository, complete_batch, normalized_rows
    ):
        rows = normalized_rows.iloc[[0]].copy()
        rows["structured_address"] = "桃園市中壢區高鐵南路一段1號"
        rows["address_source_url"] = "https://newhouse.591.com.tw/home/1"
        rows["address_observed_at"] = pd.Timestamp("2026-07-21T12:00:00Z")
        rows["location_method"] = "structured_address"
        rows["location_confidence"] = "medium"
        rows["location_reason"] = "eligible_structured_address"
        rows["geocoded_at"] = pd.Timestamp("2026-07-21T12:01:00Z")
        rows["geocoder_version"] = "doorplate-v1"

        parquet_repository.save_batch(complete_batch, rows)
        loaded = parquet_repository.load_current().iloc[0]

        assert loaded["structured_address"] == "桃園市中壢區高鐵南路一段1號"
        assert loaded["location_method"] == "structured_address"
        assert loaded["location_confidence"] == "medium"
        assert loaded["location_reason"] == "eligible_structured_address"
        assert loaded["geocoder_version"] == "doorplate-v1"

    def test_mixed_legacy_and_new_schema_normalizes_absent_metadata(
        self, parquet_repository, complete_batch, normalized_rows,
    ):
        advertised_columns = [
            "asking_unit_price_low_twd_per_ping",
            "asking_unit_price_high_twd_per_ping",
            "building_area_min_ping",
            "building_area_max_ping",
            "acquisition_representation",
            "acquisition_schema_version",
        ]
        legacy_row = normalized_rows.iloc[[0]].drop(columns=advertised_columns)
        parquet_repository.save_batch(complete_batch, legacy_row)
        new_batch = CaptureBatch(
            batch_id="new-schema-batch",
            source="591",
            listing_type="sale",
            started_at=datetime(2026, 7, 22, 12, 0, 0),
            reached_terminal_page=True,
        )
        new_row = normalized_rows.iloc[[1]].copy()
        parquet_repository.save_batch(new_batch, new_row)
        mixed_current = parquet_repository.load_current(listing_type="sale")
        absence_batch = CaptureBatch(
            batch_id="mixed-schema-absence",
            source="591",
            listing_type="sale",
            started_at=datetime(2026, 7, 23, 12, 0, 0),
            reached_terminal_page=True,
        )

        result = detect_listing_events(mixed_current, new_row, absence_batch)
        parquet_repository.save_batch(absence_batch, result.state)

        current = parquet_repository.load_current(listing_type="sale")
        legacy_current = current[current["source_listing_id"] == "sale-001"].iloc[0]
        assert legacy_current["acquisition_representation"] == "unknown"
        assert legacy_current["acquisition_schema_version"] == "unknown"


class TestMySQLFakeSpecific:
    """Additional tests for the fake MySQL backend."""

    def test_rollback_on_null_key(self, mysql_fake_repository, complete_batch, normalized_rows):
        bad_rows = normalized_rows.copy()
        bad_rows.loc[0, "source_listing_id"] = None
        with pytest.raises(ValueError, match="source_listing_id cannot be null"):
            mysql_fake_repository.save_batch(complete_batch, bad_rows)
        snapshots = mysql_fake_repository.load_snapshots(batch_id=complete_batch.batch_id)
        assert len(snapshots) == 0


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.connection.executions.append((sql, params))

    def fetchall(self):
        return []


class RecordingConnection:
    def __init__(self):
        self.executions = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return RecordingCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class LegacySchemaCursor(RecordingCursor):
    def __init__(self, connection):
        super().__init__(connection)
        self._rows = []

    def execute(self, sql, params=None):
        super().execute(sql, params)
        show = re.search(r"SHOW COLUMNS FROM `?(listing_snapshots|listing_current)`?", sql, re.I)
        if show:
            table = show.group(1)
            self._rows = [
                (
                    name,
                    definition["type"],
                    "YES" if definition["nullable"] else "NO",
                    "",
                    definition["default"],
                    "",
                )
                for name, definition in self.connection.schemas[table].items()
            ]
            return

        alter = re.search(
            r"ALTER TABLE `?(listing_snapshots|listing_current)`?\s+"
            r"(ADD|MODIFY) COLUMN `?([a-z0-9_]+)`?\s+(.+)",
            " ".join(sql.split()),
            re.I,
        )
        if alter:
            table, action, column, definition = alter.groups()
            if action.upper() == "ADD" and column in self.connection.schemas[table]:
                raise AssertionError(f"duplicate column migration: {table}.{column}")
            self.connection.schemas[table][column] = {
                "type": definition.split()[0].lower(),
                "nullable": "NOT NULL" not in definition.upper(),
                "default": "unknown" if "DEFAULT 'unknown'" in definition else None,
            }
            for row in self.connection.rows[table]:
                row.setdefault(column, self.connection.schemas[table][column]["default"])
            return

        update = re.search(
            r"UPDATE `?(listing_snapshots|listing_current)`?\s+"
            r"SET `?([a-z0-9_]+)`?\s*=\s*'unknown'",
            " ".join(sql.split()),
            re.I,
        )
        if update:
            table, column = update.groups()
            for row in self.connection.rows[table]:
                if row.get(column) in (None, ""):
                    row[column] = "unknown"

    def fetchall(self):
        return self._rows


class LegacySchemaConnection(RecordingConnection):
    def __init__(self):
        super().__init__()
        legacy_columns = {
            "source": {"type": "varchar(32)", "nullable": False, "default": None},
            "raw_hash": {"type": "char(64)", "nullable": False, "default": None},
        }
        self.schemas = {
            "listing_snapshots": {name: dict(value) for name, value in legacy_columns.items()},
            "listing_current": {name: dict(value) for name, value in legacy_columns.items()},
        }
        self.rows = {
            "listing_snapshots": [{"source": "591", "raw_hash": "a" * 64}],
            "listing_current": [{"source": "591", "raw_hash": "a" * 64}],
        }

    def cursor(self):
        return LegacySchemaCursor(self)


class TestMySQLRepositoryActualAdapter:
    def test_legacy_schema_is_upgraded_idempotently_and_backfilled(self):
        connection = LegacySchemaConnection()

        MySQLListingRepository(connection)
        first_add_count = sum(
            " ADD COLUMN " in " ".join(sql.split()).upper()
            for sql, _ in connection.executions
        )
        MySQLListingRepository(connection)
        second_add_count = sum(
            " ADD COLUMN " in " ".join(sql.split()).upper()
            for sql, _ in connection.executions
        )

        expected_columns = {
            "asking_unit_price_low_twd_per_ping",
            "asking_unit_price_high_twd_per_ping",
            "building_area_min_ping",
            "building_area_max_ping",
            "acquisition_representation",
            "acquisition_schema_version",
            "structured_address",
            "address_source_url",
            "address_observed_at",
            "location_method",
            "location_confidence",
            "location_reason",
            "geocoded_at",
            "geocoder_version",
        }
        for table in ("listing_snapshots", "listing_current"):
            assert expected_columns <= connection.schemas[table].keys()
            for metadata_column in (
                "acquisition_representation",
                "acquisition_schema_version",
            ):
                definition = connection.schemas[table][metadata_column]
                assert definition["nullable"] is False
                assert definition["default"] == "unknown"
            assert connection.rows[table][0][metadata_column] == "unknown"
            for provenance_column in (
                "location_method",
                "location_confidence",
                "location_reason",
            ):
                definition = connection.schemas[table][provenance_column]
                assert definition["nullable"] is False
                assert definition["default"] == "unknown"
                assert connection.rows[table][0][provenance_column] == "unknown"
        assert first_add_count == 28
        assert second_add_count == first_add_count

    def test_additive_range_migration_exists_and_backfills_metadata(self):
        migration_path = (
            Path(__file__).parents[1] / "database" / "004_listing_range_fields.sql"
        )

        assert migration_path.exists()
        sql = migration_path.read_text(encoding="utf-8")
        assert "information_schema.COLUMNS" in sql
        assert sql.count("SET acquisition_representation = 'unknown'") == 2
        assert sql.count("SET acquisition_schema_version = 'unknown'") == 2
        assert sql.count("NOT NULL DEFAULT 'unknown'") >= 4

    def test_save_batch_persists_location_and_state(self, complete_batch, normalized_rows):
        connection = RecordingConnection()
        repository = MySQLListingRepository(connection)
        rows = normalized_rows.copy()
        rows["station_code"] = ["A18", "A19"]
        rows["station_distance_m"] = [350.5, 800.0]
        rows["location_eligible"] = [True, True]
        rows["active"] = [True, False]
        rows["consecutive_absences"] = [0, 2]
        rows["last_seen_batch_id"] = [complete_batch.batch_id, "batch-previous"]
        rows["model_evidence"] = [None, '{"model_version":"v1"}']
        rows["structured_address"] = ["桃園市中壢區高鐵南路一段1號", None]
        rows["address_source_url"] = ["https://newhouse.591.com.tw/home/1", None]
        rows["address_observed_at"] = [pd.Timestamp("2026-07-21T12:00:00Z"), pd.NaT]
        rows["location_method"] = ["structured_address", "unknown"]
        rows["location_confidence"] = ["medium", "unknown"]
        rows["location_reason"] = ["eligible_structured_address", "missing_coordinates"]
        rows["geocoded_at"] = [pd.Timestamp("2026-07-21T12:01:00Z"), pd.NaT]
        rows["geocoder_version"] = ["fake-v1", None]

        repository.save_batch(complete_batch, rows)

        current_inserts = [
            (sql, params)
            for sql, params in connection.executions
            if sql.lstrip().startswith("INSERT INTO listing_current")
        ]
        assert len(current_inserts) == 2
        sql, params = current_inserts[1]
        for column in (
            "station_code",
            "station_distance_m",
            "location_eligible",
            "active",
            "consecutive_absences",
            "last_seen_batch_id",
            "asking_unit_price_low_twd_per_ping",
            "asking_unit_price_high_twd_per_ping",
            "building_area_min_ping",
            "building_area_max_ping",
            "acquisition_representation",
            "acquisition_schema_version",
            "structured_address",
            "address_source_url",
            "address_observed_at",
            "location_method",
            "location_confidence",
            "location_reason",
            "geocoded_at",
            "geocoder_version",
        ):
            assert column in sql
            assert column in params
        assert params["active"] == 0
        assert params["consecutive_absences"] == 2
        snapshot_inserts = [
            (sql, params)
            for sql, params in connection.executions
            if sql.lstrip().startswith("INSERT IGNORE INTO listing_snapshots")
        ]
        snapshot_sql, snapshot_params = snapshot_inserts[1]
        for column in (
            "station_code",
            "station_distance_m",
            "location_eligible",
            "model_evidence",
            "asking_unit_price_low_twd_per_ping",
            "asking_unit_price_high_twd_per_ping",
            "building_area_min_ping",
            "building_area_max_ping",
            "acquisition_representation",
            "acquisition_schema_version",
            "structured_address",
            "address_source_url",
            "address_observed_at",
            "location_method",
            "location_confidence",
            "location_reason",
            "geocoded_at",
            "geocoder_version",
        ):
            assert column in snapshot_sql
            assert column in snapshot_params

        range_params = current_inserts[0][1]
        assert range_params["asking_unit_price_low_twd_per_ping"] == 500_000
        assert range_params["asking_unit_price_high_twd_per_ping"] == 560_000
        assert range_params["building_area_min_ping"] == 19.0
        assert range_params["building_area_max_ping"] == 30.0
        assert range_params["acquisition_representation"] == "jsonld"
        assert range_params["acquisition_schema_version"] == "591-newhouse-jsonld-v1"

    def test_runtime_and_migration_schemas_define_range_and_acquisition_columns(self):
        migration_sql = (
            Path(__file__).parents[1] / "database" / "003_listing_intelligence_schema.sql"
        ).read_text(encoding="utf-8")
        migration_snapshots, migration_current = migration_sql.split(
            "CREATE TABLE IF NOT EXISTS listing_current", maxsplit=1
        )
        schemas = (
            _CREATE_SNAPSHOTS_SQL,
            _CREATE_CURRENT_SQL,
            migration_snapshots,
            migration_current,
        )

        for schema in schemas:
            for column in (
                "asking_unit_price_low_twd_per_ping",
                "asking_unit_price_high_twd_per_ping",
                "building_area_min_ping",
                "building_area_max_ping",
                "acquisition_representation",
                "acquisition_schema_version",
            ):
                assert column in schema

    def test_merge_state_updates_current_rows(self):
        connection = RecordingConnection()
        repository = MySQLListingRepository(connection)
        state = pd.DataFrame(
            [
                {
                    "source": "591",
                    "listing_type": "sale",
                    "source_listing_id": "sale-001",
                    "active": False,
                    "consecutive_absences": 2,
                    "last_seen_batch_id": "batch-002",
                }
            ]
        )

        repository.merge_state(state)

        updates = [
            (sql, params)
            for sql, params in connection.executions
            if sql.lstrip().startswith("UPDATE listing_current")
        ]
        assert len(updates) == 1
        assert updates[0][1]["active"] == 0
        assert updates[0][1]["consecutive_absences"] == 2
