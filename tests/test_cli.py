import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qingpu_insight import cli
from qingpu_insight.cli import main
from qingpu_insight.downloads import DownloadRecord, record_file
from tests.test_market_cleaning import sample_rows

FIXTURES = Path(__file__).parent / "fixtures"


_CHINESE_DIGITS = "零一二三四五六七八九十"


def _int_to_floor(n: int) -> str:
    if n <= 10:
        return _CHINESE_DIGITS[n] + "層"
    if n < 20:
        return "十" + (_CHINESE_DIGITS[n - 10] if n > 10 else "") + "層"
    tens = n // 10
    ones = n % 10
    return _CHINESE_DIGITS[tens] + "十" + (_CHINESE_DIGITS[ones] if ones > 0 else "") + "層"


def copy_fixture_to_processed(tmp_path: Path) -> Path:
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)

    np.random.seed(42)
    base = pd.Timestamp("2023-01-01")
    total_days = 1095

    stations = ["A17", "A18", "A19"]
    types = ["住宅大樓", "華廈", "公寓"]
    ptypes = ["坡道平面", "坡道機械", ""]

    rows = []
    for ttype in ("resale", "presale"):
        n_rows = 1500
        for i in range(n_rows):
            s = stations[i % 3]
            bt = types[i % 3]
            pt = ptypes[i % 3]

            base_price = {"A17": 600000, "A18": 500000, "A19": 550000}[s]
            type_mult = {"住宅大樓": 1.0, "華廈": 0.85, "公寓": 0.70}[bt]
            target = base_price * type_mult * (1 + np.random.uniform(-0.05, 0.05))

            building_age = float(np.random.uniform(0, 30)) if ttype == "resale" else None
            fl = int(np.random.randint(1, 15))
            tfl = int(np.random.randint(5, 25))

            rows.append({
                "transaction_type": ttype,
                "record_id": f"{ttype[0]}{i}",
                "transaction_date": base + pd.DateOffset(days=int(i * total_days / n_rows)),
                "station_code": s,
                "station_distance_m": float(np.random.randint(100, 1500)),
                "building_area_ping": float(np.random.uniform(15, 60)),
                "building_area_sqm": float(np.random.uniform(49.5, 198.3)),
                "building_type": bt,
                "bedrooms": int(np.random.randint(1, 5)),
                "living_rooms": int(np.random.randint(1, 3)),
                "bathrooms": int(np.random.randint(1, 3)),
                "building_age_years": building_age,
                "floor": _int_to_floor(fl),
                "total_floors": float(tfl),
                "parking_type": pt,
                "parking_area_sqm": float(np.random.uniform(0, 33) if pt else 0),
                "parking_price_twd": float(np.random.uniform(1000000, 2500000) if pt else 0),
                "total_price_twd": float(target * (15 + np.random.uniform(0, 45))),
                "unit_price_per_ping_twd": float(target),
                "analysis_eligible": True,
                "transaction_key": f"T{ttype[0]}{i}",
                "road_key": f"R{i % 10}",
                "completion_date": (pd.Timestamp("2020-01-01") + pd.DateOffset(days=i)).date()
                    if ttype == "resale" else None,
            })

    df = pd.DataFrame(rows)
    path = processed / "market_transactions.parquet"
    df.to_parquet(path, index=False)
    return path


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


def test_market_build_command_creates_clean_dataset_and_quality_report(
    tmp_path: Path, monkeypatch
) -> None:
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    sample_rows().to_parquet(processed / "transactions.parquet", index=False)
    monkeypatch.chdir(tmp_path)

    exit_code = main(["market-build"])

    assert exit_code == 0
    assert (tmp_path / "data" / "processed" / "market_transactions.parquet").exists()
    payload = json.loads(
        (tmp_path / "outputs" / "reports" / "m1-market-quality.json").read_text("utf-8")
    )
    assert payload["output_records"] == 2


def test_mysql_load_requires_database_url(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("QINGPU_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="QINGPU_DATABASE_URL is required"):
        main(["mysql-load", "--input", str(tmp_path / "market.parquet")])


def test_mysql_load_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "QINGPU_DATABASE_URL",
        "mysql+pymysql://qingpu:password@127.0.0.1:3306/qingpu_insight",
    )
    from qingpu_insight.market_cleaning import build_market_dataset

    clean, _ = build_market_dataset(sample_rows())
    output = tmp_path / "market.parquet"
    clean.to_parquet(output, index=False)
    called_with: list = []

    def fake_load(connection, frame, batch_size=1000):
        called_with.append(frame)
        return len(frame)

    monkeypatch.setattr(cli, "load_market_rows", fake_load)

    import types

    fake_conn = types.SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(cli.pymysql, "connect", lambda **kw: fake_conn)

    exit_code = main(["mysql-load", "--input", str(output)])

    assert exit_code == 0
    assert len(called_with) == 1
    assert len(called_with[0]) == len(clean)


def test_model_train_builds_both_types_without_network(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    copy_fixture_to_processed(tmp_path)
    exit_code = main(["model-train"])
    assert exit_code == 0
    assert (tmp_path / "artifacts/resale.joblib").exists()
    assert (tmp_path / "artifacts/presale.joblib").exists()
