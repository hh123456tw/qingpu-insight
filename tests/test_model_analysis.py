import pandas as pd
import pytest

from qingpu_insight.model_analysis import run_annual_backtests
from qingpu_insight.model_tuning import TrainingProfile


def test_run_annual_backtests_uses_custom_profile(monkeypatch):
    captured = {}

    def fake_estimators(seed=42, profile=None):
        captured["profile"] = profile
        from sklearn.pipeline import Pipeline
        from sklearn.dummy import DummyRegressor

        return {
            "hist_gradient_boosting": Pipeline(
                [("model", DummyRegressor(strategy="mean"))]
            )
        }

    import qingpu_insight.model_analysis as ma

    monkeypatch.setattr(ma, "candidate_estimators", fake_estimators)

    frame = pd.DataFrame(
        {"transaction_date": pd.date_range("2020-01-01", periods=100)}
    )
    profile = TrainingProfile("custom", "custom", 0.05, 420, 520, 36)
    run_annual_backtests(frame, "hist_gradient_boosting", profile=profile)
    assert captured["profile"] == profile
