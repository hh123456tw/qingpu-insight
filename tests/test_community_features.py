import pandas as pd
import pytest

from qingpu_insight.community_features import (
    COMMUNITY_FEATURE_COLUMNS,
    CommunityFeatureValues,
    add_historical_community_features,
    build_community_feature_snapshot,
)
from qingpu_insight.community_registry import CommunityRegistry


@pytest.fixture(scope="module")
def _registry() -> CommunityRegistry:
    return CommunityRegistry(
        _data=pd.DataFrame({
            "community_id": ["comm_a", "comm_b", "comm_c"],
            "canonical_name": ["Community A", "Community B", "Community C"],
            "aliases": ["", "", ""],
            "station_code": ["A17", "A17", "A18"],
            "address_patterns": ["A Street", "B Avenue", "C Road"],
            "twd97_x": [276000.0, 276100.0, 277000.0],
            "twd97_y": [2767000.0, 2767100.0, 2768000.0],
            "completion_year": [2015, 2016, 2017],
            "source_notes": ["registry", "registry", "registry"],
        }),
        _version="test",
    )


class TestChronologicalIsolation:
    """Step 1 & 3: strictly-prior community statistics."""

    def test_prior_transactions_are_strictly_chronological(self, _registry):
        """Same-date transactions must not see each other; only strictly earlier rows."""
        frame = pd.DataFrame({
            "address": [
                "A Street 1", "A Street 2", "A Street 3", "A Street 4",
                "B Avenue 1",
            ],
            "transaction_date": pd.to_datetime([
                "2022-06-15", "2023-01-20", "2024-03-10", "2024-03-10",
                "2023-08-01",
            ]),
            "station_code": ["A17"] * 5,
            "building_type": ["住宅大樓"] * 5,
            "target_unit_price_twd": [
                300000.0, 310000.0, 320000.0, 330000.0,
                290000.0,
            ],
        })

        result = add_historical_community_features(
            frame, _registry, minimum_transactions=1
        )

        # Row 0 (2022-06-15, comm_a): no prior
        assert result.loc[0, "community_known"] == "comm_a"
        assert result.loc[0, "community_prior_count_24m"] == 0
        assert pd.isna(result.loc[0, "community_prior_median_twd_per_ping_24m"])

        # Row 1 (2023-01-20, comm_a): prior = row 0
        assert result.loc[1, "community_prior_count_24m"] == 1
        assert result.loc[1, "community_prior_median_twd_per_ping_24m"] == 300000.0

        # Row 2 (2024-03-10, comm_a): prior = rows 0,1
        assert result.loc[2, "community_prior_count_24m"] == 2
        assert result.loc[2, "community_prior_median_twd_per_ping_24m"] == 305000.0

        # Row 3 (2024-03-10, comm_a, same date as row 2): prior = rows 0,1 only
        assert result.loc[3, "community_prior_count_24m"] == 2
        assert result.loc[3, "community_prior_median_twd_per_ping_24m"] == 305000.0

    def test_24_month_window_excludes_old_transactions(self, _registry):
        """Transactions outside the 24-month lookback window are excluded."""
        frame = pd.DataFrame({
            "address": ["A Street 1", "A Street 2", "A Street 3"],
            "transaction_date": pd.to_datetime([
                "2020-01-15", "2021-12-01", "2024-03-10",
            ]),
            "station_code": ["A17"] * 3,
            "building_type": ["住宅大樓"] * 3,
            "target_unit_price_twd": [200000.0, 300000.0, 310000.0],
        })

        result = add_historical_community_features(
            frame, _registry, lookback_months=24, minimum_transactions=1
        )

        assert result.loc[0, "community_prior_count_24m"] == 0

        # Row 1: row 0 is within 24 months (23 months prior)
        assert result.loc[1, "community_prior_count_24m"] == 1

        # Row 2 (2024-03-10): lookback to 2022-03-10 — rows 0 and 1 are outside
        assert result.loc[2, "community_prior_count_24m"] == 0

    def test_minimum_transactions_threshold(self, _registry):
        """When community count < minimum_transactions, median and premium are NaN."""
        frame = pd.DataFrame({
            "address": ["A Street 1", "A Street 2", "A Street 3", "A Street 4"],
            "transaction_date": pd.to_datetime([
                "2022-01-15", "2022-06-20", "2023-03-10", "2023-08-05",
            ]),
            "station_code": ["A17"] * 4,
            "building_type": ["住宅大樓"] * 4,
            "target_unit_price_twd": [300000.0, 310000.0, 320000.0, 330000.0],
        })

        result = add_historical_community_features(frame, _registry, minimum_transactions=5)

        assert result.loc[2, "community_prior_count_24m"] == 2
        assert pd.isna(result.loc[2, "community_prior_median_twd_per_ping_24m"])
        assert pd.isna(result.loc[2, "community_premium_vs_station_24m"])

        assert result.loc[3, "community_prior_count_24m"] == 3
        assert pd.isna(result.loc[3, "community_prior_median_twd_per_ping_24m"])
        assert pd.isna(result.loc[3, "community_premium_vs_station_24m"])

    def test_premium_vs_station_comparison(self, _registry):
        """Premium is community_median − station_median for same station/building_type."""
        frame = pd.DataFrame({
            "address": [
                "A Street 1", "A Street 2",
                "B Avenue 1", "B Avenue 2",
                "A Street 3",
            ],
            "transaction_date": pd.to_datetime([
                "2022-06-15", "2023-01-20",
                "2022-08-01", "2023-03-10",
                "2024-03-10",
            ]),
            "station_code": ["A17"] * 5,
            "building_type": ["住宅大樓"] * 5,
            "target_unit_price_twd": [
                300000.0, 310000.0,
                280000.0, 290000.0,
                320000.0,
            ],
        })

        result = add_historical_community_features(
            frame, _registry, minimum_transactions=1
        )

        # Row 4 (comm_a, 2024-03-10):
        # Community prior: rows 0,1 (300k,310k) → median 305k
        # Station prior (A17/住宅大樓): rows 0,1,2,3 (300k,310k,280k,290k) → median 295k
        assert result.loc[4, "community_prior_count_24m"] == 2
        assert result.loc[4, "community_prior_median_twd_per_ping_24m"] == 305000.0
        assert result.loc[4, "community_premium_vs_station_24m"] == pytest.approx(10000.0)

    def test_unknown_community(self, _registry):
        """Unmatched transactions get community_known='unknown' and NaN stats."""
        frame = pd.DataFrame({
            "address": ["Unknown Place 999"],
            "transaction_date": pd.to_datetime(["2024-06-15"]),
            "station_code": ["A17"],
            "building_type": ["住宅大樓"],
            "target_unit_price_twd": [300000.0],
        })

        result = add_historical_community_features(frame, _registry)

        assert result.loc[0, "community_known"] == "unknown"
        assert result.loc[0, "community_prior_count_24m"] == 0
        assert pd.isna(result.loc[0, "community_prior_median_twd_per_ping_24m"])
        assert pd.isna(result.loc[0, "community_premium_vs_station_24m"])

    def test_output_has_required_columns(self, _registry):
        """Output contains all COMMUNITY_FEATURE_COLUMNS."""
        frame = pd.DataFrame({
            "address": ["A Street 1"],
            "transaction_date": pd.to_datetime(["2024-06-15"]),
            "station_code": ["A17"],
            "building_type": ["住宅大樓"],
            "target_unit_price_twd": [300000.0],
        })

        result = add_historical_community_features(frame, _registry)

        for col in COMMUNITY_FEATURE_COLUMNS:
            assert col in result.columns, f"Missing column: {col}"

    def test_preserves_original_row_order(self, _registry):
        """Output rows are in input order, not sorted order."""
        frame = pd.DataFrame({
            "address": ["A Street 3", "A Street 1", "A Street 2"],
            "transaction_date": pd.to_datetime([
                "2024-03-10", "2022-06-15", "2023-01-20",
            ]),
            "station_code": ["A17"] * 3,
            "building_type": ["住宅大樓"] * 3,
            "target_unit_price_twd": [320000.0, 300000.0, 310000.0],
        })

        result = add_historical_community_features(
            frame, _registry, minimum_transactions=1
        )

        assert result.loc[0, "community_prior_count_24m"] == 2
        assert result.loc[1, "community_prior_count_24m"] == 0
        assert result.loc[2, "community_prior_count_24m"] == 1


class TestCommunityFeatureSnapshot:
    """Step 4: immutable inference snapshot."""

    def test_respects_cutoff(self):
        """No row after cutoff is included."""
        frame = pd.DataFrame({
            "community_known": ["comm_a"] * 6,
            "station_code": ["A17"] * 6,
            "building_type": ["住宅大樓"] * 6,
            "transaction_date": pd.to_datetime([
                "2022-01-15", "2022-06-20", "2023-03-10",
                "2023-08-05", "2024-02-01", "2024-06-20",
            ]),
            "target_unit_price_twd": [
                300000.0, 310000.0, 320000.0,
                330000.0, 340000.0, 350000.0,
            ],
        })

        snapshot = build_community_feature_snapshot(
            frame, cutoff=pd.Timestamp("2024-06-01")
        )

        assert "comm_a" in snapshot
        values = snapshot["comm_a"]
        assert values.prior_count_24m == 5
        assert values.prior_median_twd_per_ping_24m == 320000.0

    def test_omits_unreliable_values(self):
        """When count < 5, median and premium are None."""
        frame = pd.DataFrame({
            "community_known": ["comm_a"] * 4,
            "station_code": ["A17"] * 4,
            "building_type": ["住宅大樓"] * 4,
            "transaction_date": pd.to_datetime([
                "2022-01-15", "2022-06-20", "2023-03-10", "2023-08-05",
            ]),
            "target_unit_price_twd": [300000.0, 310000.0, 320000.0, 330000.0],
        })

        snapshot = build_community_feature_snapshot(
            frame, cutoff=pd.Timestamp("2025-01-01")
        )

        values = snapshot["comm_a"]
        assert values.prior_count_24m == 4
        assert values.prior_median_twd_per_ping_24m is None
        assert values.premium_vs_station_24m is None

    def test_dataclass_is_frozen(self):
        """CommunityFeatureValues is immutable."""
        values = CommunityFeatureValues(
            known="comm_a",
            prior_count_24m=5,
            prior_median_twd_per_ping_24m=300000.0,
            premium_vs_station_24m=5000.0,
        )
        with pytest.raises(AttributeError):
            values.known = "other"

    def test_keys_are_community_ids(self):
        """Snapshot dict is keyed by community_id."""
        frame = pd.DataFrame({
            "community_known": ["comm_a"] * 5 + ["comm_b"] * 5,
            "station_code": ["A17"] * 10,
            "building_type": ["住宅大樓"] * 10,
            "transaction_date": pd.to_datetime([
                "2022-06-15", "2023-01-20", "2023-08-10", "2024-02-01", "2024-06-15",
            ] * 2),
            "target_unit_price_twd": [
                300000.0, 310000.0, 320000.0, 330000.0, 340000.0,
            ] * 2,
        })

        snapshot = build_community_feature_snapshot(
            frame, cutoff=pd.Timestamp("2025-01-01")
        )

        assert "comm_a" in snapshot
        assert "comm_b" in snapshot
