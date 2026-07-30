import pandas as pd
import pytest

from qingpu_insight.market_cleaning import build_market_dataset


def sample_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "transaction_type": ["resale", "presale", "resale", "resale"],
            "record_id": ["R1", "P1", "R2", "R3"],
            "transaction_subject": ["房地(土地+建物)+車位"] * 4,
            "main_use": ["住家用", "住家用", "店鋪", "住家用"],
            "transaction_date": pd.to_datetime(
                ["2026-01-10", "2026-02-10", "2026-03-10", "2026-04-10"]
            ),
            "completion_date": pd.to_datetime(["2020-01-01", None, "2020-01-01", "2020-01-01"]),
            "building_area_sqm": [99.17355, 66.1157, 99.17355, 99.17355],
            "unit_price_sqm_twd": [181500, 211750, 181500, 181500],
            "total_price_twd": [18_000_000, 14_000_000, 18_000_000, 18_000_000],
            "building_type": ["住宅大樓"] * 4,
            "bedrooms": [3, 2, 3, 3],
            "living_rooms": [2, 1, 2, 2],
            "bathrooms": [2, 1, 2, 2],
            "station_code": ["A18", "A17", "A18", None],
            "station_distance_m": [500.0, 800.0, 500.0, None],
            "coordinate_eligible": [True, True, True, False],
            "match_quality": ["exact", "nearest_number", "exact", "unmatched"],
            "longitude": [121.21, 121.22, 121.21, None],
            "latitude": [25.01, 25.02, 25.01, None],
            "source_file": ["a.csv", "b.csv", "a.csv", "a.csv"],
            "remarks": ["", "", "", ""],
        }
    )


def test_build_market_dataset_keeps_only_eligible_residential_rows() -> None:
    clean, quality = build_market_dataset(sample_rows())
    assert clean["record_id"].tolist() == ["R1", "P1"]
    assert clean["analysis_eligible"].all()
    assert quality.input_records == 4
    assert quality.output_records == 2
    assert quality.exclusion_reasons == {"non_residential": 1, "outside_life_circle": 1}


def test_build_market_dataset_derives_ping_price_age_and_stable_key() -> None:
    clean, _ = build_market_dataset(sample_rows())
    resale = clean.loc[clean["record_id"] == "R1"].iloc[0]
    assert resale["building_area_ping"] == pytest.approx(30.0, rel=1e-4)
    assert resale["unit_price_per_ping_twd"] == pytest.approx(600_000, rel=1e-4)
    assert resale["building_age_years"] == pytest.approx(6.0, abs=0.1)
    assert len(resale["transaction_key"]) == 64


def test_build_market_dataset_requires_columns() -> None:
    df = pd.DataFrame({"record_id": ["R1"]})
    with pytest.raises(ValueError, match="Missing required columns"):
        build_market_dataset(df)


def test_build_market_dataset_validates_transaction_type() -> None:
    df = sample_rows()
    df.loc[0, "transaction_type"] = "unknown"
    with pytest.raises(ValueError, match="Invalid transaction_type"):
        build_market_dataset(df)


def test_build_market_dataset_excludes_confirmed_non_market_transactions() -> None:
    normal = sample_rows().iloc[[0]].copy()
    normal["record_id"] = "normal"

    building_only = normal.copy()
    building_only["record_id"] = "building"
    building_only["transaction_subject"] = "建物"

    related_party = normal.copy()
    related_party["record_id"] = "related"
    related_party["remarks"] = "親友、員工、共有人或其他特殊關係間之交易；"

    ambiguous_registration = normal.copy()
    ambiguous_registration["record_id"] = "ambiguous"
    ambiguous_registration["remarks"] = "預售屋、或土地及建物分件登記案件；"

    clean, quality = build_market_dataset(
        pd.concat(
            [normal, building_only, related_party, ambiguous_registration],
            ignore_index=True,
        )
    )

    assert clean["record_id"].tolist() == ["normal", "ambiguous"]
    assert quality.exclusion_reasons == {
        "non_market_subject": 1,
        "special_relationship": 1,
    }


def test_build_market_dataset_applies_completion_checks_to_resale_only() -> None:
    normal = sample_rows().iloc[[0]].copy()
    rows = pd.concat([normal] * 6, ignore_index=True)
    rows["record_id"] = [
        "equal_completion",
        "future_completion",
        "missing_completion",
        "presale_missing_completion",
        "presale_future_completion",
        "ineligible_missing_completion",
    ]
    rows.loc[[3, 4], "transaction_type"] = "presale"
    rows.loc[5, "main_use"] = "店鋪"
    rows["completion_date"] = pd.to_datetime(
        ["2026-01-10", "2026-01-11", None, None, "2026-01-11", None]
    )

    clean, quality = build_market_dataset(rows)

    assert clean["record_id"].tolist() == [
        "equal_completion",
        "presale_missing_completion",
        "presale_future_completion",
    ]
    assert quality.exclusion_reasons == {
        "non_residential": 1,
        "missing_completion_date": 1,
        "future_completion_transfer": 1,
    }
