"""Selenium-based capture for 591 listing pages."""

import json
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait

from qingpu_insight.listing_591 import (
    CARD_SELECTORS,
    ListingSchemaError,
    extract_rendered_page,
)
from qingpu_insight.listing_sources import (
    CaptureBatch,
    CapturedPage,
    CaptureError,
    ListingType,
)

ROUTES: dict[str, str] = {
    "sale": "https://sale.591.com.tw/?shType=list&regionid=6",
    "newhouse": "https://newhouse.591.com.tw/housing-list.html?regionid=6",
    "rental": "https://rent.591.com.tw/list?region=6",
}

EMPTY_SELECTORS: dict[str, tuple[str, ...]] = {
    "sale": ("div.no-result", ".empty-state", "p.empty"),
    "newhouse": ("div.no-result", ".empty-state", "p.empty"),
    "rental": ("div.no-result", ".empty-state", "p.empty"),
}

ROUTE_PROVENANCE: dict[str, tuple[str, str]] = {
    "sale": ("sale.591.com.tw", "regionid"),
    "newhouse": ("newhouse.591.com.tw", "regionid"),
    "rental": ("rent.591.com.tw", "region"),
}


@dataclass(frozen=True)
class ChromeConfig:
    binary: str | None = None
    profile_dir: str | None = None
    headless: bool = False
    page_timeout_seconds: int = 30
    delay_seconds: tuple[float, float] = (2.0, 5.0)
    max_retries: int = 3


class RawBatchWriter:
    def __init__(self, base_dir: Path, listing_type: ListingType):
        created_at = datetime.now(UTC)
        self._listing_type = listing_type
        timestamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        batch_stem = f"591-{listing_type}-{timestamp}"
        day_dir = (
            base_dir
            / "data"
            / "raw"
            / "listings"
            / "591"
            / created_at.strftime("%Y-%m-%d")
        )
        day_dir.mkdir(parents=True, exist_ok=True)
        collision = 0
        while True:
            suffix = "" if collision == 0 else f"-{collision:04d}"
            batch_id = f"{batch_stem}{suffix}"
            batch_dir = day_dir / batch_id
            try:
                batch_dir.mkdir(exist_ok=False)
            except FileExistsError:
                collision += 1
                continue
            self._batch_id = batch_id
            self._batch_dir = batch_dir
            break

    @property
    def batch_dir(self) -> Path:
        return self._batch_dir

    @property
    def batch_id(self) -> str:
        return self._batch_id

    @property
    def listing_type(self) -> ListingType:
        return self._listing_type

    def write_page(self, page_number: int, html: str) -> Path:
        self._batch_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._batch_dir / f"page-{page_number:04d}.html.tmp"
        final = self._batch_dir / f"page-{page_number:04d}.html"
        tmp.write_text(html, encoding="utf-8")
        tmp.replace(final)
        return final

    def write_diagnostic(self, page_number: int, html: str) -> Path:
        self._batch_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._batch_dir / f"diagnostic-page-{page_number:04d}.html.tmp"
        final = self._batch_dir / f"diagnostic-page-{page_number:04d}.html"
        tmp.write_text(html, encoding="utf-8")
        tmp.replace(final)
        return final

    def write_checkpoint(self, page_number: int) -> None:
        self._batch_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._batch_dir / "checkpoint.json.tmp"
        final = self._batch_dir / "checkpoint.json"
        tmp.write_text(json.dumps({"last_page": page_number}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(final)

    def write_manifest(self, batch: CaptureBatch) -> None:
        self._batch_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "batch_id": batch.batch_id,
            "source": batch.source,
            "listing_type": batch.listing_type,
            "started_at": batch.started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "pages": [
                {
                    "page_number": p.page_number,
                    "url": p.url,
                    "accepted_count": p.accepted_count,
                    "rejected_count": p.rejected_count,
                    "representation": p.representation,
                    "schema_version": p.schema_version,
                }
                for p in batch.pages
            ],
            "errors": [{"page_number": e.page_number, "code": e.code,
                         "message": e.message} for e in batch.errors],
            "reached_terminal_page": batch.reached_terminal_page,
            "is_complete": batch.is_complete,
        }
        tmp = self._batch_dir / "manifest.json.tmp"
        final = self._batch_dir / "manifest.json"
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(final)


class Selenium591Source:
    """Capture 591 pages with a fresh internal writer per invocation.

    An explicitly injected writer is a one-shot dependency intended for tests
    and controlled single-batch callers.
    """

    def __init__(
        self,
        browser: webdriver.Chrome | None = None,
        writer: RawBatchWriter | None = None,
        config: ChromeConfig | None = None,
        base_dir: Path | None = None,
    ):
        self._config = config or ChromeConfig()
        self._browser = browser
        self._writer = writer
        self._writer_was_injected = writer is not None
        self._injected_writer_consumed = False
        self._base_dir = base_dir or Path.cwd()

    def capture(self, listing_type: ListingType, max_pages: int = 10) -> CaptureBatch:
        if self._writer_was_injected and self._injected_writer_consumed:
            if self._browser is not None:
                self._browser.quit()
            raise RuntimeError("injected RawBatchWriter is one-shot")

        browser = self._browser or create_chrome(self._config)
        writer = self._writer if self._writer_was_injected else None

        url = ROUTES.get(listing_type)
        if not url:
            batch = CaptureBatch(
                batch_id=f"591-{listing_type}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                source="591",
                listing_type=listing_type,
                started_at=datetime.now(UTC),
            )
            batch.errors.append(
                CaptureError(page_number=0, code="invalid_type",
                             message=f"Unknown listing type: {listing_type}")
            )
            browser.quit()
            return batch
        if writer and writer.listing_type != listing_type:
            browser.quit()
            raise ValueError(
                "writer listing type "
                f"{writer.listing_type!r} does not match capture type {listing_type!r}"
            )
        writer = writer or RawBatchWriter(self._base_dir, listing_type)
        if self._writer_was_injected:
            self._injected_writer_consumed = True
        self._writer = writer

        batch = CaptureBatch(
            batch_id=writer.batch_id,
            source="591",
            listing_type=listing_type,
            started_at=datetime.now(UTC),
            batch_dir=writer.batch_dir,
        )

        try:
            browser.get(url)
        except Exception as exc:
            batch.errors.append(
                CaptureError(page_number=1, code="navigation_failed", message=str(exc))
            )
            try:
                _write_diagnostic(writer, 1, browser)
                writer.write_manifest(batch)
            finally:
                browser.quit()
            return batch

        should_stop = False
        try:
            for page_num in range(1, max_pages + 1):
                if should_stop:
                    break
                for attempt in range(self._config.max_retries + 1):
                    try:
                        ready_state = WebDriverWait(
                            browser, self._config.page_timeout_seconds
                        ).until(
                            lambda active_browser: _capture_readiness(
                                active_browser,
                                CARD_SELECTORS.get(listing_type, ()),
                                EMPTY_SELECTORS.get(listing_type, ()),
                            )
                        )
                        html = browser.page_source
                        if ready_state == "verification":
                            raise _VerificationRequired
                        if ready_state == "empty":
                            if batch.pages:
                                batch.reached_terminal_page = True
                            should_stop = True
                            break

                        if not _route_provenance_matches(
                            browser.current_url, listing_type
                        ):
                            raise _RouteProvenanceError(
                                "Final browser URL does not prove the expected "
                                f"Taoyuan {listing_type} route"
                            )
                        extraction = extract_rendered_page(html, listing_type)
                        page = CapturedPage(
                            page_number=page_num,
                            url=browser.current_url,
                            html=html,
                            accepted_count=len(extraction.listings),
                            rejected_count=len(extraction.rejected),
                            representation=extraction.representation,
                            schema_version=extraction.schema_version,
                        )
                        writer.write_page(page_num, html)
                        writer.write_checkpoint(page_num)
                        batch.pages.append(page)

                        if page_num >= max_pages:
                            break

                        try:
                            next_btn = browser.find_element(
                                "css selector", "a.next, .page-next, [rel=next]"
                            )
                            if not next_btn.is_enabled() or not next_btn.is_displayed():
                                batch.reached_terminal_page = True
                                should_stop = True
                                break
                            old_ids = {
                                listing.source_listing_id
                                for listing in extraction.listings
                            }
                            next_btn.click()
                            WebDriverWait(browser, self._config.page_timeout_seconds).until(
                                lambda active_browser,
                                page_num=page_num,
                                old_ids=old_ids: _pagination_is_fresh(
                                    active_browser,
                                    listing_type,
                                    previous_page_number=page_num,
                                    previous_ids=old_ids,
                                )
                            )
                        except NoSuchElementException:
                            batch.reached_terminal_page = True
                            should_stop = True
                            break
                        except Exception as exc:
                            batch.errors.append(
                                CaptureError(
                                    page_number=page_num,
                                    code="navigation_failed",
                                    message=str(exc),
                                )
                            )
                            _write_diagnostic(writer, page_num, browser)
                            should_stop = True
                            break

                        delay = random.uniform(*self._config.delay_seconds)
                        time.sleep(delay)
                        break

                    except _RouteProvenanceError as exc:
                        batch.errors.append(
                            CaptureError(
                                page_number=page_num,
                                code="navigation_failed",
                                message=str(exc),
                            )
                        )
                        _write_diagnostic(writer, page_num, browser)
                        should_stop = True
                        break
                    except _VerificationRequired:
                        batch.errors.append(
                            CaptureError(
                                page_number=page_num,
                                code="verification_required",
                                message="591 verification page detected",
                            )
                        )
                        should_stop = True
                        break
                    except Exception as exc:
                        if attempt < self._config.max_retries:
                            wait = 2 ** attempt
                            time.sleep(wait)
                            continue
                        _write_diagnostic(writer, page_num, browser)
                        batch.errors.append(
                            CaptureError(page_number=page_num, code="page_failed",
                                         message=str(exc))
                        )
                        should_stop = True
                        break
        finally:
            if batch.errors:
                batch.reached_terminal_page = False
            try:
                writer.write_manifest(batch)
            finally:
                browser.quit()
        return batch


class _VerificationRequired(Exception):
    pass


class _RouteProvenanceError(Exception):
    pass


def _route_provenance_matches(url: str, listing_type: ListingType) -> bool:
    expected = ROUTE_PROVENANCE.get(listing_type)
    if expected is None:
        return False
    expected_host, region_parameter = expected
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    return query.get(region_parameter) == ["6"]


def _canonical_page_number(url: str) -> int | None:
    try:
        values = parse_qs(urlsplit(url).query, keep_blank_values=True).get("page")
    except ValueError:
        return None
    if values is None or len(values) != 1 or not values[0].isdigit():
        return None
    page_number = int(values[0])
    return page_number if page_number > 0 else None


def _pagination_is_fresh(
    browser: webdriver.Chrome,
    listing_type: ListingType,
    previous_page_number: int,
    previous_ids: set[str],
) -> bool:
    if not _route_provenance_matches(browser.current_url, listing_type):
        return False
    current_page_number = _canonical_page_number(browser.current_url)
    if (
        current_page_number is not None
        and current_page_number != previous_page_number
    ):
        return True
    try:
        extraction = extract_rendered_page(browser.page_source, listing_type)
    except (ListingSchemaError, ValueError):
        return False
    current_ids = {listing.source_listing_id for listing in extraction.listings}
    return bool(current_ids) and current_ids != previous_ids


def _capture_readiness(
    browser: webdriver.Chrome,
    card_selectors: tuple[str, ...],
    empty_selectors: tuple[str, ...],
) -> str | bool:
    html = browser.page_source
    if is_verification_page(html):
        return "verification"
    card_matches = [
        browser.find_elements("css selector", selector)
        for selector in card_selectors
    ]
    empty_matches = [
        browser.find_elements("css selector", selector)
        for selector in empty_selectors
    ]
    if any(card_matches):
        return "cards"
    if any(empty_matches):
        return "empty"
    return False


def is_verification_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript", "template"]):
        element.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = soup.body.get_text(" ", strip=True) if soup.body else ""
    lowered_title = title.lower()
    lowered_body = body.lower()
    title_terms = ("驗證", "captcha", "verify")
    body_phrases = (
        "人機驗證",
        "安全驗證",
        "完成驗證",
        "captcha",
        "verify you are human",
        "verification required",
    )
    return any(term in lowered_title for term in title_terms) or any(
        phrase in lowered_body for phrase in body_phrases
    )


def _diagnostic_html(browser: webdriver.Chrome) -> str:
    try:
        return browser.page_source
    except Exception:
        return ""


def _write_diagnostic(
    writer: RawBatchWriter, page_number: int, browser: webdriver.Chrome
) -> None:
    html = _diagnostic_html(browser)
    if html and not is_verification_page(html):
        writer.write_diagnostic(page_number, html)


def create_chrome(config: ChromeConfig) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if config.headless:
        options.add_argument("--headless=new")
    if config.binary:
        options.binary_location = config.binary
    if config.profile_dir:
        options.add_argument(f"--user-data-dir={config.profile_dir}")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(config.page_timeout_seconds)
    return driver
