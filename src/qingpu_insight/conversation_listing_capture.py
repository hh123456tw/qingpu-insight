"""Capture 591 detail pages with a visible Chrome browser."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from qingpu_insight.conversation_listing_parser import (
    ListingPageVerificationRequired,
    ParsedListingDetail,
    parse_listing_detail,
)
from qingpu_insight.conversation_urls import (
    Initial591Url,
    validate_final_591_url,
    validate_redirect_target,
)
from qingpu_insight.listing_capture import ChromeConfig, is_verification_page


@dataclass(frozen=True)
class CapturedListing:
    final_url: str
    detail: ParsedListingDetail


MAX_REDIRECTS = 3


class DetailPageBrowser:
    def __init__(
        self,
        driver_factory: Callable[[], Any],
        parser: Callable[..., ParsedListingDetail] = parse_listing_detail,
        clock: type[datetime] = datetime,
        config: ChromeConfig | None = None,
    ):
        self._driver_factory = driver_factory
        self._parser = parser
        self._clock = clock
        self._config = config or ChromeConfig()

    def _wait_for_page(self, driver: Any) -> None:
        start = self._clock.now()
        while True:
            elapsed = (self._clock.now() - start).total_seconds()
            if elapsed >= self._config.page_timeout_seconds:
                raise TimeoutError(
                    f"Page did not load content within {self._config.page_timeout_seconds}s"
                )
            html = driver.page_source
            if "application/ld+json" in html or is_verification_page(html):
                return
            time.sleep(0.5)

    def capture(self, initial_url: Initial591Url) -> CapturedListing:
        driver = self._driver_factory()
        try:
            driver.get(initial_url.request_url)
            self._wait_for_page(driver)

            last_url = initial_url.request_url
            for _ in range(MAX_REDIRECTS):
                current = driver.current_url
                if current == last_url:
                    break
                validate_redirect_target(current)
                last_url = current

            if driver.current_url != last_url:
                raise RuntimeError(
                    f"Exceeded maximum redirects ({MAX_REDIRECTS})"
                )

            html = driver.page_source

            if is_verification_page(html):
                raise ListingPageVerificationRequired(
                    "Verification page detected after navigation"
                )

            validated = validate_final_591_url(driver.current_url)

            detail = self._parser(
                html,
                canonical_url=validated.canonical_url,
                listing_type=validated.listing_type,
            )

            return CapturedListing(
                final_url=driver.current_url,
                detail=detail,
            )
        finally:
            driver.quit()
