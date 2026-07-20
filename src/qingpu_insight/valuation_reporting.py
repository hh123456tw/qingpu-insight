import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qingpu_insight.model_features import FEATURE_COLUMNS
from qingpu_insight.model_training import CandidateEvaluation, TimeSplit, leakage_audit
from qingpu_insight.valuation import ValuationBundle


def write_evaluation(
    bundle: ValuationBundle,
    candidates: list[CandidateEvaluation],
    split: TimeSplit,
    report_dir: Path,
) -> Path:
    leakage = leakage_audit(split)
    selected = next(c for c in candidates if c.name == bundle.model_name)

    test_pred = bundle.pipeline.predict(split.test[list(FEATURE_COLUMNS)])
    test_actual = split.test["target_unit_price_twd"].values
    radius = bundle.interval_abs_residual_twd_per_ping
    lows = np.maximum(0, test_pred - radius)
    highs = test_pred + radius
    covered = (test_actual >= lows) & (test_actual <= highs)
    test_coverage = float(covered.mean())
    avg_interval_width = float(np.mean(highs - lows))

    policy_counts = split.train["target_policy"].value_counts().to_dict()

    candidate_metrics = {}
    for c in candidates:
        candidate_metrics[c.name] = {
            "overall_mae": c.overall_mae,
            "station_mape": c.station_mape,
            "metrics": c.metrics.to_dict(),
        }

    report = {
        "transaction_type": bundle.transaction_type,
        "model_version": bundle.model_version,
        "selected_model": bundle.model_name,
        "candidates": candidate_metrics,
        "grouped_metrics": bundle.metrics,
        "split": {
            "train_start": str(split.train["transaction_date"].min().date()),
            "train_end": str(split.train["transaction_date"].max().date()),
            "calibration_start": str(split.calibration["transaction_date"].min().date()),
            "calibration_end": str(split.calibration["transaction_date"].max().date()),
            "test_start": str(split.test["transaction_date"].min().date()),
            "test_end": str(split.test["transaction_date"].max().date()),
            "train_count": len(split.train),
            "calibration_count": len(split.calibration),
            "test_count": len(split.test),
        },
        "leakage_audit": leakage,
        "target_policy_counts": policy_counts,
        "calibration_quantile_twd_per_ping": radius,
        "test_coverage": round(test_coverage, 4),
        "average_interval_width_twd_per_ping": round(avg_interval_width, 2),
        "feature_ranges": bundle.feature_ranges,
        "data_date": bundle.data_max_date,
    }

    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{bundle.transaction_type}-evaluation.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_model_card(
    bundle: ValuationBundle,
    candidates: list[CandidateEvaluation],
    leakage: dict[str, Any],
    report_dir: Path,
) -> Path:
    lines = [
        f"# {bundle.transaction_type} 估價模型卡",
        "",
        "## 資料期間",
        f"- {bundle.data_min_date} 至 {bundle.data_max_date}",
        "",
        "## 時間切割",
        "- 訓練集、校準集與測試集依交易日期時間順序切割",
        "",
        "## 候選模型",
    ]

    for c in candidates:
        marker = " ✓" if c.name == bundle.model_name else ""
        lines.append(f"- {c.name}：MAE = {c.overall_mae:,.0f}{marker}")

    lines.extend([
        "",
        "## 分群誤差",
    ])

    for group_name, group_metrics in bundle.metrics.items():
        mae = group_metrics.get("mae", "N/A")
        mape = group_metrics.get("mape", "N/A")
        count = group_metrics.get("count", 0)
        lines.append(f"- {group_name}：MAE = {mae}，MAPE = {mape}%，n = {count}")

    lines.extend([
        "",
        "## 區間覆蓋率",
        f"- 校準分位數：{bundle.interval_abs_residual_twd_per_ping:,.0f} 元/坪",
        "- 測試集覆蓋率依校準分位數計算",
        "",
        "## 限制",
    ])

    has_leakage_flag = False
    if leakage.get("target_in_features"):
        lines.append("- 目標變數出現在特徵欄位中（可能導致資料洩漏）")
        has_leakage_flag = True
    if leakage.get("transaction_key_overlap"):
        lines.append("- 交易鍵存在訓練/測試重疊（資料洩漏風險）")
        has_leakage_flag = True
    road_overlap = leakage.get("road_group_overlap_count", 0)
    if road_overlap > 0:
        lines.append(f"- 路段群組重疊：{road_overlap} 筆")
        has_leakage_flag = True
    if not has_leakage_flag:
        lines.append("- 無重大資料洩漏")

    lines.extend([
        "",
        "## 不適用情境",
        "- 輸入數值超出特徵訓練範圍",
        "- 交易類型與模型類型不符",
        "- 缺乏附近站點近期交易資料",
        "",
    ])

    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{bundle.transaction_type}-model-card.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
