from pathlib import Path

from qingpu_insight.config import get_settings


def test_settings_use_project_relative_paths(tmp_path: Path) -> None:
    settings = get_settings(tmp_path)

    assert settings.raw_dir == tmp_path / "data" / "raw"
    assert settings.processed_dir == tmp_path / "data" / "processed"
    assert settings.report_dir == tmp_path / "outputs" / "reports"


def test_settings_lock_scope_and_thresholds(tmp_path: Path) -> None:
    settings = get_settings(tmp_path)

    assert settings.districts == ("中壢區", "大園區")
    assert [station.code for station in settings.stations] == ["A17", "A18", "A19"]
    assert settings.radius_m == 2_000.0
    assert settings.thresholds.minimum_total_by_type == 500
    assert settings.thresholds.minimum_station_type_cell == 50
    assert settings.thresholds.minimum_coordinate_coverage == 0.60
    assert settings.thresholds.minimum_recent_by_type == 100
