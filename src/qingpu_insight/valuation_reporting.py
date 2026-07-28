import json
from pathlib import Path
from typing import Any

import numpy as np

from qingpu_insight.model_training import (
    ModelExperiment,
    TimeSplit,
    leakage_audit,
)
from qingpu_insight.model_tuning import TrainingProfile
from qingpu_insight.valuation import ValuationBundle


def compute_interval_summary(
    bundle: ValuationBundle,
    evaluated: Any,
    split: TimeSplit,
) -> dict[str, float]:
    test_pred = evaluated.estimator.predict(
        split.test[list(bundle.feature_columns)]
    )
    actual = split.test["target_unit_price_twd"].to_numpy()
    radius = bundle.interval_abs_residual_twd_per_ping
    lows = np.maximum(0, test_pred - radius)
    highs = test_pred + radius
    return {
        "test_coverage": float(((actual >= lows) & (actual <= highs)).mean()),
        "average_interval_width_twd_per_ping": float(np.mean(highs - lows)),
    }


def write_evaluation(
    bundle: ValuationBundle,
    experiment: ModelExperiment,
    split: TimeSplit,
    report_dir: Path,
    selected_profile: TrainingProfile | None = None,
    diagnostics: dict[str, object] | None = None,
    feature_experiments: list[dict[str, object]] | None = None,
    backtests: list[dict[str, object]] | None = None,
    release_checks: dict[str, bool] | None = None,
    reason_codes: list[str] | None = None,
    automl_info: dict[str, object] | None = None,
) -> Path:
    leakage = leakage_audit(split)
    selected_name = getattr(
        experiment,
        "selected_name",
        getattr(experiment, "selected_model", bundle.model_name),
    )
    evaluated = experiment.final_test_results[selected_name]
    interval_summary = compute_interval_summary(bundle, evaluated, split)

    policy_counts = split.train["target_policy"].value_counts().to_dict()

    selection_metrics: dict[str, dict[str, object]] = {}
    if hasattr(experiment, "selection_results"):
        for c in experiment.selection_results:
            selection_metrics[c.name] = c.metrics.to_dict(orient="index")
    else:
        for profile_eval in experiment.profile_results:
            for candidate in profile_eval.candidates:
                selection_metrics[
                    f"{profile_eval.profile.name}:{candidate.evaluation.name}"
                ] = (
                    candidate.evaluation.metrics.to_dict(orient="index")
                )

    final_test_metrics: dict[str, dict[str, object]] = {}
    for name, c in experiment.final_test_results.items():
        final_test_metrics[name] = c.metrics.to_dict(orient="index")

    report = {
        "transaction_type": bundle.transaction_type,
        "model_version": bundle.model_version,
        "selected_model": bundle.model_name,
        "selection_metrics": selection_metrics,
        "final_test_metrics": final_test_metrics,
        "recommendation": {
            "status": (
                "recommended"
                if release_checks
                and release_checks.get("recommended", False)
                or not release_checks
                and experiment.recommended
                else "not_recommended"
            ),
            "reason_codes": (
                reason_codes if reason_codes is not None else list(experiment.reason_codes)
            ),
        },
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
        "calibration_quantile_twd_per_ping": (
            bundle.interval_abs_residual_twd_per_ping
        ),
        "test_coverage": round(interval_summary["test_coverage"], 4),
        "average_interval_width_twd_per_ping": round(
            interval_summary["average_interval_width_twd_per_ping"],
            2,
        ),
        "feature_ranges": bundle.feature_ranges,
        "data_date": bundle.data_max_date,
    }
    policy = bundle.parking_price_policy
    if policy is not None:
        report["parking_policy"] = {
            "version": int(policy.version),
            "minimum_type_samples": int(policy.minimum_type_samples),
            "by_type": {
                k: {"price_twd": int(v.price_twd), "sample_size": int(v.sample_size)}
                for k, v in policy.by_type.items()
            },
            "market_fallback": (
                {
                    "price_twd": int(policy.market_fallback.price_twd),
                    "sample_size": int(policy.market_fallback.sample_size),
                }
                if policy.market_fallback else None
            ),
        }
    if diagnostics is not None:
        report["diagnostics"] = diagnostics
    if feature_experiments is not None:
        report["feature_experiments"] = feature_experiments
    if backtests is not None:
        report["backtests"] = backtests
    if release_checks is not None:
        report["release_checks"] = release_checks

    if selected_profile is not None:
        report["selected_profile"] = selected_profile.name
        if hasattr(experiment, "profile_results"):
            report["profile_results"] = {
                profile_eval.profile.name: {
                    "parameters": profile_eval.profile.snapshot(),
                    "selection_metrics": {
                        candidate.model_name: (
                            candidate.evaluation.metrics.to_dict(orient="index")
                        )
                        for candidate in profile_eval.candidates
                    },
                    "candidate_errors": profile_eval.candidate_errors,
                }
                for profile_eval in experiment.profile_results
            }
        else:
            report["profile_results"] = {
                selected_profile.name: {
                    "parameters": selected_profile.snapshot(),
                },
            }
        if (
            selected_profile.recency_half_life_months is not None
            and bundle.transaction_type == "resale"
        ):
            report["recency_weighting"] = {
                "half_life_months": selected_profile.recency_half_life_months,
            }

    if automl_info is not None:
        report["automl_info"] = automl_info

    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{bundle.transaction_type}-evaluation.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_model_card(
    bundle: ValuationBundle,
    experiment: ModelExperiment,
    leakage: dict[str, Any],
    report_dir: Path,
    selected_profile: TrainingProfile | None = None,
    feature_experiments: list[dict[str, object]] | None = None,
    backtests: list[dict[str, object]] | None = None,
    release_checks: dict[str, bool] | None = None,
    reason_codes: list[str] | None = None,
    automl_info: dict[str, object] | None = None,
) -> Path:
    lines = [
        f"# {bundle.transaction_type} 估價模型卡",
        "",
        "## 資料期間",
        f"- {bundle.data_min_date} 至 {bundle.data_max_date}",
        "",
    ]

    _policy = bundle.parking_price_policy
    if _policy is not None:
        lines.append("## 車位估值政策")
        lines.append(f"- 政策版本：{_policy.version}")
        lines.append("- 房屋模型不包含車位特徵")
        for pt, stat in _policy.by_type.items():
            lines.append(f"- {pt}：{stat.price_twd:,} 元（樣本數 {stat.sample_size}）")
        if _policy.market_fallback:
            lines.append(
                f"- 市場中位數：{_policy.market_fallback.price_twd:,} 元"
                f"（樣本數 {_policy.market_fallback.sample_size}）"
            )
        lines.append("")

    lines.extend([
        "## 時間切割",
        "- 訓練集、校準集與測試集依交易日期時間順序切割",
        "",
        "## 候選模型",
    ])

    if hasattr(experiment, "selection_results"):
        candidates = experiment.selection_results
    else:
        candidates = [
            c.evaluation
            for pe in experiment.profile_results
            for c in pe.candidates
        ]
    for c in candidates:
        marker = " ✓" if c.name == bundle.model_name else ""
        lines.append(f"- {c.name}：MAE = {c.overall_mae:,.0f}{marker}")

    if bundle.transaction_type == "resale":
        lines.extend(
            [
                "",
                "## 近期資料加權",
                (
                    "- 半衰期 "
                    f"{selected_profile.recency_half_life_months if selected_profile else 48} "
                    "個月，最低權重 0.10；評估指標不加權。"
                ),
                "",
                "## 特徵實驗與消融",
            ]
        )
        for item in feature_experiments or []:
            metrics = item.get("metrics", {})
            overall = metrics.get("overall", {}) if isinstance(metrics, dict) else {}
            lines.append(
                f"- {item.get('name', 'unknown')}："
                f"{item.get('selected_model') or '無'}，"
                f"MAE = {overall.get('mae', 'N/A')}"
            )

        lines.extend(["", "## 三期時間回測"])
        for backtest in backtests or []:
            candidate = backtest.get("candidate_metrics", {})
            overall = candidate.get("overall", {}) if isinstance(candidate, dict) else {}
            lines.append(
                f"- {backtest.get('cutoff_date', 'unknown')}："
                f"MAE = {overall.get('mae', 'N/A')}，"
                f"{'通過' if backtest.get('passed') else '未通過'}"
            )

        lines.extend(["", "## 發布檢查"])
        for check, passed in (release_checks or {}).items():
            lines.append(f"- {check}：{'通過' if passed else '未通過'}")
        if reason_codes:
            lines.append(f"- 保留原因：{', '.join(reason_codes)}")

    lines.extend(
        [
            "",
            "## 分群誤差",
        ]
    )

    for group_name, group_metrics in bundle.metrics.items():
        mae = group_metrics.get("mae", "N/A")
        mape = group_metrics.get("mape", "N/A")
        count = group_metrics.get("count", 0)
        lines.append(f"- {group_name}：MAE = {mae}，MAPE = {mape}%，n = {count}")

    lines.extend(
        [
            "",
            "## 區間覆蓋率",
            f"- 校準分位數：{bundle.interval_abs_residual_twd_per_ping:,.0f} 元/坪",
            "- 測試集覆蓋率依校準分位數計算",
            "",
            "## 限制",
        ]
    )

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

    lines.extend(
        [
            "",
            "## 不適用情境",
            "- 輸入數值超出特徵訓練範圍",
            "- 交易類型與模型類型不符",
            "- 缺乏附近站點近期交易資料",
            "",
        ]
    )

    if (
        bundle.transaction_type == "resale"
        and selected_profile is not None
        and selected_profile.recency_half_life_months is not None
    ):
        lines.append("## 近期交易權重")
        lines.append(
            f"- 近期交易加權半衰期：{selected_profile.recency_half_life_months} 個月"
        )
        lines.append("")

    if automl_info is not None:
        lines.append("## AutoML 搜尋")
        budget_name = automl_info.get("budget_name", "unknown")
        completed_trials = automl_info.get("completed_trials", 0)
        selected_trial = automl_info.get("selected_trial_number")
        fit_spec = automl_info.get("fit_spec")
        blockers = automl_info.get("release_blockers", [])
        lines.append(f"- 預算方案：{budget_name}")
        lines.append(f"- 完成試驗數：{completed_trials} 次")
        if selected_trial is not None:
            lines.append(f"- 入選試驗編號：{selected_trial}")
        else:
            lines.append("- 入選試驗：無")
        if fit_spec:
            model_name = fit_spec.get("model_name", "unknown")
            params = fit_spec.get("parameters", {})
            lines.append(f"- 入選模型：{model_name}")
            for k, v in params.items():
                lines.append(f"  - {k}: {v}")
        if blockers:
            lines.append(f"- 釋出阻斷：{', '.join(blockers)}")
        lines.append("")

    lines.extend(
        [
            "## 版本狀態",
            "- 此版本為未發布候選模型，不會替換網站正式估價模型。",
            "",
        ]
    )

    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{bundle.transaction_type}-model-card.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
