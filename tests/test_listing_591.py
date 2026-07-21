from pathlib import Path

import pytest

from qingpu_insight.listing_591 import ListingSchemaError, parse_rendered_page

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "listings"


def fixture_path(name: str) -> Path:
    return FIXTURE_DIR / name


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
