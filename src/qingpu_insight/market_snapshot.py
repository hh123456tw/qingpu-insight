"""Reuse the valuation model frame until the market data changes.

Every valuation needs the full market table turned into the model frame, which is far
more work than the valuation itself. :class:`ModelFrameCache` keeps one frame per
transaction type and rebuilds it only when the data version changes:

* a data source may expose ``data_version(transaction_type)``, a cheap hashable token
  (the Parquet file's size and mtime, or a MySQL count/max-date aggregate), checked
  before loading anything;
* otherwise the table is loaded and fingerprinted by row count and latest transaction
  date, which still skips rebuilding the model frame.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from threading import Lock

import pandas as pd

from qingpu_insight.market_metrics import MarketFilters
from qingpu_insight.market_repository import MarketDataSource
from qingpu_insight.model_features import build_model_frame


@dataclass(frozen=True)
class MarketSnapshot:
    """A read-only model frame for one transaction type and data version.

    Callers must not modify ``model_frame`` in place; it is shared between requests.
    """

    transaction_type: str
    version: Hashable
    row_count: int
    latest_data_date: pd.Timestamp | None
    model_frame: pd.DataFrame


def _fingerprint(market: pd.DataFrame) -> tuple[str, int, str | None]:
    if market.empty:
        return ("rows", 0, None)
    return ("rows", len(market), str(pd.Timestamp(market["transaction_date"].max())))


class ModelFrameCache:
    def __init__(
        self,
        data_source: MarketDataSource,
        *,
        builder: Callable[[pd.DataFrame, str], pd.DataFrame] = build_model_frame,
    ) -> None:
        self._source = data_source
        self._builder = builder
        self._lock = Lock()
        self._snapshots: dict[str, MarketSnapshot] = {}

    def _source_version(self, transaction_type: str) -> Hashable | None:
        version = getattr(self._source, "data_version", None)
        return version(transaction_type) if callable(version) else None

    def snapshot(self, transaction_type: str) -> MarketSnapshot:
        with self._lock:
            cached = self._snapshots.get(transaction_type)
            version = self._source_version(transaction_type)
            if version is not None and cached is not None and cached.version == version:
                return cached
            market = self._source.load(MarketFilters(transaction_type=transaction_type))
            if version is None:
                version = _fingerprint(market)
                if cached is not None and cached.version == version:
                    return cached
            snapshot = MarketSnapshot(
                transaction_type=transaction_type,
                version=version,
                row_count=len(market),
                latest_data_date=(
                    pd.Timestamp(market["transaction_date"].max()) if not market.empty else None
                ),
                model_frame=self._builder(market, transaction_type),
            )
            self._snapshots[transaction_type] = snapshot
            return snapshot
