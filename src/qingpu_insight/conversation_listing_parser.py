"""Parser for 591 detail pages (single listing)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from qingpu_insight.listing_capture import is_verification_page

_PHONE_RE = re.compile(
    r"(?<!\d)(?:09\d{8}|0[2-8][-\s]?\d{7,8})(?!\d)"
)
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)


class ListingPageVerificationRequired(RuntimeError):
    pass


class ListingDetailParseError(ValueError):
    pass


def _safe_persisted_text(
    value: str | None,
    *,
    field: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    cleaned = _PHONE_RE.sub("[已移除電話]", cleaned)
    cleaned = _EMAIL_RE.sub("[已移除信箱]", cleaned)
    if len(cleaned) > max_length:
        raise ListingDetailParseError(
            f"{field} exceeds {max_length} characters"
        )
    return cleaned or None


@dataclass(frozen=True)
class ParsedListingDetail:
    listing_type: Literal["sale", "newhouse"]
    source_listing_id: str
    title: str
    total_price_twd: int | None
    unit_price_twd_per_ping: int | None
    area_ping: Decimal | None
    layout: str | None
    address: str | None
    community_name: str | None
    builder_name: str | None
    building_type: str | None
    floor: str | None
    total_floors: int | None
    age_years: Decimal | None
    parking_type: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    source_updated_text: str | None


_PING_RE = re.compile(r"(-?[\d,.]+)\s*坪")
_WAN_PRICE_RE = re.compile(r"(-?[\d,.]+)\s*萬")
_UNIT_PRICE_RE = re.compile(r"(-?[\d,.]+)\s*萬/坪")
_FLOOR_RE = re.compile(r"(\d+)\s*[Ff]\s*/\s*(\d+)\s*[Ff]")
_AGE_RE = re.compile(r"(-?[\d.]+)\s*年")
_LAYOUT_SEGMENT_RE = re.compile(r"\d+\s*[房廳衛]")


def _extract_ping(text: str) -> Decimal | None:
    if "~" in text or "﹣" in text or "—" in text:
        return None
    m = _PING_RE.search(text)
    if m:
        return Decimal(m.group(1).replace(",", ""))
    return None


def _extract_price_wan(text: str) -> int | None:
    m = _WAN_PRICE_RE.search(text.replace(",", ""))
    if m:
        try:
            return int(Decimal(m.group(1)) * 10000)
        except Exception:
            return None
    return None


def _extract_unit_price(text: str) -> int | None:
    m = _UNIT_PRICE_RE.search(text.replace(",", ""))
    if m:
        try:
            return int(Decimal(m.group(1)) * 10000)
        except Exception:
            return None
    return None


def _element_text(soup: BeautifulSoup, *selectors: str) -> str | None:
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(" ", strip=True)
            if text:
                return text
    return None


def _price_text(soup: BeautifulSoup) -> str | None:
    el = soup.select_one(".info-price")
    if el:
        text = el.get_text(" ", strip=True)
        if text:
            return text
    for el in soup.select("[class*='price']"):
        classes = " ".join(el.get("class", []))
        if "unit" in classes.lower():
            continue
        text = el.get_text(" ", strip=True)
        if text:
            return text
    return None


def _extract_listing_id_from_url(canonical_url: str) -> str:
    parts = urlsplit(canonical_url)
    path = parts.path.rstrip("/")
    segments = path.split("/")
    if "home/house/detail" in path:
        return segments[-1].removesuffix(".html")
    return segments[-1] if segments[-1].isdigit() else segments[-2]


def _parse_floor_info(floor_str: str | None) -> tuple[str | None, int | None]:
    if not floor_str:
        return None, None
    m = _FLOOR_RE.search(floor_str)
    if m:
        return floor_str, int(m.group(2))
    return floor_str, None


def _extract_age_years(text: str | None) -> Decimal | None:
    if not text:
        return None
    m = _AGE_RE.search(text)
    if m:
        return Decimal(m.group(1))
    return None


def _validate_positive_int(value: int | None, name: str) -> int | None:
    if value is not None and value < 0:
        raise ListingDetailParseError(f"{name} is negative: {value}")
    return value


def _validate_positive_decimal(value: Decimal | None, name: str) -> Decimal | None:
    if value is not None and value < 0:
        raise ListingDetailParseError(f"{name} is negative: {value}")
    return value


def _parse_jsonld(jsonld: dict) -> dict:
    result: dict = {}

    name = jsonld.get("name")
    if isinstance(name, str):
        result["title"] = name.strip()

    offers = jsonld.get("offers")
    if isinstance(offers, dict):
        price = offers.get("price")
        if price is not None:
            try:
                result["total_price_twd"] = int(price)
            except (ValueError, TypeError):
                pass

    description = jsonld.get("description", "")
    if isinstance(description, str) and description:
        area = _extract_ping(description)
        if area is not None:
            result["area_ping"] = area
        segments = _LAYOUT_SEGMENT_RE.findall(description)
        if segments:
            result["layout"] = "".join(segments)

    address_obj = jsonld.get("address")
    if isinstance(address_obj, dict):
        street = address_obj.get("streetAddress")
        if isinstance(street, str) and street.strip():
            result["address"] = street.strip()

    geo = jsonld.get("geo")
    if isinstance(geo, dict):
        lat = geo.get("latitude")
        lng = geo.get("longitude")
        if lat is not None:
            try:
                result["latitude"] = Decimal(str(lat))
            except Exception:
                pass
        if lng is not None:
            try:
                result["longitude"] = Decimal(str(lng))
            except Exception:
                pass

    url_from_json = jsonld.get("url")
    if isinstance(url_from_json, str) and url_from_json.strip():
        result["jsonld_url"] = url_from_json.strip()

    return result


def parse_listing_detail(
    html: str, *, canonical_url: str, listing_type: Literal["sale", "newhouse"]
) -> ParsedListingDetail:
    if is_verification_page(html):
        raise ListingPageVerificationRequired(
            "Page contains verification or captcha markers"
        )

    source_listing_id = _extract_listing_id_from_url(canonical_url)
    soup = BeautifulSoup(html, "html.parser")

    jsonld_fields: dict = {}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            jsonld_fields = _parse_jsonld(data)
            break
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    jsonld_fields = _parse_jsonld(item)
                    break
            if jsonld_fields:
                break

    jsonld_url = jsonld_fields.pop("jsonld_url", None)
    if jsonld_url is not None:
        jsonld_id = _extract_listing_id_from_url(jsonld_url)
        if jsonld_id != source_listing_id:
            raise ListingDetailParseError(
                f"listing_id mismatch: URL has {source_listing_id!r}, "
                f"JSON-LD has {jsonld_id!r}"
            )

    title = jsonld_fields.get("title")
    if not title:
        title_tag = soup.title
        if title_tag:
            title = title_tag.get_text(" ", strip=True)
    if not title:
        raise ListingDetailParseError("missing required field: title")

    total_price_twd = jsonld_fields.get("total_price_twd")
    if total_price_twd is None:
        price_text = _price_text(soup)
        if price_text:
            total_price_twd = _extract_price_wan(price_text)

    unit_price_text = _element_text(
        soup, ".info-unit-price", "[class*='unit-price']", "[class*='unitPrice']"
    )
    unit_price_twd_per_ping = (
        _extract_unit_price(unit_price_text) if unit_price_text else None
    )

    area_ping = jsonld_fields.get("area_ping")
    if area_ping is None:
        area_text = _element_text(soup, ".info-area", "[class*='area']")
        if area_text:
            area_ping = _extract_ping(area_text)

    layout = jsonld_fields.get("layout")
    if layout is None:
        layout = _element_text(
            soup, ".info-layout", "[class*='layout']", "[class*='room']"
        )

    address = jsonld_fields.get("address")
    if address is None:
        address = _element_text(soup, ".info-address", "[class*='address']")

    community_name = _element_text(
        soup, ".info-community", "[class*='community']", "[class*='community']"
    )

    builder_name = _element_text(
        soup, ".info-builder", "[class*='builder']", "[class*='建商']"
    )

    building_type = _element_text(
        soup,
        ".info-building-type",
        "[class*='building-type']",
        "[class*='building']",
        "[class*='type']",
    )

    floor_str = _element_text(soup, ".info-floor", "[class*='floor']")
    floor, total_floors = _parse_floor_info(floor_str)

    age_text = _element_text(soup, ".info-age", "[class*='age']", "[class*='year']")
    age_years = _extract_age_years(age_text)

    parking_type = _element_text(
        soup, ".info-parking", "[class*='parking']", "[class*='車位']"
    )

    latitude = jsonld_fields.get("latitude")
    longitude = jsonld_fields.get("longitude")

    source_updated_text = _element_text(
        soup, ".info-updated", "[class*='update']", "[class*='time']"
    )

    total_price_twd = _validate_positive_int(total_price_twd, "total_price_twd")
    unit_price_twd_per_ping = _validate_positive_int(
        unit_price_twd_per_ping, "unit_price_twd_per_ping"
    )
    area_ping = _validate_positive_decimal(area_ping, "area_ping")
    if total_floors is not None and total_floors < 0:
        raise ListingDetailParseError(f"total_floors is negative: {total_floors}")
    age_years = _validate_positive_decimal(age_years, "age_years")

    if latitude is not None and (
        latitude < Decimal("-90") or latitude > Decimal("90")
    ):
        raise ListingDetailParseError(f"latitude out of range: {latitude}")
    if longitude is not None and (
        longitude < Decimal("-180") or longitude > Decimal("180")
    ):
        raise ListingDetailParseError(f"longitude out of range: {longitude}")

    title = _safe_persisted_text(
        title,
        field="title",
        max_length=160,
    )
    if title is None:
        raise ListingDetailParseError("listing title is required")
    layout = _safe_persisted_text(
        layout,
        field="layout",
        max_length=80,
    )
    address = _safe_persisted_text(
        address,
        field="address",
        max_length=300,
    )
    community_name = _safe_persisted_text(
        community_name,
        field="community_name",
        max_length=160,
    )
    builder_name = _safe_persisted_text(
        builder_name,
        field="builder_name",
        max_length=160,
    )
    building_type = _safe_persisted_text(
        building_type,
        field="building_type",
        max_length=80,
    )
    floor = _safe_persisted_text(
        floor,
        field="floor",
        max_length=40,
    )
    parking_type = _safe_persisted_text(
        parking_type,
        field="parking_type",
        max_length=80,
    )
    source_updated_text = _safe_persisted_text(
        source_updated_text,
        field="source_updated_text",
        max_length=120,
    )

    return ParsedListingDetail(
        listing_type=listing_type,
        source_listing_id=source_listing_id,
        title=title,
        total_price_twd=total_price_twd,
        unit_price_twd_per_ping=unit_price_twd_per_ping,
        area_ping=area_ping,
        layout=layout,
        address=address,
        community_name=community_name,
        builder_name=builder_name,
        building_type=building_type,
        floor=floor,
        total_floors=total_floors,
        age_years=age_years,
        parking_type=parking_type,
        latitude=latitude,
        longitude=longitude,
        source_updated_text=source_updated_text,
    )
