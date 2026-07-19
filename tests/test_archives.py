from pathlib import Path
from zipfile import ZipFile

import pytest

from qingpu_insight.archives import extract_taoyuan_tables


def test_extract_taoyuan_tables_keeps_resale_and_presale(tmp_path: Path) -> None:
    archive = tmp_path / "season.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("H_lvr_land_A.csv", "resale")
        bundle.writestr("H_lvr_land_B.csv", "presale")
        bundle.writestr("A_lvr_land_A.csv", "taipei")

    paths = extract_taoyuan_tables(archive, tmp_path / "out")

    assert [path.name for path in paths] == ["h_lvr_land_a.csv", "h_lvr_land_b.csv"]


def test_extract_rejects_zip_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("../H_lvr_land_A.csv", "unsafe")

    with pytest.raises(ValueError, match="unsafe archive member"):
        extract_taoyuan_tables(archive, tmp_path / "out")
