"""Tests for conversation listing page parser."""

from decimal import Decimal
from pathlib import Path

import pytest

from qingpu_insight.conversation_listing_parser import (
    ListingDetailParseError,
    ListingPageVerificationRequired,
    has_listing_detail_content,
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

    def test_current_sale_dom_without_jsonld(self) -> None:
        html = """<html><head>
<title>青埔測試中古屋 - 591售屋網</title>
<meta name="description"
 content="桃園市中壢區住宅出售：總價2298萬，面積39.169坪，位於測試社區，更多出售詳情">
</head><body>
<span class="info-price-num-2">2,298</span>
<span class="per-price-text">58.67萬/坪</span>
<div class="info-floor-left-2">
  <div class="info-floor-key-2">2房2廳2衛1陽台</div>
  <div class="info-floor-value">格局</div>
</div>
<div class="info-floor-left-2">
  <div class="info-floor-key-2">9個月</div>
  <div class="info-floor-value">屋齡</div>
</div>
<div class="info-addr-content">
  <span class="info-addr-key">樓層</span>
  <span class="info-addr-value-text">13F/17F</span>
</div>
<div class="detail-house-item">
  <div class="detail-house-key">型<duncak></duncak>態</div>
  <span>：</span>
  <div class="detail-house-value"><nmbx></nmbx>華廈</div>
</div>
<div class="info-addr-content">
  <span class="info-addr-key">地址</span>
  <span class="info-addr-value-text">桃園市中壢區高鐵站前路</span>
</div>
<a class="community-info-a">測試社區</a>
<div class="type-menu">房屋介紹</div>
<script id="payMap" type="text/html">
  <iframe src="/map?lat=25.0094795&amp;lng=121.2187076"></iframe>
</script>
</body></html>"""
        assert has_listing_detail_content(html, listing_type="sale") is True
        result = parse_listing_detail(
            html,
            canonical_url=SALE_URL,
            listing_type="sale",
        )
        assert result.total_price_twd == 22980000
        assert result.unit_price_twd_per_ping == 586700
        assert result.area_ping == Decimal("39.169")
        assert result.layout == "2房2廳2衛1陽台"
        assert result.floor == "13F/17F"
        assert result.total_floors == 17
        assert result.age_years == Decimal("0.75")
        assert result.address == "桃園市中壢區高鐵站前路"
        assert result.community_name == "測試社區"
        assert result.building_type == "華廈"
        assert result.latitude == Decimal("25.0094795")
        assert result.longitude == Decimal("121.2187076")

    def test_current_sale_dom_layout_wins_over_community_jsonld(self) -> None:
        html = """<html><head>
<title>青埔測試中古屋 - 591售屋網</title>
<script type="application/ld+json">
{"@type":"Product","name":"青埔測試中古屋",
 "description":"一房61房2廳3衛 二房14房3廳1衛 三房22房9廳3衛 四房2房6廳6衛",
 "url":"https://sale.591.com.tw/home/house/detail/137/1586.html"}
</script>
</head><body>
<span class="info-price-num-2">2,298</span>
<div class="info-floor-left-2">
  <div class="info-floor-key-2">2房2廳2衛1陽台</div>
  <div class="info-floor-value">格局</div>
</div>
<div class="info-addr-content">
  <span class="info-addr-key">地址</span>
  <span class="info-addr-value-text">桃園市中壢區高鐵站前路</span>
</div>
</body></html>"""

        result = parse_listing_detail(
            html,
            canonical_url=SALE_URL,
            listing_type="sale",
        )

        assert result.layout == "2房2廳2衛1陽台"


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

    def test_current_newhouse_dom_uses_meta_instead_of_large_layout(self) -> None:
        html = """<html><head>
<title>【力璞翔-豐禾】開價70~75萬/坪 - 591新建案</title>
<meta name="description"
 content="591為您提供:「力璞翔-豐禾」位於桃園市大園區，格局規劃2~3房、坪數規劃20~52坪。">
</head><body>
<p class="build-price info-item">70~75 萬/坪</p>
<div class="info-item address">基地地址 桃園市大園區致善路二段</div>
<div class="layout-module">
  主推格局以及大量不應寫入欄位的說明文字
</div>
<div class="layout-info main-layout"><span class="layout-text">二房</span></div>
<div class="update-package">24分鐘前更新 · 日更新48次</div>
</body></html>"""
        assert has_listing_detail_content(html, listing_type="newhouse") is True
        result = parse_listing_detail(
            html,
            canonical_url=NEWHOUSE_URL,
            listing_type="newhouse",
        )
        assert result.title == "力璞翔-豐禾"
        assert result.total_price_twd is None
        assert result.unit_price_twd_per_ping is None
        assert result.unit_price_low_twd_per_ping == 700000
        assert result.unit_price_high_twd_per_ping == 750000
        assert result.area_low_ping == Decimal("20")
        assert result.area_high_ping == Decimal("52")
        assert result.layout == "2~3房"
        assert result.address == "桃園市大園區致善路二段"
        assert result.community_name == "力璞翔-豐禾"
        assert result.source_updated_text == "24分鐘前更新 · 日更新48次"


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
