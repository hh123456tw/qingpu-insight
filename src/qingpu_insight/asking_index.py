"""Asking-price index: how far today's 591 asking prices sit above the model.

Each radar run quick-values every unique property in the list batch. The median of
``ln(asking / estimate)`` over those properties is a mix-adjusted asking-price level:
the model already accounts for location, size, age and floor, so a rising median
means asking prices are moving faster than the transactions the model learned from.

Two things are inside the level: the usual asking premium (sellers ask above what
they settle for) and market drift since the model's training data. Only *changes*
within one model version are drift; a model release resets the baseline, so the
series is compared within ``model_version`` only. Using the drift to correct the
valuation model waits until the series spans a few months and passes the
pre-registered backtest protocol (docs/m2-valuation-methodology.md).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

INDEX_FILE = "asking_index.parquet"
MIN_PROPERTIES = 30
STATIONS = ("A17", "A18", "A19")
INDEX_COLUMNS = (
    "radar_batch_id",
    "observed_at",
    "model_version",
    "station",
    "properties",
    "median_log_ratio",
    "p25_log_ratio",
    "p75_log_ratio",
)


def _log_ratio(record: Mapping[str, Any]) -> float | None:
    try:
        asking = float(record.get("asking_price_twd"))  # type: ignore[arg-type]
        estimate = float(record.get("prescreen_estimate_twd"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(asking) and math.isfinite(estimate)) or asking <= 0 or estimate <= 0:
        return None
    return math.log(asking / estimate)


def asking_ratio_rows(
    records: Iterable[Mapping[str, Any]],
    *,
    radar_batch_id: str,
    observed_at: str,
    model_version: str | None,
) -> list[dict[str, Any]]:
    """Index rows (all stations, then each station) for one radar run.

    Only property representatives count, so a flat listed by twenty agents weighs once.
    """
    by_station: dict[str, list[float]] = {"all": []}
    for record in records:
        if record.get("status") == "duplicate":
            continue
        ratio = _log_ratio(record)
        if ratio is None:
            continue
        by_station["all"].append(ratio)
        station = record.get("station_code")
        if station in STATIONS:
            by_station.setdefault(str(station), []).append(ratio)
    rows = []
    for station in ("all", *STATIONS):
        values = pd.Series(by_station.get(station, []), dtype="float64")
        if len(values) < MIN_PROPERTIES:
            continue
        rows.append(
            {
                "radar_batch_id": radar_batch_id,
                "observed_at": observed_at,
                "model_version": model_version or "unknown",
                "station": station,
                "properties": int(len(values)),
                "median_log_ratio": float(values.median()),
                "p25_log_ratio": float(values.quantile(0.25)),
                "p75_log_ratio": float(values.quantile(0.75)),
            }
        )
    return rows


def public_index_row(row: Mapping[str, Any]) -> dict[str, Any]:
    median = float(row["median_log_ratio"])
    return {
        "station": row["station"],
        "properties": int(row["properties"]),
        "median_ratio": round(math.exp(median), 4),
        "p25_ratio": round(math.exp(float(row["p25_log_ratio"])), 4),
        "p75_ratio": round(math.exp(float(row["p75_log_ratio"])), 4),
    }


class AskingIndexStore:
    def __init__(self, base_dir: Path) -> None:
        self.path = Path(base_dir) / INDEX_FILE

    def load(self) -> pd.DataFrame:
        try:
            return pd.read_parquet(self.path)
        except (FileNotFoundError, OSError, ValueError):
            return pd.DataFrame(columns=list(INDEX_COLUMNS))

    def append(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        """Add one run's rows, replacing any earlier rows of the same radar batch."""
        frame = self.load()
        if rows:
            batch_ids = {row["radar_batch_id"] for row in rows}
            frame = frame[~frame["radar_batch_id"].isin(batch_ids)]
            new = pd.DataFrame(rows, columns=list(INDEX_COLUMNS))
            frame = new if frame.empty else pd.concat([frame, new], ignore_index=True)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            frame.to_parquet(temporary, index=False)
            temporary.replace(self.path)
        return frame

    def backfill_from_runs(self, runs_dir: Path) -> pd.DataFrame:
        """Rebuild rows for every stored radar run that has prescreen estimates."""
        rows: list[dict[str, Any]] = []
        for meta_path in sorted(Path(runs_dir).glob("radar-*.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                records = pd.read_parquet(meta_path.with_suffix(".parquet"))
            except (OSError, ValueError):
                continue
            if "prescreen_estimate_twd" not in records:
                continue
            versions = [v for v in meta.get("model_versions") or [] if v and v != "None"]
            rows.extend(
                asking_ratio_rows(
                    records.to_dict("records"),
                    radar_batch_id=str(meta.get("radar_batch_id")),
                    observed_at=str(meta.get("started_at")),
                    model_version=versions[0] if len(versions) == 1 else None,
                )
            )
        return self.append(rows)


def asking_drift(index: pd.DataFrame, *, station: str = "all", min_days: int = 28
                 ) -> dict[str, Any] | None:
    """Change of the median log ratio within the latest model version.

    None until the same model version has rows at least ``min_days`` apart.
    """
    if index.empty:
        return None
    rows = index[index["station"] == station].copy()
    if rows.empty:
        return None
    rows["observed_at"] = pd.to_datetime(rows["observed_at"], utc=True, errors="coerce")
    rows = rows.dropna(subset=["observed_at"]).sort_values("observed_at")
    latest = rows.iloc[-1]
    same = rows[rows["model_version"] == latest["model_version"]]
    span = (latest["observed_at"] - same["observed_at"].iloc[0]).days
    if span < min_days:
        return None
    first = same.iloc[0]
    change = float(latest["median_log_ratio"] - first["median_log_ratio"])
    return {
        "station": station,
        "model_version": latest["model_version"],
        "from": first["observed_at"].isoformat(),
        "to": latest["observed_at"].isoformat(),
        "days": int(span),
        "log_change": change,
        "pct_change": math.exp(change) - 1,
    }
