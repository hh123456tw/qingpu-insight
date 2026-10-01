"""Resale valuation anchored on the price history of the same building.

A plain log-price HGB cannot price a new project whose price level is absent from
training data. This model also fits an HGB on log(price / prior), where the prior is
the median price of the same building's earlier transfers (pre-completion or presale
deals, then earlier resales), then a nearby-sales median, then the station baseline.
The two log predictions are averaged.

Everything is decided from valuation inputs only (month, twd97 coordinates, building
age), so training rows and live valuations resolve anchors by the same rules, and
every anchor is dated strictly before the month being priced.

With time_trend, both members work net of a hedonic monthly price index
(HedonicTimeIndex): the plain HGB models log(price) - L(t), anchors are restated from
their sale month to the month being priced, and months after the training data get the
index extrapolated with a damped recent trend. Trees alone hold the last level, which
underestimates valuations made months after the data cutoff in a rising market.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.ensemble import HistGradientBoostingRegressor

from qingpu_insight.model_features import net_unit_prices

ANCHOR_RADIUS_M = 80.0
COMPLETION_TOLERANCE = pd.Timedelta(days=round(1.5 * 365.25))
MIN_ANCHORS = 3
PRESALE_MAX_AGE_YEARS = 5.0
KNN_NEIGHBOURS = 10
KNN_MAX_DISTANCE_M = 800.0

ANCHOR_COLUMNS = (
    "transaction_date",
    "completion_date",
    "twd97_x",
    "twd97_y",
    "unit_price_twd",
    "source",
)
BUILDING_SOURCES = ("precompletion", "presale", "resale")
OFFSET_FEATURES = ("log_prior", "prior_source", "knn_distance")
_MONOTONE_DECREASING = ("numeric__building_area_ping", "numeric__building_age_years")


def build_anchor_table(
    precompletion: pd.DataFrame | None,
    presale: pd.DataFrame | None,
    parking_pool: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Anchor rows priced on the same net-of-parking basis as resale targets."""
    parts = []
    for frame, source in ((precompletion, "precompletion"), (presale, "presale")):
        if frame is None or frame.empty or not {"twd97_x", "twd97_y"} <= set(frame.columns):
            continue  # anchors need coordinates to identify the building
        unit, _ = net_unit_prices(frame, parking_pool if parking_pool is not None else frame)
        part = pd.DataFrame(
            {
                "transaction_date": pd.to_datetime(frame["transaction_date"]).to_numpy(),
                "completion_date": pd.to_datetime(frame["completion_date"]).to_numpy()
                if "completion_date" in frame
                else pd.NaT,
                "twd97_x": pd.to_numeric(frame["twd97_x"], errors="coerce").to_numpy(),
                "twd97_y": pd.to_numeric(frame["twd97_y"], errors="coerce").to_numpy(),
                "unit_price_twd": unit,
                "source": source,
            }
        )
        if source == "presale":
            part["completion_date"] = pd.NaT
        usable = (
            np.isfinite(part["twd97_x"])
            & np.isfinite(part["twd97_y"])
            & np.isfinite(part["unit_price_twd"])
            & part["unit_price_twd"].gt(0)
        )
        parts.append(part.loc[usable])
    if not parts:
        return pd.DataFrame(
            {
                "transaction_date": pd.Series(dtype="datetime64[ns]"),
                "completion_date": pd.Series(dtype="datetime64[ns]"),
                "twd97_x": pd.Series(dtype=float),
                "twd97_y": pd.Series(dtype=float),
                "unit_price_twd": pd.Series(dtype=float),
                "source": pd.Series(dtype=object),
            }
        )
    return pd.concat(parts, ignore_index=True).loc[:, list(ANCHOR_COLUMNS)]


def month_starts(X: pd.DataFrame) -> np.ndarray:
    return pd.to_datetime(
        {"year": X["transaction_year"], "month": X["transaction_month"], "day": 1}
    ).to_numpy("datetime64[ns]")


def implied_completion(X: pd.DataFrame) -> np.ndarray:
    age_days = pd.to_numeric(X["building_age_years"], errors="coerce").to_numpy(float) * 365.25
    completion = month_starts(X).astype("datetime64[D]").astype(float) - age_days
    return completion  # days since epoch; NaN when age is unknown


PRICE_INDEX_MIN_ROWS = 30


def _month_ordinals(dates) -> np.ndarray:
    stamps = pd.DatetimeIndex(pd.to_datetime(np.asarray(dates, "datetime64[ns]")))
    return (stamps.year * 12 + stamps.month - 1).to_numpy(int)


class LocalPriceIndex:
    """Point-in-time local log price level by month.

    level(m) is the median log unit price of the sales in the `window_months` months
    before month m, so it never uses sales from month m or later. Months without
    PRICE_INDEX_MIN_ROWS sales carry the nearest earlier level (or the first level).
    """

    def __init__(self, dates, prices, window_months: int = 6) -> None:
        months = _month_ordinals(dates)
        logs = np.log(np.asarray(prices, float))
        usable = np.isfinite(logs)
        months, logs = months[usable], logs[usable]
        self.first_ = int(months.min()) + 1 if len(months) else 0
        last = int(months.max()) + 1 if len(months) else 0
        levels = np.full(last - self.first_ + 1, np.nan)
        for offset, month in enumerate(range(self.first_, last + 1)):
            window = logs[(months >= month - window_months) & (months < month)]
            if len(window) >= PRICE_INDEX_MIN_ROWS:
                levels[offset] = np.median(window)
        levels = pd.Series(levels).ffill().bfill().to_numpy(float)
        self.levels_ = np.nan_to_num(levels, nan=0.0)

    def level(self, dates) -> np.ndarray:
        """Log level for the month containing each date, from sales before that month."""
        if not len(self.levels_):
            return np.zeros(len(dates))
        offsets = np.clip(_month_ordinals(dates) - self.first_, 0, len(self.levels_) - 1)
        return self.levels_[offsets]

    def level_after(self, dates) -> np.ndarray:
        """Log level at the end of each date's month (includes that month's sales)."""
        next_month = (
            pd.DatetimeIndex(pd.to_datetime(np.asarray(dates, "datetime64[ns]")))
            .to_period("M")
            .to_timestamp()
            + pd.DateOffset(months=1)
        )
        return self.level(next_month.to_numpy("datetime64[ns]"))


# Hedonic time index (issue log §22). Ridge penalties: month-to-month level changes
# (random walk) and building fixed effects. The extrapolation is a damped trend.
TREND_STEP_ALPHA = 200.0
TREND_BUILDING_ALPHA = 1.0
TREND_MOMENTUM_MONTHS = 6
TREND_DAMPING = 0.9
TREND_MAX_HORIZON_MONTHS = 12
TREND_BUILDING_GRID_M = ANCHOR_RADIUS_M


def building_keys(X: pd.DataFrame) -> np.ndarray:
    """Coarse building identity from valuation inputs: coordinate grid cell, implied
    completion year and station (rows without coordinates share a cell)."""
    xs = pd.to_numeric(X["twd97_x"], errors="coerce").to_numpy(float)
    ys = pd.to_numeric(X["twd97_y"], errors="coerce").to_numpy(float)
    known = np.isfinite(xs) & np.isfinite(ys)
    cell_x = np.where(known, np.round(np.nan_to_num(xs) / TREND_BUILDING_GRID_M), -1)
    cell_y = np.where(known, np.round(np.nan_to_num(ys) / TREND_BUILDING_GRID_M), -1)
    completion_year = implied_completion(X) / 365.25
    year = np.where(np.isfinite(completion_year), np.round(completion_year), -1)
    stations = X["station_code"].astype(str).to_numpy()
    return np.array(
        [
            f"{int(a)}|{int(b)}|{int(c)}|{s}"
            for a, b, c, s in zip(cell_x, cell_y, year, stations, strict=True)
        ],
        dtype=object,
    )


class HedonicTimeIndex:
    """Monthly log price level net of building mix, with a damped extrapolation.

    A ridge regression of log unit price on monthly level steps (random-walk penalty),
    building fixed effects and log area / floor ratio. level(m) is the fitted level for
    months in the data; after the last data month T it is
    L(T) + slope * sum_{j=1..h} TREND_DAMPING**j with h capped at
    TREND_MAX_HORIZON_MONTHS and slope the average monthly change over the last
    TREND_MOMENTUM_MONTHS months. Before the first month the first level is held.
    """

    def __init__(self, dates, prices, buildings, covariates=None) -> None:
        log_prices = np.log(np.asarray(prices, float))
        usable = np.isfinite(log_prices)
        months = _month_ordinals(dates)[usable]
        log_prices = log_prices[usable]
        self.first_ = int(months.min())
        self.last_ = int(months.max())
        n_months = self.last_ - self.first_ + 1
        n = len(log_prices)

        blocks = [np.ones((n, 1)), (months - self.first_)[:, None] >= np.arange(1, n_months)]
        penalties = [np.zeros(1), np.full(n_months - 1, TREND_STEP_ALPHA)]
        codes, unique = pd.factorize(pd.Series(np.asarray(buildings, object)[usable]))
        one_hot = np.zeros((n, len(unique)))
        one_hot[np.arange(n), codes] = 1.0
        blocks.append(one_hot)
        penalties.append(np.full(len(unique), TREND_BUILDING_ALPHA))
        if covariates is not None:
            values = np.asarray(covariates, float)[usable]
            medians = np.nanmedian(np.where(np.isfinite(values), values, np.nan), axis=0)
            values = np.where(np.isfinite(values), values, np.nan_to_num(medians))
            spread = values.std(axis=0)
            keep = spread > 1e-9
            blocks.append((values[:, keep] - values[:, keep].mean(axis=0)) / spread[keep])
            penalties.append(np.ones(int(keep.sum())))
        design = np.hstack([block.astype(float) for block in blocks])
        gram = design.T @ design + np.diag(np.concatenate(penalties))
        beta = np.linalg.solve(gram, design.T @ log_prices)

        self.levels_ = np.concatenate([[0.0], np.cumsum(beta[1:n_months])])
        span = min(TREND_MOMENTUM_MONTHS, n_months - 1)
        self.slope_ = (
            float((self.levels_[-1] - self.levels_[-1 - span]) / span) if span > 0 else 0.0
        )

    @classmethod
    def from_rows(cls, X: pd.DataFrame, prices) -> HedonicTimeIndex:
        covariates = np.c_[
            np.log(pd.to_numeric(X["building_area_ping"], errors="coerce").to_numpy(float)),
            pd.to_numeric(X["floor_ratio"], errors="coerce").to_numpy(float),
        ]
        return cls(month_starts(X), prices, building_keys(X), covariates)

    def level(self, dates) -> np.ndarray:
        months = _month_ordinals(dates)
        inside = np.clip(months - self.first_, 0, len(self.levels_) - 1)
        levels = self.levels_[inside].astype(float)
        horizon = np.clip(months - self.last_, 0, TREND_MAX_HORIZON_MONTHS)
        damped = TREND_DAMPING * (1 - TREND_DAMPING**horizon) / (1 - TREND_DAMPING)
        return levels + self.slope_ * damped

    def level_after(self, dates) -> np.ndarray:
        """Level of each sale's own month (the fit already includes that month)."""
        return self.level(dates)


class _Pool:
    def __init__(self, table: pd.DataFrame, index: LocalPriceIndex | None = None) -> None:
        self.x = table["twd97_x"].to_numpy(float)
        self.y = table["twd97_y"].to_numpy(float)
        self.dates = table["transaction_date"].to_numpy("datetime64[ns]")
        completion = table["completion_date"].to_numpy("datetime64[ns]")
        self.completion = np.where(
            np.isnat(completion), np.nan, completion.astype("datetime64[D]").astype(float)
        )
        self.price = table["unit_price_twd"].to_numpy(float)
        self.source = table["source"].to_numpy(object)
        self.tree = cKDTree(np.c_[self.x, self.y]) if len(table) else None
        # Local price level just after each sale, for restating it to a later month.
        self.level = index.level_after(self.dates) if index is not None and len(table) else None

    def prices(self, idx: np.ndarray, row_level: float | None) -> np.ndarray:
        if self.level is None or row_level is None:
            return self.price[idx]
        return self.price[idx] * np.exp(row_level - self.level[idx])


def anchor_priors(
    X: pd.DataFrame,
    anchors: pd.DataFrame,
    history: pd.DataFrame,
    cutoff: np.datetime64 | None = None,
    price_index: LocalPriceIndex | None = None,
) -> pd.DataFrame:
    """Per-row prior price, its source, anchor count and distance to the 10th neighbour.

    Only anchors dated before min(row month start, cutoff) are used. Building matches
    need to be within ANCHOR_RADIUS_M; pre-completion and resale anchors must also have a
    completion date within COMPLETION_TOLERANCE of the row's implied completion, and
    presale anchors (no completion date) only count for buildings at most
    PRESALE_MAX_AGE_YEARS old. prior is NaN where the station baseline should be used.
    With a price_index, every anchor price is first restated from its sale month to that
    limit month by the change in the local price level, all from sales before the limit.
    """
    n = len(X)
    limits = month_starts(X)
    if cutoff is not None:
        limits = np.minimum(limits, np.datetime64(cutoff, "ns"))
    xs = pd.to_numeric(X["twd97_x"], errors="coerce").to_numpy(float)
    ys = pd.to_numeric(X["twd97_y"], errors="coerce").to_numpy(float)
    ages = pd.to_numeric(X["building_age_years"], errors="coerce").to_numpy(float)
    completions = implied_completion(X)
    tolerance = COMPLETION_TOLERANCE.days
    row_levels = price_index.level(limits) if price_index is not None and n else None

    anchor_pool = _Pool(anchors, price_index)
    history_pool = _Pool(history, price_index)
    history_order = np.argsort(history_pool.dates, kind="stable")
    history_dates = history_pool.dates[history_order]

    prior = np.full(n, np.nan)
    source = np.full(n, "baseline", dtype=object)
    count = np.zeros(n, dtype=int)
    knn_distance = np.full(n, np.nan)

    def building_matches(pool: _Pool, i: int) -> np.ndarray:
        if pool.tree is None:
            return np.empty(0, dtype=int)
        idx = np.asarray(pool.tree.query_ball_point([xs[i], ys[i]], ANCHOR_RADIUS_M), int)
        if not len(idx):
            return idx
        idx = idx[pool.dates[idx] < limits[i]]
        has_completion = np.isfinite(pool.completion[idx])
        same_building = np.abs(pool.completion[idx] - completions[i]) <= tolerance
        young = np.isfinite(ages[i]) and ages[i] <= PRESALE_MAX_AGE_YEARS
        keep = np.where(has_completion, same_building, young)
        return idx[keep]

    for i in range(n):
        if not (np.isfinite(xs[i]) and np.isfinite(ys[i])):
            continue
        row_level = row_levels[i] if row_levels is not None else None
        if np.isfinite(completions[i]) or np.isfinite(ages[i]):
            pre = building_matches(anchor_pool, i)
            if len(pre) >= MIN_ANCHORS:
                prior[i] = np.median(anchor_pool.prices(pre, row_level))
                labels = anchor_pool.source[pre]
                source[i] = "precompletion" if np.any(labels == "precompletion") else "presale"
                count[i] = len(pre)
            else:
                same = building_matches(history_pool, i)
                if len(same) >= MIN_ANCHORS:
                    prior[i] = np.median(history_pool.prices(same, row_level))
                    source[i] = "resale"
                    count[i] = len(same)
        earlier = history_order[: np.searchsorted(history_dates, limits[i], "left")]
        if len(earlier) >= KNN_NEIGHBOURS:
            distances = np.hypot(history_pool.x[earlier] - xs[i], history_pool.y[earlier] - ys[i])
            nearest = np.argpartition(distances, KNN_NEIGHBOURS - 1)[:KNN_NEIGHBOURS]
            knn_distance[i] = distances[nearest].max()
            close = earlier[nearest][distances[nearest] <= KNN_MAX_DISTANCE_M]
            if source[i] == "baseline" and len(close):
                prior[i] = np.median(history_pool.prices(close, row_level))
                source[i] = "knn"
    return pd.DataFrame(
        {
            "prior": prior,
            "prior_source": source,
            "anchor_count": count,
            "knn_distance": knn_distance,
        },
        index=X.index,
    )


def _history_table(X: pd.DataFrame, y: np.ndarray) -> pd.DataFrame:
    dates = month_starts(X)
    completion_days = implied_completion(X)
    completion = pd.to_datetime(completion_days, unit="D", errors="coerce")
    table = pd.DataFrame(
        {
            "transaction_date": dates,
            "completion_date": completion,
            "twd97_x": pd.to_numeric(X["twd97_x"], errors="coerce").to_numpy(float),
            "twd97_y": pd.to_numeric(X["twd97_y"], errors="coerce").to_numpy(float),
            "unit_price_twd": np.asarray(y, float),
            "source": "resale",
        }
    )
    usable = (
        np.isfinite(table["twd97_x"])
        & np.isfinite(table["twd97_y"])
        & table["completion_date"].notna()
    )
    return table.loc[usable].reset_index(drop=True)


class AnchorBlendRegressor(RegressorMixin, BaseEstimator):
    """Average of a log-price HGB and a same-building price-anchor offset HGB."""

    # Models pickled before the time trend existed load without these attributes.
    time_trend = False
    time_index_ = None

    def __init__(
        self,
        anchor_table: pd.DataFrame | None = None,
        learning_rate: float = 0.04,
        max_iter: int = 600,
        max_leaf_nodes: int = 31,
        l2_regularization: float = 1.0,
        random_state: int = 42,
        anchor_index_months: int | None = None,
        time_trend: bool = False,
    ) -> None:
        self.anchor_table = anchor_table
        self.learning_rate = learning_rate
        self.max_iter = max_iter
        self.max_leaf_nodes = max_leaf_nodes
        self.l2_regularization = l2_regularization
        self.random_state = random_state
        self.anchor_index_months = anchor_index_months
        self.time_trend = time_trend

    @property
    def extrapolates_time_trend(self) -> bool:
        """Whether predictions move with the transaction month beyond the training data."""
        return self.time_index_ is not None

    def _time_level(self, dates: np.ndarray) -> np.ndarray:
        if self.time_index_ is None:
            return np.zeros(len(dates))
        return self.time_index_.level(dates)

    def _hgb(self, preprocessor) -> HistGradientBoostingRegressor:
        names = preprocessor.get_feature_names_out()
        return HistGradientBoostingRegressor(
            learning_rate=self.learning_rate,
            max_iter=self.max_iter,
            max_leaf_nodes=self.max_leaf_nodes,
            l2_regularization=self.l2_regularization,
            random_state=self.random_state,
            monotonic_cst=[-1 if name in _MONOTONE_DECREASING else 0 for name in names],
        )

    def _anchors(self) -> pd.DataFrame:
        if self.anchor_table is None:
            return build_anchor_table(None, None)
        return self.anchor_table

    def _priors(self, X: pd.DataFrame, cutoff: np.datetime64 | None) -> pd.DataFrame:
        # Artifacts pickled before the index existed have no price_index_ attribute.
        index = getattr(self, "price_index_", None)
        priors = anchor_priors(X, self._anchors(), self.history_, cutoff, price_index=index)
        missing = priors["prior"].isna().to_numpy()
        if missing.any():
            baseline_rows = X.loc[missing, ["station_code", "building_type"]]
            priors.loc[missing, "prior"] = self.baseline_.predict(baseline_rows)
        return priors

    def _offset_frame(self, X: pd.DataFrame, priors: pd.DataFrame) -> pd.DataFrame:
        frame = X.loc[:, list(self.feature_columns_)].copy()
        frame["log_prior"] = np.log(priors["prior"].to_numpy(float))
        frame["prior_source"] = priors["prior_source"].to_numpy()
        frame["knn_distance"] = priors["knn_distance"].to_numpy(float)
        return frame

    def fit(self, X: pd.DataFrame, y, sample_weight=None) -> AnchorBlendRegressor:
        from qingpu_insight.model_training import (
            CATEGORICAL_FEATURES,
            NUMERIC_FEATURES,
            RecentMedianBaseline,
            make_preprocessor,
        )

        if self.time_trend and self.anchor_index_months:
            raise ValueError("time_trend restates anchors itself; unset anchor_index_months")
        X = X.reset_index(drop=True)
        y = np.asarray(y, float)
        # Every column the caller passes that the preprocessors know is a model feature.
        known = (set(NUMERIC_FEATURES) | set(CATEGORICAL_FEATURES)) - set(OFFSET_FEATURES)
        self.feature_columns_ = tuple(c for c in X.columns if c in known)
        dates = month_starts(X)
        self.fit_until_ = (pd.Timestamp(dates.max()) + pd.DateOffset(months=1)).to_datetime64()
        self.history_ = _history_table(X, y)
        self.time_index_ = HedonicTimeIndex.from_rows(X, y) if self.time_trend else None
        if self.time_index_ is not None:
            self.price_index_ = self.time_index_
        elif self.anchor_index_months:
            self.price_index_ = LocalPriceIndex(dates, y, window_months=self.anchor_index_months)
        else:
            self.price_index_ = None
        self.baseline_ = RecentMedianBaseline(months=12).fit(
            pd.DataFrame(
                {
                    "transaction_date": dates,
                    "station_code": X["station_code"].to_numpy(),
                    "building_type": X["building_type"].to_numpy(),
                    "target_unit_price_twd": y,
                }
            )
        )

        plain_X = X.loc[:, list(self.feature_columns_)]
        self.plain_preprocessor_ = make_preprocessor(self.feature_columns_).fit(plain_X)
        self.plain_model_ = self._hgb(self.plain_preprocessor_).fit(
            self.plain_preprocessor_.transform(plain_X),
            np.log(y) - self._time_level(dates),
            sample_weight=sample_weight,
        )

        priors = self._priors(X, cutoff=None)
        offset_X = self._offset_frame(X, priors)
        offset_columns = self.feature_columns_ + OFFSET_FEATURES
        self.offset_preprocessor_ = make_preprocessor(offset_columns).fit(offset_X)
        self.offset_model_ = self._hgb(self.offset_preprocessor_).fit(
            self.offset_preprocessor_.transform(offset_X),
            np.log(y) - offset_X["log_prior"].to_numpy(),
            sample_weight=sample_weight,
        )
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        X = X.reset_index(drop=True)
        dates = month_starts(X)
        priors = self._priors(X, cutoff=self.fit_until_)
        plain_log = self.plain_model_.predict(
            self.plain_preprocessor_.transform(X.loc[:, list(self.feature_columns_)])
        ) + self._time_level(dates)
        offset_X = self._offset_frame(X, priors)
        # Anchors are restated up to the anchor cutoff; move them on to the priced month.
        limits = np.minimum(dates, np.datetime64(self.fit_until_, "ns"))
        offset_log = (
            self.offset_model_.predict(self.offset_preprocessor_.transform(offset_X))
            + offset_X["log_prior"].to_numpy()
            + self._time_level(dates)
            - self._time_level(limits)
        )
        return np.exp((plain_log + offset_log) / 2)

    def prior_sources(self, X: pd.DataFrame) -> np.ndarray:
        X = X.reset_index(drop=True)
        return self._priors(X, cutoff=self.fit_until_)["prior_source"].to_numpy()

    def anchored_mask(self, X: pd.DataFrame) -> np.ndarray:
        return np.isin(self.prior_sources(X), BUILDING_SOURCES)
