from collections.abc import Iterable
from pathlib import Path

from qingpu_insight.feasibility import FeasibilityResult

CHECK_LABELS = {
    "minimum_total_by_type": "中古屋或預售屋的總筆數不足",
    "minimum_station_type_cell": "至少一個站點／交易類型組合不足 50 筆",
    "minimum_coordinate_coverage": "可用座標覆蓋率低於 60%",
    "minimum_recent_by_type": "最近 24 個月的中古屋或預售屋不足 100 筆",
}


_RULE = (
    "只有在中古屋與預售屋總筆數、各站點／類型筆數、座標覆蓋率"
    "及最近 24 個月筆數全部通過時，結果才是 GO。"
    "NO-GO 代表先修正資料範圍或定位方法，不進入模型與網站實作。"
)


def render_markdown(result: FeasibilityResult, sources: Iterable[str]) -> str:
    failures = (
        "\n".join(f"- {CHECK_LABELS[item]} (`{item}`)" for item in result.failed_checks)
        if result.failed_checks
        else "- 所有 M0 門檻均通過。"
    )
    source_lines = "\n".join(f"- {source}" for source in sources)
    table = (
        result.summary.to_markdown(index=False)
        if not result.summary.empty
        else "無可歸屬紀錄。"
    )
    return f"""# 青埔智價 M0 資料可行性報告

## 結論

**{result.decision}**

## 品質摘要

- 可用座標覆蓋率：{result.coordinate_coverage:.1%}
- 最新交易日期：{result.latest_date.date().isoformat()}
- 最近資料門檻起日：{result.recent_cutoff.date().isoformat()}

{failures}

## A17～A19 可用紀錄

{table}

## 官方來源

{source_lines}

## 決策規則

{_RULE}
"""


def write_report(
    result: FeasibilityResult,
    report_dir: Path,
    sources: Iterable[str],
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = report_dir / "m0-data-feasibility.md"
    csv_path = report_dir / "m0-station-summary.csv"
    markdown_path.write_text(render_markdown(result, sources), encoding="utf-8")
    result.summary.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return markdown_path, csv_path
