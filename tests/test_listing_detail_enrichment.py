"""Tests for conservative 591 new-house detail address enrichment."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from qingpu_insight.listing_591 import SourceListing
from qingpu_insight.listing_detail_enrichment import (
    DetailEnrichmentBlocked,
    ListingDetailEnricher,
    extract_detail_address,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "listings"


class FakeDetailBrowser:
    def __init__(self, html: str, final_url: str | None = None) -> None:
        self.page_source = html
        self.current_url = final_url or ""
        self.calls: list[str] = []

    def get(self, url: str) -> None:
        self.calls.append(f"get:{url}")
        if not self.current_url:
            self.current_url = url

    def quit(self) -> None:
        self.calls.append("quit")


def fixed_clock() -> datetime:
    return datetime(2026, 7, 22, tzinfo=UTC)


def newhouse_listing(
    *, source_url: str = "https://newhouse.591.com.tw/home/housing/detail?hid=123",
    source_listing_id: str = "123",
) -> SourceListing:
    return SourceListing(
        source_listing_id=source_listing_id,
        listing_type="newhouse",
        source_url=source_url,
        payload={"title": "原始建案", "nested": {"keep": True}},
    )


@pytest.fixture
def detail_html() -> str:
    return (FIXTURE_DIR / "591_newhouse_detail.html").read_text(encoding="utf-8")


def test_extracts_jsonld_postal_address_with_provenance(detail_html: str) -> None:
    observed_at = fixed_clock()

    result = extract_detail_address(
        detail_html,
        "https://newhouse.591.com.tw/home/housing/detail?hid=123",
        observed_at,
    )

    assert result is not None
    assert result.address == "桃園市中壢區高鐵南路一段1號"
    assert result.representation == "jsonld_postal_address"
    assert result.source_url.startswith("https://newhouse.591.com.tw/")
    assert result.observed_at == observed_at


def test_jsonld_does_not_duplicate_a_full_street_address() -> None:
    html = """
    <script type="application/ld+json">
      {"@type":"PostalAddress","addressRegion":"桃園市","addressLocality":"中壢區",
       "streetAddress":"桃園市中壢區高鐵南路一段1號"}
    </script>
    """

    result = extract_detail_address(
        html, "https://newhouse.591.com.tw/home/housing/detail?hid=123", fixed_clock()
    )

    assert result is not None
    assert result.address == "桃園市中壢區高鐵南路一段1號"


def test_parser_uses_explicit_dom_street_address_only() -> None:
    html = (
        "<html><body><span itemprop='streetAddress'>"
        "桃園市大園區領航北路四段2號</span></body></html>"
    )

    result = extract_detail_address(
        html, "https://newhouse.591.com.tw/home/housing/detail?hid=123", fixed_clock()
    )

    assert result is not None
    assert result.address == "桃園市大園區領航北路四段2號"
    assert result.representation == "dom_street_address"


@pytest.mark.parametrize(
    "html",
    [
        "<p>桃園市中壢區高鐵特區，鄰近捷運。</p>",
        "<div class='detail-address'>桃園市中壢區</div>",
        "<div class='detail-address'>台北市中山區南京東路1號</div>",
    ],
)
def test_parser_does_not_guess_or_accept_invalid_addresses(html: str) -> None:
    assert extract_detail_address(
        html, "https://newhouse.591.com.tw/home/housing/detail?hid=123", fixed_clock()
    ) is None


def test_enricher_rejects_verification_page_without_accepted_raw_evidence(tmp_path: Path) -> None:
    browser = FakeDetailBrowser("<html><title>驗證</title><body>captcha</body></html>")
    enricher = ListingDetailEnricher(browser, fixed_clock)

    with pytest.raises(DetailEnrichmentBlocked, match="verification_required"):
        enricher.enrich(newhouse_listing(), tmp_path)

    assert not list((tmp_path / "details").glob("*.html"))
    assert not list((tmp_path / "details-diagnostic").glob("*.html"))
    assert browser.calls[-1] == "quit"


def test_non_newhouse_listing_is_returned_without_navigation(tmp_path: Path) -> None:
    browser = FakeDetailBrowser("<html/>")
    listing = SourceListing("s-1", "sale", "https://sale.591.com.tw/home/house/detail/1", {})

    result = ListingDetailEnricher(browser, fixed_clock).enrich(listing, tmp_path)

    assert result is listing
    assert browser.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://newhouse.591.com.tw/home/housing/detail?hid=123",
        "https://newhouse.591.com.tw.evil.test/home/housing/detail?hid=123",
        "https://newhouse.591.com.tw@evil.test/home/housing/detail?hid=123",
        "https://newhouse.591.com.tw:444/home/housing/detail?hid=123",
        "https://newhouse.591.com.tw/home/housing/detail?hid=123#untrusted-fragment",
        "https://sale.591.com.tw/home/housing/detail?hid=123",
    ],
)
def test_enricher_rejects_unsafe_url_before_navigation(tmp_path: Path, url: str) -> None:
    browser = FakeDetailBrowser("<html/>")

    with pytest.raises(DetailEnrichmentBlocked, match="unsafe_source_url"):
        ListingDetailEnricher(browser, fixed_clock).enrich(
            newhouse_listing(source_url=url), tmp_path
        )

    assert browser.calls == []


def test_enricher_rejects_unsafe_redirect_before_saving_evidence(
    tmp_path: Path, detail_html: str
) -> None:
    browser = FakeDetailBrowser(detail_html, "https://newhouse.591.com.tw.evil.test/redirect")

    with pytest.raises(DetailEnrichmentBlocked, match="unsafe_final_url"):
        ListingDetailEnricher(browser, fixed_clock).enrich(newhouse_listing(), tmp_path)

    assert not list((tmp_path / "details").glob("*.html"))
    assert browser.calls[-1] == "quit"


def test_normal_page_without_address_is_diagnostic_and_returns_original_listing(
    tmp_path: Path,
) -> None:
    browser = FakeDetailBrowser("<html><body><p>正常公開建案頁，沒有地址。</p></body></html>")
    listing = newhouse_listing()

    result = ListingDetailEnricher(browser, fixed_clock).enrich(listing, tmp_path)

    assert result is listing
    assert not list((tmp_path / "details").glob("*.html"))
    diagnostic = tmp_path / "details-diagnostic" / "123.html"
    assert diagnostic.read_text(encoding="utf-8") == browser.page_source
    assert browser.calls[-1] == "quit"


def test_success_saves_accepted_evidence_and_copies_payload(
    tmp_path: Path, detail_html: str
) -> None:
    browser = FakeDetailBrowser(detail_html)
    listing = newhouse_listing()

    result = ListingDetailEnricher(browser, fixed_clock).enrich(listing, tmp_path)

    assert result is not listing
    assert result.payload is not listing.payload
    assert result.payload["structured_address"] == "桃園市中壢區高鐵南路一段1號"
    assert result.payload["address_source_url"] == listing.source_url
    assert result.payload["address_observed_at"] == fixed_clock()
    assert listing.payload == {"title": "原始建案", "nested": {"keep": True}}
    assert (tmp_path / "details" / "123.html").read_text(encoding="utf-8") == detail_html
    assert not list((tmp_path / "details").glob("*.tmp"))
    assert not (tmp_path / "details-diagnostic" / "123.html").exists()


def test_enricher_rejects_path_traversal_listing_id_before_creating_file(
    tmp_path: Path, detail_html: str
) -> None:
    browser = FakeDetailBrowser(detail_html)

    with pytest.raises(DetailEnrichmentBlocked, match="unsafe_listing_id"):
        ListingDetailEnricher(browser, fixed_clock).enrich(
            newhouse_listing(source_listing_id="../../escape"), tmp_path
        )

    assert browser.calls == []
    assert not (tmp_path / "escape.html").exists()


def test_failed_atomic_evidence_write_leaves_no_partial_file(
    tmp_path: Path, detail_html: str, monkeypatch
) -> None:
    browser = FakeDetailBrowser(detail_html)
    enricher = ListingDetailEnricher(browser, fixed_clock)

    def fail_replace(self, target):
        raise OSError("disk failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        enricher.enrich(newhouse_listing(), tmp_path)

    assert not list((tmp_path / "details").glob("*.html"))
    assert not list((tmp_path / "details").glob("*.tmp"))
