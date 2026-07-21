"""Tests for listing capture infrastructure."""

import json

import pytest

from qingpu_insight.listing_capture import (
    ChromeConfig,
    RawBatchWriter,
    Selenium591Source,
)
from qingpu_insight.listing_sources import ListingType
from tests.fake_browser import FakeBrowser

SALE_HTML = """<html><body>
<div class="ware-item" data-id="S-1001" data-lat="25.0100" data-lng="121.2150">
  <div class="ware-item__header">
    <a href="https://sale.591.com.tw/home/house/detail/2/S-1001.html">A18 範例住宅</a>
  </div>
  <div class="ware-item__attrs">3房2廳2衛 32.5坪 10F/20F</div>
  <div class="ware-item__price-value">1,880萬</div>
</div>
</body></html>"""


def test_newhouse_uses_human_facing_taoyuan_route():
    from qingpu_insight.listing_capture import ROUTES

    assert ROUTES["newhouse"] == "https://newhouse.591.com.tw/housing-list.html?regionid=6"


def test_visible_browser_is_default():
    assert ChromeConfig().headless is False


def test_writer_batch_dir_contains_type_and_matches_manifest(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    batch = Selenium591Source(
        browser=FakeBrowser(pages=[SALE_HTML]), writer=writer,
        config=ChromeConfig(max_retries=0),
    ).capture("sale", 1)
    manifest = json.loads((writer.batch_dir / "manifest.json").read_text("utf-8"))

    assert writer.batch_dir.name == batch.batch_id
    assert writer.batch_dir.name.startswith("591-sale-")
    assert manifest["batch_id"] == batch.batch_id


def test_schema_failure_writes_diagnostic_html(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    source = Selenium591Source(
        browser=FakeBrowser(pages=["<html>changed</html>"]),
        writer=writer,
        config=ChromeConfig(page_timeout_seconds=1, max_retries=0),
    )
    batch = source.capture("sale", 1)

    assert batch.errors[0].code == "page_failed"
    assert (writer.batch_dir / "diagnostic-page-0001.html").exists()


def test_readiness_uses_one_timeout_for_all_selector_alternatives(tmp_path, monkeypatch):
    class FakeWait:
        def __init__(self, browser, timeout):
            self.browser = browser
            browser.wait_clock.record(timeout)

        def until(self, predicate):
            result = predicate(self.browser)
            if not result:
                raise Exception("timed out")
            return result

    monkeypatch.setattr("qingpu_insight.listing_capture.WebDriverWait", FakeWait)
    source = Selenium591Source(
        browser=FakeBrowser(
            pages=["<script type='application/ld+json'>{}</script>"],
            found_selectors={"#__NUXT_DATA__"},
        ),
        writer=RawBatchWriter(tmp_path, "newhouse"),
        config=ChromeConfig(page_timeout_seconds=7, max_retries=0),
    )

    source.capture("newhouse", 1)

    assert source._browser.wait_clock.timeouts == [7]


def test_capture_records_extraction_summary_in_manifest(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    batch = Selenium591Source(
        browser=FakeBrowser(pages=[SALE_HTML]), writer=writer,
        config=ChromeConfig(max_retries=0),
    ).capture("sale", 1)
    manifest = json.loads((writer.batch_dir / "manifest.json").read_text("utf-8"))

    assert batch.pages[0].accepted_count == 1
    assert batch.pages[0].rejected_count == 0
    assert manifest["pages"][0]["representation"] == "dom"
    assert manifest["pages"][0]["schema_version"] == "591-sale-dom-v1"


def test_verification_page_is_not_saved_as_accepted_evidence(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    batch = Selenium591Source(
        browser=FakeBrowser(pages=["<html><title>驗證</title><body>captcha</body></html>"]),
        writer=writer,
        config=ChromeConfig(page_timeout_seconds=1, max_retries=0),
    ).capture("sale", 1)

    assert batch.errors[0].code == "verification_required"
    assert batch.pages == []
    assert not (writer.batch_dir / "page-0001.html").exists()


def test_incomplete_navigation_writes_manifest_but_never_complete(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML], fail_on_next=True)
    writer = RawBatchWriter(tmp_path, "sale")
    source = Selenium591Source(browser=browser, writer=writer)
    batch = source.capture("sale", max_pages=3)
    assert batch.is_complete is False
    manifest_path = writer.batch_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["is_complete"] is False
    assert manifest["errors"][0]["code"] == "navigation_failed"
    assert "quit" in browser.calls


def test_next_page_navigation_failure_never_marks_batch_complete(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML], fail_next_click=True)
    writer = RawBatchWriter(tmp_path, "sale")
    source = Selenium591Source(
        browser=browser,
        writer=writer,
        config=ChromeConfig(page_timeout_seconds=1, max_retries=0),
    )

    batch = source.capture("sale", max_pages=2)

    assert batch.is_complete is False
    assert batch.reached_terminal_page is False
    assert [(error.page_number, error.code) for error in batch.errors] == [
        (1, "navigation_failed")
    ]


def test_terminal_empty_state_preserves_batch(tmp_path):
    browser = FakeBrowser(
        pages=["<html><body><div class='no-result'>沒結果</div></body></html>"],
        found_selectors={"div.no-result"},
    )
    source = Selenium591Source(browser=browser, writer=RawBatchWriter(tmp_path, "sale"))
    batch = source.capture("sale", max_pages=1)
    assert batch.batch_id.startswith("591-sale-")
    assert batch.source == "591"


def test_atomic_write_uses_tmp_before_rename(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    writer.write_page(1, "<html/>")
    batch_dir = writer.batch_dir
    assert not list(batch_dir.glob("*.tmp"))
    assert (batch_dir / "page-0001.html").exists()


def test_atomic_manifest(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML])
    source = Selenium591Source(browser=browser, writer=RawBatchWriter(tmp_path, "sale"))
    batch = source.capture("sale", max_pages=1)
    manifest_path = source._writer.batch_dir / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["batch_id"] == batch.batch_id
    assert manifest["source"] == "591"
    assert manifest["reached_terminal_page"] == batch.reached_terminal_page


def test_checkpoint_written_after_page(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    writer.write_page(1, "<html/>")
    writer.write_checkpoint(1)
    checkpoint = json.loads((writer.batch_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["last_page"] == 1


@pytest.mark.parametrize("listing_type", ["sale", "newhouse", "rental"])
def test_all_types_have_routes(listing_type: ListingType) -> None:
    from qingpu_insight.listing_capture import ROUTES
    assert listing_type in ROUTES
    assert ROUTES[listing_type].startswith("https://")


def test_invalid_type_returns_error(tmp_path):
    source = Selenium591Source(browser=FakeBrowser(), writer=RawBatchWriter(tmp_path, "sale"))
    batch = source.capture("invalid_type", max_pages=1)
    assert len(batch.errors) == 1
    assert batch.errors[0].code == "invalid_type"


def test_browser_quit_called(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML])
    source = Selenium591Source(browser=browser, writer=RawBatchWriter(tmp_path, "sale"))
    source.capture("sale", max_pages=1,)
    assert "quit" in browser.calls


def test_explicit_wait_timeout(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML], fail_on_find=True)
    config = ChromeConfig(page_timeout_seconds=1, max_retries=0)
    source = Selenium591Source(
        browser=browser, writer=RawBatchWriter(tmp_path, "sale"), config=config
    )
    batch = source.capture("sale", max_pages=1)
    assert batch.reached_terminal_page is False
    assert len(batch.errors) == 1
    assert batch.errors[0].code == "page_failed"


def test_three_retries(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML], fail_page_source=3)
    source = Selenium591Source(browser=browser, writer=RawBatchWriter(tmp_path, "sale"))
    batch = source.capture("sale", max_pages=1)
    assert len(batch.pages) == 1
    assert len(batch.errors) == 0


def test_checkpoint_resume(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML, SALE_HTML])
    source = Selenium591Source(browser=browser, writer=RawBatchWriter(tmp_path, "sale"))
    source.capture("sale", max_pages=2)
    checkpoint = source._writer.batch_dir / "checkpoint.json"
    assert checkpoint.exists()
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert data["last_page"] == 2


def test_empty_result_marked_terminal(tmp_path):
    browser = FakeBrowser(
        pages=["<html><body><div class='no-result'>沒結果</div></body></html>"],
        found_selectors={"div.no-result", ".empty-state", "p.empty"},
    )
    config = ChromeConfig(page_timeout_seconds=1, max_retries=0)
    source = Selenium591Source(
        browser=browser, writer=RawBatchWriter(tmp_path, "sale"), config=config
    )
    batch = source.capture("sale", max_pages=1)
    assert batch.reached_terminal_page is False
    assert len(batch.pages) == 0
    assert len(batch.errors) == 0


def test_neither_cards_nor_empty_is_error(tmp_path):
    browser = FakeBrowser(
        pages=["<html><body><div>some other page</div></body></html>"],
        found_selectors=set(),
    )
    config = ChromeConfig(page_timeout_seconds=1, max_retries=0)
    source = Selenium591Source(
        browser=browser, writer=RawBatchWriter(tmp_path, "sale"), config=config
    )
    batch = source.capture("sale", max_pages=1)
    assert batch.reached_terminal_page is False
    assert len(batch.pages) == 0
    assert len(batch.errors) == 1
    assert batch.errors[0].code == "page_failed"


def test_from_checkpoint_classmethod(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    batch_dir = writer.batch_dir
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "checkpoint.json").write_text(
        json.dumps({"last_page": 2}), encoding="utf-8"
    )
    resume_writer = RawBatchWriter.from_checkpoint(tmp_path, batch_dir)
    assert resume_writer.batch_dir == batch_dir


def test_checkpoint_resume_from_file(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML, SALE_HTML, SALE_HTML])
    writer = RawBatchWriter(tmp_path, "sale")
    batch_dir = writer.batch_dir
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "checkpoint.json").write_text(
        json.dumps({"last_page": 2}), encoding="utf-8"
    )
    (batch_dir / "page-0002.html").write_text("<html/>", encoding="utf-8")
    resume_writer = RawBatchWriter.from_checkpoint(tmp_path, batch_dir)
    source = Selenium591Source(
        browser=browser, writer=resume_writer, resume_batch_id="resume-001"
    )
    batch = source.capture("sale", max_pages=5)
    assert len(batch.pages) == 3
    assert batch.pages[0].page_number == 3
    assert batch.pages[1].page_number == 4
    assert batch.pages[2].page_number == 5
