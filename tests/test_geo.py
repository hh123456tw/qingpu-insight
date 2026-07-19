import pandas as pd

from qingpu_insight.geo import assign_life_circle


def test_assign_life_circle_uses_nearest_station_inside_radius() -> None:
    transactions = pd.DataFrame(
        {
            "twd97_x": [100.0, 5_000.0],
            "twd97_y": [0.0, 0.0],
            "coordinate_eligible": [True, True],
        }
    )
    stations = pd.DataFrame(
        {
            "station_code": ["A17", "A18", "A19"],
            "twd97_x": [0.0, 1_000.0, 2_000.0],
            "twd97_y": [0.0, 0.0, 0.0],
        }
    )

    result = assign_life_circle(transactions, stations, radius_m=2_000.0)

    assert result.loc[0, "station_code"] == "A17"
    assert result.loc[0, "station_distance_m"] == 100.0
    assert pd.isna(result.loc[1, "station_code"])
