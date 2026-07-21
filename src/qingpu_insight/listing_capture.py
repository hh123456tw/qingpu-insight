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

from qingpu_insight.listing_591 import CARD_SELECTORS
from qingpu_insight.listing_sources import (
    CaptureBatch,
    CapturedPage,
    CaptureError,
    ListingType,
)

ROUTES: dict[str, str] = {
    "sale": "https://sale.591.com.tw/?shType=list&regionid=6",
    "newhouse": "https://newhouse.591.com.tw/home/housing/search?regionid=6",
    "rental": "https://rent.591.com.tw/list?region=6",
}


@dataclass(frozen=True)
class ChromeConfig:
    binary: str | None = None
    profile_dir: str | None = None
    headless: bool = True
    page_timeout_seconds: int = 30
    delay_seconds: tuple[float, float] = (2.0, 5.0)
    max_retries: int = 3


class FakeBrowser:
    def __init__(self, pages: list[str] | None = None, fail_on_next: bool = False):
        self.pages = pages or []
        self._page_index = 0
        self._fail_on_next = fail_on_next
        self.current_url = ""
        self.page_source = ""
        self.calls: list[str] = []

    def get(self, url: str) -> None:
        self.calls.append(f"get:{url}")
        self.current_url = url
        if self._fail_on_next:
            self._fail_on_next = False
            raise Exception("navigation_failed")
        if self._page_index < len(self.pages):
            self.page_source = self.pages[self._page_index]
            self._page_index += 1
        else:
            self.page_source = ""

    def find_element(self, by: str, value: str | None = None, selector: str | None = None):
        self.calls.append(f"find_element:{value or selector}")
        if isinstance(value, str) and "next" in value.lower():
            raise Exception("no such element")
        return _FakeElement()

    def find_elements(self, by: str, value: str | None = None, selector: str | None = None):
        self.calls.append(f"find_elements:{value or selector}")
        return []

    def quit(self) -> None:
        self.calls.append("quit")


class _FakeElement:
    def is_enabled(self) -> bool:
        return False

    def is_displayed(self) -> bool:
        return False

    def click(self) -> None:
        pass

    def text(self) -> str:
        return ""

    @property
    def tag_name(self) -> str:
        return "div"


class RawBatchWriter:
    def __init__(self, base_dir: Path):
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        self._batch_dir = base_dir / "data" / "raw" / "listings" / "591" / date_str / f"591-{ts}"

    @property
    def batch_dir(self) -> Path:
        return self._batch_dir

    def write_page(self, page_number: int, html: str) -> Path:
        self._batch_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._batch_dir / f"page-{page_number:04d}.html.tmp"
        final = self._batch_dir / f"page-{page_number:04d}.html"
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
            "pages": [{"page_number": p.page_number, "url": p.url} for p in batch.pages],
            "errors": [{"page_number": e.page_number, "code": e.code,
                         "message": e.message} for e in batch.errors],
            "is_complete": batch.is_complete,
        }
        tmp = self._batch_dir / "manifest.json.tmp"
        final = self._batch_dir / "manifest.json"
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(final)


class Selenium591Source:
    def __init__(
        self,
        browser: webdriver.Chrome | FakeBrowser | None = None,
        writer: RawBatchWriter | None = None,
        config: ChromeConfig | None = None,
    ):
        self._config = config or ChromeConfig()
        self._browser = browser
        self._writer = writer

    def capture(self, listing_type: ListingType, max_pages: int = 10) -> CaptureBatch:
        browser = self._browser or create_chrome(self._config)
        writer = self._writer or RawBatchWriter(Path.cwd())

        batch_id = f"591-{listing_type}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        batch = CaptureBatch(
            batch_id=batch_id,
            source="591",
            listing_type=listing_type,
            started_at=datetime.now(UTC),
        )

        url = ROUTES.get(listing_type)
        if not url:
            batch.errors.append(
                CaptureError(page_number=0, code="invalid_type",
                             message=f"Unknown listing type: {listing_type}")
            )
            return batch

        try:
            browser.get(url)
        except Exception as exc:
            batch.errors.append(
                CaptureError(page_number=1, code="navigation_failed", message=str(exc))
            )
            writer.write_manifest(batch)
            return batch

        try:
            for page_num in range(1, max_pages + 1):
                for attempt in range(self._config.max_retries + 1):
                    try:
                        selectors = CARD_SELECTORS.get(listing_type, ())
                        matched = False
                        for sel in selectors:
                            try:
                                WebDriverWait(browser, self._config.page_timeout_seconds).until(
                                    EC.presence_of_element_located(("css selector", sel))
                                )
                                matched = True
                                break
                            except Exception:
                                continue

                        if not matched:
                            batch.reached_terminal_page = True
                            break

                        html = browser.page_source
                        page = CapturedPage(
                            page_number=page_num, url=browser.current_url, html=html
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
                                break
                            next_btn.click()
                        except Exception:
                            batch.reached_terminal_page = True
                            break

                        delay = random.uniform(*self._config.delay_seconds)
                        time.sleep(delay)
                        break

                    except Exception as exc:
                        if attempt < self._config.max_retries:
                            wait = 2 ** attempt
                            time.sleep(wait)
                            continue
                        batch.errors.append(
                            CaptureError(page_number=page_num, code="page_failed",
                                         message=str(exc))
                        )
        finally:
            writer.write_manifest(batch)
            browser.quit()
        return batch


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
