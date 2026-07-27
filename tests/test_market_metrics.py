import pandas as pd
import pytest

from qingpu_insight.market_metrics import (
    MapBounds,
    MarketFilters,
    filter_market,
    market_map_points,
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
    result = recent_transactions(market_frame, MarketFilters(transaction_type="presale"), limit=3)
    assert len(result) == 3
    assert result[0]["transaction_date"] >= result[1]["transaction_date"]
    assert len(str(result[0]["latitude"]).split(".")[-1]) <= 4


def test_recent_transactions_exposes_only_public_fields(
    market_frame: pd.DataFrame,
) -> None:
    result = recent_transactions(market_frame, MarketFilters(transaction_type="resale"), limit=1)
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
        MarketFilters(transaction_type="resale", date_from=pd.Timestamp("2099-01-01")),
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


def _map_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "transaction_type": ["resale", "resale", "resale", "presale"],
            "station_code": ["A18", "A18", "A18", "A18"],
            "transaction_date": pd.to_datetime(
                ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]
            ),
            "unit_price_per_ping_twd": [400_000, 500_000, 600_000, 700_000],
            "latitude": [25.0001, 25.0003, None, 25.0003],
            "longitude": [121.0001, 121.0003, 121.0003, 121.0004],
        }
    )


def test_market_map_counts_all_filtered_rows_but_groups_only_located_rows() -> None:
    result = market_map_points(
        _map_frame(),
        MarketFilters(transaction_type="resale"),
        zoom=12,
    )

    assert result["total_records"] == 3
    assert result["located_records"] == 2
    assert result["unlocated_records"] == 1
    assert result["group_count"] == 1
    assert result["items"] == [
        {
            "latitude": 25.0002,
            "longitude": 121.0002,
            "record_count": 2,
            "median_unit_price_per_ping_twd": 450_000.0,
            "latest_transaction_date": "2026-02-01",
        }
    ]


def test_market_map_bounds_limit_groups_not_complete_counts() -> None:
    result = market_map_points(
        _map_frame(),
        MarketFilters(transaction_type="resale"),
        zoom=16,
        bounds=MapBounds(24.999, 120.999, 25.00015, 121.00015),
    )

    assert result["total_records"] == 3
    assert result["located_records"] == 2
    assert result["unlocated_records"] == 1
    assert sum(item["record_count"] for item in result["items"]) == 1


def test_market_map_higher_zoom_splits_close_coordinates() -> None:
    frame = _map_frame().iloc[:2].copy()
    frame.loc[frame.index[1], ["latitude", "longitude"]] = [25.0011, 121.0011]

    coarse = market_map_points(
        frame, MarketFilters(transaction_type="resale"), zoom=12
    )
    detailed = market_map_points(
        frame, MarketFilters(transaction_type="resale"), zoom=16
    )

    assert coarse["group_count"] == 1
    assert detailed["group_count"] == 2


def test_market_map_adapts_grid_to_group_limit() -> None:
    count = 620
    frame = pd.DataFrame(
        {
            "transaction_type": ["resale"] * count,
            "station_code": ["A18"] * count,
            "transaction_date": pd.to_datetime(["2026-01-01"] * count),
            "unit_price_per_ping_twd": [500_000] * count,
            "latitude": [24.8 + index * 0.02 for index in range(count)],
            "longitude": [120.8 + index * 0.021 for index in range(count)],
        }
    )

    result = market_map_points(
        frame,
        MarketFilters(transaction_type="resale"),
        zoom=19,
        max_groups=500,
    )

    assert result["total_records"] == 620
    assert result["group_count"] <= 500
    assert sum(item["record_count"] for item in result["items"]) == 620


def test_market_map_empty_filter_returns_zero_counts_and_no_groups() -> None:
    result = market_map_points(
        _map_frame(),
        MarketFilters(
            transaction_type="resale",
            date_from=pd.Timestamp("2099-01-01"),
        ),
        zoom=14,
    )

    assert result == {
        "total_records": 0,
        "located_records": 0,
        "unlocated_records": 0,
        "group_count": 0,
        "items": [],
    }
