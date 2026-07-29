import pandas as pd
import pytest

from qingpu_insight.config import Station
from qingpu_insight.geo import assign_life_circle, station_points, wgs84_to_twd97


def test_wgs84_to_twd97_projects_listing_location() -> None:
    x, y = wgs84_to_twd97(121.2187076, 25.0094795)

    assert x == pytest.approx(272_000, abs=2_000)
    assert y == pytest.approx(2_766_000, abs=2_000)


def test_wgs84_to_twd97_rejects_nonfinite_coordinates() -> None:
    with pytest.raises(ValueError, match="finite"):
        wgs84_to_twd97(float("nan"), 25.0094795)


def test_station_points_requires_exact_official_doorplates() -> None:
    doorplates = pd.DataFrame(
        {
            "district": ["大園區", "中壢區", "中壢區"],
            "normalized_address": [
                "領航北路四段351號",
                "高鐵北路一段5號",
                "高鐵南路二段350號",
            ],
            "road_key": ["領航北路四段", "高鐵北路一段", "高鐵南路二段"],
            "house_number": [351, 5, 350],
            "twd97_x": [274000.0, 276000.0, 275000.0],
            "twd97_y": [2768000.0, 2767000.0, 2766000.0],
        }
    )
    exact_stations = (
        Station("A17", "領航站", "桃園市大園區領航北路四段351號"),
        Station("A18", "高鐵桃園站", "桃園市中壢區高鐵北路一段5號"),
        Station("A19", "桃園體育園區站", "桃園市中壢區高鐵南路二段350號"),
    )

    exact = station_points(exact_stations, doorplates)

    assert exact["station_code"].tolist() == ["A17", "A18", "A19"]
    with pytest.raises(ValueError, match="exact"):
        station_points(
            (
                Station("A17", "領航站", "桃園市大園區領航北路四段352號"),
                exact_stations[1],
                exact_stations[2],
            ),
            doorplates,
        )


@pytest.mark.parametrize(
    ("column", "invalid_coordinate"),
    [
        pytest.param("twd97_x", float("inf"), id="positive-infinity"),
        pytest.param("twd97_y", float("-inf"), id="negative-infinity"),
        pytest.param("twd97_x", "not-a-coordinate", id="non-numeric"),
    ],
)
def test_station_points_rejects_nonfinite_exact_coordinates(
    column: str, invalid_coordinate: object
) -> None:
    doorplates = pd.DataFrame(
        {
            "district": ["大園區", "中壢區", "中壢區"],
            "normalized_address": [
                "領航北路四段351號",
                "高鐵北路一段5號",
                "高鐵南路二段350號",
            ],
            "road_key": ["領航北路四段", "高鐵北路一段", "高鐵南路二段"],
            "house_number": [351, 5, 350],
            "twd97_x": [274000.0, 276000.0, 275000.0],
            "twd97_y": [2768000.0, 2767000.0, 2766000.0],
        }
    )
    doorplates[column] = doorplates[column].astype(object)
    doorplates.loc[0, column] = invalid_coordinate
    original = doorplates.copy(deep=True)
    stations = (
        Station("A17", "領航站", "桃園市大園區領航北路四段351號"),
        Station("A18", "高鐵桃園站", "桃園市中壢區高鐵北路一段5號"),
        Station("A19", "桃園體育園區站", "桃園市中壢區高鐵南路二段350號"),
    )

    with pytest.raises(ValueError, match="exact"):
        station_points(stations, doorplates)

    pd.testing.assert_frame_equal(doorplates, original)


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
