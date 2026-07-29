from dataclasses import dataclass

import numpy as np
import pandas as pd

from qingpu_insight.community_registry import CommunityRegistry

COMMUNITY_FEATURE_COLUMNS = (
    "community_known",
    "community_prior_count_24m",
    "community_prior_median_twd_per_ping_24m",
    "community_premium_vs_station_24m",
)


def add_historical_community_features(
    frame: pd.DataFrame,
    registry: CommunityRegistry,
    *,
    lookback_months: int = 24,
    minimum_transactions: int = 5,
) -> pd.DataFrame:
    result = frame.copy()

    communities = []
    for _, row in result.iterrows():
        match = registry.match_transaction(
            address=row.get("address"),
            twd97_x=row.get("twd97_x"),
            twd97_y=row.get("twd97_y"),
            completion_year=row.get("completion_year"),
        )
        communities.append(match.community_id if match.community_id else None)

    result["community_id"] = communities
    result["community_known"] = result["community_id"].fillna("unknown")

    n = len(result)
    counts = np.zeros(n, dtype=int)
    medians = np.full(n, np.nan)
    premiums = np.full(n, np.nan)

    cid_arr = np.array(communities, dtype=object)
    station_arr = result["station_code"].values
    btype_arr = result["building_type"].values
    price_arr = result["target_unit_price_twd"].values.astype(float)
    date_arr = result["transaction_date"].values

    for i in range(n):
        cid = cid_arr[i]
        if cid is None:
            continue

        current_date = date_arr[i]
        prior = date_arr < current_date
        cutoff_date = current_date - pd.DateOffset(months=lookback_months)
        prior_window = prior & (date_arr >= cutoff_date)

        comm_mask = prior_window & (cid_arr == cid)
        prior_prices = price_arr[comm_mask]
        prior_count = prior_prices.size
        counts[i] = prior_count

        if prior_count >= minimum_transactions:
            median_price = float(np.median(prior_prices))
            medians[i] = median_price

            station = station_arr[i]
            btype = btype_arr[i]
            station_mask = prior_window & (station_arr == station) & (btype_arr == btype)
            station_prices = price_arr[station_mask]
            if station_prices.size > 0:
                station_median = float(np.median(station_prices))
                premiums[i] = median_price - station_median

    result["community_prior_count_24m"] = counts
    result["community_prior_median_twd_per_ping_24m"] = medians
    result["community_premium_vs_station_24m"] = premiums

    return result


@dataclass(frozen=True)
class CommunityFeatureValues:
    known: str
    prior_count_24m: int
    prior_median_twd_per_ping_24m: float | None
    premium_vs_station_24m: float | None


def build_community_feature_snapshot(
    frame: pd.DataFrame,
    *,
    cutoff: pd.Timestamp,
) -> dict[str, CommunityFeatureValues]:
    subset = frame[frame["transaction_date"] <= cutoff]

    result: dict[str, CommunityFeatureValues] = {}
    for community_known in subset["community_known"].unique():
        if community_known == "unknown" or pd.isna(community_known):
            continue

        comm_data = subset[subset["community_known"] == community_known]
        count = len(comm_data)

        median_price: float | None = None
        premium: float | None = None

        if count >= 5:
            median_price = float(comm_data["target_unit_price_twd"].median())

            station = comm_data.iloc[0]["station_code"]
            btype = comm_data.iloc[0]["building_type"]
            station_data = subset[
                (subset["station_code"] == station)
                & (subset["building_type"] == btype)
            ]
            if len(station_data) > 0:
                station_median = float(station_data["target_unit_price_twd"].median())
                premium = median_price - station_median

        result[community_known] = CommunityFeatureValues(
            known=community_known,
            prior_count_24m=count,
            prior_median_twd_per_ping_24m=median_price,
            premium_vs_station_24m=premium,
        )

    return result
