"""Tests for conversation listing page parser."""

from decimal import Decimal
from pathlib import Path

import pytest

from qingpu_insight.conversation_listing_parser import (
    ListingDetailParseError,
    ListingPageVerificationRequired,
    parse_listing_detail,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


SALE_URL = "https://sale.591.com.tw/home/house/detail/137/1586.html"
NEWHOUSE_URL = "https://newhouse.591.com.tw/789/detail"


class TestParseSaleDetail:
    def test_full_parse(self) -> None:
        html = _load("591_sale_detail.html")
        result = parse_listing_detail(html, canonical_url=SALE_URL, listing_type="sale")
        assert result.listing_type == "sale"
        assert result.source_listing_id == "1586"
        assert result.title == "近A17站三房平車"
        assert result.total_price_twd == 16800000
        assert result.unit_price_twd_per_ping == 501000
        assert result.area_ping == Decimal("33.5")
        assert result.layout == "3房2廳2衛"
        assert result.address == "桃園市大園區測試路123號"
        assert result.community_name == "測試社區"
        assert result.builder_name is None
        assert result.building_type is None
        assert result.floor == "11F/15F"
        assert result.total_floors == 15
        assert result.age_years == Decimal("3")
        assert result.parking_type == "坡道平面"
        assert result.latitude == Decimal("25.033611")
        assert result.longitude == Decimal("121.565")
        assert result.source_updated_text == "今日更新"

    def test_returns_frozen_dataclass(self) -> None:
        html = _load("591_sale_detail.html")
        result = parse_listing_detail(html, canonical_url=SALE_URL, listing_type="sale")
        with pytest.raises(AttributeError):
            result.title = "changed"  # type: ignore[misc]


class TestParseNewhouseDetail:
    def test_full_parse(self) -> None:
        html = _load("591_newhouse_detail.html")
        result = parse_listing_detail(html, canonical_url=NEWHOUSE_URL, listing_type="newhouse")
        assert result.listing_type == "newhouse"
        assert result.source_listing_id == "789"
        assert result.title == "青埔新建案"
        assert result.total_price_twd is None
        assert result.unit_price_twd_per_ping == 550000
        assert result.area_ping is None
        assert result.layout == "3房2廳2衛"
        assert result.address == "桃園市中壢區青埔路一段100號"
        assert result.community_name == "青埔新建案社區"
        assert result.builder_name == "測試建商"
        assert result.building_type == "住宅大樓"
        assert result.floor == "5F/12F"
        assert result.total_floors == 12
        assert result.age_years is None
        assert result.parking_type == "坡道平面"
        assert result.latitude == Decimal("25.0")
        assert result.longitude == Decimal("121.2")
        assert result.source_updated_text == "3天前更新"


class TestVerification:
    def test_raises_error(self) -> None:
        html = _load("591_verification.html")
        with pytest.raises(ListingPageVerificationRequired):
            parse_listing_detail(html, canonical_url=SALE_URL, listing_type="sale")


class TestEdgeCases:
    def test_missing_title_raises_error(self) -> None:
        html = "<html><body><div>no title data</div></body></html>"
        with pytest.raises(ListingDetailParseError) as excinfo:
            parse_listing_detail(html, canonical_url=SALE_URL, listing_type="sale")
        assert "title" in str(excinfo.value).lower()

    def test_empty_html_raises_error(self) -> None:
        with pytest.raises(ListingDetailParseError):
            parse_listing_detail("", canonical_url=SALE_URL, listing_type="sale")

    def test_negative_price_raises_error(self) -> None:
        html = """<html><head><title>Test</title></head><body>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"T","offers":{"@type":"Offer","price":-100,"priceCurrency":"TWD"},"description":"30坪"}
</script></body></html>"""
        with pytest.raises(ListingDetailParseError) as excinfo:
            parse_listing_detail(html, canonical_url=SALE_URL, listing_type="sale")
        assert "total_price_twd" in str(excinfo.value)

    def test_negative_area_raises_error(self) -> None:
        html = """<html><head><title>Test</title></head><body>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"T","offers":{"@type":"Offer","price":1000,"priceCurrency":"TWD"},"description":"-5坪"}
</script></body></html>"""
        with pytest.raises(ListingDetailParseError) as excinfo:
            parse_listing_detail(html, canonical_url=SALE_URL, listing_type="sale")
        assert "area_ping" in str(excinfo.value)

    def test_inconsistent_listing_id_raises_error(self) -> None:
        html = """<html><head><title>Test</title></head><body>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"T","url":"https://sale.591.com.tw/home/house/detail/137/9999.html","offers":{"@type":"Offer","price":1000,"priceCurrency":"TWD"},"description":"30坪"}
</script></body></html>"""
        with pytest.raises(ListingDetailParseError) as excinfo:
            parse_listing_detail(html, canonical_url=SALE_URL, listing_type="sale")
        assert "listing_id" in str(excinfo.value).lower()

    def test_dom_fallback_without_jsonld(self) -> None:
        html = """<html><head><title>Fallback社區 2房2廳</title></head><body>
<div class="info-price">2,000萬</div>
<div class="info-unit-price">60萬/坪</div>
<div class="info-area">33.3坪</div>
<div class="info-layout">2房2廳1衛</div>
<div class="info-address">桃園市大園區測試路456號</div>
<div class="info-community">Fallback社區</div>
<div class="info-floor">8F/14F</div>
<div class="info-parking">機械車位</div>
<div class="info-updated">昨日</div>
</body></html>"""
        result = parse_listing_detail(html, canonical_url=SALE_URL, listing_type="sale")
        assert result.title == "Fallback社區 2房2廳"
        assert result.total_price_twd == 20000000
        assert result.unit_price_twd_per_ping == 600000
        assert result.area_ping == Decimal("33.3")
        assert result.layout == "2房2廳1衛"
        assert result.address == "桃園市大園區測試路456號"
        assert result.community_name == "Fallback社區"
        assert result.floor == "8F/14F"
        assert result.total_floors == 14
        assert result.parking_type == "機械車位"
