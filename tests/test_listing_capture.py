"""Tests for listing capture infrastructure."""

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qingpu_insight.listing_capture import (
    ChromeConfig,
    RawBatchWriter,
    Selenium591Source,
    is_verification_page,
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

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "listings"


def test_newhouse_uses_human_facing_taoyuan_route():
    from qingpu_insight.listing_capture import ROUTES

    assert ROUTES["newhouse"] == "https://newhouse.591.com.tw/housing-list.html?regionid=6"


def test_visible_browser_is_default():
    assert ChromeConfig().headless is False


def test_verification_helper_is_public_and_ignores_script_tokens():
    assert is_verification_page("<html><title>驗證</title><body>captcha</body></html>")
    assert not is_verification_page("<html><script>window.verify = 'analytics'</script></html>")


def test_writer_batch_dir_contains_type_and_matches_manifest(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    batch = Selenium591Source(
        browser=FakeBrowser(pages=[SALE_HTML]),
        writer=writer,
        config=ChromeConfig(max_retries=0),
    ).capture("sale", 1)
    manifest = json.loads((writer.batch_dir / "manifest.json").read_text("utf-8"))

    assert writer.batch_dir.name == batch.batch_id
    assert batch.batch_dir == writer.batch_dir
    assert writer.batch_dir.name.startswith("591-sale-")
    assert manifest["batch_id"] == batch.batch_id


def test_writer_type_mismatch_fails_before_browser_navigation(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML])
    source = Selenium591Source(
        browser=browser,
        writer=RawBatchWriter(tmp_path, "sale"),
    )

    message = "writer listing type 'sale' does not match capture type 'rental'"
    with pytest.raises(ValueError, match=message):
        source.capture("rental", 1)

    assert not any(call.startswith("get:") for call in browser.calls)
    assert "quit" in browser.calls


def test_capture_api_does_not_claim_automatic_checkpoint_resume():
    assert not hasattr(RawBatchWriter, "from_checkpoint")
    assert "resume_batch_id" not in inspect.signature(Selenium591Source).parameters


def test_same_type_writers_claim_distinct_directories_at_identical_time(tmp_path, monkeypatch):
    from qingpu_insight import listing_capture

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 22, 12, 0, 0, 123456, tzinfo=UTC)

    monkeypatch.setattr(listing_capture, "datetime", FrozenDateTime)

    first = RawBatchWriter(tmp_path, "sale")
    second = RawBatchWriter(tmp_path, "sale")

    assert first.batch_id != second.batch_id
    assert first.batch_dir != second.batch_dir
    assert first.batch_dir.is_dir()
    assert second.batch_dir.is_dir()


def test_transient_page_write_does_not_duplicate_manifest_page(tmp_path):
    class TransientPageWriter(RawBatchWriter):
        def __init__(self, base_dir):
            super().__init__(base_dir, "sale")
            self._failed_once = False

        def write_page(self, page_number, html):
            if not self._failed_once:
                self._failed_once = True
                raise OSError("temporary disk failure")
            return super().write_page(page_number, html)

    writer = TransientPageWriter(tmp_path)
    batch = Selenium591Source(
        browser=FakeBrowser(pages=[SALE_HTML]),
        writer=writer,
        config=ChromeConfig(delay_seconds=(0, 0), max_retries=1),
    ).capture("sale", 1)
    manifest = json.loads((writer.batch_dir / "manifest.json").read_text("utf-8"))

    assert len(batch.pages) == 1
    assert len(manifest["pages"]) == 1


def test_navigation_failure_writes_diagnostic_when_page_source_is_available(tmp_path):
    browser = FakeBrowser(fail_on_next=True)
    browser.page_source = "<html><body>navigation failed</body></html>"
    writer = RawBatchWriter(tmp_path, "sale")

    batch = Selenium591Source(browser=browser, writer=writer).capture("sale", 1)

    assert batch.errors[0].code == "navigation_failed"
    assert (writer.batch_dir / "diagnostic-page-0001.html").exists()


def test_pagination_failure_writes_diagnostic(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    batch = Selenium591Source(
        browser=FakeBrowser(pages=[SALE_HTML], fail_next_click=True),
        writer=writer,
        config=ChromeConfig(page_timeout_seconds=1, max_retries=0),
    ).capture("sale", 2)

    assert batch.errors[0].code == "navigation_failed"
    assert (writer.batch_dir / "diagnostic-page-0001.html").exists()


def test_missing_next_control_after_valid_page_marks_complete_terminal_batch(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    batch = Selenium591Source(
        browser=FakeBrowser(
            pages=[SALE_HTML], found_selectors={"div.ware-item[data-id]"}
        ),
        writer=writer,
        config=ChromeConfig(page_timeout_seconds=0, max_retries=0),
    ).capture("sale", 2)

    manifest = json.loads((writer.batch_dir / "manifest.json").read_text("utf-8"))
    assert [page.page_number for page in batch.pages] == [1]
    assert batch.errors == []
    assert batch.reached_terminal_page is True
    assert batch.is_complete is True
    assert manifest["is_complete"] is True


def test_url_only_change_with_stale_cards_fails_navigation_without_duplicate_page(
    tmp_path,
):
    class TrackingOnlyBrowser(FakeBrowser):
        def find_element(self, by, value=None, selector=None):
            selected = value or selector or ""
            if selected == "a.next, .page-next, [rel=next]":
                browser = self

                class TrackingLink:
                    def is_enabled(self):
                        return True

                    def is_displayed(self):
                        return True

                    def click(self):
                        browser.current_url += "&tracking=next"

                return TrackingLink()
            return super().find_element(by, value, selector)

    writer = RawBatchWriter(tmp_path, "sale")
    batch = Selenium591Source(
        browser=TrackingOnlyBrowser(pages=[SALE_HTML]),
        writer=writer,
        config=ChromeConfig(page_timeout_seconds=0, delay_seconds=(0, 0), max_retries=0),
    ).capture("sale", 2)

    manifest = json.loads((writer.batch_dir / "manifest.json").read_text("utf-8"))
    assert [(page.page_number, page.accepted_count) for page in batch.pages] == [(1, 1)]
    assert [(error.page_number, error.code) for error in batch.errors] == [(1, "navigation_failed")]
    assert manifest["is_complete"] is False
    assert not (writer.batch_dir / "page-0002.html").exists()


def test_changed_stable_ids_prove_freshness_even_when_url_is_unchanged(tmp_path):
    second_page = SALE_HTML.replace("S-1001", "S-2002").replace("A18 範例住宅", "A17 第二頁住宅")

    class SameUrlFreshBrowser(FakeBrowser):
        def find_element(self, by, value=None, selector=None):
            selected = value or selector or ""
            if selected == "a.next, .page-next, [rel=next]":
                browser = self

                class SameUrlLink:
                    def is_enabled(self):
                        return True

                    def is_displayed(self):
                        return True

                    def click(self):
                        browser._page_source = browser.pages[browser._page_index]
                        browser._page_index += 1

                return SameUrlLink()
            return super().find_element(by, value, selector)

    batch = Selenium591Source(
        browser=SameUrlFreshBrowser(pages=[SALE_HTML, second_page]),
        writer=RawBatchWriter(tmp_path, "sale"),
        config=ChromeConfig(page_timeout_seconds=0, delay_seconds=(0, 0), max_retries=0),
    ).capture("sale", 2)

    assert [page.page_number for page in batch.pages] == [1, 2]
    assert batch.errors == []


@pytest.mark.parametrize(
    ("listing_type", "fixture_name", "redirect_url"),
    [
        ("sale", "591_sale_live_page.html", "https://sale.591.com.tw/?regionid=1"),
        ("rental", "591_rental_live_page.html", "https://rent.591.com.tw/list?region=1"),
        (
            "newhouse",
            "591_newhouse_live_page.html",
            "https://sale.591.com.tw/list?regionid=6",
        ),
    ],
)
def test_capture_rejects_wrong_host_or_region_provenance(
    tmp_path, listing_type, fixture_name, redirect_url
):
    class RedirectedBrowser(FakeBrowser):
        def get(self, url):
            super().get(url)
            self.current_url = redirect_url

    writer = RawBatchWriter(tmp_path, listing_type)
    batch = Selenium591Source(
        browser=RedirectedBrowser(pages=[(FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")]),
        writer=writer,
        config=ChromeConfig(page_timeout_seconds=0, max_retries=0),
    ).capture(listing_type, 1)

    assert batch.pages == []
    assert [(error.page_number, error.code) for error in batch.errors] == [(1, "navigation_failed")]
    assert not (writer.batch_dir / "page-0001.html").exists()


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
        browser=FakeBrowser(pages=[SALE_HTML]),
        writer=writer,
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
    assert not (writer.batch_dir / "diagnostic-page-0001.html").exists()


def test_script_verification_token_does_not_block_normal_listing_capture(tmp_path):
    html = SALE_HTML.replace("</body>", "<script>window.verify = 'analytics';</script></body>")
    batch = Selenium591Source(
        browser=FakeBrowser(pages=[html]),
        writer=RawBatchWriter(tmp_path, "sale"),
        config=ChromeConfig(max_retries=0),
    ).capture("sale", 1)

    assert len(batch.pages) == 1
    assert batch.errors == []


def test_verification_code_button_does_not_block_normal_listing_capture(tmp_path):
    html = SALE_HTML.replace(
        "</body>", '<button class="roster-popup__form-get-code">獲取驗證碼</button></body>'
    )
    batch = Selenium591Source(
        browser=FakeBrowser(pages=[html]),
        writer=RawBatchWriter(tmp_path, "sale"),
        config=ChromeConfig(max_retries=0),
    ).capture("sale", 1)

    assert len(batch.pages) == 1
    assert batch.errors == []


@pytest.mark.parametrize("fail_on_next", [False, True])
def test_browser_quits_when_manifest_write_fails(tmp_path, fail_on_next):
    class FailingManifestWriter(RawBatchWriter):
        def write_manifest(self, batch):
            raise OSError("manifest write failed")

    browser = FakeBrowser(pages=[SALE_HTML], fail_on_next=fail_on_next)
    source = Selenium591Source(
        browser=browser,
        writer=FailingManifestWriter(tmp_path, "sale"),
        config=ChromeConfig(max_retries=0),
    )

    with pytest.raises(OSError, match="manifest write failed"):
        source.capture("sale", 1)

    assert "quit" in browser.calls


def test_internal_writer_uses_configured_base_dir(tmp_path):
    source = Selenium591Source(
        browser=FakeBrowser(pages=[SALE_HTML]),
        base_dir=tmp_path,
        config=ChromeConfig(max_retries=0),
    )

    source.capture("sale", 1)

    assert source._writer is not None
    assert source._writer.batch_dir.is_relative_to(tmp_path)


def test_reused_source_creates_fresh_internal_writer_for_each_capture(tmp_path):
    source = Selenium591Source(
        browser=FakeBrowser(pages=[SALE_HTML, SALE_HTML]),
        base_dir=tmp_path,
        config=ChromeConfig(max_retries=0),
    )

    first = source.capture("sale", 1)
    second = source.capture("sale", 1)

    assert first.batch_id != second.batch_id
    assert first.batch_dir != second.batch_dir
    assert first.batch_dir is not None
    assert second.batch_dir is not None
    first_manifest = json.loads((first.batch_dir / "manifest.json").read_text("utf-8"))
    second_manifest = json.loads((second.batch_dir / "manifest.json").read_text("utf-8"))
    assert first_manifest["batch_id"] == first.batch_id
    assert second_manifest["batch_id"] == second.batch_id


def test_explicit_writer_is_a_one_shot_dependency(tmp_path):
    writer = RawBatchWriter(tmp_path, "sale")
    source = Selenium591Source(
        browser=FakeBrowser(pages=[SALE_HTML, SALE_HTML]),
        writer=writer,
        config=ChromeConfig(max_retries=0),
    )

    first = source.capture("sale", 1)

    with pytest.raises(RuntimeError, match="injected RawBatchWriter is one-shot"):
        source.capture("sale", 1)
    manifest = json.loads((writer.batch_dir / "manifest.json").read_text("utf-8"))
    assert manifest["batch_id"] == first.batch_id


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
    assert [(error.page_number, error.code) for error in batch.errors] == [(1, "navigation_failed")]


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
    source.capture(
        "sale",
        max_pages=1,
    )
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


def test_checkpoint_records_diagnostic_progress(tmp_path):
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
    assert "quit" in browser.calls
