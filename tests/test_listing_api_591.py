"""591 logged-in list API: sanitising, extraction and the paginating capture source."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from qingpu_insight.listing_api_591 import (
    API_SCHEMA_VERSION,
    CONTACT_FIELDS,
    Logged591ApiSource,
    MissingProfileDir,
    extract_api_page,
    extract_captured_page,
    sanitize_api_response,
    station_from_distance_name,
)
from qingpu_insight.listing_capture import ROUTES, ChromeConfig, RawBatchWriter
from qingpu_insight.listing_normalization import normalize_listing

FIXTURES = Path(__file__).parent / "fixtures" / "listings"
FIRST_PAGE = FIXTURES / "591_sale_api_first_row_0.json"
DEEP_PAGE = FIXTURES / "591_sale_api_first_row_3000.json"


def _raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _with_contacts(document: dict) -> dict:
    """The fixtures are redacted; put realistic-looking contact values back in."""
    document = copy.deepcopy(document)
    for item in document["data"]["house_list"]:
        item.update(
            linkman="王小明",
            nick_name="仲介小明",
            phonenum="0912-345-678",
            mobile="0912345678",
            avatar="https://img.591.com.tw/avatar/1.jpg",
            user_id="998877",
            email="agent@example.com",
        )
    return document


def _page(total: int, ids: list[int], *, status: object = 1) -> dict:
    template = _raw(DEEP_PAGE)["data"]["house_list"][0]
    items = []
    for houseid in ids:
        item = copy.deepcopy(template)
        item["houseid"] = houseid
        items.append(item)
    return {"status": status, "data": {"house_list": items, "total": str(total)}}


# --------------------------------------------------------------------------- sanitising


def test_sanitize_keeps_only_whitelisted_listing_fields() -> None:
    sanitized = sanitize_api_response(_with_contacts(_raw(DEEP_PAGE)), first_row=3000)
    text = json.dumps(sanitized, ensure_ascii=False)
    for field in CONTACT_FIELDS:
        assert f'"{field}"' not in text
    for value in ("王小明", "0912-345-678", "0912345678", "agent@example.com", "998877"):
        assert value not in text
    assert sanitized["first_row"] == 3000
    assert sanitized["total"] == 5494
    assert len(sanitized["house_list"]) == 31
    first = sanitized["house_list"][0]
    assert {"houseid", "price", "area", "distance_name", "community_name"} <= set(first)
    assert "personal" not in sanitized and "seo" not in sanitized


def test_sanitize_drops_newhouse_ads_and_scrubs_titles() -> None:
    document = _raw(FIRST_PAGE)
    document["data"]["house_list"][2]["title"] = "急售 洽 0912-345-678 王先生"
    sanitized = sanitize_api_response(document, first_row=0)
    assert all(not item.get("is_newhouse") for item in sanitized["house_list"])
    assert sanitized["dropped_ads"] == 2
    assert "0912-345-678" not in json.dumps(sanitized, ensure_ascii=False)


# --------------------------------------------------------------------------- extraction


def test_station_names_map_to_a17_a19() -> None:
    assert station_from_distance_name("領航") == "A17"
    assert station_from_distance_name("領航站") == "A17"
    assert station_from_distance_name("高鐵桃園站") == "A18"
    assert station_from_distance_name("桃園體育園區") == "A19"
    assert station_from_distance_name("桃園體育園區站") == "A19"
    assert station_from_distance_name("坑口") is None
    assert station_from_distance_name(None) is None


def test_extract_maps_sale_items_to_source_listings() -> None:
    result = extract_api_page(sanitize_api_response(_raw(DEEP_PAGE), first_row=3000))
    assert result.representation == "api"
    assert result.schema_version == API_SCHEMA_VERSION
    by_id = {listing.source_listing_id: listing for listing in result.listings}
    listing = by_id["20661373"]
    payload = listing.payload
    assert listing.listing_type == "sale"
    assert listing.source_url == "https://sale.591.com.tw/home/house/detail/2/20661373.html"
    assert payload["asking_price_twd"] == 15_980_000
    assert payload["area_ping"] == 31.75
    assert (payload["layout_rooms"], payload["layout_living_rooms"],
            payload["layout_bathrooms"]) == (2, 2, 1)
    assert (payload["floor"], payload["total_floors"]) == (11, 13)
    assert payload["building_type"] == "電梯大樓"
    assert payload["building_age_years"] == 2.0
    assert payload["parking_type"] == "平面式"
    assert payload["station_code"] == "A18"
    assert payload["station_distance_m"] == 536.0
    assert payload["community_name"] == "站前新鋭"
    assert payload["main_area_ping"] == 14.06
    assert payload["original_price_twd"] == 21_980_000
    assert payload["down_price_percent"] == 27.3
    assert payload["posted_at"].startswith("2026-")
    assert payload["lat"] is None and payload["lng"] is None
    for field in CONTACT_FIELDS:
        assert field not in payload


def test_extract_excludes_ads_out_of_area_and_duplicates() -> None:
    document = sanitize_api_response(_raw(FIRST_PAGE), first_row=0)
    document["house_list"][0]["distance"] = 2400
    document["house_list"][1]["distance_name"] = "坑口"
    document["house_list"].append(dict(document["house_list"][2]))
    result = extract_api_page(document)
    reasons = sorted(r.reason_code for r in result.rejected)
    assert reasons.count("out_of_area") == 2
    assert reasons.count("duplicate") == 1
    ids = [listing.source_listing_id for listing in result.listings]
    assert len(ids) == len(set(ids)) == 31 - 2


def test_extract_parses_ages_and_whole_building_floors() -> None:
    document = sanitize_api_response(_raw(FIRST_PAGE), first_row=0)
    payloads = {
        listing.source_listing_id: listing.payload
        for listing in extract_api_page(document).listings
    }
    months = [p for p in payloads.values() if p.get("building_age_text") == "5個月"]
    assert months and all(p["building_age_years"] == pytest.approx(5 / 12) for p in months)
    unknown = [p for p in payloads.values() if p.get("building_age_text") == "-"]
    assert unknown and all(p["building_age_years"] is None for p in unknown)
    whole = [p for p in payloads.values() if p["building_type"] == "透天厝"]
    assert whole and all(p["floor"] is None and p["total_floors"] for p in whole)
    assert all(p["parking_type"] == "無車位" for p in whole)


def test_extract_reads_listed_common_area_ratio_when_present() -> None:
    document = sanitize_api_response(_raw(DEEP_PAGE), first_row=3000)
    document["house_list"][0]["ratio"] = "31.5%"
    payload = extract_api_page(document).listings[0].payload
    assert payload["listed_common_area_percent"] == 31.5


def test_normalize_listing_keeps_api_building_fields() -> None:
    listing = extract_api_page(sanitize_api_response(_raw(DEEP_PAGE), first_row=3000)).listings[0]
    normalized = normalize_listing(listing, datetime(2026, 10, 1, tzinfo=UTC))
    assert normalized.building_type == "電梯大樓"
    assert normalized.parking_type == "平面式"
    assert normalized.building_age_years == 2.0
    assert normalized.acquisition_representation == "api"
    assert normalized.latitude is None


def test_extract_captured_page_dispatches_on_representation() -> None:
    text = json.dumps(sanitize_api_response(_raw(DEEP_PAGE), first_row=3000), ensure_ascii=False)
    assert extract_captured_page(text, "sale", "api").representation == "api"
    dom = (FIXTURES / "591_sale_page.html").read_text(encoding="utf-8")
    assert extract_captured_page(dom, "sale", "dom").representation == "dom"


# --------------------------------------------------------------------------- capture source


class FakeApiBrowser:
    """Selenium stand-in: list page navigation plus in-page fetch via execute_async_script."""

    def __init__(self, responses, *, page_source: str = "<html><body>591 售屋</body></html>",
                 current_url: str | None = None) -> None:
        self._responses = responses
        self.page_source = page_source
        self._landing_url = current_url
        self.current_url = ""
        self.calls: list[str] = []
        self.requested_rows: list[int] = []
        self.script_timeout: float | None = None

    def get(self, url: str) -> None:
        self.calls.append(f"get:{url}")
        self.current_url = self._landing_url or url

    def set_script_timeout(self, seconds: float) -> None:
        self.script_timeout = seconds

    def execute_async_script(self, script: str, url: str):
        assert "credentials" in script and "include" in script
        parsed = urlsplit(url)
        assert parsed.hostname == "bff-house.591.com.tw"
        query = parse_qs(parsed.query)
        assert query["regionid"] == ["6"] and query["type"] == ["2"]
        assert query["station"] == ["66331,66332,66330"]
        first_row = int(query["firstRow"][0])
        self.requested_rows.append(first_row)
        response = self._responses(first_row) if callable(self._responses) else (
            self._responses[len(self.requested_rows) - 1]
        )
        if isinstance(response, dict) and "status_code" in response:
            return {"status": response["status_code"], "body": response.get("body", "")}
        return {"status": 200, "body": json.dumps(response, ensure_ascii=False)}

    def quit(self) -> None:
        self.calls.append("quit")


def _config(**kwargs) -> ChromeConfig:
    return ChromeConfig(profile_dir="instance/chrome-591", delay_seconds=(3.0, 6.0), **kwargs)


def _source(tmp_path: Path, browser, **kwargs) -> tuple[Logged591ApiSource, list[float]]:
    sleeps: list[float] = []
    source = Logged591ApiSource(
        browser=browser, config=kwargs.pop("config", _config()), base_dir=tmp_path,
        sleep=sleeps.append, **kwargs,
    )
    return source, sleeps


def test_source_requires_a_profile_dir(tmp_path: Path) -> None:
    with pytest.raises(MissingProfileDir, match="QINGPU_591_PROFILE_DIR"):
        Logged591ApiSource(browser=FakeApiBrowser([]), config=ChromeConfig(), base_dir=tmp_path)


def test_source_refuses_impolite_delays(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="3"):
        Logged591ApiSource(
            browser=FakeApiBrowser([]),
            config=ChromeConfig(profile_dir="p", delay_seconds=(1.0, 2.0)),
            base_dir=tmp_path,
        )


def test_capture_pages_until_total_and_marks_terminal(tmp_path: Path) -> None:
    pages = {0: _page(65, list(range(1, 31))), 30: _page(65, list(range(31, 61))),
             60: _page(65, list(range(61, 66)))}
    browser = FakeApiBrowser(lambda row: pages[row])
    source, sleeps = _source(tmp_path, browser)

    batch = source.capture("sale", max_pages=50)

    assert browser.calls[0] == f"get:{ROUTES['sale']}"
    assert browser.requested_rows == [0, 30, 60]
    assert batch.reached_terminal_page and batch.is_complete
    assert [p.page_number for p in batch.pages] == [1, 2, 3]
    assert all(p.representation == "api" for p in batch.pages)
    assert sum(p.accepted_count for p in batch.pages) == 65
    assert len(sleeps) == 2 and all(3.0 <= s <= 6.0 for s in sleeps)
    assert browser.calls[-1] == "quit"
    manifest = json.loads((batch.batch_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["is_complete"] is True
    assert manifest["pages"][0]["representation"] == "api"
    assert (batch.batch_dir / "page-0003.json").exists()
    checkpoint = json.loads((batch.batch_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["last_page"] == 3


def test_capture_stops_at_max_pages_without_claiming_completion(tmp_path: Path) -> None:
    browser = FakeApiBrowser(lambda row: _page(5494, list(range(row + 1, row + 31))))
    source, _ = _source(tmp_path, browser)
    batch = source.capture("sale", max_pages=2)
    assert browser.requested_rows == [0, 30]
    assert not batch.reached_terminal_page and not batch.is_complete
    assert not batch.errors


def test_written_pages_never_contain_contact_fields(tmp_path: Path) -> None:
    contacts = _with_contacts(_raw(DEEP_PAGE))
    contacts["data"]["total"] = "30"
    browser = FakeApiBrowser([contacts])
    source, _ = _source(tmp_path, browser)
    batch = source.capture("sale", max_pages=5)
    assert batch.is_complete
    for path in batch.batch_dir.iterdir():
        text = path.read_text(encoding="utf-8")
        for value in ("王小明", "0912-345-678", "0912345678", "agent@example.com", "998877"):
            assert value not in text, path.name
        for field in CONTACT_FIELDS:
            assert f'"{field}"' not in text, (path.name, field)
    for page in batch.pages:
        assert "0912" not in page.html


@pytest.mark.parametrize(
    ("response", "code"),
    [
        ({"status_code": 403, "body": "forbidden"}, "login_required"),
        ({"status_code": 401, "body": ""}, "login_required"),
        ({"status_code": 429, "body": ""}, "rate_limited"),
        ({"status_code": 500, "body": "oops"}, "http_error"),
        ({"status": 0, "msg": "請先登入會員"}, "login_required"),
        ({"status": 0, "msg": "系統忙碌"}, "api_status"),
        (
            {"status_code": 200, "body": "<html><title>安全驗證</title></html>"},
            "verification_required",
        ),
        ({"status_code": 200, "body": "not json"}, "invalid_response"),
    ],
)
def test_capture_stops_cleanly_on_bad_responses(tmp_path: Path, response, code) -> None:
    responses = [_page(90, list(range(1, 31))), response]
    browser = FakeApiBrowser(responses)
    source, _ = _source(tmp_path, browser)
    batch = source.capture("sale", max_pages=10)
    assert [e.code for e in batch.errors] == [code]
    assert batch.errors[0].page_number == 2
    assert len(batch.pages) == 1
    assert not batch.reached_terminal_page and not batch.is_complete
    assert browser.requested_rows == [0, 30]
    assert browser.calls[-1] == "quit"
    manifest = json.loads((batch.batch_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["errors"][0]["code"] == code
    assert manifest["is_complete"] is False


def test_empty_page_before_total_is_an_error(tmp_path: Path) -> None:
    browser = FakeApiBrowser([_page(90, list(range(1, 31))), _page(90, [])])
    source, _ = _source(tmp_path, browser)
    batch = source.capture("sale", max_pages=10)
    assert [e.code for e in batch.errors] == ["empty_page"]


def test_verification_on_list_page_stops_before_any_api_call(tmp_path: Path) -> None:
    browser = FakeApiBrowser(
        [], page_source="<html><title>安全驗證</title><body>請完成驗證</body></html>"
    )
    source, _ = _source(tmp_path, browser)
    batch = source.capture("sale", max_pages=10)
    assert [e.code for e in batch.errors] == ["verification_required"]
    assert browser.requested_rows == []


def test_login_redirect_on_list_page_stops_before_any_api_call(tmp_path: Path) -> None:
    browser = FakeApiBrowser([], current_url="https://www.591.com.tw/user-login.html")
    source, _ = _source(tmp_path, browser)
    batch = source.capture("sale", max_pages=10)
    assert [e.code for e in batch.errors] == ["login_required"]
    assert browser.requested_rows == []


def test_non_sale_types_are_rejected(tmp_path: Path) -> None:
    browser = FakeApiBrowser([])
    source, _ = _source(tmp_path, browser)
    batch = source.capture("rental", max_pages=1)
    assert [e.code for e in batch.errors] == ["invalid_type"]
    assert browser.calls == ["quit"]


def test_injected_writer_is_used(tmp_path: Path) -> None:
    writer = RawBatchWriter(tmp_path, "sale")
    browser = FakeApiBrowser([_page(3, [1, 2, 3])])
    source, _ = _source(tmp_path, browser, writer=writer)
    batch = source.capture("sale", max_pages=1)
    assert batch.batch_dir == writer.batch_dir
    assert batch.is_complete
