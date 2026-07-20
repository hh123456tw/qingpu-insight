import pandas as pd
import pytest

from qingpu_insight.market_metrics import (
    MarketFilters,
    filter_market,
    market_summary,
    market_trends,
    recent_transactions,
)


def test_filter_market_never_mixes_transaction_types(market_frame: pd.DataFrame) -> None:
    result = filter_market(market_frame, MarketFilters(transaction_type="presale"))
    assert set(result["transaction_type"]) == {"presale"}


def test_summary_returns_station_kpis_and_data_date(market_frame: pd.DataFrame) -> None:
    result = market_summary(
        market_frame,
        MarketFilters(transaction_type="resale", station_codes=("A18",)),
    )
    assert result["transaction_type"] == "resale"
    assert result["station_codes"] == ["A18"]
    assert result["record_count"] == 2
    assert result["median_unit_price_per_ping_twd"] > 0
    assert result["latest_transaction_date"] == "2026-02-15"


def test_trends_group_by_calendar_month(market_frame: pd.DataFrame) -> None:
    result = market_trends(market_frame, MarketFilters(transaction_type="resale"))
    assert [item["month"] for item in result] == ["2026-01", "2026-02"]
    assert all("median_unit_price_per_ping_twd" in item for item in result)


def test_recent_transactions_limit_and_round_coordinates(market_frame: pd.DataFrame) -> None:
    result = recent_transactions(
        market_frame, MarketFilters(transaction_type="presale"), limit=3
    )
    assert len(result) == 3
    assert result[0]["transaction_date"] >= result[1]["transaction_date"]
    assert len(str(result[0]["latitude"]).split(".")[-1]) <= 4


def test_recent_transactions_exposes_only_public_fields(
    market_frame: pd.DataFrame,
) -> None:
    result = recent_transactions(
        market_frame, MarketFilters(transaction_type="resale"), limit=1
    )
    assert set(result[0]) == {
        "transaction_type",
        "record_id",
        "station_code",
        "transaction_date",
        "building_area_ping",
        "unit_price_per_ping_twd",
        "total_price_twd",
        "building_type",
        "bedrooms",
        "living_rooms",
        "bathrooms",
        "building_age_years",
        "station_distance_m",
        "longitude",
        "latitude",
        "match_quality",
    }


def test_empty_summary_uses_none_instead_of_nan(market_frame: pd.DataFrame) -> None:
    result = market_summary(
        market_frame,
        MarketFilters(
            transaction_type="resale", date_from=pd.Timestamp("2099-01-01")
        ),
    )
    assert result["record_count"] == 0
    assert result["median_unit_price_per_ping_twd"] is None
    assert result["median_total_price_twd"] is None


def test_invalid_transaction_type_raises_value_error() -> None:
    with pytest.raises(ValueError, match="transaction_type must be resale or presale"):
        MarketFilters(transaction_type="invalid")


def test_invalid_station_codes_raises_value_error() -> None:
    with pytest.raises(ValueError, match="station_codes must contain"):
        MarketFilters(transaction_type="resale", station_codes=("Z99",))


def test_negative_area_ping_min_raises_value_error() -> None:
    with pytest.raises(ValueError, match="area_ping_min must be non-negative"):
        MarketFilters(transaction_type="resale", area_ping_min=-1.0)


def test_area_ping_min_exceeds_max_raises_value_error() -> None:
    with pytest.raises(ValueError, match="area_ping_min must not exceed area_ping_max"):
        MarketFilters(transaction_type="resale", area_ping_min=100.0, area_ping_max=50.0)
