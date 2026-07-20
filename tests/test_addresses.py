from pathlib import Path

import pandas as pd

from qingpu_insight.addresses import (
    build_doorplate_frame,
    match_addresses,
    normalize_address,
)


def test_normalize_address_removes_city_district_and_width_variants() -> None:
    assert normalize_address("桃園市中壢區領航北路四段351號") == "領航北路四段351號"


def test_build_doorplate_uses_address_components_not_village_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "doorplates.csv"
    source.write_text(
        "省市縣市代碼,鄉鎮市區代碼,村里,鄰,街路段,地區,巷,弄,號,橫座標,縱座標\n"
        "68,6800200,洽溪里,013,領航北路二段,,５９巷,２弄,３號,270392.73,2766682.73\n",
        encoding="utf-8-sig",
    )

    result = build_doorplate_frame(source)

    assert result.iloc[0]["normalized_address"] == "領航北路二段59巷2弄3號"
    assert result.iloc[0]["road_key"] == "領航北路二段"
    assert result.iloc[0]["house_number"] == 3


def test_build_doorplate_does_not_append_second_suffix_after_floor(
    tmp_path: Path,
) -> None:
    source = tmp_path / "doorplates.csv"
    source.write_text(
        "省市縣市代碼,鄉鎮市區代碼,村里,鄰,街路段,地區,巷,弄,號,橫座標,縱座標\n"
        "68,6800600,青峰里,001,高鐵北路一段,,,,５號三樓,276000,2767000\n",
        encoding="utf-8-sig",
    )

    result = build_doorplate_frame(source)

    assert result.iloc[0]["normalized_address"] == "高鐵北路一段5號三樓"


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
