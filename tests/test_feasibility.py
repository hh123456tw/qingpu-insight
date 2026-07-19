from pathlib import Path

import pandas as pd

from qingpu_insight.config import Thresholds
from qingpu_insight.feasibility import evaluate_feasibility
from qingpu_insight.reporting import write_report


def sample_frame(rows_per_cell: int) -> pd.DataFrame:
    rows = []
    for transaction_type in ("resale", "presale"):
        for station_code in ("A17", "A18", "A19"):
            for index in range(rows_per_cell):
                rows.append(
                    {
                        "transaction_type": transaction_type,
                        "station_code": station_code,
                        "transaction_date": pd.Timestamp("2026-01-01"),
                        "coordinate_eligible": True,
                        "match_quality": "exact",
                        "total_price_twd": 10_000_000 + index,
                    }
                )
    return pd.DataFrame(rows)


def test_feasibility_passes_when_all_thresholds_pass() -> None:
    thresholds = Thresholds(
        minimum_total_by_type=100,
        minimum_station_type_cell=20,
        minimum_coordinate_coverage=0.60,
        minimum_recent_by_type=100,
    )
    result = evaluate_feasibility(sample_frame(60), thresholds)

    assert result.decision == "GO"
    assert result.failed_checks == ()


def test_feasibility_reports_failed_station_cell() -> None:
    result = evaluate_feasibility(sample_frame(10), Thresholds())

    assert result.decision == "NO-GO"
    assert "minimum_station_type_cell" in result.failed_checks


def test_write_report_creates_markdown_and_csv(tmp_path: Path) -> None:
    result = evaluate_feasibility(sample_frame(60), Thresholds(100, 20, 0.60, 100))

    markdown, csv = write_report(result, tmp_path, ["https://data.gov.tw/dataset/77051"])

    assert "# 青埔智價 M0 資料可行性報告" in markdown.read_text(encoding="utf-8")
    assert "GO" in markdown.read_text(encoding="utf-8")
    assert csv.exists()
