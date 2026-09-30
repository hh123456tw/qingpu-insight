from __future__ import annotations

import os

import pandas as pd

from qingpu_insight.market_repository import ParquetMarketDataSource
from qingpu_insight.market_snapshot import ModelFrameCache
from qingpu_insight.valuation_store import FileValuationStore


def _market(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "transaction_type": ["resale"] * len(dates),
            "transaction_date": pd.to_datetime(dates),
            "total_price_twd": [10_000_000] * len(dates),
        }
    )


class CountingSource:
    def __init__(self, frame: pd.DataFrame, version=None) -> None:
        self.frame = frame
        self.version = version
        self.loads = 0

    def load(self, filters):
        self.loads += 1
        return self.frame


class VersionedSource(CountingSource):
    def data_version(self, transaction_type: str):
        return self.version


class CountingBuilder:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, frame: pd.DataFrame, transaction_type: str) -> pd.DataFrame:
        self.calls += 1
        return frame.assign(built=self.calls)


def test_model_frame_is_built_once_per_fingerprint() -> None:
    source = CountingSource(_market(["2026-01-05", "2026-03-10"]))
    builder = CountingBuilder()
    cache = ModelFrameCache(source, builder=builder)

    first = cache.snapshot("resale")
    second = cache.snapshot("resale")

    assert second is first
    assert builder.calls == 1
    assert first.row_count == 2
    assert first.latest_data_date == pd.Timestamp("2026-03-10")

    source.frame = _market(["2026-01-05", "2026-03-10", "2026-04-01"])
    third = cache.snapshot("resale")
    assert builder.calls == 2
    assert third.latest_data_date == pd.Timestamp("2026-04-01")


def test_data_version_skips_loading_until_it_changes() -> None:
    source = VersionedSource(_market(["2026-01-05"]), version=("v", 1))
    builder = CountingBuilder()
    cache = ModelFrameCache(source, builder=builder)

    cache.snapshot("resale")
    cache.snapshot("resale")
    assert (source.loads, builder.calls) == (1, 1)

    source.version = ("v", 2)
    cache.snapshot("resale")
    assert (source.loads, builder.calls) == (2, 2)


def test_empty_market_snapshot() -> None:
    cache = ModelFrameCache(CountingSource(pd.DataFrame()), builder=CountingBuilder())
    snapshot = cache.snapshot("resale")
    assert snapshot.row_count == 0
    assert snapshot.latest_data_date is None


def test_parquet_data_version_tracks_the_file(tmp_path) -> None:
    path = tmp_path / "market.parquet"
    source = ParquetMarketDataSource(path)
    assert source.data_version("resale") is None
    _market(["2026-01-05"]).to_parquet(path)
    first = source.data_version("resale")
    assert first == source.data_version("resale")
    _market(["2026-01-05", "2026-02-01"]).to_parquet(path)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert source.data_version("resale") != first


def test_valuation_store_keeps_newest_records(tmp_path) -> None:
    store = FileValuationStore(tmp_path, max_records=3)
    ids = []
    for index in range(5):
        vid = store.save({"index": index})
        path = tmp_path / f"{vid}.json"
        os.utime(path, ns=(index * 1_000_000_000, index * 1_000_000_000))
        ids.append(vid)

    assert sorted(p.name for p in tmp_path.glob("*.json")) == sorted(
        f"{vid}.json" for vid in ids[-3:]
    )
    assert store.get(ids[0]) is None
    assert store.get(ids[-1]) == {"index": 4}


def test_valuation_store_is_unbounded_by_default(tmp_path) -> None:
    store = FileValuationStore(tmp_path)
    for index in range(4):
        store.save({"index": index})
    assert len(list(tmp_path.glob("*.json"))) == 4
