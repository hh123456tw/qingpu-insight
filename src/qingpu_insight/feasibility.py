from dataclasses import dataclass

import pandas as pd

from qingpu_insight.config import Thresholds


@dataclass(frozen=True)
class FeasibilityResult:
    decision: str
    failed_checks: tuple[str, ...]
    summary: pd.DataFrame
    coordinate_coverage: float
    latest_date: pd.Timestamp
    recent_cutoff: pd.Timestamp


def evaluate_feasibility(frame: pd.DataFrame, thresholds: Thresholds) -> FeasibilityResult:
    if frame.empty:
        raise ValueError("cannot evaluate an empty transaction frame")
    latest_date = frame["transaction_date"].max()
    if pd.isna(latest_date):
        raise ValueError("transaction frame has no valid transaction dates")
    recent_cutoff = latest_date - pd.DateOffset(months=24)
    eligible = frame["coordinate_eligible"].fillna(False)
    coordinate_coverage = float(eligible.mean())
    assigned = frame[frame["station_code"].notna()].copy()
    summary = (
        assigned.groupby(["transaction_type", "station_code"], observed=True)
        .agg(
            assigned_records=("station_code", "size"),
            first_date=("transaction_date", "min"),
            last_date=("transaction_date", "max"),
            median_total_price_twd=("total_price_twd", "median"),
        )
        .reset_index()
    )
    total_by_type = assigned.groupby("transaction_type").size()
    recent_by_type = assigned[assigned["transaction_date"] >= recent_cutoff].groupby(
        "transaction_type"
    ).size()
    expected_types = {"resale", "presale"}
    failed: list[str] = []
    if any(
        total_by_type.get(kind, 0) < thresholds.minimum_total_by_type
        for kind in expected_types
    ):
        failed.append("minimum_total_by_type")
    expected_cells = {
        (kind, station) for kind in expected_types for station in ("A17", "A18", "A19")
    }
    actual_cells = {
        (row.transaction_type, row.station_code): row.assigned_records
        for row in summary.itertuples(index=False)
    }
    if any(
        actual_cells.get(cell, 0) < thresholds.minimum_station_type_cell
        for cell in expected_cells
    ):
        failed.append("minimum_station_type_cell")
    if coordinate_coverage < thresholds.minimum_coordinate_coverage:
        failed.append("minimum_coordinate_coverage")
    if any(
        recent_by_type.get(kind, 0) < thresholds.minimum_recent_by_type
        for kind in expected_types
    ):
        failed.append("minimum_recent_by_type")
    return FeasibilityResult(
        decision="GO" if not failed else "NO-GO",
        failed_checks=tuple(failed),
        summary=summary,
        coordinate_coverage=coordinate_coverage,
        latest_date=latest_date,
        recent_cutoff=recent_cutoff,
    )
