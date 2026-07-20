import pandas as pd
import pytest

from qingpu_insight.model_features import FEATURE_COLUMNS
from qingpu_insight.model_training import CandidateEvaluation
from qingpu_insight.valuation import ValuationBundle
from qingpu_insight.valuation_reporting import write_evaluation, write_model_card


@pytest.fixture
def trained_bundle():
    class FakePipeline:
        def predict(self, X):
            import numpy as np
            return np.full(len(X), 500_000.0)

    return ValuationBundle(
        transaction_type="resale",
        model_name="ridge",
        model_version="resale-2026-06-01-a1b2c3d4",
        pipeline=FakePipeline(),
        interval_abs_residual_twd_per_ping=50_000,
        feature_ranges={
            "building_area_ping": (20, 80),
            "station_distance_m": (100, 1500),
        },
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[{"feature": "station_distance_m", "importance": 0.15}],
        reference_rows=pd.DataFrame({"dummy": [1]}),
        data_min_date="2024-01-01",
        data_max_date="2026-06-01",
        metrics={
            "overall": {"mae": 45000, "mape": 8.5, "rmse": 55000, "r2": 0.72, "count": 200},
            "station:A17": {"mae": 42000, "mape": 7.8, "rmse": 51000, "r2": 0.75, "count": 80},
        },
    )


@pytest.fixture
def candidate_results():
    return [
        CandidateEvaluation(
            name="baseline", estimator=None,
            overall_mae=100_000.0,
            station_mape={"A17": 5.0, "A18": 6.0, "A19": 7.0},
            metrics=pd.DataFrame(),
        ),
        CandidateEvaluation(
            name="ridge", estimator=None,
            overall_mae=90_000.0,
            station_mape={"A17": 4.5, "A18": 5.5, "A19": 6.5},
            metrics=pd.DataFrame(),
        ),
    ]


@pytest.fixture
def leakage():
    return {
        "target_in_features": False,
        "transaction_key_overlap": False,
        "road_group_overlap_count": 0,
    }


def test_model_card_discloses_required_evidence(tmp_path, trained_bundle, candidate_results, leakage):
    path = write_model_card(trained_bundle, candidate_results, leakage, tmp_path)
    text = path.read_text(encoding="utf-8")
    for heading in ("資料期間", "時間切割", "候選模型", "分群誤差", "區間覆蓋率", "限制", "不適用情境"):
        assert heading in text


def test_write_evaluation_creates_json(tmp_path, trained_bundle, candidate_results):
    import json
    import numpy as np

    np.random.seed(42)
    n = 400
    dates = pd.date_range("2022-01-01", periods=n, freq="D")
    train = pd.DataFrame({
        "transaction_date": dates[:200],
        "target_unit_price_twd": np.random.uniform(300_000, 800_000, 200),
        "target_policy": np.random.choice(["split", "official_unit_price"], 200),
        "transaction_key": [f"T{i}" for i in range(200)],
    })
    for col in ("station_code", "building_type", "parking_type", "road_key"):
        train[col] = "A17"

    cal = pd.DataFrame({
        "transaction_date": dates[200:250],
        "target_unit_price_twd": np.random.uniform(300_000, 800_000, 50),
        "target_policy": np.random.choice(["split", "official_unit_price"], 50),
        "transaction_key": [f"T{i}" for i in range(200, 250)],
    })
    for col in ("station_code", "building_type", "parking_type", "road_key"):
        cal[col] = "A17"

    test = pd.DataFrame({
        "transaction_date": dates[250:400],
        "target_unit_price_twd": np.random.uniform(300_000, 800_000, 150),
        "target_policy": np.random.choice(["split", "official_unit_price"], 150),
        "transaction_key": [f"T{i}" for i in range(250, 400)],
    })
    for col in FEATURE_COLUMNS:
        if col not in test.columns:
            test[col] = 0
    for col in ("station_code", "building_type", "parking_type"):
        test[col] = "A17"

    from qingpu_insight.model_training import TimeSplit
    split = TimeSplit(train=train, calibration=cal, test=test)

    path = write_evaluation(trained_bundle, candidate_results, split, tmp_path)
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["transaction_type"] == "resale"
    assert payload["selected_model"] == "ridge"
    assert "candidates" in payload
    assert "leakage_audit" in payload
    assert "test_coverage" in payload
    assert payload["data_date"] == "2026-06-01"

