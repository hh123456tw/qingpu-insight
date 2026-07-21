import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qingpu_insight import cli
from qingpu_insight.cli import main
from qingpu_insight.downloads import DownloadRecord, record_file
from qingpu_insight.listing_591 import SourceListing
from qingpu_insight.listing_normalization import normalize_listing
from qingpu_insight.listing_sources import CaptureBatch, CapturedPage
from tests.test_market_cleaning import sample_rows

FIXTURES = Path(__file__).parent / "fixtures"


def test_listing_commands_accept_headless_and_profile_dir() -> None:
    scrape_args = cli.build_parser().parse_args(
        [
            "listing-scrape",
            "--types",
            "sale",
            "--headless",
            "--profile-dir",
            "C:/ChromeProfile",
        ]
    )
    sync_args = cli.build_parser().parse_args(
        [
            "listing-sync",
            "--types",
            "sale",
            "--headless",
            "--profile-dir",
            "C:/ChromeProfile",
        ]
    )

    assert scrape_args.headless is True
    assert scrape_args.profile_dir == "C:/ChromeProfile"
    assert sync_args.headless is True
    assert sync_args.profile_dir == "C:/ChromeProfile"


def test_normalized_rows_preserve_ranges_and_acquisition_metadata() -> None:
    source = SourceListing(
        source_listing_id="newhouse-001",
        listing_type="newhouse",
        source_url="https://newhouse.591.com.tw/home/123",
        payload={
            "title": "高鐵站前兩房",
            "asking_price_twd": None,
            "asking_unit_price_low_twd_per_ping": 500_000,
            "asking_unit_price_high_twd_per_ping": 560_000,
            "area_min_ping": 19.0,
            "area_max_ping": 30.0,
            "representation": "jsonld",
            "schema_version": "591-newhouse-jsonld-v1",
        },
    )
    normalized = normalize_listing(source, datetime(2026, 7, 21, tzinfo=UTC))

    row = cli._normalized_to_rows([normalized])[0]

    assert row["asking_unit_price_low_twd_per_ping"] == 500_000
    assert row["asking_unit_price_high_twd_per_ping"] == 560_000
    assert row["building_area_min_ping"] == 19.0
    assert row["building_area_max_ping"] == 30.0
    assert row["acquisition_representation"] == "jsonld"
    assert row["acquisition_schema_version"] == "591-newhouse-jsonld-v1"


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

            rows.append(
                {
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
                    if ttype == "resale"
                    else None,
                }
            )

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

    def fake_download_current(base_url: str, table_name: str, destination: Path) -> DownloadRecord:
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
    manifest = json.loads((tmp_path / "data" / "raw" / "manifest.json").read_text(encoding="utf-8"))
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


@pytest.fixture
def fake_source():
    class FakeListingSource:
        def __init__(self):
            self.calls: list[str] = []
            self._counter = 0

        def capture(self, listing_type: str, max_pages: int = 10) -> CaptureBatch:
            self.calls.append(listing_type)
            self._counter += 1
            return CaptureBatch(
                batch_id=f"fake-{listing_type}-{self._counter:04d}",
                source="591",
                listing_type=listing_type,
                started_at=datetime.now(UTC),
                pages=[
                    CapturedPage(
                        page_number=1,
                        url=f"https://{listing_type}.591.com.tw/",
                        html=(
                            FIXTURES / "listings" / f"591_{listing_type}_page.html"
                        ).read_text(encoding="utf-8"),
                        accepted_count=2,
                        rejected_count=0,
                        representation="dom",
                        schema_version=f"591-{listing_type}-dom-v1",
                    )
                ],
                reached_terminal_page=True,
            )

    return FakeListingSource()


class TestListingScrape:
    def test_prints_capture_summary_with_evidence_metadata(
        self, tmp_path, monkeypatch, capsys
    ):
        captured_configs = []

        class Source:
            def capture(self, listing_type, max_pages):
                return CaptureBatch(
                    batch_id="summary-batch",
                    source="591",
                    listing_type=listing_type,
                    started_at=datetime(2026, 7, 22, tzinfo=UTC),
                    pages=[
                        CapturedPage(
                            page_number=1,
                            url="https://sale.591.com.tw/",
                            html="<html></html>",
                            accepted_count=1,
                            rejected_count=0,
                            representation="dom",
                            schema_version="591-sale-dom-v1",
                        )
                    ],
                )

        def create_source(root, config):
            captured_configs.append(config)
            return Source()

        monkeypatch.setattr(cli, "create_listing_source", create_source)
        args = cli.build_parser().parse_args(
            [
                "listing-scrape",
                "--types",
                "sale",
                "--headless",
                "--profile-dir",
                "C:/ChromeProfile",
            ]
        )

        assert cli.listing_scrape(tmp_path, args) == 0
        assert captured_configs == [
            cli.ChromeConfig(
                headless=True,
                profile_dir="C:/ChromeProfile",
                page_timeout_seconds=30,
                delay_seconds=(2.0, 5.0),
            )
        ]
        output = capsys.readouterr().out
        assert "captured_pages=1" in output
        assert "accepted=1" in output
        assert "rejected=0" in output
        assert "representation=dom" in output
        assert "complete=false" in output
        assert "batch_path=" in output

    def test_returns_nonzero_when_capture_has_no_accepted_records(
        self, tmp_path, monkeypatch
    ):
        class Source:
            def capture(self, listing_type, max_pages):
                return CaptureBatch(
                    batch_id="empty-batch",
                    source="591",
                    listing_type=listing_type,
                    started_at=datetime(2026, 7, 22, tzinfo=UTC),
                    pages=[
                        CapturedPage(
                            page_number=1,
                            url="https://sale.591.com.tw/",
                            html="<html></html>",
                            rejected_count=1,
                            representation="dom",
                        )
                    ],
                    reached_terminal_page=True,
                )

        monkeypatch.setattr(cli, "create_listing_source", lambda *_: Source())
        args = cli.build_parser().parse_args(["listing-scrape", "--types", "sale"])

        assert cli.listing_scrape(tmp_path, args) == 1

    def test_summary_uses_capture_batch_dir_across_utc_midnight(
        self, tmp_path, monkeypatch, capsys
    ):
        batch_dir = (
            tmp_path
            / "data"
            / "raw"
            / "listings"
            / "591"
            / "2026-07-23"
            / "591-sale-20260723T000001Z"
        )
        batch_dir.mkdir(parents=True)

        class Source:
            def capture(self, listing_type, max_pages):
                return CaptureBatch(
                    batch_id="591-sale-20260723T000001Z",
                    source="591",
                    listing_type=listing_type,
                    started_at=datetime(2026, 7, 22, 23, 59, 59, tzinfo=UTC),
                    batch_dir=batch_dir,
                    pages=[
                        CapturedPage(
                            page_number=1,
                            url="https://sale.591.com.tw/",
                            html="<html></html>",
                            accepted_count=1,
                            representation="dom",
                        )
                    ],
                )

        monkeypatch.setattr(cli, "create_listing_source", lambda *_: Source())
        args = cli.build_parser().parse_args(["listing-scrape", "--types", "sale"])

        assert cli.listing_scrape(tmp_path, args) == 0
        assert batch_dir.is_dir()
        assert f"batch_path={batch_dir}" in capsys.readouterr().out

    def test_invalid_type_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["listing-scrape", "--types", "INVALID"]) != 0

    def test_max_pages_less_than_one_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["listing-scrape", "--types", "sale", "--max-pages", "0"]) != 0

    def test_delay_min_gt_delay_max_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert (
            main(
                ["listing-scrape", "--types", "sale", "--delay-min", "10", "--delay-max", "1"]
            )
            != 0
        )


class TestListingBuild:
    def test_missing_doorplates_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["listing-build"]) != 0

    def test_incomplete_manifest_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True)
        shutil.copy2(FIXTURES / "doorplates.csv", raw / "doorplates.csv")
        batch_dir = raw / "listings" / "591" / "2026-07-21" / "batch-001"
        batch_dir.mkdir(parents=True)
        shutil.copy2(
            FIXTURES / "listings" / "591_sale_page.html",
            batch_dir / "page-0001.html",
        )
        (batch_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "batch_id": "batch-001",
                    "source": "591",
                    "listing_type": "sale",
                    "started_at": "2026-07-21T12:00:00+00:00",
                    "reached_terminal_page": False,
                    "is_complete": False,
                    "errors": [],
                    "pages": [{"page_number": 1}],
                }
            ),
            encoding="utf-8",
        )

        assert main(["listing-build", "--batch-dir", str(batch_dir)]) == 1

    def test_complete_manifest_is_preserved_in_repository(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("QINGPU_DATABASE_URL", raising=False)
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True)
        shutil.copy2(FIXTURES / "doorplates.csv", raw / "doorplates.csv")
        batch_dir = raw / "listings" / "591" / "2026-07-21" / "batch-002"
        batch_dir.mkdir(parents=True)
        shutil.copy2(
            FIXTURES / "listings" / "591_sale_page.html",
            batch_dir / "page-0001.html",
        )
        (batch_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "batch_id": "batch-002",
                    "source": "591",
                    "listing_type": "sale",
                    "started_at": "2026-07-21T12:00:00+00:00",
                    "reached_terminal_page": True,
                    "is_complete": True,
                    "errors": [],
                    "pages": [{"page_number": 1}],
                }
            ),
            encoding="utf-8",
        )
        saved = []
        parquet_repository = cli.ParquetListingRepository(
            tmp_path / "data" / "processed"
        )

        class Repository:
            def save_batch(self, batch, rows):
                saved.append((batch, rows))
                parquet_repository.save_batch(batch, rows)

            def load_snapshots(self):
                return parquet_repository.load_snapshots()

        monkeypatch.setattr(cli, "create_listing_repository", lambda root: Repository())

        assert main(["listing-build", "--batch-dir", str(batch_dir)]) == 0
        assert saved[0][0].is_complete is True
        snapshots = pd.read_parquet(
            tmp_path / "data" / "processed" / "listing_snapshots.parquet"
        )
        assert len(snapshots) == 2
        assert set(snapshots["batch_id"]) == {"batch-002"}

    def test_schema_error_reports_prior_page_rejections_and_rejects_batch(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True)
        shutil.copy2(FIXTURES / "doorplates.csv", raw / "doorplates.csv")
        batch_dir = raw / "listings" / "591" / "2026-07-21" / "batch-003"
        batch_dir.mkdir(parents=True)
        page_one_html = (FIXTURES / "listings" / "591_sale_live_page.html").read_text(
            encoding="utf-8"
        ).replace(
            "</body>",
            """
            <div class="ware-item" data-id="bad-001">
              <div class="ware-item__header"><a href="https://sale.591.com.tw/bad-001">缺少價格</a></div>
              <div class="ware-item__attrs">2房1廳1衛 20坪 2F/10F</div>
            </div>
            </body>
            """,
        )
        (batch_dir / "page-0001.html").write_text(page_one_html, encoding="utf-8")
        (batch_dir / "page-0002.html").write_text(
            "<html><body>changed schema</body></html>", encoding="utf-8"
        )
        (batch_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "batch_id": "batch-003",
                    "source": "591",
                    "listing_type": "sale",
                    "started_at": "2026-07-21T12:00:00+00:00",
                    "reached_terminal_page": True,
                    "is_complete": True,
                    "errors": [],
                    "pages": [{"page_number": 1}, {"page_number": 2}],
                }
            ),
            encoding="utf-8",
        )

        assert main(["listing-build", "--batch-dir", str(batch_dir)]) == 1
        assert "rejection_reasons=missing_price:1" in capsys.readouterr().out

    def test_reports_aggregated_card_rejection_reasons(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True)
        shutil.copy2(FIXTURES / "doorplates.csv", raw / "doorplates.csv")
        batch_dir = raw / "listings" / "591" / "2026-07-22" / "batch-004"
        batch_dir.mkdir(parents=True)
        html = (FIXTURES / "listings" / "591_sale_live_page.html").read_text(
            encoding="utf-8"
        ).replace(
            "</body>",
            """
            <div class="ware-item" data-id="bad-001">
              <div class="ware-item__header"><a href="https://sale.591.com.tw/bad-001">缺少價格</a></div>
              <div class="ware-item__attrs">2房1廳1衛 20坪 2F/10F</div>
            </div>
            </body>
            """,
        )
        (batch_dir / "page-0001.html").write_text(html, encoding="utf-8")
        (batch_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "batch_id": "batch-004",
                    "source": "591",
                    "listing_type": "sale",
                    "started_at": "2026-07-22T12:00:00+00:00",
                    "reached_terminal_page": True,
                    "is_complete": True,
                    "errors": [],
                    "pages": [{"page_number": 1}],
                }
            ),
            encoding="utf-8",
        )

        class Repository:
            def save_batch(self, batch, rows):
                pass

            def load_snapshots(self):
                return pd.DataFrame()

        monkeypatch.setattr(cli, "create_listing_repository", lambda *_: Repository())

        assert main(["listing-build", "--batch-dir", str(batch_dir)]) == 0
        assert "rejection_reasons=missing_price:1" in capsys.readouterr().out


def test_listing_repository_factory_uses_mysql_url(tmp_path, monkeypatch):
    sentinel = object()
    captured = {}

    class Connection:
        pass

    connection = Connection()
    monkeypatch.setenv(
        "QINGPU_DATABASE_URL",
        "mysql+pymysql://root:example_password@127.0.0.1:3306/qingpu_insight",
    )
    monkeypatch.setattr(
        cli.pymysql,
        "connect",
        lambda **kwargs: captured.update(kwargs) or connection,
    )
    monkeypatch.setattr(cli, "MySQLListingRepository", lambda conn: sentinel, raising=False)

    repository = cli.create_listing_repository(tmp_path)

    assert repository is sentinel
    assert captured == {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "root",
        "password": "example_password",
        "database": "qingpu_insight",
        "charset": "utf8mb4",
    }


class TestListingSync:
    def test_runs_types_independently(
        self, tmp_path, monkeypatch, fake_source, capsys
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("QINGPU_DATABASE_URL", raising=False)
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True)
        shutil.copy2(
            Path(__file__).parent / "fixtures" / "doorplates.csv",
            raw / "doorplates.csv",
        )
        monkeypatch.setattr(
            "qingpu_insight.cli.create_listing_source", lambda *_: fake_source
        )
        assert (
            main(
                [
                    "listing-sync",
                    "--types",
                    "sale",
                    "newhouse",
                    "rental",
                    "--max-pages",
                    "1",
                ]
            )
            == 0
        )
        output = capsys.readouterr().out
        for listing_type in ("sale", "newhouse", "rental"):
            assert f"[{listing_type}] captured_pages=1 accepted=2 rejected=0" in output
        assert "representation=dom" in output
        assert "complete=true" in output
        assert "batch_path=" in output
        assert (tmp_path / "data/processed" / "listing_snapshots.parquet").exists()
        assert set(fake_source.calls) == {"sale", "newhouse", "rental"}
        snapshots = pd.read_parquet(
            tmp_path / "data/processed" / "listing_snapshots.parquet"
        )
        assert len(snapshots) == 6
        assert set(snapshots["listing_type"]) == {"sale", "newhouse", "rental"}
        assert set(snapshots["acquisition_representation"]) == {"dom"}
        assert set(snapshots["acquisition_schema_version"]) == {
            "591-sale-dom-v1",
            "591-newhouse-dom-v1",
            "591-rental-dom-v1",
        }
        assert set(snapshots["station_code"].dropna()).issubset({"A17", "A18", "A19"})
        assert snapshots.loc[
            snapshots["listing_type"] == "rental", "model_evidence"
        ].isna().all()
        assert {
            "phone", "contact_name", "email", "password"
        }.isdisjoint(snapshots.columns)
        first_events = pd.read_parquet(
            tmp_path / "data/processed" / "events.parquet"
        )

        assert (
            main(
                [
                    "listing-sync",
                    "--types",
                    "sale",
                    "newhouse",
                    "rental",
                    "--max-pages",
                    "1",
                ]
            )
            == 0
        )
        repeated_events = pd.read_parquet(
            tmp_path / "data/processed" / "events.parquet"
        )
        assert len(repeated_events) == len(first_events)

    def test_schema_failure_is_fail_closed_without_saving_batch(
        self, tmp_path, monkeypatch, capsys
    ):
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True)
        shutil.copy2(FIXTURES / "doorplates.csv", raw / "doorplates.csv")
        saved = []

        class Source:
            def capture(self, listing_type, max_pages):
                return CaptureBatch(
                    batch_id="schema-failure",
                    source="591",
                    listing_type=listing_type,
                    started_at=datetime(2026, 7, 22, tzinfo=UTC),
                    pages=[
                        CapturedPage(
                            page_number=1,
                            url="https://sale.591.com.tw/",
                            html=(FIXTURES / "listings" / "591_sale_page.html").read_text(
                                encoding="utf-8"
                            ),
                            accepted_count=2,
                            representation="dom",
                        ),
                        CapturedPage(
                            page_number=2,
                            url="https://sale.591.com.tw/?page=2",
                            html="<html><body>changed schema</body></html>",
                            representation="dom",
                        ),
                    ],
                    reached_terminal_page=True,
                )

        class Repository:
            def save_batch(self, batch, rows):
                saved.append((batch, rows))

            def load_snapshots(self):
                return pd.DataFrame({"source": pd.Series(dtype="str")})

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli, "create_listing_source", lambda *_: Source())
        monkeypatch.setattr(cli, "create_listing_repository", lambda *_: Repository())

        assert main(["listing-sync", "--types", "sale"]) == 1
        assert saved == []
        assert "解析失敗" in capsys.readouterr().err

    def test_invalid_type_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["listing-sync", "--types", "INVALID"]) != 0

    def test_max_pages_less_than_one_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["listing-sync", "--types", "sale", "--max-pages", "0"]) != 0

    def test_delay_min_gt_delay_max_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert (
            main(
                [
                    "listing-sync",
                    "--types",
                    "sale",
                    "--delay-min",
                    "10",
                    "--delay-max",
                    "1",
                ]
            )
            != 0
        )
