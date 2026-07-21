from pathlib import Path

import pytest

from qingpu_insight.listing_591 import (
    ListingSchemaError,
    extract_rendered_page,
    parse_rendered_page,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "listings"


def fixture_path(name: str) -> Path:
    return FIXTURE_DIR / name


def live_fixture(name: str) -> str:
    return fixture_path(name).read_text(encoding="utf-8")


def test_live_sale_dom_extracts_required_fields():
    result = extract_rendered_page(live_fixture("591_sale_live_page.html"), "sale")
    row = result.listings[0]

    assert result.representation == "dom"
    assert result.schema_version == "591-sale-dom-v1"
    assert row.source_listing_id == "20215131"
    assert row.source_url == "https://sale.591.com.tw/home/house/detail/2/20215131.html"
    assert row.payload["asking_price_twd"] == 8_500_000
    assert row.payload["area_ping"] == 30.66
    assert row.payload["layout_rooms"] == 3
    assert row.payload["floor"] == 10
    assert set(row.payload).isdisjoint({"phone", "name", "contact", "agent", "role_name"})


def test_live_rental_dom_extracts_required_fields():
    result = extract_rendered_page(live_fixture("591_rental_live_page.html"), "rental")
    row = result.listings[0]

    assert result.representation == "dom"
    assert result.schema_version == "591-rental-dom-v1"
    assert row.source_listing_id == "21649547"
    assert row.source_url == "https://rent.591.com.tw/21649547"
    assert row.payload["monthly_rent_twd"] == 14_500
    assert row.payload["area_ping"] == 24.5
    assert row.payload["layout_rooms"] == 3
    assert set(row.payload).isdisjoint({"phone", "name", "contact", "agent", "role_name"})


def test_live_rental_dom_reads_price_from_strong_without_unit_text():
    html = live_fixture("591_rental_live_page.html").replace(" 元/月", "")

    result = extract_rendered_page(html, "rental")

    assert result.listings[0].payload["monthly_rent_twd"] == 14_500


def test_live_newhouse_jsonld_preserves_advertised_ranges():
    result = extract_rendered_page(live_fixture("591_newhouse_live_page.html"), "newhouse")
    row = result.listings[0]

    assert result.representation == "jsonld"
    assert result.schema_version == "591-newhouse-jsonld-v1"
    assert row.payload["asking_price_twd"] is None
    assert row.payload["asking_unit_price_low_twd_per_ping"] == 500_000
    assert row.payload["asking_unit_price_high_twd_per_ping"] == 560_000
    assert row.payload["area_min_ping"] == 19.0
    assert row.payload["area_max_ping"] == 30.0
    assert set(row.payload).isdisjoint({"phone", "name", "contact", "agent", "role_name"})


def test_live_dom_rejects_malformed_card_without_discarding_valid_sibling():
    html = live_fixture("591_sale_live_page.html").replace(
        "</body>",
        """
        <div class=\"ware-item\" data-id=\"bad-001\">
          <div class=\"ware-item__header\"><a href=\"https://sale.591.com.tw/bad-001\">缺少價格</a></div>
          <div class=\"ware-item__attrs\">2房1廳1衛 20坪 2F/10F</div>
        </div>
        </body>
        """,
    )

    result = extract_rendered_page(html, "sale")

    assert [row.source_listing_id for row in result.listings] == ["20215131"]
    assert [(rejection.source_ref, rejection.reason_code) for rejection in result.rejected] == [
        ("bad-001", "missing_price")
    ]


@pytest.mark.parametrize(
    ("listing_type", "expected_price_field"),
    [("sale", "asking_price_twd"), ("newhouse", "asking_price_twd"),
     ("rental", "monthly_rent_twd")],
)
def test_parse_rendered_page_keeps_types_isolated(listing_type, expected_price_field):
    html = fixture_path(f"591_{listing_type}_page.html").read_text(encoding="utf-8")
    rows = parse_rendered_page(html, listing_type)
    assert len(rows) == 2
    assert all(row.listing_type == listing_type for row in rows)
    assert all(row.payload[expected_price_field] > 0 for row in rows)


@pytest.mark.parametrize("listing_type", ["sale", "newhouse", "rental"])
def test_parse_yields_stable_ids(listing_type):
    html = fixture_path(f"591_{listing_type}_page.html").read_text(encoding="utf-8")
    rows = parse_rendered_page(html, listing_type)
    for row in rows:
        assert row.source_listing_id
        assert row.source_listing_id == row.payload.get("id")


@pytest.mark.parametrize("listing_type", ["sale", "newhouse", "rental"])
def test_parse_yields_canonical_https_urls(listing_type):
    html = fixture_path(f"591_{listing_type}_page.html").read_text(encoding="utf-8")
    rows = parse_rendered_page(html, listing_type)
    for row in rows:
        assert row.source_url.startswith("https://")
        assert "591.com.tw" in row.source_url
        assert row.payload.get("url") == row.source_url


@pytest.mark.parametrize("listing_type", ["sale", "newhouse", "rental"])
def test_parse_yields_numeric_fields(listing_type):
    html = fixture_path(f"591_{listing_type}_page.html").read_text(encoding="utf-8")
    rows = parse_rendered_page(html, listing_type)
    for row in rows:
        p = row.payload
        assert isinstance(p.get("area_ping"), (int, float))
        assert p["area_ping"] > 0
        assert isinstance(p.get("layout_rooms"), int)
        assert p["layout_rooms"] > 0
        assert isinstance(p.get("layout_living_rooms"), int)
        assert isinstance(p.get("layout_bathrooms"), int)
        assert isinstance(p.get("floor"), int)
        assert isinstance(p.get("total_floors"), int)


@pytest.mark.parametrize("listing_type", ["sale", "newhouse", "rental"])
def test_parse_yields_coordinates(listing_type):
    html = fixture_path(f"591_{listing_type}_page.html").read_text(encoding="utf-8")
    rows = parse_rendered_page(html, listing_type)
    for row in rows:
        p = row.payload
        assert isinstance(p.get("lat"), (int, float))
        assert isinstance(p.get("lng"), (int, float))
        assert 20 < p["lat"] < 30
        assert 115 < p["lng"] < 125


@pytest.mark.parametrize("listing_type", ["sale", "newhouse", "rental"])
def test_parse_has_no_personal_data(listing_type):
    html = fixture_path(f"591_{listing_type}_page.html").read_text(encoding="utf-8")
    rows = parse_rendered_page(html, listing_type)
    for row in rows:
        for key in row.payload:
            assert key not in ("phone", "name", "contact", "agent")


def test_raise_on_empty_html():
    with pytest.raises(ListingSchemaError, match="No recognized cards"):
        parse_rendered_page("<html><body></body></html>", "sale")


def test_fallback_data_id_is_used_as_stable_listing_id():
    html = """
    <div data-id="fallback-001" class="house-card">
      <a class="listing-link" href="https://sale.591.com.tw/home/house/detail/fallback-001">
        <h2 class="listing-title">備援卡片</h2>
      </a>
      <span class="price">1,000 萬</span>
      <span class="area">20坪</span>
      <span class="layout">2房1廳1衛</span>
      <span class="floor">2F/10F</span>
    </div>
    """

    row = parse_rendered_page(html, "sale")[0]

    assert row.source_listing_id == "fallback-001"
    assert row.payload["id"] == "fallback-001"


def test_card_without_any_stable_id_is_rejected():
    html = """
    <article data-houseid="" class="house-card">
      <a class="listing-link" href="https://sale.591.com.tw/home/house/detail/missing-id">
        <h2 class="listing-title">缺少 ID</h2>
      </a>
      <span class="price">1,000 萬</span>
      <span class="area">20坪</span>
      <span class="layout">2房1廳1衛</span>
      <span class="floor">2F/10F</span>
    </article>
    """

    with pytest.raises(ListingSchemaError, match="stable listing ID"):
        parse_rendered_page(html, "sale")
