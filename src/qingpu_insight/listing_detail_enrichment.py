"""Conservative address enrichment from public 591 new-house detail pages."""

import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from qingpu_insight.listing_591 import SourceListing
from qingpu_insight.listing_capture import ChromeConfig, create_chrome, is_verification_page

_NEW_HOUSE_HOST = "newhouse.591.com.tw"
# listing_591 emits numeric JSON-LD IDs and canonical legacy DOM IDs (NH-<digits>).
_SAFE_LISTING_ID = re.compile(r"(?:[0-9]{1,32}|NH-[0-9]{1,32})\Z")
_DOORPLATE = (
    r"(?:[0-9]+(?:之[0-9]+)?號(?:[0-9]+樓)?|"
    r"[0-9]+號之[0-9]+(?: [0-9]+樓)?)"
)
_ADDRESS = re.compile(
    r"桃園市(?:中壢區|大園區)[一-龥A-Za-z0-9]+(?:路|街)"
    r"(?:[一二三四五六七八九十0-9]+段)?(?:[0-9]+巷)?(?:[0-9]+弄)?"
    + _DOORPLATE
    + r"\Z"
)


class DetailBrowser(Protocol):
    page_source: str
    current_url: str

    def get(self, url: str) -> None: ...

    def quit(self) -> None: ...


class DetailEnrichmentBlocked(RuntimeError):
    """A detail page cannot safely be used for address enrichment."""


@dataclass(frozen=True)
class DetailAddress:
    address: str
    source_url: str
    observed_at: datetime
    representation: Literal["jsonld_postal_address", "dom_street_address"]


def extract_detail_address(
    html: str, source_url: str, observed_at: datetime
) -> DetailAddress | None:
    """Extract a validated target-area address from explicit public markup only."""
    soup = BeautifulSoup(html, "html.parser")
    for postal_address in _jsonld_postal_addresses(soup):
        address = _postal_address_text(postal_address)
        if _is_accepted_address(address):
            return DetailAddress(
                address=address,
                source_url=source_url,
                observed_at=observed_at,
                representation="jsonld_postal_address",
            )

    for selector in ('[itemprop="streetAddress"]', ".detail-address", ".house-address"):
        for element in soup.select(selector):
            if element.find(True) is not None:
                continue
            address = _normalize_address(element.get_text(" ", strip=True))
            if _is_accepted_address(address):
                return DetailAddress(
                    address=address,
                    source_url=source_url,
                    observed_at=observed_at,
                    representation="dom_street_address",
                )
    return None


class ListingDetailEnricher:
    """Fetch detail evidence through a visible, config-aware Chrome factory.

    Pass a concrete browser for deterministic tests, or use a
    ``create_chrome(config)``-compatible ``browser_factory`` with an explicit
    ``ChromeConfig``. Headless configurations are rejected.
    """

    def __init__(
        self,
        browser: DetailBrowser | None = None,
        clock: Callable[[], datetime] | None = None,
        *,
        browser_factory: Callable[[ChromeConfig], DetailBrowser] = create_chrome,
        chrome_config: ChromeConfig | None = None,
    ) -> None:
        if browser is not None and callable(browser):
            raise TypeError("pass browser factories with browser_factory= and chrome_config=")
        self._chrome_config = chrome_config or ChromeConfig()
        if self._chrome_config.headless:
            raise ValueError("detail enrichment requires a visible Chrome configuration")
        self._browser = browser
        self._browser_factory = browser_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def enrich(self, listing: SourceListing, batch_dir: Path) -> SourceListing:
        if listing.listing_type != "newhouse":
            return listing
        safe_name = _safe_evidence_name(listing.source_listing_id)
        if safe_name is None:
            raise DetailEnrichmentBlocked("unsafe_listing_id")
        if not _is_allowed_detail_url(listing.source_url):
            raise DetailEnrichmentBlocked("unsafe_source_url")

        browser = self._new_browser()
        try:
            browser.get(listing.source_url)
            final_url = browser.current_url
            if not _is_allowed_detail_url(final_url):
                raise DetailEnrichmentBlocked("unsafe_final_url")
            html = browser.page_source
            if is_verification_page(html):
                raise DetailEnrichmentBlocked("verification_required")

            observed_at = self._observed_at()
            address = extract_detail_address(html, final_url, observed_at)
            if address is None:
                _atomic_write(_evidence_path(batch_dir, "details-diagnostic", safe_name), html)
                return listing

            _atomic_write(_evidence_path(batch_dir, "details", safe_name), html)
            payload = dict(listing.payload)
            payload.update(
                {
                    "structured_address": address.address,
                    "address_source_url": address.source_url,
                    "address_observed_at": address.observed_at,
                }
            )
            return SourceListing(
                source_listing_id=listing.source_listing_id,
                listing_type=listing.listing_type,
                source_url=listing.source_url,
                payload=payload,
            )
        finally:
            browser.quit()

    def _new_browser(self) -> DetailBrowser:
        return self._browser or self._browser_factory(self._chrome_config)

    def _observed_at(self) -> datetime:
        observed_at = self._clock()
        if observed_at.utcoffset() is None:
            return observed_at.replace(tzinfo=UTC)
        return observed_at.astimezone(UTC)


def _is_allowed_detail_url(url: str) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == _NEW_HOUSE_HOST
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.fragment
    )


def _safe_evidence_name(source_listing_id: str) -> str | None:
    if not isinstance(source_listing_id, str) or not _SAFE_LISTING_ID.fullmatch(source_listing_id):
        return None
    return f"{source_listing_id}.html"


def _jsonld_postal_addresses(soup: BeautifulSoup) -> Iterator[dict[str, object]]:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            document = json.loads(script.get_text())
        except json.JSONDecodeError:
            continue
        yield from _walk_postal_addresses(document)


def _walk_postal_addresses(value: object) -> Iterator[dict[str, object]]:
    if isinstance(value, dict):
        kind = value.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "PostalAddress" in kinds:
            yield value
        for child in value.values():
            yield from _walk_postal_addresses(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_postal_addresses(child)


def _postal_address_text(postal_address: dict[str, object]) -> str:
    region = postal_address.get("addressRegion")
    locality = postal_address.get("addressLocality")
    street = postal_address.get("streetAddress")
    if not all(isinstance(part, str) and part.strip() for part in (region, locality, street)):
        return ""
    prefix = _normalize_address(f"{region}{locality}")
    normalized_street = _normalize_address(street)
    if normalized_street.startswith(prefix):
        return normalized_street
    if normalized_street.startswith(_normalize_address(locality)):
        return _normalize_address(f"{region}{normalized_street}")
    return f"{prefix}{normalized_street}"


def _normalize_address(address: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in address):
        return ""
    normalized = address.strip().replace("台", "臺")
    floor_match = re.search(r"號之([0-9]+)[ \u3000]+([0-9]+樓)\Z", normalized)
    if floor_match:
        prefix = normalized[: floor_match.start()].replace(" ", "").replace("\u3000", "")
        return f"{prefix}號之{floor_match.group(1)} {floor_match.group(2)}"
    return normalized.replace(" ", "").replace("\u3000", "")


def _is_accepted_address(address: str) -> bool:
    return bool(_ADDRESS.fullmatch(address))


def _evidence_path(batch_dir: Path, directory_name: str, safe_name: str) -> Path:
    batch_root = batch_dir.resolve(strict=False)
    output_directory = batch_dir / directory_name
    if _unsafe_output_directory(output_directory):
        raise DetailEnrichmentBlocked("unsafe_output_directory")
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise DetailEnrichmentBlocked("unsafe_output_directory") from error
    if _unsafe_output_directory(output_directory):
        raise DetailEnrichmentBlocked("unsafe_output_directory")
    try:
        output_directory.resolve(strict=True).relative_to(batch_root)
    except ValueError as error:
        raise DetailEnrichmentBlocked("unsafe_output_directory") from error
    return output_directory / safe_name


def _unsafe_output_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    is_reparse_point = bool(getattr(metadata, "st_file_attributes", 0) & reparse_attribute)
    return stat.S_ISLNK(metadata.st_mode) or is_reparse_point or not stat.S_ISDIR(metadata.st_mode)


def _atomic_write(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(html)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
