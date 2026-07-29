import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from qingpu_insight.model_analysis import (
    ABLATIONS,
    build_resale_diagnostics,
    evaluate_release_checks,
    run_annual_backtests,
    run_feature_experiments,
)
from qingpu_insight.model_features import (
    BASE_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
    add_derived_features,
)
from qingpu_insight.model_training import split_by_time
from qingpu_insight.model_tuning import TrainingProfile


@pytest.fixture
def large_model_frame():
    np.random.seed(42)
    stations = ["A17", "A18", "A19"]
    bt_types = ["住宅大樓", "華廈", "公寓"]
    rows = []

    # Ensure >=100 rows per split: train (dates < 2024-12-16),
    # calibration (2024-12-16 <= dates < 2025-06-16),
    # test (dates >= 2025-06-16)
    periods = [
        (350, "train", datetime(2021, 1, 1), datetime(2024, 12, 15)),
        (150, "calibration", datetime(2024, 12, 16), datetime(2025, 6, 15)),
        (150, "test", datetime(2025, 6, 16), datetime(2026, 6, 15)),
    ]

    for count, _, start, end in periods:
        span = (end - start).days
        for _ in range(count):
            d = start + timedelta(days=int(np.random.uniform(0, span)))
            rows.append(
                {
                    "station_code": np.random.choice(stations),
                    "station_distance_m": float(np.random.uniform(100, 1500)),
                    "building_area_ping": float(np.random.uniform(20, 80)),
                    "building_type": np.random.choice(bt_types),
                    "bedrooms": int(np.random.choice([2, 3, 4])),
                    "living_rooms": int(np.random.choice([1, 2])),
                    "bathrooms": int(np.random.choice([1, 2])),
                    "building_age_years": float(np.random.uniform(0, 40)),
                    "floor": int(np.random.randint(1, 15)),
                    "total_floors": int(np.random.randint(5, 20)),
                    "floor_ratio": float(np.random.uniform(0.1, 0.9)),
                    "parking_type": np.random.choice(
                        ["坡道平面", "坡道機械", None], p=[0.4, 0.3, 0.3]
                    ),
                    "parking_area_ping": float(np.random.uniform(0, 15)),
                    "transaction_year": d.year,
                    "transaction_month": d.month,
                    "transaction_date": d,
                    "target_unit_price_twd": float(np.random.uniform(200000, 800000)),
                }
            )
    df = pd.DataFrame(rows)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    return df


def test_resale_diagnostics_exposes_a18_drift(large_model_frame):
    split = split_by_time(large_model_frame)
    diagnostics = build_resale_diagnostics(large_model_frame, split)
    assert set(diagnostics["station_counts"]) == {"A17", "A18", "A19"}
    assert "building_age_years" in diagnostics["missing_rates"]
    assert diagnostics["monthly_summary"]
    assert any(row["station_code"] == "A18" for row in diagnostics["building_type_summary"])
    assert set(diagnostics["split_summary"]) == {
        "train",
        "calibration",
        "test",
    }


def test_diagnostics_are_json_serializable(large_model_frame):
    payload = build_resale_diagnostics(large_model_frame, split_by_time(large_model_frame))
    json.dumps(payload)


@pytest.fixture
def model_frame():
    np.random.seed(42)
    stations = ["A17", "A18", "A19"]
    bt_types = ["住宅大樓", "華廈"]
    rows = []
    dates = pd.date_range("2021-01-01", "2026-06-30", periods=2000)
    for d in dates:
        age = float(np.random.uniform(0, 40))
        beds = int(np.random.choice([2, 3, 4]))
        station = np.random.choice(stations)
        signal = age * 8000 + beds * 20000 + (800000 if station == "A17" else 0)
        rows.append(
            {
                "station_code": station,
                "station_distance_m": float(np.random.uniform(100, 1500)),
                "building_area_ping": float(np.random.uniform(20, 80)),
                "building_type": np.random.choice(bt_types),
                "bedrooms": beds,
                "living_rooms": int(np.random.choice([1, 2])),
                "bathrooms": int(np.random.choice([1, 2])),
                "building_age_years": age,
                "floor": int(np.random.randint(1, 15)),
                "total_floors": int(np.random.randint(5, 20)),
                "floor_ratio": float(np.random.uniform(0.1, 0.9)),
                "parking_type": np.random.choice(["坡道平面", "坡道機械", ""]),
                "parking_area_ping": float(np.random.uniform(0, 15)),
                "transaction_year": d.year,
                "transaction_month": d.month,
                "transaction_date": d,
                "target_unit_price_twd": float(np.random.uniform(300000, 500000) + signal),
            }
        )
    df = pd.DataFrame(rows)
    return add_derived_features(df)


def test_feature_experiments_use_identical_time_rows(model_frame):
    split = split_by_time(model_frame)
    experiments = run_feature_experiments(split)

    expected_names = [
        "base",
        "enhanced",
        "without_transaction_trend",
        "without_station_building_type",
        "without_age_band",
        "without_area_band",
        "without_floor_band",
    ]
    assert [e.name for e in experiments] == expected_names

    assert experiments[0].feature_columns == BASE_FEATURE_COLUMNS
    assert experiments[1].feature_columns == FEATURE_COLUMNS

    for i, (ab_name, removed) in enumerate(ABLATIONS.items(), start=2):
        assert experiments[i].name == ab_name
        assert experiments[i].candidate_errors == {}
        for r in removed:
            assert r not in experiments[i].feature_columns


def test_feature_experiment_metrics_include_overall_and_a18(model_frame):
    split = split_by_time(model_frame)
    experiments = run_feature_experiments(split)

    for exp in experiments:
        assert "overall" in exp.metrics
        assert "station:A18" in exp.metrics


def _metrics(mae, a17=10.0, a18=20.0, a19=10.0):
    return {
        "overall": {"mae": mae},
        "station:A17": {"mape": a17},
        "station:A18": {"mape": a18},
        "station:A19": {"mape": a19},
    }


def _backtest(passed, stations_within_limit=True):
    return {
        "passed": passed,
        "stations_within_limit": stations_within_limit,
    }


def test_backtests_never_train_on_future_rows(model_frame, monkeypatch):
    import qingpu_insight.model_analysis as model_analysis

    captured_profiles = []
    original_candidate_estimators = model_analysis.candidate_estimators

    def capture_candidate_estimators(*args, **kwargs):
        captured_profiles.append(kwargs.get("profile"))
        return original_candidate_estimators(*args, **kwargs)

    monkeypatch.setattr(
        model_analysis,
        "candidate_estimators",
        capture_candidate_estimators,
    )
    profile = TrainingProfile("custom", "custom", 0.05, 420, 520, 36)
    rows = run_annual_backtests(
        model_frame,
        selected_model_name="hist_gradient_boosting",
        feature_columns=FEATURE_COLUMNS,
        profile=profile,
    )
    assert len(rows) == 3
    assert captured_profiles == [profile, profile, profile]
    for row in rows:
        assert row["train_max_date"] < row["test_min_date"]
        assert row["source_max_date"] <= row["cutoff_date"]


def test_release_checks_require_strict_a18_improvement():
    checks = evaluate_release_checks(
        _metrics(98.0),
        _metrics(100.0),
        [_backtest(True), _backtest(True), _backtest(False)],
        date(2026, 6, 12),
        date(2026, 6, 12),
    )
    assert checks["overall_mae_improved"] is True
    assert checks["a18_improved"] is False
    assert checks["recommended"] is False


def test_release_checks_require_two_passing_backtests():
    checks = evaluate_release_checks(
        _metrics(97.0, a18=18.0),
        _metrics(100.0),
        [_backtest(True), _backtest(False), _backtest(False)],
        date(2026, 6, 12),
        date(2026, 6, 12),
    )
    assert checks["backtests_passed"] is False
    assert checks["recommended"] is False


def test_release_checks_allow_one_historical_station_regression():
    checks = evaluate_release_checks(
        _metrics(97.0, a18=18.0),
        _metrics(100.0),
        [
            _backtest(True, stations_within_limit=True),
            _backtest(True, stations_within_limit=False),
            _backtest(True, stations_within_limit=True),
        ],
        date(2026, 6, 12),
        date(2026, 6, 12),
    )
    assert checks["backtests_passed"] is True
    assert checks["backtest_stations_within_limit"] is True
    assert checks["recommended"] is True


def test_release_checks_reject_two_historical_station_regressions():
    checks = evaluate_release_checks(
        _metrics(97.0, a18=18.0),
        _metrics(100.0),
        [
            _backtest(True, stations_within_limit=False),
            _backtest(True, stations_within_limit=False),
            _backtest(True, stations_within_limit=True),
        ],
        date(2026, 6, 12),
        date(2026, 6, 12),
    )
    assert checks["backtest_stations_within_limit"] is False
    assert checks["recommended"] is False


def test_release_checks_require_all_three_backtests():
    checks = evaluate_release_checks(
        _metrics(97.0, a18=18.0),
        _metrics(100.0),
        [_backtest(True), _backtest(True)],
        date(2026, 6, 12),
        date(2026, 6, 12),
    )
    assert checks["backtests_passed"] is False
    assert checks["backtest_stations_within_limit"] is False
    assert checks["recommended"] is False
