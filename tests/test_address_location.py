from pathlib import Path

import pandas as pd
import pytest

from qingpu_insight.address_location import (
    AddressLocatorUnavailable,
    DoorplateAddressIndex,
    LazyDoorplateIndex,
    location_from_twd97,
    location_from_wgs84,
)
from qingpu_insight.geo import station_from_coords, wgs84_to_twd97

HEADER = "省市縣市代碼,鄉鎮市區代碼,村里,鄰,街路段,地區,巷,弄,號,橫座標,縱座標\n"


def _doorplates(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "doorplates.csv"
    path.write_text(HEADER + "".join(row + "\n" for row in rows), encoding="utf-8-sig")
    return path


@pytest.fixture
def index(tmp_path: Path) -> DoorplateAddressIndex:
    path = _doorplates(
        tmp_path,
        [
            "68,6800200,青埔里,001,青埔路二段,,,,２８９號,272032.43,2766404.50",
            "68,6800200,青埔里,001,青埔路二段,,,,２８９號二樓,272032.43,2766404.50",
            "68,6800200,青埔里,001,青埔路二段,,,,２９５號,272050.00,2766390.00",
            "68,6800200,青埔里,001,青埔路二段,,１００巷,,３號,271500.00,2766000.00",
            "68,6800200,青埔里,001,青埔路二段,,,,１７５之２號,272100.00,2766300.00",
            "68,6800600,橫峰里,001,領航北路四段,,,,３５１號,273500.00,2767800.00",
            "68,6800200,興和里,001,文化路,,,,１號,270000.00,2765000.00",
            "68,6800600,橫峰里,001,文化路,,,,１號,276000.00,2769000.00",
            "68,6800100,三元里,015,青埔路二段,,,,２８９號,282372.09,2766451.47",
        ],
    )
    return DoorplateAddressIndex.from_csv(path)


def test_exact_doorplate_resolves_with_city_district_width_and_floor_noise(index) -> None:
    match = index.resolve("桃園市中壢區青埔路2段２８９號5樓之3")

    assert match is not None
    assert (match.twd97_x, match.twd97_y) == pytest.approx((272032.43, 2766404.50))
    assert match.match_quality == "exact"


def test_exact_doorplate_resolves_without_district_when_unambiguous(index) -> None:
    match = index.resolve("領航北路四段351號")

    assert match is not None
    assert match.match_quality == "exact"
    assert (match.twd97_x, match.twd97_y) == pytest.approx((273500.0, 2767800.0))


def test_village_and_neighbourhood_are_ignored(index) -> None:
    match = index.resolve("桃園市中壢區青埔里１鄰青埔路二段289號")

    assert match is not None
    assert match.match_quality == "exact"


def test_hyphen_sub_number_is_read_as_zhi(index) -> None:
    match = index.resolve("中壢區青埔路二段175-2號")

    assert match is not None
    assert match.match_quality == "exact"


def test_nearest_number_stays_on_the_same_lane(index) -> None:
    near = index.resolve("中壢區青埔路二段291號")
    assert near is not None
    assert near.match_quality == "nearest_number"
    assert near.twd97_x == pytest.approx(272032.43)

    # 100巷5號 must not borrow a main-road doorplate just because the numbers are close.
    lane = index.resolve("中壢區青埔路二段100巷5號")
    assert lane is not None
    assert lane.twd97_x == pytest.approx(271500.0)
    assert index.resolve("中壢區青埔路二段100巷40號") is None


def test_ambiguous_or_unknown_addresses_are_not_resolved(index) -> None:
    assert index.resolve("文化路1號") is None  # exists in both districts
    assert index.resolve("中壢區文化路1號") is not None
    assert index.resolve("中壢區不存在路1號") is None
    assert index.resolve("青埔路二段") is None  # no house number
    assert index.resolve("") is None


def test_lazy_index_reports_missing_data_as_unavailable(tmp_path: Path) -> None:
    lazy = LazyDoorplateIndex(tmp_path / "missing.csv")

    with pytest.raises(AddressLocatorUnavailable):
        lazy.resolve("中壢區青埔路二段289號")


def test_lazy_index_loads_once(tmp_path: Path) -> None:
    calls = []

    class Stub:
        def resolve(self, address):
            return None

    def loader(path):
        calls.append(path)
        return Stub()

    path = _doorplates(tmp_path, [])
    lazy = LazyDoorplateIndex(path, loader=loader)
    lazy.resolve("a")
    lazy.resolve("b")
    assert calls == [path]


def test_location_from_coordinates_derives_station_and_distance() -> None:
    x, y = wgs84_to_twd97(121.2143, 25.0137)  # A18

    from_twd97 = location_from_twd97(x, y)
    from_wgs84 = location_from_wgs84(121.2143, 25.0137)

    assert from_twd97.station_code == "A18"
    assert from_twd97.station_distance_m == pytest.approx(0, abs=1)
    assert from_wgs84.station_code == "A18"
    assert (from_wgs84.twd97_x, from_wgs84.twd97_y) == pytest.approx((x, y))


def test_location_outside_service_area_is_rejected() -> None:
    with pytest.raises(ValueError, match="service area"):
        location_from_wgs84(121.5654, 25.0330)  # Taipei 101
    with pytest.raises(ValueError, match="Taiwan"):
        location_from_wgs84(0.0, 0.0)


def test_station_from_coords_picks_nearest_station() -> None:
    code, distance = station_from_coords(121.2373, 25.0223)
    assert code == "A17"
    assert distance == pytest.approx(0, abs=0.5)
    assert station_from_coords(121.2046, 25.0011)[0] == "A19"


def test_index_accepts_prebuilt_frame() -> None:
    frame = pd.DataFrame(
        {
            "district": ["中壢區"],
            "normalized_address": ["高鐵南路二段350號"],
            "road_key": ["高鐵南路二段"],
            "house_number": [350],
            "twd97_x": [272000.0],
            "twd97_y": [2766000.0],
        }
    )
    assert DoorplateAddressIndex(frame).resolve("高鐵南路二段350號") is not None
