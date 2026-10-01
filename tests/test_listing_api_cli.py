"""Wiring of the 591 API source into listing-scrape/-build/-update and 591-login."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from qingpu_insight import cli
from qingpu_insight.cli import ListingSourceOptions, RoutedListingSource, main
from qingpu_insight.listing_api_591 import CONTACT_FIELDS, MissingProfileDir
from qingpu_insight.listing_capture import ChromeConfig
from tests.test_listing_api_591 import DEEP_PAGE, FIRST_PAGE, FakeApiBrowser, _raw, _with_contacts

FIXTURES = Path(__file__).parent / "fixtures"
CONTACT_VALUES = ("王小明", "0912-345-678", "0912345678", "agent@example.com", "998877")


@pytest.fixture(autouse=True)
def _no_profile_env(monkeypatch):
    monkeypatch.delenv("QINGPU_591_PROFILE_DIR", raising=False)
    monkeypatch.delenv("QINGPU_DATABASE_URL", raising=False)


def _two_pages() -> dict[int, dict]:
    first = _with_contacts(_raw(FIRST_PAGE))
    deep = _with_contacts(_raw(DEEP_PAGE))
    first["data"]["total"] = "60"
    deep["data"]["total"] = "60"
    return {0: first, 30: deep}


# --------------------------------------------------------------------------- options


def test_source_defaults_to_api_only_when_a_profile_is_configured(monkeypatch) -> None:
    assert ListingSourceOptions.resolve().kind == "dom"
    monkeypatch.setenv("QINGPU_591_PROFILE_DIR", "instance/chrome-591")
    options = ListingSourceOptions.resolve()
    assert (options.kind, options.profile_dir, options.api_max_pages) == (
        "api", "instance/chrome-591", 300,
    )
    assert ListingSourceOptions.resolve("dom").kind == "dom"
    assert ListingSourceOptions.resolve(None, "C:/p").profile_dir == "C:/p"


def test_api_source_without_profile_is_refused() -> None:
    with pytest.raises(MissingProfileDir):
        ListingSourceOptions.resolve("api")


def test_listing_update_api_without_profile_exits_before_service(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli, "_create_listing_update_service",
        lambda *a, **k: pytest.fail("service must not be created"),
    )
    assert main(["listing-update", "--types", "sale", "--source", "api"]) == 1
    assert "QINGPU_591_PROFILE_DIR" in capsys.readouterr().err


def _capture_service(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def factory(root, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop after construction")

    monkeypatch.setattr(cli, "_create_listing_update_service", factory)
    return calls


def test_listing_update_uses_api_when_profile_env_is_set(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QINGPU_591_PROFILE_DIR", "instance/chrome-591")
    calls = _capture_service(monkeypatch)
    assert main(["listing-update", "--types", "sale"]) == 1
    options = calls[0]["source_options"]
    assert options.kind == "api" and options.api_max_pages == 300


def test_listing_update_flags_override_env(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QINGPU_591_PROFILE_DIR", "instance/chrome-591")
    calls = _capture_service(monkeypatch)
    main(["listing-update", "--types", "sale", "--source", "dom", "--max-pages", "3"])
    main(["listing-update", "--types", "sale", "--profile-dir", "D:/p", "--max-pages", "250"])
    assert calls[0]["source_options"].kind == "dom"
    assert calls[1]["source_options"].kind == "api"
    assert calls[1]["source_options"].profile_dir == "D:/p"
    assert calls[1]["source_options"].api_max_pages == 250


def test_routed_source_sends_only_sale_to_the_api() -> None:
    seen: list[tuple[str, str, int]] = []

    class _Source:
        def __init__(self, name):
            self.name = name

        def capture(self, listing_type, max_pages):
            seen.append((self.name, listing_type, max_pages))
            return listing_type

    routed = RoutedListingSource(lambda: _Source("api"), lambda: _Source("dom"), 300)
    routed.capture("sale", 10)
    routed.capture("rental", 10)
    assert seen == [("api", "sale", 300), ("dom", "rental", 10)]


def test_preparation_runner_builds_the_api_source(tmp_path, monkeypatch) -> None:
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    shutil.copy2(FIXTURES / "doorplates.csv", raw / "doorplates.csv")
    built: list[ChromeConfig] = []

    class _FakeApi:
        def __init__(self, base_dir, config):
            built.append(config)

        def capture(self, listing_type, max_pages):
            return (listing_type, max_pages)

    monkeypatch.setattr(cli, "Logged591ApiSource", _FakeApi)
    runner = cli.M3ListingPreparationRunner(
        tmp_path,
        lambda: None,
        source_options=ListingSourceOptions(kind="api", profile_dir="P", api_max_pages=7),
    )
    source = runner._build_source()
    assert source.capture("sale", 10) == ("sale", 7)
    assert built[0].profile_dir == "P"
    assert built[0].delay_seconds == (3.0, 6.0)
    assert built[0].headless is False


# --------------------------------------------------------------------------- scrape + build


def test_scrape_api_then_offline_build_keeps_no_contact_data(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    shutil.copy2(FIXTURES / "doorplates.csv", raw / "doorplates.csv")
    pages = _two_pages()
    browser = FakeApiBrowser(lambda row: pages[row])
    monkeypatch.setattr(
        "qingpu_insight.listing_api_591.create_chrome", lambda config: browser
    )
    monkeypatch.setattr("qingpu_insight.listing_api_591.time.sleep", lambda _: None)

    assert main([
        "listing-scrape", "--types", "sale", "--source", "api",
        "--profile-dir", "instance/chrome-591", "--delay-min", "3", "--delay-max", "4",
    ]) == 0
    assert browser.requested_rows == [0, 30]
    batch_dir = next((raw / "listings" / "591").rglob("manifest.json")).parent
    assert sorted(p.name for p in batch_dir.glob("page-*")) == ["page-0001.json", "page-0002.json"]

    assert main(["listing-build"]) == 0
    rows = pd.read_parquet(tmp_path / "data" / "processed" / "listing_snapshots.parquet")
    assert len(rows) == 62
    assert set(rows["station_code"].dropna()) <= {"A17", "A18", "A19"}
    assert rows["station_code"].notna().all()
    assert not rows["location_eligible"].any()
    row = rows[rows["source_listing_id"] == "20661373"].iloc[0]
    assert row["building_type"] == "電梯大樓"
    assert row["acquisition_representation"] == "api"
    assert row["station_distance_m"] == 536.0
    for column in rows.columns:
        assert column not in CONTACT_FIELDS
    text = rows.to_json(force_ascii=False)
    for value in CONTACT_VALUES:
        assert value not in text
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".html"}:
            content = path.read_text(encoding="utf-8")
            for value in CONTACT_VALUES:
                assert value not in content, path


def test_scrape_api_refuses_impolite_delay(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    code = main([
        "listing-scrape", "--types", "sale", "--source", "api", "--profile-dir", "p",
        "--delay-min", "2", "--delay-max", "5",
    ])
    assert code == 1
    assert "delay-min" in capsys.readouterr().err


# --------------------------------------------------------------------------- 591-login


class _LoginDriver:
    def __init__(self, open_polls: int) -> None:
        self.visited: list[str] = []
        self.quit_calls = 0
        self._open_polls = open_polls

    def get(self, url):
        self.visited.append(url)

    @property
    def window_handles(self):
        if self._open_polls <= 0:
            raise RuntimeError("chrome not reachable")
        self._open_polls -= 1
        return ["main"]

    def quit(self):
        self.quit_calls += 1


def test_591_login_opens_profile_and_waits_for_close(tmp_path, monkeypatch, capsys) -> None:
    driver = _LoginDriver(open_polls=2)
    configs: list[ChromeConfig] = []
    sleeps: list[float] = []
    args = cli.build_parser().parse_args(["591-login", "--profile-dir", "instance/chrome-591"])

    code = cli.login_591(
        tmp_path, args,
        browser_factory=lambda config: configs.append(config) or driver,
        sleep=sleeps.append,
    )

    assert code == 0
    assert configs[0].headless is False
    assert Path(configs[0].profile_dir) == tmp_path / "instance" / "chrome-591"
    assert (tmp_path / "instance" / "chrome-591").is_dir()
    assert driver.visited == [cli.ROUTES["sale"]]
    assert sleeps == [2.0, 2.0]
    assert driver.quit_calls == 1
    assert "自行登入" in capsys.readouterr().out


def test_591_login_requires_a_profile(tmp_path, capsys) -> None:
    args = cli.build_parser().parse_args(["591-login"])
    assert cli.login_591(tmp_path, args, browser_factory=lambda c: pytest.fail("no chrome")) == 1
    assert "QINGPU_591_PROFILE_DIR" in capsys.readouterr().err


def test_manifest_json_is_valid_for_api_batches(tmp_path, monkeypatch) -> None:
    pages = _two_pages()
    browser = FakeApiBrowser(lambda row: pages[row])
    from qingpu_insight.listing_api_591 import Logged591ApiSource

    source = Logged591ApiSource(
        browser=browser, config=ChromeConfig(profile_dir="p", delay_seconds=(3, 3)),
        base_dir=tmp_path, sleep=lambda _: None,
    )
    batch = source.capture("sale", 300)
    manifest = json.loads((batch.batch_dir / "manifest.json").read_text(encoding="utf-8"))
    assert cli._valid_listing_build_manifest(manifest)
