import pandas as pd
import pytest

from qingpu_insight.model_features import add_derived_features
from qingpu_insight.model_training import (
    ModelExperiment,
    TimeSplit,
    run_model_experiment,
)
from qingpu_insight.model_tuning import TrainingProfile
from qingpu_insight.parking_valuation import ParkingPricePolicy, ParkingPriceStat
from qingpu_insight.valuation import ValuationBundle
from qingpu_insight.valuation_reporting import write_evaluation, write_model_card


@pytest.fixture
def trained_bundle():
    class FakePipeline:
        def predict(self, X):
            import numpy as np

            return np.full(len(X), 500_000.0)

    bundle = ValuationBundle(
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
    bundle.parking_price_policy = ParkingPricePolicy(
        version=1,
        minimum_type_samples=20,
        by_type={
            "\u5761\u9053\u5e73\u9762": ParkingPriceStat(price_twd=1_500_000, sample_size=50),
            "\u5761\u9053\u6a5f\u68b0": ParkingPriceStat(price_twd=800_000, sample_size=30),
        },
        market_fallback=ParkingPriceStat(price_twd=1_200_000, sample_size=100),
    )
    return bundle


def _build_experiment_frame(n_train: int, n_cal: int, n_test: int, seed: int = 42) -> TimeSplit:
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
                "floor_ratio": float(np.random.randint(1, 15) / np.random.randint(5, 25)),
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
    frame = add_derived_features(frame)
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


def test_model_card_discloses_required_evidence(tmp_path, trained_bundle, experiment, leakage):
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
    assert "車位估值政策" in text
    assert "政策版本：1" in text
    assert "坡道平面" in text
    assert "1,500,000" in text


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
    assert payload["selected_model"] == trained_bundle.model_name
    assert "selection_metrics" in payload
    assert "final_test_metrics" in payload
    assert payload["selection_metrics"][exp.selected_name]["overall"]["count"] == 100
    assert payload["final_test_metrics"][exp.selected_name]["overall"]["count"] == 200
    assert payload["recommendation"] == {
        "status": "recommended",
        "reason_codes": [],
    }
    assert "leakage_audit" in payload
    assert "test_coverage" in payload
    assert payload["data_date"] == "2026-06-01"
    assert "parking_policy" in payload
    assert payload["parking_policy"]["version"] == 1
    assert payload["parking_policy"]["by_type"]["坡道平面"]["price_twd"] == 1_500_000
    assert payload["parking_policy"]["market_fallback"]["price_twd"] == 1_200_000


def test_write_evaluation_with_selected_profile(tmp_path, trained_bundle, experiment):
    import json
    from dataclasses import replace

    split = _build_experiment_frame(300, 100, 200, seed=99)
    exp = run_model_experiment(split)
    exp = replace(exp, recommended=True, reason_codes=())

    profile = TrainingProfile("custom", "custom", 0.05, 420, 520, 36)
    path = write_evaluation(
        trained_bundle, exp, split, tmp_path, selected_profile=profile
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["selected_profile"] == "custom"
    assert payload["recency_weighting"]["half_life_months"] == 36
    assert payload["profile_results"]["custom"]["parameters"]["hgb_max_iter"] == 420


def test_write_evaluation_presale_omits_recency_weighting(
    tmp_path, trained_bundle, experiment
):
    import json
    from dataclasses import replace

    split = _build_experiment_frame(300, 100, 200, seed=99)
    exp = run_model_experiment(split)
    exp = replace(exp, recommended=True, reason_codes=())

    profile = TrainingProfile("custom", "custom", 0.05, 420, 520, None)
    path = write_evaluation(
        trained_bundle, exp, split, tmp_path, selected_profile=profile
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "recency_weighting" not in payload


def test_write_evaluation_presale_omits_recency_weighting_actual_presale(
    tmp_path, trained_bundle, experiment
):
    import json
    from dataclasses import replace

    split = _build_experiment_frame(300, 100, 200, seed=99)
    exp = run_model_experiment(split)
    exp = replace(exp, recommended=True, reason_codes=())

    presale_bundle = replace(trained_bundle, transaction_type="presale")
    profile = TrainingProfile("custom", "custom", 0.05, 420, 520, 36)
    path = write_evaluation(
        presale_bundle, exp, split, tmp_path, selected_profile=profile
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "recency_weighting" not in payload


def test_model_card_with_profile_uses_exact_half_life(
    tmp_path, trained_bundle, experiment, leakage
):
    from dataclasses import replace

    split = _build_experiment_frame(300, 100, 200, seed=99)
    exp = run_model_experiment(split)
    exp = replace(exp, recommended=True, reason_codes=())

    profile = TrainingProfile("custom", "custom", 0.05, 420, 520, 36)
    path = write_model_card(
        trained_bundle, exp, leakage, tmp_path, selected_profile=profile
    )
    text = path.read_text(encoding="utf-8")
    assert "24 個月" not in text


def test_model_card_explains_log_target_candidate(
    tmp_path, trained_bundle, experiment, leakage
):
    path = write_model_card(trained_bundle, experiment, leakage, tmp_path)

    text = path.read_text(encoding="utf-8")
    assert "HGB（對數價格）" in text
    assert "驗證與跨年度回測" in text


def test_write_evaluation_with_automl_info(tmp_path, trained_bundle, experiment):
    import json
    from dataclasses import replace

    split = _build_experiment_frame(300, 100, 200, seed=99)
    exp = run_model_experiment(split)
    exp = replace(exp, recommended=True, reason_codes=())

    automl_info = {
        "mode": "automl",
        "budget_name": "quick",
        "budget_seconds": 300,
        "completed_trials": 10,
        "selected_trial_number": 5,
        "fit_spec": {
            "model_name": "hist_gradient_boosting",
            "parameters": {"learning_rate": 0.1, "max_iter": 200},
            "recency_half_life_months": 48,
        },
        "release_blockers": [],
    }
    path = write_evaluation(
        trained_bundle, exp, split, tmp_path, automl_info=automl_info
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["automl_info"]["mode"] == "automl"
    assert payload["automl_info"]["budget_name"] == "quick"
    assert payload["automl_info"]["completed_trials"] == 10
    assert payload["automl_info"]["selected_trial_number"] == 5
    assert "selected_profile" not in payload


def test_write_evaluation_with_automl_blockers(tmp_path, trained_bundle, experiment):
    import json
    from dataclasses import replace

    split = _build_experiment_frame(300, 100, 200, seed=99)
    exp = run_model_experiment(split)
    exp = replace(exp, recommended=True, reason_codes=())

    automl_info = {
        "mode": "automl",
        "budget_name": "standard",
        "budget_seconds": 900,
        "completed_trials": 25,
        "selected_trial_number": None,
        "fit_spec": None,
        "release_blockers": ["overall_mae_not_improved", "backtest_insufficient"],
    }
    path = write_evaluation(
        trained_bundle, exp, split, tmp_path, automl_info=automl_info
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["automl_info"]["release_blockers"] == [
        "overall_mae_not_improved",
        "backtest_insufficient",
    ]
    assert payload["automl_info"]["selected_trial_number"] is None
    assert "selected_profile" not in payload


def test_model_card_with_automl_info(tmp_path, trained_bundle, experiment, leakage):
    from dataclasses import replace

    split = _build_experiment_frame(300, 100, 200, seed=99)
    exp = run_model_experiment(split)
    exp = replace(exp, recommended=True, reason_codes=())

    automl_info = {
        "mode": "automl",
        "budget_name": "deep",
        "budget_seconds": 1800,
        "completed_trials": 55,
        "selected_trial_number": 3,
        "fit_spec": {
            "model_name": "random_forest",
            "parameters": {"n_estimators": 500},
            "recency_half_life_months": 48,
        },
        "release_blockers": [],
    }
    path = write_model_card(
        trained_bundle, exp, leakage, tmp_path, automl_info=automl_info
    )
    text = path.read_text(encoding="utf-8")
    assert "AutoML" in text
    assert "deep" in text
    assert "55 次" in text
    assert "隨機森林" in text or "random_forest" in text
