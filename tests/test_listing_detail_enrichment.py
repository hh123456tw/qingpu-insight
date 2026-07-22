"""Tests for conservative 591 new-house detail address enrichment."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest

import qingpu_insight.listing_detail_enrichment as detail_enrichment
from qingpu_insight.listing_591 import SourceListing, extract_rendered_page
from qingpu_insight.listing_capture import ChromeConfig
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


@pytest.mark.parametrize(
    "html",
    [
        "<div class='detail-address'>桃園市中壢區高鐵南路一段1號，鄰近公車</div>",
        "<div class='detail-address'>桃園市中壢區高鐵南路一段1號<script>alert(1)</script></div>",
        "<div class='detail-address'>桃園市中壢區高鐵南路一段1號\x00</div>",
        """
        <script type="application/ld+json">
        {"@type":"PostalAddress","addressRegion":"桃園市","addressLocality":"中壢區",
         "streetAddress":"高鐵南路一段1號，鄰近公車"}
        </script>
        """,
        """
        <script type="application/ld+json">
        {"@type":"PostalAddress","addressRegion":"桃園市","addressLocality":"中壢區",
         "streetAddress":"高鐵南路一段1號<img src=x onerror=alert(1)>"}
        </script>
        """,
    ],
)
def test_parser_rejects_descriptive_or_markup_like_addresses(html: str) -> None:
    assert extract_detail_address(
        html, "https://newhouse.591.com.tw/home/housing/detail?hid=123", fixed_clock()
    ) is None


def test_parser_accepts_only_explicit_bounded_address_suffixes() -> None:
    html = "<div class='detail-address'>桃園市大園區領航北路四段2之3號5樓</div>"

    result = extract_detail_address(
        html, "https://newhouse.591.com.tw/home/housing/detail?hid=123", fixed_clock()
    )

    assert result is not None
    assert result.address == "桃園市大園區領航北路四段2之3號5樓"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("桃園市中壢區高鐵南路一段1之2號", "桃園市中壢區高鐵南路一段1之2號"),
        ("桃園市中壢區高鐵南路一段1號之2", "桃園市中壢區高鐵南路一段1號之2"),
        ("桃園市中壢區高鐵南路一段1號之2 3樓", "桃園市中壢區高鐵南路一段1號之2 3樓"),
    ],
)
def test_parser_accepts_bounded_doorplate_subnumber_forms(source: str, expected: str) -> None:
    html = f"<div class='detail-address'>{source}</div>"

    result = extract_detail_address(
        html, "https://newhouse.591.com.tw/home/housing/detail?hid=123", fixed_clock()
    )

    assert result is not None
    assert result.address == expected


@pytest.mark.parametrize(
    "address",
    [
        "桃園市中壢區高鐵南路一段1號之",
        "桃園市中壢區高鐵南路一段1號之2公車站",
        "桃園市中壢區高鐵南路一段1號之2，鄰近公車",
        "桃園市中壢區高鐵南路一段1號之2 3樓接待中心",
    ],
)
def test_parser_rejects_invalid_or_descriptive_doorplate_subnumber_forms(address: str) -> None:
    assert extract_detail_address(
        f"<div class='detail-address'>{address}</div>",
        "https://newhouse.591.com.tw/home/housing/detail?hid=123",
        fixed_clock(),
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


def test_enricher_uses_existing_factory_with_explicit_visible_chrome_config(
    tmp_path: Path, detail_html: str
) -> None:
    calls: list[ChromeConfig] = []
    browser = FakeDetailBrowser(detail_html)

    def factory(config: ChromeConfig) -> FakeDetailBrowser:
        calls.append(config)
        return browser

    result = ListingDetailEnricher(
        browser_factory=factory,
        chrome_config=ChromeConfig(),
        clock=fixed_clock,
    ).enrich(newhouse_listing(), tmp_path)

    assert result.payload["structured_address"] == "桃園市中壢區高鐵南路一段1號"
    assert calls == [ChromeConfig()]
    assert calls[0].headless is False


def test_enricher_rejects_headless_factory_config() -> None:
    with pytest.raises(ValueError, match="visible"):
        ListingDetailEnricher(
            browser_factory=lambda config: FakeDetailBrowser(""),
            chrome_config=ChromeConfig(headless=True),
        )


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


def test_enricher_accepts_legacy_parser_newhouse_identifier(
    tmp_path: Path, detail_html: str
) -> None:
    legacy_html = (FIXTURE_DIR / "591_newhouse_page.html").read_text(encoding="utf-8")
    listing = extract_rendered_page(legacy_html, "newhouse").listings[0]
    browser = FakeDetailBrowser(detail_html)

    result = ListingDetailEnricher(browser, fixed_clock).enrich(listing, tmp_path)

    assert listing.source_listing_id == "NH-2001"
    assert browser.calls[0] == f"get:{listing.source_url}"
    assert result.payload["structured_address"] == "桃園市中壢區高鐵南路一段1號"
    assert (tmp_path / "details" / "NH-2001.html").exists()


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


@pytest.mark.parametrize(
    "source_listing_id",
    ["CON", "con", "AUX", "NUL", "COM1", "123.html", "nh-2001", "ＮＨ-2001", "NH-2001."],
)
def test_enricher_rejects_noncanonical_or_windows_reserved_listing_id(
    tmp_path: Path, detail_html: str, source_listing_id: str
) -> None:
    browser = FakeDetailBrowser(detail_html)

    with pytest.raises(DetailEnrichmentBlocked, match="unsafe_listing_id"):
        ListingDetailEnricher(browser, fixed_clock).enrich(
            newhouse_listing(source_listing_id=source_listing_id), tmp_path
        )

    assert browser.calls == []


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


def test_concurrent_atomic_writes_do_not_share_a_fixed_temp_file(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / "details" / "123.html"
    barrier = Barrier(2)
    created_temporaries: list[Path] = []
    original_named_temporary_file = detail_enrichment.tempfile.NamedTemporaryFile

    def synchronize_temp_creation(*args, **kwargs):
        handle = original_named_temporary_file(*args, **kwargs)
        created_temporaries.append(Path(handle.name))
        barrier.wait(timeout=5)
        return handle

    monkeypatch.setattr(
        detail_enrichment.tempfile, "NamedTemporaryFile", synchronize_temp_creation
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(detail_enrichment._atomic_write, destination, f"<html>{value}</html>")
            for value in ("first", "second")
        ]
        for future in futures:
            future.result(timeout=5)

    assert destination.read_text(encoding="utf-8") in {"<html>first</html>", "<html>second</html>"}
    assert len(created_temporaries) == len(set(created_temporaries)) == 2
    assert not list(destination.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "directory, html", [("details", "detail"), ("details-diagnostic", "missing")]
)
def test_enricher_refuses_symlinked_output_directories(
    tmp_path: Path, detail_html: str, directory: str, html: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    output_directory = tmp_path / directory
    try:
        output_directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")

    browser_html = detail_html if html == "detail" else "<html><body>no address</body></html>"
    with pytest.raises(DetailEnrichmentBlocked, match="unsafe_output_directory"):
        ListingDetailEnricher(FakeDetailBrowser(browser_html), fixed_clock).enrich(
            newhouse_listing(), tmp_path
        )

    assert not list(outside.iterdir())
