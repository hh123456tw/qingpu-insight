import pandas as pd

from qingpu_insight.model_training import (
    candidate_estimators,
    evaluate_candidate,
    TimeSplit,
)
from qingpu_insight.model_tuning import TrainingProfile, BALANCED_PROFILE


def run_annual_backtests(
    frame: pd.DataFrame,
    selected_model_name: str,
    profile: TrainingProfile = BALANCED_PROFILE,
) -> dict:
    candidate_est = candidate_estimators(profile=profile)[selected_model_name]

    frame = frame.sort_values("transaction_date")
    years = sorted(frame["transaction_date"].dt.year.unique())

    annual_results: dict[str, dict] = {}
    for year in years:
        test = frame[frame["transaction_date"].dt.year == year]
        train = frame[
            frame["transaction_date"] < pd.Timestamp(year=year, month=1, day=1)
        ]
        if len(train) < 100 or len(test) < 30:
            continue
        evaluation = evaluate_candidate(
            name=selected_model_name,
            estimator=candidate_est,
            train_frame=train,
            evaluation_frame=test,
            use_recency_weights=profile.recency_half_life_months is not None,
            recency_half_life_months=profile.recency_half_life_months or 48,
        )
        annual_results[str(year)] = {
            "mae": evaluation.overall_mae,
            "metrics": evaluation.metrics.to_dict(orient="index"),
        }

    return annual_results
