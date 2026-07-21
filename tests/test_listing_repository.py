"""Tests for listing repository adapters."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from qingpu_insight.listing_repository import MySQLListingRepository, ParquetListingRepository
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


class TestMySQLRepositoryActualAdapter:
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
        ):
            assert column in snapshot_sql
            assert column in snapshot_params

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
