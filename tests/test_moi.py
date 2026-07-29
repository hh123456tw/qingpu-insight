from pathlib import Path

import pandas as pd
import pytest

from qingpu_insight.moi import read_moi_csv, roc_date_to_timestamp

FIXTURES = Path(__file__).parent / "fixtures"


def test_roc_date_conversion() -> None:
    assert roc_date_to_timestamp("1150615") == pd.Timestamp("2026-06-15")
    assert pd.isna(roc_date_to_timestamp(""))


def test_resale_parser_removes_metadata_and_other_districts() -> None:
    frame = read_moi_csv(FIXTURES / "moi_resale.csv", "resale")

    assert frame["district"].tolist() == ["中壢區"]
    assert frame["transaction_type"].tolist() == ["resale"]
    assert frame.loc[0, "total_price_twd"] == 20_000_000
    assert frame.loc[0, "transaction_date"] == pd.Timestamp("2026-06-15")


def test_presale_parser_keeps_type_separate() -> None:
    frame = read_moi_csv(FIXTURES / "moi_presale.csv", "presale")

    assert frame["district"].tolist() == ["大園區"]
    assert frame["transaction_type"].tolist() == ["presale"]
    assert frame.loc[0, "parking_price_twd"] == 1_800_000


def test_parser_ignores_malformed_rows_outside_project_districts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "moi.csv"
    source.write_text(
        "鄉鎮市區,土地位置建物門牌,交易年月日,總價元\n"
        "觀音區,長春街,1150101,10000000,未引用逗號\n"
        "中壢區,高鐵北路一段5號,1150102,20000000\n",
        encoding="utf-8-sig",
    )

    result = read_moi_csv(source, "resale")

    assert result["district"].tolist() == ["中壢區"]


def test_parser_rejects_malformed_rows_inside_project_districts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "moi.csv"
    source.write_text(
        "鄉鎮市區,土地位置建物門牌,交易年月日,總價元\n"
        "中壢區,高鐵北路一段5號,1150102,20000000,未引用逗號\n",
        encoding="utf-8-sig",
    )

    with pytest.raises(ValueError, match="malformed in-scope MOI row 2"):
        read_moi_csv(source, "resale")


def test_resale_parser_exposes_residential_analysis_fields() -> None:
    frame = read_moi_csv(FIXTURES / "moi_resale.csv", "resale")
    row = frame.iloc[0]
    assert row["transaction_subject"] == "房地(土地+建物)+車位"
    assert row["main_use"] == "住家用"
    assert row["completion_date"] == pd.Timestamp("2020-01-15")
    assert row[["bedrooms", "living_rooms", "bathrooms"]].tolist() == [3, 2, 2]
    assert row["has_management"]
    assert row["main_building_area_sqm"] == 61.2
    assert row["auxiliary_building_area_sqm"] == 4.8
    assert row["building_area_sqm"] == 110.0
    assert row["parking_area_sqm"] == 25.0


def test_resale_parser_handles_blank_components_and_no_management(
    tmp_path: Path,
) -> None:
    source = tmp_path / "moi_blank.csv"
    source.write_text(
        "鄉鎮市區,土地位置建物門牌,交易年月日,"
        "建物移轉總面積平方公尺,主建物面積,附屬建物面積,"
        "總價元,單價元平方公尺,建物型態,移轉層次,總樓層數,"
        "車位類別,車位移轉總面積(平方公尺),車位總價元,編號,"
        "交易標的,主要用途,建築完成年月,"
        "建物現況格局-房,建物現況格局-廳,建物現況格局-衛,"
        "有無管理組織,備註\n"
        "中壢區,高鐵北路一段5號,1150615,110.0,,,"
        "20000000,500000,住宅大樓,八層,十五層,"
        "坡道平面,25.0,2000000,H-001,"
        "房地(土地+建物)+車位,住家用,10901,"
        "3,2,2,無,\n",
        encoding="utf-8-sig",
    )
    frame = read_moi_csv(source, "resale")
    row = frame.iloc[0]
    assert pd.isna(row["main_building_area_sqm"])
    assert pd.isna(row["auxiliary_building_area_sqm"])
    assert not row["has_management"]


def test_presale_parser_allows_missing_completion_date() -> None:
    frame = read_moi_csv(FIXTURES / "moi_presale.csv", "presale")
    assert pd.isna(frame.loc[0, "completion_date"])
