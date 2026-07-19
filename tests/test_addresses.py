import pandas as pd

from qingpu_insight.addresses import match_addresses, normalize_address


def test_normalize_address_removes_city_district_and_width_variants() -> None:
    assert normalize_address("桃園市中壢區領航北路四段351號") == "領航北路四段351號"


def test_match_addresses_marks_exact_and_nearest_number() -> None:
    transactions = pd.DataFrame(
        {
            "district": ["中壢區", "中壢區"],
            "address": ["領航北路四段351號", "領航北路四段9號"],
        }
    )
    doorplates = pd.DataFrame(
        {
            "district": ["中壢區", "中壢區"],
            "normalized_address": ["領航北路四段351號", "領航北路四段350號"],
            "road_key": ["領航北路四段", "領航北路四段"],
            "house_number": [5, 10],
            "twd97_x": [276000.0, 276010.0],
            "twd97_y": [2767000.0, 2767010.0],
        }
    )

    result = match_addresses(transactions, doorplates)

    assert result["match_quality"].tolist() == ["exact", "nearest_number"]
    assert result["coordinate_eligible"].tolist() == [True, True]
