from __future__ import annotations

import pytest

from qingpu_insight.conversation_urls import (
    Unsupported591Url,
    parse_initial_591_url,
    validate_final_591_url,
    validate_redirect_target,
)


class TestParseInitial591Url:
    @pytest.mark.parametrize(
        ("url", "expected_kind"),
        [
            ("https://sale.591.com.tw/home/house/detail/123/456.html", "direct"),
            ("https://newhouse.591.com.tw/789/", "direct"),
            ("https://newhouse.591.com.tw/789/detail", "direct"),
            ("https://591.to/abc123", "short"),
            ("https://591.to/a_b-c", "short"),
        ],
    )
    def test_accepted(self, url: str, expected_kind: str) -> None:
        result = parse_initial_591_url(url)
        assert result.request_url == url
        assert result.kind == expected_kind

    @pytest.mark.parametrize(
        "url",
        [
            "http://sale.591.com.tw/home/house/detail/123/456.html",
            "https://rent.591.com.tw/home/house/detail/123/456.html",
            "https://sale.591.com.tw/home/house/list/",
            "https://newhouse.591.com.tw/search/",
            "https://user:pass@sale.591.com.tw/home/house/detail/123/456.html",
            "https://sale.591.com.tw:8080/home/house/detail/123/456.html",
            "https://192.168.1.1/",
            "https://591.to/a",
            "https://591.to/" + "x" * 65,
            "https://other.591.com.tw/",
            "https://sale.591.com.tw/",
            "https://sale.591.com.tw/home/house/detail/abc/456.html",
            "https://sale.591.com.tw/home/house/detail/123/abc.html",
        ],
    )
    def test_rejected(self, url: str) -> None:
        with pytest.raises(Unsupported591Url):
            parse_initial_591_url(url)


class TestValidateRedirectTarget:
    @pytest.mark.parametrize(
        "url",
        [
            "https://sale.591.com.tw/home/house/detail/123/456.html",
            "https://newhouse.591.com.tw/789/",
            "https://newhouse.591.com.tw/789/detail",
            "https://591.to/abc123",
        ],
    )
    def test_accepted(self, url: str) -> None:
        validate_redirect_target(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://sale.591.com.tw/home/house/detail/123/456.html",
            "https://user:pass@sale.591.com.tw/home/house/detail/123/456.html",
            "https://sale.591.com.tw:8080/home/house/detail/123/456.html",
            "https://192.168.1.1/",
            "https://rent.591.com.tw/home/house/detail/123/456.html",
            "https://sale.591.com.tw/home/house/list/",
            "https://unsupported.example.com/",
        ],
    )
    def test_rejected(self, url: str) -> None:
        with pytest.raises(Unsupported591Url):
            validate_redirect_target(url)


class TestValidateFinal591Url:
    def test_sale_url(self) -> None:
        result = validate_final_591_url(
            "https://sale.591.com.tw/home/house/detail/123/456.html"
        )
        assert result.canonical_url == "https://sale.591.com.tw/home/house/detail/123/456.html"
        assert result.listing_type == "sale"
        assert result.source_listing_id == "456"

    def test_newhouse_with_slash(self) -> None:
        result = validate_final_591_url("https://newhouse.591.com.tw/789/")
        assert result.canonical_url == "https://newhouse.591.com.tw/789/"
        assert result.listing_type == "newhouse"
        assert result.source_listing_id == "789"

    def test_newhouse_detail(self) -> None:
        result = validate_final_591_url("https://newhouse.591.com.tw/789/detail")
        assert result.canonical_url == "https://newhouse.591.com.tw/789/detail"
        assert result.listing_type == "newhouse"
        assert result.source_listing_id == "789"

    def test_newhouse_no_trailing_slash(self) -> None:
        result = validate_final_591_url("https://newhouse.591.com.tw/789")
        assert result.canonical_url == "https://newhouse.591.com.tw/789"
        assert result.listing_type == "newhouse"
        assert result.source_listing_id == "789"

    def test_removes_query_string(self) -> None:
        result = validate_final_591_url(
            "https://sale.591.com.tw/home/house/detail/123/456.html?foo=bar"
        )
        assert result.canonical_url == "https://sale.591.com.tw/home/house/detail/123/456.html"

    def test_removes_fragment(self) -> None:
        result = validate_final_591_url(
            "https://sale.591.com.tw/home/house/detail/123/456.html#section"
        )
        assert result.canonical_url == "https://sale.591.com.tw/home/house/detail/123/456.html"

    def test_removes_query_and_fragment(self) -> None:
        result = validate_final_591_url(
            "https://sale.591.com.tw/home/house/detail/123/456.html?foo=bar#section"
        )
        assert result.canonical_url == "https://sale.591.com.tw/home/house/detail/123/456.html"

    @pytest.mark.parametrize(
        "url",
        [
            "http://sale.591.com.tw/home/house/detail/123/456.html",
            "https://rent.591.com.tw/home/house/detail/123/456.html",
            "https://sale.591.com.tw/home/house/list/",
            "https://newhouse.591.com.tw/search/",
            "https://user:pass@sale.591.com.tw/home/house/detail/123/456.html",
            "https://sale.591.com.tw:8080/home/house/detail/123/456.html",
            "https://192.168.1.1/",
            "https://other.591.com.tw/",
            "https://591.to/some-token",
            "https://sale.591.com.tw/",
            "https://sale.591.com.tw/home/house/detail/abc/456.html",
            "https://sale.591.com.tw/home/house/detail/123/abc.html",
        ],
    )
    def test_rejected(self, url: str) -> None:
        with pytest.raises(Unsupported591Url):
            validate_final_591_url(url)
