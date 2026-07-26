import pandas as pd
import pytest

from qingpu_insight.model_training import (
    ModelExperiment,
    TimeSplit,
    run_model_experiment,
)
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
            "overall": {
                "mae": 45000,
                "mape": 8.5,
                "rmse": 55000,
                "r2": 0.72,
                "count": 200,
            },
            "station:A17": {
                "mae": 42000,
                "mape": 7.8,
                "rmse": 51000,
                "r2": 0.75,
                "count": 80,
            },
        },
    )


def _build_experiment_frame(
    n_train: int, n_cal: int, n_test: int, seed: int = 42
) -> TimeSplit:
    import numpy as np

    np.random.seed(seed)
    base = pd.Timestamp("2020-01-01")
    total = n_train + n_cal + n_test
    total_days = 1825
    dates = [base + pd.DateOffset(days=int(i * total_days / total)) for i in range(total)]

    stations = ["A17", "A18", "A19"]
    types = ["住宅大樓", "華廈"]

    rows = []
    for i in range(total):
        s = stations[i % 3]
        t = types[i % 2]
        base_price = {"A17": 600_000, "A18": 500_000, "A19": 550_000}[s]
        type_mult = {"住宅大樓": 1.0, "華廈": 0.85}[t]
        target = base_price * type_mult + np.random.uniform(-50_000, 50_000)

        rows.append(
            {
                "transaction_date": dates[i],
                "station_code": s,
                "station_distance_m": float(np.random.uniform(100, 1500)),
                "building_area_ping": float(np.random.uniform(15, 60)),
                "building_type": t,
                "bedrooms": int(np.random.randint(1, 5)),
                "living_rooms": int(np.random.randint(1, 3)),
                "bathrooms": int(np.random.randint(1, 3)),
                "building_age_years": float(np.random.uniform(0, 30)),
                "floor": int(np.random.randint(1, 15)),
                "total_floors": int(np.random.randint(5, 25)),
                "floor_ratio": float(
                    np.random.randint(1, 15) / np.random.randint(5, 25)
                ),
                "parking_type": "",
                "parking_area_ping": 0.0,
                "transaction_year": dates[i].year,
                "transaction_month": dates[i].month,
                "target_unit_price_twd": target,
                "target_policy": "official_unit_price",
                "transaction_key": f"T{i}",
                "road_key": f"R{i % 10}",
            }
        )

    frame = pd.DataFrame(rows)
    return TimeSplit(
        train=frame.iloc[:n_train],
        calibration=frame.iloc[n_train : n_train + n_cal],
        test=frame.iloc[n_train + n_cal : n_train + n_cal + n_test],
    )


@pytest.fixture
def experiment() -> ModelExperiment:
    split = _build_experiment_frame(300, 100, 200)
    return run_model_experiment(split)


@pytest.fixture
def leakage():
    return {
        "target_in_features": False,
        "transaction_key_overlap": False,
        "road_group_overlap_count": 0,
    }


def test_model_card_discloses_required_evidence(
    tmp_path, trained_bundle, experiment, leakage
):
    path = write_model_card(trained_bundle, experiment, leakage, tmp_path)
    text = path.read_text(encoding="utf-8")
    for heading in (
        "資料期間",
        "時間切割",
        "候選模型",
        "分群誤差",
        "區間覆蓋率",
        "限制",
        "不適用情境",
        "版本狀態",
    ):
        assert heading in text
    assert "overall：MAE = 45000" in text
    assert "mae：MAE = N/A" not in text
    assert "此版本為未發布候選模型，不會替換網站正式估價模型。" in text


def test_write_evaluation_creates_json(tmp_path, trained_bundle, experiment):
    import json
    from dataclasses import replace


    split = _build_experiment_frame(300, 100, 200, seed=99)
    exp = run_model_experiment(split)
    exp = replace(exp, recommended=True, reason_codes=())

    path = write_evaluation(trained_bundle, exp, split, tmp_path)
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["transaction_type"] == "resale"
    assert payload["selected_model"] == "ridge"
    assert "selection_metrics" in payload
    assert "final_test_metrics" in payload
    assert payload["selection_metrics"]["ridge"]["overall"]["count"] == 100
    assert payload["final_test_metrics"]["ridge"]["overall"]["count"] == 200
    assert payload["recommendation"] == {
        "status": "recommended",
        "reason_codes": [],
    }
    assert "leakage_audit" in payload
    assert "test_coverage" in payload
    assert payload["data_date"] == "2026-06-01"
