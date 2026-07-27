import math

import pytest

from qingpu_insight.model_tuning import (
    PROFILE_ORDER,
    TuningValidationError,
    parse_tuning_plan,
)


def test_default_plan_contains_exact_server_profiles() -> None:
    plan = parse_tuning_plan(("resale", "presale"), None)
    assert PROFILE_ORDER == ("quick", "balanced", "thorough", "custom")
    assert [p.name for p in plan.profiles] == ["quick", "balanced", "thorough"]
    assert [
        (p.hgb_learning_rate, p.hgb_max_iter, p.rf_n_estimators)
        for p in plan.profiles
    ] == [(0.08, 180, 160), (0.06, 350, 400), (0.04, 600, 700)]
    assert [p.recency_half_life_months for p in plan.profiles] == [48, 48, 48]


def test_resale_custom_profile_round_trips_exact_values() -> None:
    plan = parse_tuning_plan(
        ("resale",),
        {
            "mode": "preset_comparison",
            "include_custom": True,
            "custom": {
                "hgb_learning_rate": 0.05,
                "hgb_max_iter": 420,
                "rf_n_estimators": 520,
                "recency_half_life_months": 36,
            },
        },
    )
    custom = plan.profiles[-1]
    assert custom.name == "custom"
    assert custom.source == "custom"
    assert custom.snapshot()["recency_half_life_months"] == 36


def test_presale_custom_rejects_recency_half_life() -> None:
    with pytest.raises(TuningValidationError) as caught:
        parse_tuning_plan(
            ("presale",),
            {
                "mode": "preset_comparison",
                "include_custom": True,
                "custom": {
                    "hgb_learning_rate": 0.05,
                    "hgb_max_iter": 420,
                    "rf_n_estimators": 520,
                    "recency_half_life_months": 36,
                },
            },
        )
    assert caught.value.fields == {
        "custom.recency_half_life_months": "not_applicable"
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hgb_learning_rate", 0.0),
        ("hgb_learning_rate", 0.21),
        ("hgb_learning_rate", math.nan),
        ("hgb_max_iter", 99),
        ("hgb_max_iter", 1001),
        ("hgb_max_iter", True),
        ("rf_n_estimators", 99),
        ("rf_n_estimators", 1001),
        ("recency_half_life_months", 11),
        ("recency_half_life_months", 85),
    ],
)
def test_custom_profile_rejects_invalid_numeric_fields(field, value) -> None:
    custom = {
        "hgb_learning_rate": 0.05,
        "hgb_max_iter": 420,
        "rf_n_estimators": 520,
        "recency_half_life_months": 36,
    }
    custom[field] = value
    with pytest.raises(TuningValidationError) as caught:
        parse_tuning_plan(
            ("resale",),
            {"mode": "preset_comparison", "include_custom": True, "custom": custom},
        )
    assert f"custom.{field}" in caught.value.fields


def test_custom_rejects_unknown_tuning_keys() -> None:
    with pytest.raises(TuningValidationError) as caught:
        parse_tuning_plan(
            ("resale",),
            {
                "mode": "preset_comparison",
                "include_custom": True,
                "custom": {
                    "hgb_learning_rate": 0.05,
                    "hgb_max_iter": 420,
                    "rf_n_estimators": 520,
                    "recency_half_life_months": 36,
                    "unknown_key": 123,
                },
            },
        )
    assert "custom.unknown_key" in caught.value.fields


def test_custom_rejects_unsupported_mode() -> None:
    with pytest.raises(TuningValidationError) as caught:
        parse_tuning_plan(
            ("resale",),
            {"mode": "single", "include_custom": False},
        )
    assert "mode" in caught.value.fields


def test_custom_rejected_when_include_custom_false() -> None:
    with pytest.raises(TuningValidationError) as caught:
        parse_tuning_plan(
            ("resale",),
            {
                "mode": "preset_comparison",
                "include_custom": False,
                "custom": {
                    "hgb_learning_rate": 0.05,
                    "hgb_max_iter": 420,
                    "rf_n_estimators": 520,
                    "recency_half_life_months": 36,
                },
            },
        )
    assert "custom" in caught.value.fields


def test_custom_rejects_payload_with_bad_mode() -> None:
    with pytest.raises(TuningValidationError) as caught:
        parse_tuning_plan(
            ("resale",),
            {"mode": 42, "include_custom": False},
        )
    assert "mode" in caught.value.fields


def test_presale_custom_with_exactly_three_model_fields() -> None:
    plan = parse_tuning_plan(
        ("presale",),
        {
            "mode": "preset_comparison",
            "include_custom": True,
            "custom": {
                "hgb_learning_rate": 0.10,
                "hgb_max_iter": 500,
                "rf_n_estimators": 600,
            },
        },
    )
    custom = plan.profiles[-1]
    assert custom.name == "custom"
    assert custom.recency_half_life_months is None
