"""Parsers for rendered 591 listing pages."""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from qingpu_insight.listing_sources import ListingType


class ListingSchemaError(ValueError):
    """Raised when a listing page cannot be parsed."""


@dataclass(frozen=True)
class SourceListing:
    source_listing_id: str
    listing_type: ListingType
    source_url: str
    payload: dict[str, object]


CARD_SELECTORS: dict[str, tuple[str, ...]] = {
    "sale": ("article[data-houseid]", "[data-id][class*='house']"),
    "newhouse": ("article[data-housingid]", "[data-id][class*='housing']"),
    "rental": ("article[data-houseid]", "[data-id][class*='item']"),
}


def parse_rendered_page(html: str, listing_type: ListingType) -> list[SourceListing]:
    soup = BeautifulSoup(html, "html.parser")
    cards = _first_nonempty_selector(soup, CARD_SELECTORS[listing_type])
    if not cards:
        raise ListingSchemaError(
            f"No recognized cards for listing type {listing_type!r}"
        )
    return [_parse_card(card, listing_type) for card in cards]


def _first_nonempty_selector(soup: BeautifulSoup, selectors: tuple[str, ...]) -> list:
    for selector in selectors:
        results = soup.select(selector)
        if results:
            return results
    return []


def _parse_card(card, listing_type: ListingType) -> SourceListing:
    price_func = _parse_price_wan if listing_type in ("sale", "newhouse") else _parse_price_monthly
    price_field = "asking_price_twd" if listing_type in ("sale", "newhouse") else "monthly_rent_twd"

    listing_id = _extract_id(card, listing_type)
    url = _extract_url(card)
    title = _extract_title(card)
    price = price_func(_extract_text(card, "price"))
    area = _parse_area(_extract_text(card, "area"))
    layout_rooms, layout_living_rooms, layout_bathrooms = _parse_layout(
        _extract_text(card, "layout")
    )
    floor, total_floors = _parse_floor(_extract_text(card, "floor"))
    lat, lng = _extract_coordinates(card)

    payload: dict[str, object] = {
        "id": listing_id,
        "url": url,
        "title": title,
        price_field: price,
        "area_ping": area,
        "layout_rooms": layout_rooms,
        "layout_living_rooms": layout_living_rooms,
        "layout_bathrooms": layout_bathrooms,
        "floor": floor,
        "total_floors": total_floors,
        "lat": lat,
        "lng": lng,
    }

    return SourceListing(
        source_listing_id=listing_id,
        listing_type=listing_type,
        source_url=url,
        payload=payload,
    )


def _extract_text(card, class_name: str) -> str:
    el = card.select_one(f".{class_name}")
    return el.get_text(strip=True) if el else ""


def _extract_id(card, listing_type: ListingType) -> str:
    attr = "data-houseid" if listing_type in ("sale", "rental") else "data-housingid"
    return card.get(attr, "")


def _extract_url(card) -> str:
    link = card.select_one("a.listing-link")
    return link["href"] if link and link.has_attr("href") else ""


def _extract_title(card) -> str:
    title = card.select_one("h2.listing-title")
    return title.get_text(strip=True) if title else ""


def _extract_coordinates(card) -> tuple[float, float]:
    lat_str = card.get("data-lat", "0")
    lng_str = card.get("data-lng", "0")
    try:
        return float(lat_str), float(lng_str)
    except (ValueError, TypeError):
        return 0.0, 0.0


def _parse_price_wan(text: str) -> int:
    if not text:
        return 0
    clean = text.replace(",", "").replace(" ", "")
    match = re.search(r"([\d.]+)", clean)
    if not match:
        return 0
    return int(float(match.group(1)) * 10000)


def _parse_price_monthly(text: str) -> int:
    if not text:
        return 0
    clean = text.replace(",", "").replace(" ", "")
    match = re.search(r"([\d.]+)", clean)
    if not match:
        return 0
    return int(float(match.group(1)))


def _parse_area(text: str) -> float:
    if not text:
        return 0.0
    match = re.search(r"([\d.]+)", text)
    return float(match.group(1)) if match else 0.0


def _parse_layout(text: str) -> tuple[int, int, int]:
    if not text:
        return 0, 0, 0
    rooms = _extract_digit_before(text, "房")
    living = _extract_digit_before(text, "廳")
    baths = _extract_digit_before(text, "衛")
    return rooms, living, baths


def _extract_digit_before(text: str, marker: str) -> int:
    idx = text.find(marker)
    if idx == -1:
        return 0
    digits = ""
    i = idx - 1
    while i >= 0 and text[i].isdigit():
        digits = text[i] + digits
        i -= 1
    return int(digits) if digits else 0


def _parse_floor(text: str) -> tuple[int, int]:
    if not text:
        return 0, 0
    match = re.match(r"(\d+)F/(\d+)F", text.strip())
    if match:
        return int(match.group(1)), int(match.group(2))
    return 0, 0
