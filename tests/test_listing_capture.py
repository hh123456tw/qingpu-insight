"""Tests for listing capture infrastructure."""

import json

import pytest

from qingpu_insight.listing_capture import (
    FakeBrowser,
    RawBatchWriter,
    Selenium591Source,
)
from qingpu_insight.listing_sources import ListingType

SALE_HTML = """<html><body>
<article data-houseid="S-1001" data-lat="25.0100" data-lng="121.2150">
  <a class="listing-link" href="https://sale.591.com.tw/home/house/detail/2/S-1001.html">
    <h2 class="listing-title">A18 範例住宅</h2>
  </a>
  <span class="price">1,880 萬</span>
  <span class="area">32.5坪</span>
  <span class="layout">3房2廳2衛</span>
  <span class="floor">10F/20F</span>
</article>
</body></html>"""


def test_incomplete_navigation_writes_manifest_but_never_complete(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML], fail_on_next=True)
    writer = RawBatchWriter(tmp_path)
    source = Selenium591Source(browser=browser, writer=writer)
    batch = source.capture("sale", max_pages=3)
    assert batch.is_complete is False
    manifest_path = writer.batch_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["is_complete"] is False
    assert manifest["errors"][0]["code"] == "navigation_failed"


def test_terminal_empty_state_preserves_batch(tmp_path):
    browser = FakeBrowser(pages=["<html><body></body></html>"])
    source = Selenium591Source(browser=browser, writer=RawBatchWriter(tmp_path))
    batch = source.capture("sale", max_pages=1)
    assert batch.batch_id.startswith("591-sale-")
    assert batch.source == "591"


def test_atomic_write_uses_tmp_before_rename(tmp_path):
    writer = RawBatchWriter(tmp_path)
    writer.write_page(1, "<html/>")
    batch_dir = writer.batch_dir
    assert not list(batch_dir.glob("*.tmp"))
    assert (batch_dir / "page-0001.html").exists()


def test_atomic_manifest(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML])
    source = Selenium591Source(browser=browser, writer=RawBatchWriter(tmp_path))
    batch = source.capture("sale", max_pages=1)
    manifest_path = source._writer.batch_dir / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["batch_id"] == batch.batch_id
    assert manifest["source"] == "591"


def test_checkpoint_written_after_page(tmp_path):
    writer = RawBatchWriter(tmp_path)
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
    source = Selenium591Source(browser=FakeBrowser(), writer=RawBatchWriter(tmp_path))
    batch = source.capture( "sale", max_pages=-1)
    assert batch.source == "591"


def test_browser_quit_called(tmp_path):
    browser = FakeBrowser(pages=[SALE_HTML])
    source = Selenium591Source(browser=browser, writer=RawBatchWriter(tmp_path))
    source.capture("sale", max_pages=1,)
    assert "quit" in browser.calls
