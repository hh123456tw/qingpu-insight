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
    unit_price_low_twd_per_ping: int | None = None
    unit_price_high_twd_per_ping: int | None = None
    area_low_ping: Decimal | None = None
    area_high_ping: Decimal | None = None


_PING_RE = re.compile(r"(-?[\d,.]+)\s*坪")
_WAN_PRICE_RE = re.compile(r"(-?[\d,.]+)\s*萬")
_UNIT_PRICE_RE = re.compile(r"(-?[\d,.]+)\s*萬/坪")
_FLOOR_RE = re.compile(r"(\d+)\s*[Ff]\s*/\s*(\d+)\s*[Ff]")
_AGE_RE = re.compile(r"(-?[\d.]+)\s*年")
_AGE_MONTH_RE = re.compile(r"(\d+)\s*個月")
_LAYOUT_SEGMENT_RE = re.compile(r"\d+\s*[房廳衛]")
_RANGE_RE = re.compile(r"([\d,.]+)\s*[~～﹣—]\s*([\d,.]+)")
_SALE_META_PRICE_RE = re.compile(r"總價([\d,.]+)萬")
_SALE_META_AREA_RE = re.compile(r"面積([\d,.]+)坪")
_SALE_META_COMMUNITY_RE = re.compile(r"位於(.+?)，")
_NEWHOUSE_META_LAYOUT_RE = re.compile(
    r"格局規劃(.+?)(?:、坪數規劃|[，。])"
)
_NEWHOUSE_META_NAME_RE = re.compile(r"「(.+?)」")


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
    if any(separator in text for separator in ("~", "～", "﹣", "—")):
        return None
    m = _UNIT_PRICE_RE.search(text.replace(",", ""))
    if m:
        try:
            return int(Decimal(m.group(1)) * 10000)
        except Exception:
            return None
    return None


def _extract_unit_price_range(
    text: str,
) -> tuple[int | None, int | None]:
    match = _RANGE_RE.search(text)
    if match is None or "萬/坪" not in text.replace(" ", ""):
        return None, None
    low = int(Decimal(match.group(1).replace(",", "")) * 10000)
    high = int(Decimal(match.group(2).replace(",", "")) * 10000)
    return (low, high) if low <= high else (high, low)


def _extract_ping_range(
    text: str,
) -> tuple[Decimal | None, Decimal | None]:
    match = _RANGE_RE.search(text)
    if match is None or "坪" not in text:
        return None, None
    low = Decimal(match.group(1).replace(",", ""))
    high = Decimal(match.group(2).replace(",", ""))
    return (low, high) if low <= high else (high, low)


def _element_text(soup: BeautifulSoup, *selectors: str) -> str | None:
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(" ", strip=True)
            if text:
                return text
    return None


def _meta_content(
    soup: BeautifulSoup,
    *,
    name: str | None = None,
    property_name: str | None = None,
) -> str | None:
    attrs = {"name": name} if name is not None else {"property": property_name}
    element = soup.find("meta", attrs=attrs)
    if element is None:
        return None
    content = element.get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None


def _labeled_value(
    soup: BeautifulSoup,
    *,
    row_selector: str,
    label_selector: str,
    value_selector: str,
    label: str,
) -> str | None:
    for row in soup.select(row_selector):
        label_element = row.select_one(label_selector)
        if label_element is None:
            continue
        normalized_label = "".join(
            label_element.get_text(" ", strip=True).split()
        )
        if normalized_label != "".join(label.split()):
            continue
        value_element = row.select_one(value_selector)
        if value_element is not None:
            value = value_element.get_text(" ", strip=True)
            if value:
                return value
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
    month_match = _AGE_MONTH_RE.search(text)
    if month_match:
        return Decimal(month_match.group(1)) / Decimal("12")
    return None


def _extract_map_coordinates(
    soup: BeautifulSoup,
) -> tuple[Decimal | None, Decimal | None]:
    map_template = soup.select_one("#payMap")
    if map_template is None:
        return None, None
    text = map_template.get_text(" ", strip=True) or map_template.decode_contents()
    match = re.search(
        r"[?&]lat=(-?[\d.]+)&(?:amp;)?lng=(-?[\d.]+)",
        text,
    )
    if match is None:
        return None, None
    return Decimal(match.group(1)), Decimal(match.group(2))


def has_listing_detail_content(
    html: str,
    *,
    listing_type: Literal["sale", "newhouse"],
) -> bool:
    """Return whether the current 591 detail page has rendered its core data."""
    if "application/ld+json" in html:
        return True
    soup = BeautifulSoup(html, "html.parser")
    if listing_type == "sale":
        return (
            soup.select_one(".info-price-num-2") is not None
            and soup.select_one(".info-floor-key-2") is not None
            and soup.select_one(".info-addr-content") is not None
        )
    return (
        soup.select_one(".build-price") is not None
        and soup.select_one(".info-item.address") is not None
    )


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
    meta_description = _meta_content(soup, name="description")

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
    newhouse_project_name = None
    if listing_type == "newhouse" and meta_description:
        project_name_match = _NEWHOUSE_META_NAME_RE.search(meta_description)
        if project_name_match:
            newhouse_project_name = project_name_match.group(1)
    if title is None and newhouse_project_name is not None:
        title = newhouse_project_name
    if not title:
        title_tag = soup.title
        if title_tag:
            title = title_tag.get_text(" ", strip=True)
    if not title:
        raise ListingDetailParseError("missing required field: title")

    total_price_twd = jsonld_fields.get("total_price_twd")
    if total_price_twd is None and listing_type == "sale" and meta_description:
        meta_price = _SALE_META_PRICE_RE.search(meta_description)
        if meta_price:
            total_price_twd = int(
                Decimal(meta_price.group(1).replace(",", "")) * 10000
            )
    if total_price_twd is None and listing_type == "sale":
        price_text = _price_text(soup)
        if price_text:
            total_price_twd = _extract_price_wan(price_text)

    unit_price_text = _element_text(
        soup,
        ".per-price-text",
        ".build-price.info-item",
        ".info-unit-price",
        "[class*='unit-price']",
        "[class*='unitPrice']",
    )
    unit_price_twd_per_ping = (
        _extract_unit_price(unit_price_text) if unit_price_text else None
    )
    unit_price_low_twd_per_ping, unit_price_high_twd_per_ping = (
        _extract_unit_price_range(unit_price_text)
        if unit_price_text
        else (None, None)
    )

    area_ping = jsonld_fields.get("area_ping")
    area_low_ping = None
    area_high_ping = None
    if area_ping is None and listing_type == "sale" and meta_description:
        meta_area = _SALE_META_AREA_RE.search(meta_description)
        if meta_area:
            area_ping = Decimal(meta_area.group(1).replace(",", ""))
    if area_ping is None:
        area_text = _element_text(soup, ".info-area", "[class*='area']")
        if area_text:
            area_ping = _extract_ping(area_text)
    if listing_type == "newhouse" and meta_description:
        area_match = re.search(
            r"坪數規劃(.+?)(?:[，。]|$)",
            meta_description,
        )
        if area_match:
            area_low_ping, area_high_ping = _extract_ping_range(
                area_match.group(1)
            )

    layout = None
    if listing_type == "sale":
        layout = _labeled_value(
            soup,
            row_selector=".info-floor-left-2",
            label_selector=".info-floor-value",
            value_selector=".info-floor-key-2",
            label="格局",
        )
    if layout is None:
        layout = jsonld_fields.get("layout")
    if layout is None and listing_type == "newhouse" and meta_description:
        layout_match = _NEWHOUSE_META_LAYOUT_RE.search(meta_description)
        if layout_match:
            layout = layout_match.group(1)
    if layout is None:
        layout = _element_text(
            soup,
            ".info-layout",
            ".layout-info.main-layout .layout-text",
            "[class*='room']",
        )

    address = jsonld_fields.get("address")
    if address is None and listing_type == "sale":
        address = _labeled_value(
            soup,
            row_selector=".info-addr-content",
            label_selector=".info-addr-key",
            value_selector=".info-addr-value-text",
            label="地址",
        )
    if address is None:
        address = _element_text(
            soup,
            ".info-address",
            ".info-item.address",
            "p.address",
            "[class*='address']",
        )
    if listing_type == "newhouse" and address:
        address = re.sub(r"^基地地址\s*", "", address)

    community_name = _element_text(soup, ".community-info-a", ".info-community")
    if community_name is None and newhouse_project_name is not None:
        community_name = newhouse_project_name
    if (
        community_name is None
        and listing_type == "sale"
        and meta_description
    ):
        meta_community = _SALE_META_COMMUNITY_RE.search(meta_description)
        if meta_community:
            community_name = meta_community.group(1)

    builder_name = _element_text(
        soup, ".info-builder", "[class*='builder']", "[class*='建商']"
    )

    building_type = _element_text(
        soup,
        ".info-building-type",
        "[class*='building-type']",
    )
    if building_type is None and listing_type == "sale":
        building_type = _labeled_value(
            soup,
            row_selector=".detail-house-item",
            label_selector=".detail-house-key",
            value_selector=".detail-house-value",
            label="型態",
        )
    if building_type is None and listing_type == "sale":
        for label in ("型態", "建物型態", "建物類型"):
            building_type = _labeled_value(
                soup,
                row_selector=".info-addr-content",
                label_selector=".info-addr-key",
                value_selector=".info-addr-value-text, .info-addr-value",
                label=label,
            )
            if building_type is not None:
                break

    floor_str = None
    if listing_type == "sale":
        floor_str = _labeled_value(
            soup,
            row_selector=".info-addr-content",
            label_selector=".info-addr-key",
            value_selector=".info-addr-value-text",
            label="樓層",
        )
    if floor_str is None:
        floor_str = _element_text(soup, ".info-floor", "[class*='floor']")
    floor, total_floors = _parse_floor_info(floor_str)

    age_text = None
    if listing_type == "sale":
        age_text = _labeled_value(
            soup,
            row_selector=".info-floor-left-2",
            label_selector=".info-floor-value",
            value_selector=".info-floor-key-2",
            label="屋齡",
        )
    if age_text is None:
        age_text = _element_text(
            soup, ".info-age", "[class*='age']", "[class*='year']"
        )
    age_years = _extract_age_years(age_text)

    parking_type = None
    if listing_type == "sale":
        parking_type = _labeled_value(
            soup,
            row_selector=".detail-house-item",
            label_selector=".detail-house-key",
            value_selector=".detail-house-value",
            label="車位",
        )
    if parking_type is None and listing_type == "sale":
        parking_type = _labeled_value(
            soup,
            row_selector=".info-addr-content",
            label_selector=".info-addr-key",
            value_selector=".info-addr-value-text, .info-addr-value",
            label="車位",
        )
    if parking_type is None:
        parking_type = _element_text(
            soup, ".info-parking", "[class*='parking']", "[class*='車位']"
        )

    latitude = jsonld_fields.get("latitude")
    longitude = jsonld_fields.get("longitude")
    if latitude is None or longitude is None:
        map_latitude, map_longitude = _extract_map_coordinates(soup)
        latitude = latitude if latitude is not None else map_latitude
        longitude = longitude if longitude is not None else map_longitude

    source_updated_text = _element_text(
        soup, ".update-package", ".info-updated", "[class*='update']"
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
        unit_price_low_twd_per_ping=unit_price_low_twd_per_ping,
        unit_price_high_twd_per_ping=unit_price_high_twd_per_ping,
        area_low_ping=area_low_ping,
        area_high_ping=area_high_ping,
    )
