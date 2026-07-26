import pandas as pd
import pytest

from qingpu_insight.official_data import replace_market_rows


def market_frame(n: int) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append(
            {
                "transaction_key": f"TK{i}",
                "transaction_type": "resale",
                "record_id": f"R{i}",
                "station_code": "A18",
                "transaction_date": pd.Timestamp("2025-01-01"),
                "building_area_sqm": 100.0,
                "building_area_ping": 30.25,
                "unit_price_sqm_twd": 200000.0,
                "unit_price_per_ping_twd": 600000.0,
                "total_price_twd": 18000000,
                "building_type": "住宅大樓",
                "bedrooms": 3,
                "living_rooms": 2,
                "bathrooms": 2,
                "building_age_years": 5.0,
                "station_distance_m": 500.0,
                "longitude": 121.2,
                "latitude": 25.0,
                "match_quality": "exact",
                "source_file": "test.csv",
                "floor": "5層",
                "total_floors": "15",
                "parking_type": "坡道平面",
                "parking_area_sqm": 10.0,
                "parking_price_twd": 2000000,
                "analysis_eligible": True,
            }
        )
    return pd.DataFrame(rows)


class FailingConnection:
    def __init__(self, fail_on_batch: int):
        self.fail_on_batch = fail_on_batch
        self.commits = 0
        self.rollbacks = 0
        self._batch_count = 0

    def cursor(self):
        return _FailingCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class _FailingCursor:
    def __init__(self, connection: FailingConnection):
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        pass

    def executemany(self, sql, rows):
        self._connection._batch_count += 1
        if self._connection._batch_count >= self._connection.fail_on_batch:
            raise RuntimeError(
                f"batch {self._connection.fail_on_batch} failed"
            )

    def close(self):
        pass


class RecordingConnection:
    def __init__(self):
        self.operation_names: list[str] = []

    def cursor(self):
        return _RecordingCursor(self)

    def commit(self):
        self.operation_names.append("commit")

    def rollback(self):
        pass

    def close(self):
        pass


class _RecordingCursor:
    def __init__(self, connection: RecordingConnection):
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        sql_upper = sql.strip().upper()
        if sql_upper.startswith("DELETE"):
            self._connection.operation_names.append("delete_market_rows")
        elif "DATA_REFRESHES" in sql_upper:
            self._connection.operation_names.append("insert_refresh")

    def executemany(self, sql, rows):
        self._connection.operation_names.append("insert_market_rows")

    def close(self):
        pass


def test_replace_market_rows_rolls_back_delete_and_insert_together():
    connection = FailingConnection(fail_on_batch=2)
    with pytest.raises(RuntimeError):
        replace_market_rows(connection, market_frame(1500), "v-test")
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_replace_market_rows_records_refresh_only_after_rows():
    connection = RecordingConnection()
    count = replace_market_rows(connection, market_frame(2), "v-test")
    assert count == 2
    assert connection.operation_names == [
        "delete_market_rows",
        "insert_market_rows",
        "insert_refresh",
        "commit",
    ]
