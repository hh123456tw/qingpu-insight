import json
from pathlib import Path

import pytest

from qingpu_insight import cli
from qingpu_insight.cli import main
from qingpu_insight.downloads import DownloadRecord, record_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_analyse_command_builds_outputs_without_network(tmp_path: Path, monkeypatch) -> None:
    raw = tmp_path / "data" / "raw" / "current"
    raw.mkdir(parents=True)
    (raw / "h_lvr_land_a.csv").write_bytes((FIXTURES / "moi_resale.csv").read_bytes())
    (raw / "h_lvr_land_b.csv").write_bytes((FIXTURES / "moi_presale.csv").read_bytes())
    doorplates = tmp_path / "data" / "raw" / "doorplates.csv"
    doorplates.write_bytes((FIXTURES / "doorplates.csv").read_bytes())
    monkeypatch.chdir(tmp_path)

    exit_code = main(["analyse", "--allow-no-go"])

    assert exit_code == 0
    assert (tmp_path / "data" / "processed" / "transactions.parquet").exists()
    assert (tmp_path / "outputs" / "reports" / "m0-data-feasibility.md").exists()


def test_acquire_continues_after_one_season_fails_and_checkpoints_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    attempted: list[str] = []

    def create_record(url: str, path: Path) -> DownloadRecord:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(url.encode())
        return record_file(url, path)

    def fake_download_season(base_url: str, season: str, destination: Path) -> DownloadRecord:
        attempted.append(season)
        if season == "110S3":
            raise RuntimeError("connection reset")
        return create_record(f"{base_url}/{season}", destination)

    def fake_download_current(
        base_url: str, table_name: str, destination: Path
    ) -> DownloadRecord:
        return create_record(f"{base_url}/{table_name}", destination)

    def fake_download_file(url: str, destination: Path) -> DownloadRecord:
        return create_record(url, destination)

    monkeypatch.setattr(cli, "iter_seasons", lambda start, end: ("110S3", "110S4"))
    monkeypatch.setattr(cli, "download_season", fake_download_season)
    monkeypatch.setattr(cli, "download_current_table", fake_download_current)
    monkeypatch.setattr(cli, "download_file", fake_download_file)
    monkeypatch.setattr(cli, "extract_taoyuan_tables", lambda archive, destination: ())

    with pytest.raises(RuntimeError, match="110S3"):
        cli.acquire(tmp_path, "110S3", "110S4")

    assert attempted == ["110S3", "110S4"]
    manifest = json.loads(
        (tmp_path / "data" / "raw" / "manifest.json").read_text(encoding="utf-8")
    )
    urls = {item["source_url"] for item in manifest}
    assert "https://plvr.land.moi.gov.tw/110S4" in urls
    assert "https://plvr.land.moi.gov.tw/h_lvr_land_a.csv" in urls
    assert "https://plvr.land.moi.gov.tw/h_lvr_land_b.csv" in urls
