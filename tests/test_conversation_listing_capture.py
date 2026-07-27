"""Tests for conversation listing capture."""

from __future__ import annotations

import pytest

from qingpu_insight.conversation_listing_capture import (
    CapturedListing,
    DetailPageBrowser,
    Safe591RedirectResolver,
)
from qingpu_insight.conversation_listing_parser import (
    ListingDetailParseError,
    ListingPageVerificationRequired,
    parse_listing_detail,
)
from qingpu_insight.conversation_urls import (
    Initial591Url,
    Unsupported591Url,
    validate_final_591_url,
)
from qingpu_insight.listing_capture import ChromeConfig
from tests.fake_browser import FakeBrowser

SALE_DETAIL_HTML = """\
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"測試住宅",\
"offers":{"@type":"Offer","price":18800000},\
"description":"32.5坪 3房2廳2衛 電梯大樓 屋齡3年"}
</script></head><body>
<div class="info-price">1,880萬</div>
<span class="info-unit-price">57.8萬/坪</span>
<div class="info-community">測試社區</div>
<div class="info-builder">測試建商</div>
<div class="info-floor">12F/15F</div>
<div class="info-age">3年</div>
<div class="info-parking">坡道平面</div>
<span class="info-updated">2025-06-01 更新</span>
</body></html>"""

SALE_URL = "https://sale.591.com.tw/home/house/detail/1/2.html"


def _resolved_sale(_: Initial591Url):
    return validate_final_591_url(SALE_URL)


class _Response:
    def __init__(self, status_code: int, location: str | None = None):
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self, responses: list[_Response]):
        self.responses = list(responses)
        self.requested: list[str] = []

    def get(self, url: str, **_: object) -> _Response:
        self.requested.append(url)
        return self.responses.pop(0)


def test_safe_redirect_resolver_validates_before_following() -> None:
    session = _Session([
        _Response(302, "https://10.0.0.1/private"),
    ])
    resolver = Safe591RedirectResolver(session=session)
    initial = Initial591Url("https://591.to/abc", "short")

    with pytest.raises(Unsupported591Url):
        resolver.resolve(initial)

    assert session.requested == ["https://591.to/abc"]


class _RedirectFakeBrowser:
    """Fake browser that returns a sequence of URLs for current_url."""

    def __init__(self, chain: list[str], page_source: str = ""):
        self._chain = list(chain)
        self._index = 0
        self._page_source = page_source
        self.calls: list[str] = []

    def get(self, url: str) -> None:
        self.calls.append(f"get:{url}")

    @property
    def current_url(self) -> str:
        if self._index < len(self._chain):
            url = self._chain[self._index]
            self._index += 1
            return url
        return self._chain[-1] if self._chain else ""

    @property
    def page_source(self) -> str:
        return self._page_source

    @page_source.setter
    def page_source(self, value: str) -> None:
        self._page_source = value

    def quit(self) -> None:
        self.calls.append("quit")


class TestDetailPageBrowser:
    """Tests for DetailPageBrowser."""

    def test_direct_navigation(self) -> None:
        driver = FakeBrowser(pages=[SALE_DETAIL_HTML])
        browser = DetailPageBrowser(
            driver_factory=lambda: driver,
            parser=parse_listing_detail,
            redirect_resolver=_resolved_sale,
        )
        initial = Initial591Url(request_url=SALE_URL, kind="direct")
        result = browser.capture(initial)
        assert isinstance(result, CapturedListing)
        assert result.final_url == SALE_URL
        assert result.detail.title == "測試住宅"
        assert result.detail.total_price_twd == 18800000
        assert "quit" in driver.calls

    def test_short_redirect(self) -> None:
        driver = _RedirectFakeBrowser(
            chain=[SALE_URL],
            page_source=SALE_DETAIL_HTML,
        )
        browser = DetailPageBrowser(
            driver_factory=lambda: driver,
            parser=parse_listing_detail,
            redirect_resolver=_resolved_sale,
        )
        initial = Initial591Url(
            request_url="https://591.to/abc123",
            kind="short",
        )
        result = browser.capture(initial)
        assert isinstance(result, CapturedListing)
        assert result.final_url == SALE_URL
        assert result.detail.title == "測試住宅"

    def test_excessive_redirects(self) -> None:
        driver = _RedirectFakeBrowser(
            chain=[
                "https://591.to/abc1",
                "https://591.to/abc2",
                "https://591.to/abc3",
                "https://591.to/abc4",
            ],
            page_source=SALE_DETAIL_HTML,
        )
        def reject_redirect(_: Initial591Url):
            raise RuntimeError("Exceeded maximum redirects (3)")

        browser = DetailPageBrowser(
            driver_factory=lambda: driver,
            redirect_resolver=reject_redirect,
        )
        initial = Initial591Url(
            request_url="https://591.to/abc0",
            kind="short",
        )
        with pytest.raises(RuntimeError):
            browser.capture(initial)
        assert driver.calls == []

    def test_redirect_to_private_ip(self) -> None:
        driver = _RedirectFakeBrowser(
            chain=["https://10.0.0.1/evil"],
            page_source=SALE_DETAIL_HTML,
        )
        def reject_redirect(_: Initial591Url):
            raise Unsupported591Url("IP literals are not allowed")

        browser = DetailPageBrowser(
            driver_factory=lambda: driver,
            redirect_resolver=reject_redirect,
        )
        initial = Initial591Url(
            request_url="https://591.to/abc",
            kind="short",
        )
        with pytest.raises(Unsupported591Url):
            browser.capture(initial)
        assert driver.calls == []

    def test_redirect_to_unrelated_domain(self) -> None:
        driver = _RedirectFakeBrowser(
            chain=["https://evil.com/phish"],
            page_source=SALE_DETAIL_HTML,
        )
        def reject_redirect(_: Initial591Url):
            raise Unsupported591Url("unsupported redirect target")

        browser = DetailPageBrowser(
            driver_factory=lambda: driver,
            redirect_resolver=reject_redirect,
        )
        initial = Initial591Url(
            request_url="https://591.to/abc",
            kind="short",
        )
        with pytest.raises(Unsupported591Url):
            browser.capture(initial)
        assert driver.calls == []

    def test_verification_page(self) -> None:
        verification_html = (
            '<html><head><title>驗證</title></head>'
            '<body><p>安全驗證</p></body></html>'
        )
        driver = FakeBrowser(pages=[verification_html])
        browser = DetailPageBrowser(
            driver_factory=lambda: driver,
            redirect_resolver=_resolved_sale,
        )
        initial = Initial591Url(request_url=SALE_URL, kind="direct")
        with pytest.raises(ListingPageVerificationRequired):
            browser.capture(initial)
        assert "quit" in driver.calls

    def test_parser_failure(self) -> None:
        bad_html = (
            '<html><script type="application/ld+json">'
            '{"invalid":true}</script><body>No data</body></html>'
        )
        driver = FakeBrowser(pages=[bad_html])
        browser = DetailPageBrowser(
            driver_factory=lambda: driver,
            parser=parse_listing_detail,
            redirect_resolver=_resolved_sale,
        )
        initial = Initial591Url(request_url=SALE_URL, kind="direct")
        with pytest.raises(ListingDetailParseError):
            browser.capture(initial)
        assert "quit" in driver.calls

    def test_driver_cleanup_on_success(self) -> None:
        driver = FakeBrowser(pages=[SALE_DETAIL_HTML])
        browser = DetailPageBrowser(
            driver_factory=lambda: driver,
            redirect_resolver=_resolved_sale,
        )
        initial = Initial591Url(request_url=SALE_URL, kind="direct")
        browser.capture(initial)
        assert "quit" in driver.calls

    def test_driver_cleanup_on_error(self) -> None:
        driver = FakeBrowser(fail_on_next=True)
        browser = DetailPageBrowser(
            driver_factory=lambda: driver,
            redirect_resolver=_resolved_sale,
        )
        initial = Initial591Url(request_url=SALE_URL, kind="direct")
        with pytest.raises(Exception, match="navigation_failed"):
            browser.capture(initial)
        assert "quit" in driver.calls

    def test_chrome_not_headless(self) -> None:
        config = ChromeConfig()
        assert config.headless is False

    def test_timeout(self) -> None:
        driver = FakeBrowser(pages=["<html><body>Loading...</body></html>"])
        browser = DetailPageBrowser(
            driver_factory=lambda: driver,
            config=ChromeConfig(page_timeout_seconds=0),
            redirect_resolver=_resolved_sale,
        )
        initial = Initial591Url(request_url=SALE_URL, kind="direct")
        with pytest.raises(TimeoutError):
            browser.capture(initial)
        assert "quit" in driver.calls
