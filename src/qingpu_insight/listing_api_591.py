"""591 sale list through its JSON API, called from inside a logged-in Chrome profile.

Data flow::

    dedicated Chrome profile (logged in once by hand: ``qingpu-data 591-login``)
      -> load ROUTES["sale"] once (refuse verification / login pages)
      -> in-page ``fetch(bff-house.591.com.tw/v1/web/sale/list?...&firstRow=N,
         {credentials: "include"})`` stepping ``firstRow`` by the page size, 3–6 s apart,
         until ``firstRow >= total`` (terminal, complete batch) or ``max_pages``
      -> sanitize_api_response: whitelist of listing fields only; agent names, phones,
         avatars and user ids are never written; newhouse ads dropped; titles scrubbed
      -> page-NNNN.json + checkpoint.json + manifest.json in the raw batch dir
      -> extract_api_page -> SourceListing payloads for listing_normalization

Anything unexpected (non-200, ``status != 1``, a login or verification answer) stops the
batch with a CaptureError. Nothing here works around 591's login or verification.
"""

from __future__ import annotations

import json
import math
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from qingpu_insight.conversation_listing_parser import scrub_contact_text
from qingpu_insight.listing_591 import (
    ExtractionResult,
    ListingSchemaError,
    RejectedListing,
    SourceListing,
    extract_rendered_page,
)
from qingpu_insight.listing_capture import (
    ROUTES,
    ChromeConfig,
    RawBatchWriter,
    _route_provenance_matches,
    create_chrome,
    is_verification_page,
)
from qingpu_insight.listing_sources import (
    CaptureBatch,
    CapturedPage,
    CaptureError,
    ListingType,
)

API_URL = "https://bff-house.591.com.tw/v1/web/sale/list"
# Same filter as ROUTES["sale"]: 桃園 (regionid 6), 機場捷運 (metro 278), A17–A19 stations.
API_QUERY: tuple[tuple[str, str], ...] = (
    ("type", "2"),
    ("category", "1"),
    ("regionid", "6"),
    ("metro", "278"),
    ("station", "66331,66332,66330"),
)
API_PAGE_SIZE = 30
API_REPRESENTATION = "api"
API_SCHEMA_VERSION = "591-sale-api-v1"
API_DEFAULT_MAX_PAGES = 300
MIN_API_DELAY_SECONDS = 3.0
SERVICE_RADIUS_M = 2_000.0
PROFILE_ENV = "QINGPU_591_PROFILE_DIR"

# 591 names the nearest station without the 站 suffix on most items (領航, 桃園體育園區).
_STATION_CODES = {"領航": "A17", "高鐵桃園": "A18", "桃園體育園區": "A19"}

# Personal / contact data that must never reach disk, the database or a report.
CONTACT_FIELDS = frozenset(
    {
        "linkman",
        "nick_name",
        "phonenum",
        "mobile",
        "avatar",
        "user_id",
        "call_num",
        "agent_label_text",
        "email",
        "company",
        "device_id",
    }
)
# Only these listing fields are kept from each API item.
_ITEM_FIELDS = (
    "houseid",
    "type",
    "kind",
    "kind_name",
    "shape_name",
    "region_name",
    "section_name",
    "address",
    "community_name",
    "community_link",
    "title",
    "price",
    "price_has_carport",
    "original_price",
    "diff_price",
    "is_down_price",
    "down_price_percent",
    "unitprice",
    "area",
    "mainarea",
    "ratio",
    "room",
    "floor",
    "houseage",
    "showhouseage",
    "has_carport",
    "cartmodel",
    "distance",
    "distance_name",
    "posttime",
    "refreshtime",
    "tag",
    "operation_tag",
)
_TEXT_ITEM_FIELDS = ("title", "address", "community_name", "room", "floor", "cartmodel")

_FETCH_SCRIPT = """
var url = arguments[0];
var done = arguments[arguments.length - 1];
fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}})
  .then(function (response) {
    return response.text().then(function (body) {
      done({status: response.status, body: body});
    });
  })
  .catch(function (error) { done({status: 0, body: String(error)}); });
"""

_LOGIN_TERMS = ("登入", "登录", "login", "請先登", "未登")


class MissingProfileDir(ValueError):
    """The API source needs the dedicated, logged-in Chrome profile."""

    def __init__(self) -> None:
        super().__init__(
            "591 API 來源需要已登入的專用 Chrome profile：請設定 "
            f"{PROFILE_ENV} 或 --profile-dir，並先執行 qingpu-data 591-login 手動登入。"
        )


def resolve_profile_dir(explicit: str | None, environ: dict[str, str] | None = None) -> str | None:
    """``--profile-dir`` wins, then ``QINGPU_591_PROFILE_DIR``; blank values count as unset."""
    import os

    env = os.environ if environ is None else environ
    for value in (explicit, env.get(PROFILE_ENV)):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def station_from_distance_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if name.endswith("站"):
        name = name[:-1]
    return _STATION_CODES.get(name)


# --------------------------------------------------------------------------- sanitising


def _scrub(value: object) -> object:
    if isinstance(value, str):
        return scrub_contact_text(value)
    return value


def _sanitize_item(item: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key in _ITEM_FIELDS:
        if key not in item:
            continue
        value = item[key]
        if key in _TEXT_ITEM_FIELDS:
            value = _scrub(value)
        elif key == "tag":
            value = [scrub_contact_text(v) for v in value if isinstance(v, str)] if isinstance(
                value, list
            ) else []
        elif key == "operation_tag":
            value = (
                {
                    k: scrub_contact_text(v)
                    for k, v in value.items()
                    if k in ("title", "sub_title") and isinstance(v, str)
                }
                if isinstance(value, dict)
                else None
            )
        elif isinstance(value, (dict, list)):
            continue
        kept[key] = value
    return kept


def _is_ad(item: dict[str, Any]) -> bool:
    return bool(item.get("is_newhouse")) or str(item.get("type")) == "8"


def _api_total(data: dict[str, Any]) -> int:
    try:
        total = int(str(data.get("total")).strip())
    except (TypeError, ValueError):
        raise ValueError("API response has no numeric total") from None
    if total < 0:
        raise ValueError("API response total is negative")
    return total


def sanitize_api_response(document: dict[str, Any], *, first_row: int) -> dict[str, Any]:
    """Keep only what the listing pipeline needs; contact fields never survive this."""
    data = document.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("house_list"), list):
        raise ValueError("API response has no house_list")
    items = [item for item in data["house_list"] if isinstance(item, dict)]
    sale_items = [item for item in items if not _is_ad(item)]
    return {
        "schema_version": API_SCHEMA_VERSION,
        "first_row": int(first_row),
        "total": _api_total(data),
        "dropped_ads": len(items) - len(sale_items),
        "house_list": [_sanitize_item(item) for item in sale_items],
    }


# --------------------------------------------------------------------------- extraction


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _wan_to_twd(value: object) -> int | None:
    number = _number(value)
    return int(round(number * 10_000)) if number is not None and number > 0 else None


_LAYOUT_PART = {"房": "layout_rooms", "廳": "layout_living_rooms", "衛": "layout_bathrooms"}


def _layout(room: object) -> dict[str, int | None]:
    text = room if isinstance(room, str) else ""
    out: dict[str, int | None] = {}
    for marker, key in _LAYOUT_PART.items():
        match = re.search(rf"(\d+)\s*{marker}", text)
        out[key] = int(match.group(1)) if match else None
    return out


def _floors(value: object) -> tuple[int | None, int | None]:
    text = value.strip() if isinstance(value, str) else ""
    exact = re.fullmatch(r"(\d+)\s*F\s*/\s*(\d+)\s*F", text, flags=re.IGNORECASE)
    if exact:
        return int(exact.group(1)), int(exact.group(2))
    total = re.search(r"/\s*(\d+)\s*F\s*\Z", text, flags=re.IGNORECASE)
    return None, int(total.group(1)) if total else None


def _age_years(houseage: object, text: object) -> float | None:
    label = text.strip() if isinstance(text, str) else ""
    years = re.search(r"(\d+)\s*年", label)
    months = re.search(r"(\d+)\s*個月", label)
    if years or months:
        return (int(years.group(1)) if years else 0) + (
            int(months.group(1)) / 12 if months else 0.0
        )
    number = _number(houseage)
    return float(number) if number is not None and number > 0 else None


def _parking(has_carport: object, cartmodel: object) -> str | None:
    flag = _number(has_carport)
    if flag == 1:
        model = cartmodel.strip() if isinstance(cartmodel, str) else ""
        return model or "有車位"
    if flag == 0:
        return "無車位"
    return None


def _percent(value: object) -> float | None:
    if isinstance(value, str):
        value = value.replace("%", "")
    number = _number(value)
    return number if number is not None and 0 < number < 100 else None


def _posted_at(value: object) -> str | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    try:
        return datetime.fromtimestamp(number, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


class _Reject(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _listing_from_item(item: dict[str, Any]) -> SourceListing:
    if _is_ad(item):
        raise _Reject("newhouse_ad", "newhouse advertisement")
    if str(item.get("type")) != "2":
        raise _Reject("not_sale", "item is not a resale listing")
    listing_id = str(item.get("houseid") or "").strip()
    if not listing_id.isdigit():
        raise _Reject("missing_id", "item has no numeric houseid")
    station_code = station_from_distance_name(item.get("distance_name"))
    distance = _number(item.get("distance"))
    if station_code is None or distance is None or not 0 <= distance <= SERVICE_RADIUS_M:
        raise _Reject("out_of_area", "item is not within 2 km of A17–A19")
    price = _wan_to_twd(item.get("price"))
    if price is None:
        raise _Reject("missing_price", "item has no positive price")
    area = _number(item.get("area"))
    if area is None or area <= 0:
        raise _Reject("missing_area", "item has no positive area")
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        raise _Reject("missing_title", "item has no title")

    floor, total_floors = _floors(item.get("floor"))
    main_area = _number(item.get("mainarea"))
    original = _wan_to_twd(item.get("original_price"))
    down_percent = _number(item.get("down_price_percent"))
    url = f"https://sale.591.com.tw/home/house/detail/2/{listing_id}.html"
    payload: dict[str, Any] = {
        "id": listing_id,
        "url": url,
        "title": scrub_contact_text(title.strip()),
        "asking_price_twd": price,
        "area_ping": float(area),
        **_layout(item.get("room")),
        "layout": item.get("room") if isinstance(item.get("room"), str) else None,
        "floor": floor,
        "total_floors": total_floors,
        "floor_text": item.get("floor") if isinstance(item.get("floor"), str) else None,
        "lat": None,
        "lng": None,
        "building_type": (
            item["shape_name"].strip()
            if isinstance(item.get("shape_name"), str) and item["shape_name"].strip()
            else None
        ),
        "building_age_years": _age_years(item.get("houseage"), item.get("showhouseage")),
        "building_age_text": item.get("showhouseage")
        if isinstance(item.get("showhouseage"), str)
        else None,
        "parking_type": _parking(item.get("has_carport"), item.get("cartmodel")),
        "price_has_carport": _number(item.get("price_has_carport")),
        "station_code": station_code,
        "station_name": item.get("distance_name"),
        "station_distance_m": float(distance),
        "section_name": item.get("section_name") if isinstance(item.get("section_name"), str)
        else None,
        "community_name": scrub_contact_text(item["community_name"].strip())
        if isinstance(item.get("community_name"), str) and item["community_name"].strip()
        else None,
        "main_area_ping": float(main_area) if main_area is not None and main_area > 0 else None,
        "listed_common_area_percent": _percent(item.get("ratio")),
        "original_price_twd": original if original is not None and original > price else None,
        "down_price_percent": down_percent
        if down_percent is not None and down_percent > 0 and original
        else None,
        "posted_at": _posted_at(item.get("posttime")),
        "representation": API_REPRESENTATION,
        "schema_version": API_SCHEMA_VERSION,
    }
    return SourceListing(listing_id, "sale", url, payload)


def extract_api_page(document: dict[str, Any] | str) -> ExtractionResult:
    """Sanitized API page -> validated A17–A19 sale listings (deduplicated by houseid)."""
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError:
            raise ListingSchemaError("API page is not valid JSON") from None
    if not isinstance(document, dict) or not isinstance(document.get("house_list"), list):
        raise ListingSchemaError("API page has no house_list")
    listings: list[SourceListing] = []
    rejected: list[RejectedListing] = []
    seen: set[str] = set()
    for item in document["house_list"]:
        if not isinstance(item, dict):
            rejected.append(RejectedListing("unknown", "malformed_item", "item is not an object"))
            continue
        ref = str(item.get("houseid") or "unknown")
        try:
            listing = _listing_from_item(item)
        except _Reject as error:
            rejected.append(RejectedListing(ref, error.code, error.message))
            continue
        if listing.source_listing_id in seen:
            rejected.append(RejectedListing(ref, "duplicate", "houseid repeated"))
            continue
        seen.add(listing.source_listing_id)
        listings.append(listing)
    return ExtractionResult(
        listings=listings,
        rejected=rejected,
        representation=API_REPRESENTATION,  # type: ignore[arg-type]
        schema_version=API_SCHEMA_VERSION,
    )


def extract_captured_page(
    content: str, listing_type: ListingType, representation: str
) -> ExtractionResult:
    """One captured page of either source: API JSON pages or rendered DOM pages."""
    if representation == API_REPRESENTATION:
        if listing_type != "sale":
            raise ListingSchemaError("API pages only carry sale listings")
        return extract_api_page(content)
    return extract_rendered_page(content, listing_type)


def page_file_name(page_number: int, representation: str) -> str:
    suffix = "json" if representation == API_REPRESENTATION else "html"
    return f"page-{page_number:04d}.{suffix}"


# --------------------------------------------------------------------------- capture


def api_list_url(first_row: int, timestamp_ms: int) -> str:
    params = [("timestamp", str(timestamp_ms)), *API_QUERY,
              ("firstRow", str(first_row)), ("shType", "list")]
    return f"{API_URL}?{urlencode(params, safe=',')}"


class _Stop(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = scrub_contact_text(message[:200]) or ""


def _looks_like_login(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _LOGIN_TERMS)


def _parse_response(response: object) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise _Stop("invalid_response", "in-page fetch returned no response object")
    status = response.get("status")
    body = response.get("body")
    body = body if isinstance(body, str) else ""
    if status in (401, 403):
        raise _Stop("login_required", f"591 API answered HTTP {status}; log in again")
    if status == 429:
        raise _Stop("rate_limited", "591 API answered HTTP 429; stopped politely")
    if status != 200:
        raise _Stop("http_error", f"591 API answered HTTP {status}")
    if body.lstrip().startswith("<") and is_verification_page(body):
        raise _Stop("verification_required", "591 verification page detected")
    try:
        document = json.loads(body)
    except ValueError:
        raise _Stop("invalid_response", "591 API returned non-JSON content") from None
    if not isinstance(document, dict):
        raise _Stop("invalid_response", "591 API returned an unexpected JSON shape")
    if str(document.get("status")) != "1":
        message = " ".join(
            str(document.get(key)) for key in ("msg", "message", "info") if document.get(key)
        )
        if _looks_like_login(message):
            raise _Stop("login_required", f"591 API requires login: {message}")
        raise _Stop("api_status", f"591 API status {document.get('status')!r}: {message}")
    return document


class Logged591ApiSource:
    """ListingSource for ``sale`` over 591's list API inside the logged-in profile."""

    def __init__(
        self,
        browser: Any | None = None,
        writer: RawBatchWriter | None = None,
        config: ChromeConfig | None = None,
        base_dir: Path | None = None,
        *,
        page_size: int = API_PAGE_SIZE,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        browser_factory: Callable[[ChromeConfig], Any] = create_chrome,
    ) -> None:
        self._config = config or ChromeConfig()
        if not self._config.profile_dir:
            raise MissingProfileDir()
        low, high = self._config.delay_seconds
        if low < MIN_API_DELAY_SECONDS or high < low:
            raise ValueError(
                f"API delay must be at least {MIN_API_DELAY_SECONDS:g}s and ordered"
            )
        if page_size < 1:
            raise ValueError("page_size must be positive")
        self._browser = browser
        self._writer = writer
        self._base_dir = base_dir or Path.cwd()
        self._page_size = page_size
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._clock = clock
        self._browser_factory = browser_factory

    def capture(self, listing_type: ListingType, max_pages: int = API_DEFAULT_MAX_PAGES
                ) -> CaptureBatch:
        browser = self._browser or self._browser_factory(self._config)
        started = self._clock()
        if listing_type != "sale":
            batch = CaptureBatch(
                batch_id=f"591-{listing_type}-{started.strftime('%Y%m%dT%H%M%SZ')}",
                source="591",
                listing_type=listing_type,
                started_at=started,
            )
            batch.errors.append(CaptureError(0, "invalid_type",
                                             f"API source only supports sale, not {listing_type}"))
            browser.quit()
            return batch
        writer = self._writer or RawBatchWriter(self._base_dir, listing_type)
        self._writer = None  # an injected writer is one-shot
        batch = CaptureBatch(
            batch_id=writer.batch_id,
            source="591",
            listing_type=listing_type,
            started_at=started,
            batch_dir=writer.batch_dir,
        )
        try:
            self._open_list_page(browser)
            self._paginate(browser, writer, batch, max_pages)
        except _Stop as stop:
            page_number = len(batch.pages) + 1
            batch.errors.append(CaptureError(page_number, stop.code, stop.message))
        except Exception as error:  # driver crashes, script timeouts
            page_number = len(batch.pages) + 1
            batch.errors.append(
                CaptureError(page_number, "page_failed", type(error).__name__)
            )
        finally:
            if batch.errors:
                batch.reached_terminal_page = False
            try:
                writer.write_manifest(batch)
            finally:
                browser.quit()
        return batch

    def _open_list_page(self, browser: Any) -> None:
        url = ROUTES["sale"]
        try:
            browser.get(url)
        except Exception as error:
            raise _Stop("navigation_failed", type(error).__name__) from None
        current = str(getattr(browser, "current_url", "") or "")
        if is_verification_page(str(browser.page_source or "")):
            raise _Stop("verification_required", "591 verification page detected")
        if not _route_provenance_matches(current, "sale"):
            path = (urlsplit(current).path or "").lower() if current else ""
            if "login" in path or "login" in current.lower():
                raise _Stop("login_required", "591 redirected to its login page; log in again")
            raise _Stop("navigation_failed", "list page did not stay on the Taoyuan sale route")
        try:
            browser.set_script_timeout(self._config.page_timeout_seconds)
        except Exception:
            pass

    def _paginate(self, browser: Any, writer: RawBatchWriter, batch: CaptureBatch,
                  max_pages: int) -> None:
        first_row = 0
        for page_number in range(1, max_pages + 1):
            if page_number > 1:
                self._sleep(self._rng.uniform(*self._config.delay_seconds))
            url = api_list_url(first_row, int(self._clock().timestamp() * 1000))
            document = _parse_response(browser.execute_async_script(_FETCH_SCRIPT, url))
            try:
                sanitized = sanitize_api_response(document, first_row=first_row)
            except ValueError as error:
                raise _Stop("invalid_response", str(error)) from None
            total = sanitized["total"]
            terminal = first_row + self._page_size >= total
            if not sanitized["house_list"] and not terminal and first_row < total:
                raise _Stop("empty_page", f"no listings at firstRow={first_row} of {total}")
            text = json.dumps(sanitized, ensure_ascii=False)
            extraction = extract_api_page(sanitized)
            writer.write_json_page(page_number, text)
            writer.write_checkpoint(page_number)
            batch.pages.append(
                CapturedPage(
                    page_number=page_number,
                    url=API_URL,
                    html=text,
                    accepted_count=len(extraction.listings),
                    rejected_count=len(extraction.rejected),
                    representation=API_REPRESENTATION,
                    schema_version=API_SCHEMA_VERSION,
                )
            )
            first_row += self._page_size
            if first_row >= total:
                batch.reached_terminal_page = True
                return
