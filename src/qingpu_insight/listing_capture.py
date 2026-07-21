"""Selenium-based capture for 591 listing pages."""

import json
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from qingpu_insight.listing_591 import CARD_SELECTORS, extract_rendered_page
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
        timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
        self._batch_id = f"591-{listing_type}-{timestamp}"
        self._batch_dir = (
            base_dir / "data" / "raw" / "listings" / "591"
            / created_at.strftime("%Y-%m-%d") / self._batch_id
        )

    @property
    def batch_dir(self) -> Path:
        return self._batch_dir

    @property
    def batch_id(self) -> str:
        return self._batch_id

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

    @classmethod
    def from_checkpoint(cls, base_dir: Path, existing_batch_dir: Path) -> "RawBatchWriter":
        writer = cls.__new__(cls)
        writer._batch_dir = existing_batch_dir
        writer._batch_id = existing_batch_dir.name
        return writer

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
    def __init__(
        self,
        browser: webdriver.Chrome | None = None,
        writer: RawBatchWriter | None = None,
        config: ChromeConfig | None = None,
        resume_batch_id: str | None = None,
    ):
        self._config = config or ChromeConfig()
        self._browser = browser
        self._writer = writer
        self._resume_batch_id = resume_batch_id

    def capture(self, listing_type: ListingType, max_pages: int = 10) -> CaptureBatch:
        browser = self._browser or create_chrome(self._config)
        writer = self._writer

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
        writer = writer or RawBatchWriter(Path.cwd(), listing_type)

        start_page = 1
        if self._resume_batch_id and writer:
            checkpoint_path = writer.batch_dir / "checkpoint.json"
            if checkpoint_path.exists():
                try:
                    data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                    start_page = data.get("last_page", 0) + 1
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

        batch = CaptureBatch(
            batch_id=writer.batch_id,
            source="591",
            listing_type=listing_type,
            started_at=datetime.now(UTC),
        )

        try:
            browser.get(url)
        except Exception as exc:
            batch.errors.append(
                CaptureError(page_number=1, code="navigation_failed", message=str(exc))
            )
            writer.write_manifest(batch)
            browser.quit()
            return batch

        should_stop = False
        try:
            for page_num in range(start_page, max_pages + 1):
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
                            raise _VerificationRequired(html)
                        if ready_state == "empty":
                            if batch.pages:
                                batch.reached_terminal_page = True
                            should_stop = True
                            break

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
                        batch.pages.append(page)
                        writer.write_page(page_num, html)
                        writer.write_checkpoint(page_num)

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
                            old_url = browser.current_url
                            next_btn.click()
                            WebDriverWait(browser, self._config.page_timeout_seconds).until(
                                EC.url_changes(old_url)
                            )
                        except Exception as exc:
                            batch.errors.append(
                                CaptureError(
                                    page_number=page_num,
                                    code="navigation_failed",
                                    message=str(exc),
                                )
                            )
                            should_stop = True
                            break

                        delay = random.uniform(*self._config.delay_seconds)
                        time.sleep(delay)
                        break

                    except _VerificationRequired as exc:
                        writer.write_diagnostic(page_num, exc.html)
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
                        writer.write_diagnostic(page_num, _diagnostic_html(browser))
                        batch.errors.append(
                            CaptureError(page_number=page_num, code="page_failed",
                                         message=str(exc))
                        )
                        should_stop = True
                        break
        finally:
            if batch.errors:
                batch.reached_terminal_page = False
            writer.write_manifest(batch)
            browser.quit()
        return batch


class _VerificationRequired(Exception):
    def __init__(self, html: str):
        self.html = html


def _capture_readiness(
    browser: webdriver.Chrome,
    card_selectors: tuple[str, ...],
    empty_selectors: tuple[str, ...],
) -> str | bool:
    html = browser.page_source
    if _likely_verification(html):
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


def _likely_verification(html: str) -> bool:
    lowered = html.lower()
    return any(token in lowered for token in ("驗證", "captcha", "verify"))


def _diagnostic_html(browser: webdriver.Chrome) -> str:
    try:
        return browser.page_source
    except Exception:
        return ""


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
