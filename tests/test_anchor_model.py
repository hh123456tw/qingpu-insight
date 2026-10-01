import pickle

import numpy as np
import pandas as pd
import pytest
from sklearn.base import clone

from qingpu_insight.anchor_model import (
    AnchorBlendRegressor,
    anchor_priors,
    build_anchor_table,
)
from qingpu_insight.model_features import FEATURE_COLUMNS, add_derived_features


def _rows(n: int, *, start: str, x: float, age: float, price: float, seed: int = 0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=n, freq="7D")
    frame = pd.DataFrame(
        {
            "transaction_date": dates,
            "station_code": "A18",
            "station_distance_m": 600.0,
            "building_area_ping": rng.uniform(25, 45, n),
            "building_type": "住宅大樓(11層含以上有電梯)",
            "bedrooms": 3,
            "living_rooms": 2,
            "bathrooms": 2,
            "building_age_years": age,
            "floor": 8,
            "total_floors": 15,
            "floor_ratio": 8 / 15,
            "transaction_year": dates.year,
            "transaction_month": dates.month,
            "twd97_x": x + rng.uniform(-10, 10, n),
            "twd97_y": 2_770_000.0 + rng.uniform(-10, 10, n),
        }
    )
    frame = add_derived_features(frame)
    frame["target_unit_price_twd"] = price * rng.uniform(0.95, 1.05, n)
    return frame


def _anchor_rows(dates, *, x: float, completion: str | None, price: float, source: str):
    return pd.DataFrame(
        {
            "transaction_date": pd.to_datetime(dates),
            "completion_date": pd.to_datetime([completion] * len(dates)),
            "twd97_x": x,
            "twd97_y": 2_770_000.0,
            "unit_price_twd": price,
            "source": source,
        }
    )


def _query(date: str, *, x: float, age: float | None) -> pd.DataFrame:
    stamp = pd.Timestamp(date)
    frame = pd.DataFrame(
        {
            "transaction_year": [stamp.year],
            "transaction_month": [stamp.month],
            "transaction_date": [stamp],
            "twd97_x": [x],
            "twd97_y": [2_770_000.0],
            "building_age_years": [age],
            "station_code": ["A18"],
            "building_type": ["住宅大樓(11層含以上有電梯)"],
            "building_area_ping": [30.0],
            "station_distance_m": [600.0],
            "bedrooms": [3],
            "living_rooms": [2],
            "bathrooms": [2],
            "floor": [8],
            "total_floors": [15],
            "floor_ratio": [8 / 15],
        }
    )
    return add_derived_features(frame)


def test_precompletion_anchor_matches_same_building_only_before_the_row_month():
    anchors = _anchor_rows(
        ["2024-09-01", "2024-10-01", "2024-11-01", "2025-07-15"],
        x=300_000.0,
        completion="2025-05-01",
        price=160_000.0,
        source="precompletion",
    )
    empty_history = anchors.iloc[0:0]
    # A sale in 2025-07 of a building completed ~2025-05 (age 0.2 years).
    priors = anchor_priors(
        _query("2025-07-20", x=300_030.0, age=0.2), anchors, empty_history
    )
    assert priors["prior_source"].iloc[0] == "precompletion"
    # The 2025-07-15 transfer is in the row's own month, so only 3 anchors are usable.
    assert priors["anchor_count"].iloc[0] == 3
    assert priors["prior"].iloc[0] == pytest.approx(160_000.0)

    far = anchor_priors(_query("2025-07-20", x=300_500.0, age=0.2), anchors, empty_history)
    assert far["prior_source"].iloc[0] != "precompletion"

    other_building = anchor_priors(
        _query("2025-07-20", x=300_030.0, age=12.0), anchors, empty_history
    )
    assert other_building["prior_source"].iloc[0] != "precompletion"


def test_presale_anchors_only_apply_to_young_buildings():
    anchors = _anchor_rows(
        ["2023-01-01", "2023-02-01", "2023-03-01"],
        x=300_000.0,
        completion=None,
        price=150_000.0,
        source="presale",
    )
    young = anchor_priors(_query("2026-01-10", x=300_010.0, age=2.0), anchors, anchors.iloc[0:0])
    old = anchor_priors(_query("2026-01-10", x=300_010.0, age=8.0), anchors, anchors.iloc[0:0])
    assert young["prior_source"].iloc[0] == "presale"
    assert old["prior_source"].iloc[0] == "baseline"


def _index_sales(prices_by_month: dict[str, float], per_month: int = 40):
    dates, prices = [], []
    for month, price in prices_by_month.items():
        dates += [pd.Timestamp(month)] * per_month
        prices += [price] * per_month
    return np.array(dates, dtype="datetime64[ns]"), np.array(prices)


def test_local_price_index_only_uses_sales_before_the_month():
    from qingpu_insight.anchor_model import LocalPriceIndex

    dates, prices = _index_sales(
        {"2024-01-01": 100.0, "2024-02-01": 100.0, "2024-03-01": 200.0}
    )
    index = LocalPriceIndex(dates, prices, window_months=1)
    levels = index.level(np.array(["2024-02-10", "2024-03-10", "2024-04-10"], "datetime64[ns]"))
    np.testing.assert_allclose(np.exp(levels), [100.0, 100.0, 200.0])
    # A sale's own month is priced by the level including that month.
    after = index.level_after(np.array(["2024-03-05"], "datetime64[ns]"))
    np.testing.assert_allclose(np.exp(after), [200.0])


def test_price_index_restates_anchors_to_the_row_month():
    from qingpu_insight.anchor_model import LocalPriceIndex

    anchors = _anchor_rows(
        ["2024-01-05", "2024-01-06", "2024-01-07"],
        x=300_000.0,
        completion="2024-06-01",
        price=160_000.0,
        source="precompletion",
    )
    dates, prices = _index_sales(
        {"2023-12-01": 400_000.0, "2024-01-01": 400_000.0, "2024-05-01": 500_000.0}
    )
    index = LocalPriceIndex(dates, prices, window_months=1)
    query = _query("2024-06-20", x=300_020.0, age=0.05)

    plain = anchor_priors(query, anchors, anchors.iloc[0:0])
    restated = anchor_priors(query, anchors, anchors.iloc[0:0], price_index=index)

    assert plain["prior"].iloc[0] == pytest.approx(160_000.0)
    # Local prices rose 25% between the anchors' month and the month before the row.
    assert restated["prior"].iloc[0] == pytest.approx(200_000.0)


def test_rows_without_coordinates_fall_back_to_baseline():
    query = _query("2026-01-10", x=np.nan, age=5.0)
    query["twd97_y"] = np.nan
    priors = anchor_priors(query, build_anchor_table(None, None), build_anchor_table(None, None))
    assert priors["prior_source"].iloc[0] == "baseline"
    assert np.isnan(priors["prior"].iloc[0])


@pytest.fixture(scope="module")
def fitted_model():
    frames = [
        _rows(120, start="2020-01-01", x=300_000.0, age=6.0, price=420_000.0, seed=1),
        _rows(120, start="2020-01-01", x=302_000.0, age=15.0, price=300_000.0, seed=2),
    ]
    train = pd.concat(frames, ignore_index=True)
    model = AnchorBlendRegressor(max_iter=60, learning_rate=0.1)
    model.fit(train[list(FEATURE_COLUMNS)], train["target_unit_price_twd"].to_numpy())
    return model, train


def test_blend_predicts_positive_prices_and_reports_anchoring(fitted_model):
    model, train = fitted_model
    X = train[list(FEATURE_COLUMNS)].iloc[-5:]
    predictions = model.predict(X)
    assert np.all(predictions > 0)
    assert model.anchored_mask(X).dtype == bool
    assert set(model.prior_sources(X)) <= {"precompletion", "presale", "resale", "knn", "baseline"}


def test_blend_is_monotone_decreasing_in_area_and_age(fitted_model):
    model, train = fitted_model
    row = train[list(FEATURE_COLUMNS)].iloc[[-1]]
    larger = add_derived_features(row.assign(building_area_ping=row["building_area_ping"] + 15))
    older = add_derived_features(row.assign(building_age_years=row["building_age_years"] + 5))
    base = model.predict(row)[0]
    assert model.predict(larger[list(FEATURE_COLUMNS)])[0] <= base + 1e-6
    assert model.predict(older[list(FEATURE_COLUMNS)])[0] <= base + 1e-6


def test_blend_clones_and_pickles(fitted_model):
    model, train = fitted_model
    X = train[list(FEATURE_COLUMNS)].iloc[-3:]
    restored = pickle.loads(pickle.dumps(model))
    np.testing.assert_allclose(restored.predict(X), model.predict(X))
    assert clone(model).get_params()["max_iter"] == 60


def test_blend_uses_the_model_feature_columns_it_is_given(fitted_model):
    _, train = fitted_model
    columns = [*FEATURE_COLUMNS, "top_floor", "road_key"]
    X = train.assign(road_key="r1")[columns]
    model = AnchorBlendRegressor(max_iter=20, learning_rate=0.1)
    model.fit(X, train["target_unit_price_twd"].to_numpy())
    assert "top_floor" in model.feature_columns_
    # Columns the preprocessors do not know are never used as features.
    assert "road_key" not in model.feature_columns_
    assert np.all(model.predict(X.iloc[-3:]) > 0)


def test_anchor_table_skips_sources_without_coordinates():
    frame = pd.DataFrame(
        {
            "transaction_date": pd.to_datetime(["2024-01-01"]),
            "total_price_twd": [10_000_000],
            "building_area_sqm": [99.0],
            "parking_area_sqm": [0.0],
            "parking_price_twd": [0],
        }
    )
    assert build_anchor_table(frame, frame).empty


def _trend_sales(monthly_growth: float, months: int = 24, seed: int = 0):
    """Two buildings whose prices grow together; the cheap one dominates later months."""
    rng = np.random.default_rng(seed)
    frames = []
    for m in range(months):
        start = pd.Timestamp("2022-01-01") + pd.DateOffset(months=m)
        level = (1 + monthly_growth) ** m
        expensive, cheap = (30, 5) if m < months // 2 else (5, 30)
        for count, x, age, price in (
            (expensive, 300_000.0, 3.0, 500_000.0),
            (cheap, 302_000.0, 15.0, 300_000.0),
        ):
            rows = _rows(count, start=str(start.date()), x=x, age=age + m / 12, price=1.0)
            rows["transaction_date"] = start
            rows["transaction_year"] = start.year
            rows["transaction_month"] = start.month
            rows = add_derived_features(rows)
            rows["target_unit_price_twd"] = price * level * rng.uniform(0.98, 1.02, count)
            frames.append(rows)
    return pd.concat(frames, ignore_index=True)


def _hedonic_index(frame: pd.DataFrame):
    from qingpu_insight.anchor_model import HedonicTimeIndex

    return HedonicTimeIndex.from_rows(frame, frame["target_unit_price_twd"].to_numpy())


def _level(index, date: str) -> float:
    return float(index.level(np.array([pd.Timestamp(date)], "datetime64[ns]"))[0])


def test_hedonic_time_index_tracks_same_building_prices_despite_mix_shift():
    frame = _trend_sales(0.01)
    index = _hedonic_index(frame)
    monthly_median = frame.groupby("transaction_date")["target_unit_price_twd"].median()
    # The raw monthly median falls when the mix shifts to the cheap building ...
    assert np.log(monthly_median.iloc[-1] / monthly_median.iloc[0]) < -0.2
    # ... while the index follows the 1% monthly growth, smoothed by its random-walk prior.
    change = _level(index, "2023-12-15") - _level(index, "2022-01-15")
    assert 0.7 * 23 * np.log(1.01) < change <= 23 * np.log(1.01)
    assert 0.5 * np.log(1.01) < index.slope_ < 1.2 * np.log(1.01)


def test_hedonic_time_index_extrapolates_a_damped_capped_trend():
    from qingpu_insight.anchor_model import TREND_DAMPING, TREND_MAX_HORIZON_MONTHS

    index = _hedonic_index(_trend_sales(0.01))
    last = _level(index, "2023-12-01")

    def ahead(months: int) -> float:
        date = pd.Timestamp("2023-12-01") + pd.DateOffset(months=months)
        return _level(index, str(date.date())) - last

    def damped(h: int) -> float:
        return index.slope_ * TREND_DAMPING * (1 - TREND_DAMPING**h) / (1 - TREND_DAMPING)

    assert ahead(1) == pytest.approx(damped(1))
    assert ahead(6) == pytest.approx(damped(6))
    assert ahead(6) < 6 * index.slope_
    assert ahead(TREND_MAX_HORIZON_MONTHS + 24) == pytest.approx(damped(TREND_MAX_HORIZON_MONTHS))
    # Before the first month the first level is held.
    assert _level(index, "2020-01-01") == pytest.approx(_level(index, "2022-01-01"))


def test_hedonic_time_index_holds_level_in_a_flat_market():
    index = _hedonic_index(_trend_sales(0.0))
    assert abs(index.slope_) < 0.002


@pytest.fixture(scope="module")
def rising_market():
    return _trend_sales(0.01, months=36)


def _at_month(X: pd.DataFrame, date: str) -> pd.DataFrame:
    stamp = pd.Timestamp(date)
    moved = X.assign(transaction_year=stamp.year, transaction_month=stamp.month)
    return add_derived_features(moved)[list(FEATURE_COLUMNS)]


def test_time_trend_blend_extrapolates_to_the_valuation_month(rising_market):
    X = rising_market[list(FEATURE_COLUMNS)]
    y = rising_market["target_unit_price_twd"].to_numpy()
    trend = AnchorBlendRegressor(max_iter=60, learning_rate=0.1, time_trend=True).fit(X, y)
    flat = AnchorBlendRegressor(max_iter=60, learning_rate=0.1).fit(X, y)
    row = X.iloc[[-1]]

    # Both months lie after the data and share the calendar month; without coordinates
    # both use the station baseline prior, so only the extrapolated index separates them.
    unlocated = row.assign(twd97_x=np.nan, twd97_y=np.nan)
    soon = trend.predict(_at_month(unlocated, "2025-06-01"))[0]
    later = trend.predict(_at_month(unlocated, "2026-06-01"))[0]
    assert trend.extrapolates_time_trend
    expected = _level(trend.time_index_, "2026-06-01") - _level(trend.time_index_, "2025-06-01")
    assert expected > 0.005
    assert np.log(later / soon) == pytest.approx(expected, abs=1e-9)
    at_data_end = trend.predict(_at_month(unlocated, "2024-12-01"))[0]
    assert soon > at_data_end * 1.02
    # Without the index a tree model holds the last level.
    assert not flat.extrapolates_time_trend
    assert flat.predict(_at_month(unlocated, "2026-06-01"))[0] == pytest.approx(
        flat.predict(_at_month(unlocated, "2025-06-01"))[0]
    )


def test_time_trend_blend_predicts_later_months_with_less_lag(rising_market):
    columns = list(FEATURE_COLUMNS)
    train = rising_market.loc[rising_market["transaction_date"] < "2024-07-01"]
    later = rising_market.loc[rising_market["transaction_date"] >= "2024-07-01"]
    y = train["target_unit_price_twd"].to_numpy()

    def bias(model) -> float:
        model.fit(train[columns], y)
        actual = later["target_unit_price_twd"].to_numpy()
        return float(np.mean(np.log(actual / model.predict(later[columns]))))

    plain = bias(AnchorBlendRegressor(max_iter=60, learning_rate=0.1))
    trend = bias(AnchorBlendRegressor(max_iter=60, learning_rate=0.1, time_trend=True))
    assert plain > 0.03  # trees hold the last level in a rising market
    assert abs(trend) < plain / 2


def test_time_trend_restates_anchors_with_the_hedonic_index(rising_market):
    model = AnchorBlendRegressor(max_iter=20, learning_rate=0.1, time_trend=True)
    model.fit(
        rising_market[list(FEATURE_COLUMNS)], rising_market["target_unit_price_twd"].to_numpy()
    )
    assert model.price_index_ is model.time_index_


def test_time_trend_and_local_price_index_are_exclusive(rising_market):
    model = AnchorBlendRegressor(max_iter=5, time_trend=True, anchor_index_months=6)
    with pytest.raises(ValueError, match="time_trend"):
        model.fit(
            rising_market[list(FEATURE_COLUMNS)],
            rising_market["target_unit_price_twd"].to_numpy(),
        )


def test_models_pickled_before_the_time_trend_still_predict(fitted_model):
    model, train = fitted_model
    X = train[list(FEATURE_COLUMNS)].iloc[-3:]
    legacy = pickle.loads(pickle.dumps(model))
    del legacy.time_index_
    del legacy.__dict__["time_trend"]
    np.testing.assert_allclose(legacy.predict(X), model.predict(X))
    assert not legacy.extrapolates_time_trend
    assert clone(legacy).get_params()["time_trend"] is False
