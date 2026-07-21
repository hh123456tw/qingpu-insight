"""Parsers for rendered 591 listing pages."""

import json
import math
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

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


@dataclass(frozen=True)
class RejectedListing:
    source_ref: str
    reason_code: str
    message: str


@dataclass(frozen=True)
class ExtractionResult:
    listings: list[SourceListing]
    rejected: list[RejectedListing]
    representation: Literal["dom", "jsonld"]
    schema_version: str


@dataclass(frozen=True)
class _CardError(Exception):
    reason_code: str
    message: str


CARD_SELECTORS: dict[str, tuple[str, ...]] = {
    "sale": ("div.ware-item[data-id]",),
    "rental": ("div.item[data-id]",),
    "newhouse": ('script[type="application/ld+json"]', "#__NUXT_DATA__"),
}

LEGACY_CARD_SELECTORS: dict[str, tuple[str, ...]] = {
    "sale": ("article[data-houseid]", "[data-id][class*='house']"),
    "rental": ("article[data-houseid]", "[data-id][class*='item']"),
    "newhouse": ("article[data-housingid]", "[data-id][class*='housing']"),
}

EXPECTED_DOM_HOSTS: dict[str, str] = {
    "sale": "sale.591.com.tw",
    "rental": "rent.591.com.tw",
    "newhouse": "newhouse.591.com.tw",
}


def parse_rendered_page(html: str, listing_type: ListingType) -> list[SourceListing]:
    """Return accepted listings for callers using the original parser contract."""
    return extract_rendered_page(html, listing_type).listings


def extract_rendered_page(html: str, listing_type: ListingType) -> ExtractionResult:
    """Parse a rendered 591 page into validated listings and rejection diagnostics."""
    soup = BeautifulSoup(html, "html.parser")
    if listing_type == "newhouse" and soup.select("script[type='application/ld+json']"):
        jsonld_result = _extract_newhouse_jsonld(soup)
        if jsonld_result.listings:
            result = jsonld_result
        else:
            dom_result = _extract_dom(soup, listing_type)
            result = ExtractionResult(
                listings=dom_result.listings,
                rejected=[*jsonld_result.rejected, *dom_result.rejected],
                representation=dom_result.representation,
                schema_version=dom_result.schema_version,
            )
    else:
        result = _extract_dom(soup, listing_type)

    if not result.listings:
        reason_codes = sorted({rejected.reason_code for rejected in result.rejected})
        reasons = ", ".join(reason_codes) if reason_codes else "none"
        diagnostics = "; ".join(rejected.message for rejected in result.rejected)
        raise ListingSchemaError(
            "No recognized cards or valid listings for "
            f"{listing_type!r}; accepted=0 rejected={len(result.rejected)} "
            f"reasons={reasons}; diagnostics={diagnostics or 'none'}"
        )
    return result


def _extract_dom(soup: BeautifulSoup, listing_type: ListingType) -> ExtractionResult:
    if listing_type == "newhouse":
        cards = first_nonempty_selector(soup, LEGACY_CARD_SELECTORS[listing_type])
    else:
        cards = first_nonempty_selector(soup, CARD_SELECTORS[listing_type])
        if not cards:
            cards = first_nonempty_selector(soup, LEGACY_CARD_SELECTORS[listing_type])
    listings: list[SourceListing] = []
    rejected: list[RejectedListing] = []
    for card in cards:
        source_ref = _extract_id(card, listing_type) or "unknown"
        try:
            listings.append(_parse_dom_card(card, listing_type))
        except _CardError as error:
            rejected.append(RejectedListing(source_ref, error.reason_code, error.message))
        except (ValueError, OverflowError):
            rejected.append(
                RejectedListing(
                    source_ref, "missing_price", "Listing card has malformed numeric data"
                )
            )
    return ExtractionResult(
        listings=listings,
        rejected=rejected,
        representation="dom",
        schema_version=f"591-{listing_type}-dom-v1",
    )


def _parse_dom_card(card, listing_type: ListingType) -> SourceListing:
    listing_id = _extract_id(card, listing_type)
    if not listing_id:
        raise _CardError("missing_id", "Listing card has no stable listing ID")

    title, raw_url = _extract_dom_title_and_url(card, listing_type)
    if not title:
        raise _CardError("missing_title", "Listing card has no title")
    url = _canonical_url(raw_url, expected_host=EXPECTED_DOM_HOSTS[listing_type])
    if not url:
        raise _CardError("invalid_url", "Listing card URL is not a 591 HTTPS URL")

    details = _extract_dom_details(card, listing_type)
    price = (
        _parse_price_wan(details["price"])
        if listing_type in ("sale", "newhouse")
        else _parse_price_monthly(details["price"])
    )
    if price is None or price <= 0:
        raise _CardError("missing_price", "Listing card has no valid price")
    area = _parse_area(details["attributes"])
    if area <= 0:
        raise _CardError("missing_area", "Listing card has no valid area")

    layout_rooms, layout_living_rooms, layout_bathrooms = _parse_layout(details["attributes"])
    floor, total_floors = _parse_floor(details["attributes"])
    lat, lng = _extract_coordinates(card)
    price_field = (
        "asking_price_twd" if listing_type in ("sale", "newhouse") else "monthly_rent_twd"
    )
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
    return SourceListing(listing_id, listing_type, url, payload)


def _extract_dom_title_and_url(card, listing_type: ListingType) -> tuple[str, str]:
    if listing_type == "sale":
        link = card.select_one(".ware-item__header a")
    elif listing_type == "rental":
        link = card.select_one(".item-info-title a")
    else:
        link = None
    if link is None:
        link = card.select_one("a.listing-link")
    if link is None:
        return "", ""
    title = link.get_text(strip=True)
    if not title:
        legacy_title = card.select_one("h2.listing-title")
        title = legacy_title.get_text(strip=True) if legacy_title else ""
    return title, str(link.get("href", ""))


def _extract_dom_details(card, listing_type: ListingType) -> dict[str, str]:
    if listing_type == "sale" and card.select_one(".ware-item__price-value"):
        price_element = card.select_one(".ware-item__price") or card.select_one(
            ".ware-item__price-value"
        )
        return {
            "price": _text(price_element),
            "attributes": " ".join(
                _text(element)
                for element in (
                    card.select_one(".ware-item__attrs"),
                    card.select_one(".ware-item__section"),
                    card.select_one(".ware-item__address"),
                )
            ),
        }
    if listing_type == "rental" and card.select_one(".item-info-price"):
        return {
            "price": _text(card.select_one(".item-info-price strong")),
            "attributes": " ".join(_text(element) for element in card.select(".item-info-txt")),
        }
    return {
        "price": _extract_text(card, "price"),
        "attributes": " ".join(
            _extract_text(card, class_name) for class_name in ("area", "layout", "floor")
        ),
    }


def _extract_newhouse_jsonld(soup: BeautifulSoup) -> ExtractionResult:
    listings: list[SourceListing] = []
    rejected: list[RejectedListing] = []
    for script in soup.select("script[type='application/ld+json']"):
        try:
            document = json.loads(script.get_text())
        except json.JSONDecodeError:
            rejected.append(RejectedListing("jsonld", "malformed_jsonld", "Invalid JSON-LD"))
            continue
        documents = document if isinstance(document, list) else [document]
        for entry in documents:
            if not isinstance(entry, dict) or entry.get("@type") != "ItemList":
                rejected.append(
                    RejectedListing("jsonld", "malformed_jsonld", "Expected ItemList JSON-LD")
                )
                continue
            items = entry.get("itemListElement")
            if not isinstance(items, list):
                rejected.append(
                    RejectedListing(
                        "jsonld", "malformed_jsonld", "ItemList has no itemListElement"
                    )
                )
                continue
            for item in items:
                source_ref = _jsonld_source_ref(item)
                try:
                    listings.append(_parse_newhouse_item(item))
                except _CardError as error:
                    rejected.append(RejectedListing(source_ref, error.reason_code, error.message))
    return ExtractionResult(
        listings=listings,
        rejected=rejected,
        representation="jsonld",
        schema_version="591-newhouse-jsonld-v1",
    )


def _parse_newhouse_item(item: object) -> SourceListing:
    if not isinstance(item, dict) or not isinstance(item.get("item"), dict):
        raise _CardError("malformed_jsonld", "ItemList entry has no Product")
    product = item["item"]
    if product.get("@type") != "Product":
        raise _CardError("malformed_jsonld", "ItemList entry is not a Product")

    title = product.get("name")
    if not isinstance(title, str) or not title.strip():
        raise _CardError("missing_title", "Product has no title")
    url = _canonical_url(
        product.get("url"), expected_host=EXPECTED_DOM_HOSTS["newhouse"]
    )
    if not url:
        raise _CardError("invalid_url", "Product URL is not a 591 HTTPS URL")
    listing_id = _numeric_final_path_segment(url)
    if not listing_id:
        raise _CardError("missing_id", "Product URL has no stable numeric listing ID")

    description = product.get("description")
    if not isinstance(description, str):
        raise _CardError("missing_area", "Product has no description area range")
    if "桃園市" not in description:
        raise _CardError("wrong_region", "Product description is outside 桃園市")
    area_range = _parse_newhouse_area_range(description)
    if area_range is None:
        raise _CardError("missing_area", "Product has no valid advertised area range")

    offers = product.get("offers")
    if not isinstance(offers, dict) or offers.get("priceCurrency") != "TWD":
        raise _CardError("missing_price", "Product has no TWD aggregate offer")
    low_price = _positive_int(offers.get("lowPrice"))
    high_price = _positive_int(offers.get("highPrice"))
    if low_price is None and high_price is None:
        raise _CardError("missing_price", "Product has no positive aggregate offer price")
    if low_price is not None and high_price is not None and low_price > high_price:
        raise _CardError("missing_price", "Product has an inverted aggregate offer price range")

    payload: dict[str, object] = {
        "id": listing_id,
        "url": url,
        "title": title.strip(),
        "asking_price_twd": None,
        "asking_unit_price_low_twd_per_ping": low_price,
        "asking_unit_price_high_twd_per_ping": high_price,
        "area_min_ping": area_range[0],
        "area_max_ping": area_range[1],
        "lat": 0.0,
        "lng": 0.0,
    }
    return SourceListing(listing_id, "newhouse", url, payload)


def first_nonempty_selector(soup: BeautifulSoup, selectors: tuple[str, ...]) -> list:
    for selector in selectors:
        results = soup.select(selector)
        if results:
            return results
    return []


def _extract_id(card, listing_type: ListingType) -> str:
    attr = "data-houseid" if listing_type in ("sale", "rental") else "data-housingid"
    value = card.get(attr) or card.get("data-id") or ""
    return str(value).strip()


def _extract_text(card, class_name: str) -> str:
    return _text(card.select_one(f".{class_name}"))


def _text(element) -> str:
    return element.get_text(" ", strip=True) if element else ""


def _canonical_url(value: object, expected_host: str | None = None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if expected_host is not None and hostname != expected_host:
        return None
    if parsed.scheme != "https" or not (
        hostname == "591.com.tw" or hostname.endswith(".591.com.tw")
    ):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _extract_coordinates(card) -> tuple[float, float]:
    try:
        return float(card.get("data-lat", "0")), float(card.get("data-lng", "0"))
    except (ValueError, TypeError):
        return 0.0, 0.0


def _parse_price_wan(text: str) -> int | None:
    match = re.search(r"([\d.]+)\s*萬", text.replace(",", ""))
    if not match:
        return None
    try:
        price = float(match.group(1))
    except (ValueError, OverflowError):
        return None
    return int(price * 10_000) if math.isfinite(price) else None


def _parse_price_monthly(text: str) -> int | None:
    match = re.search(r"([\d.]+)", text.replace(",", ""))
    if not match:
        return None
    try:
        price = float(match.group(1))
    except (ValueError, OverflowError):
        return None
    return int(price) if math.isfinite(price) else None


def _parse_area(text: str) -> float:
    match = re.search(r"([\d.]+)\s*坪", text)
    if not match:
        return 0.0
    try:
        area = float(match.group(1))
    except (ValueError, OverflowError):
        return 0.0
    return area if math.isfinite(area) else 0.0


def _parse_layout(text: str) -> tuple[int, int, int]:
    return tuple(_extract_digit_before(text, marker) for marker in ("房", "廳", "衛"))


def _extract_digit_before(text: str, marker: str) -> int:
    match = re.search(rf"(\d+)\s*{re.escape(marker)}", text)
    return int(match.group(1)) if match else 0


def _parse_floor(text: str) -> tuple[int, int]:
    match = re.search(r"(\d+)\s*F\s*/\s*(\d+)\s*F", text, flags=re.IGNORECASE)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _jsonld_source_ref(item: object) -> str:
    if isinstance(item, dict) and isinstance(item.get("item"), dict):
        url = item["item"].get("url")
        if not isinstance(url, str):
            return "jsonld"
        try:
            segment = urlsplit(url).path.rstrip("/").rsplit("/", maxsplit=1)[-1]
        except ValueError:
            return "jsonld"
        return f"jsonld:{segment}" if segment.isdigit() else "jsonld"
    return "jsonld"


def _numeric_final_path_segment(url: str) -> str | None:
    segment = urlsplit(url).path.rstrip("/").rsplit("/", maxsplit=1)[-1]
    return segment if segment.isdigit() else None


def _parse_newhouse_area_range(description: str) -> tuple[float, float] | None:
    match = re.search(r"坪數(?:規劃)?\s*([\d.]+)\s*~\s*([\d.]+)\s*坪", description)
    if not match:
        return None
    try:
        area_min, area_max = float(match.group(1)), float(match.group(2))
    except (ValueError, OverflowError):
        return None
    if not (math.isfinite(area_min) and math.isfinite(area_max)):
        return None
    return (area_min, area_max) if 0 < area_min <= area_max and area_max > 0 else None


def _positive_int(value: object) -> int | None:
    try:
        raw = float(str(value))
        parsed = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(raw) and parsed > 0 else None
