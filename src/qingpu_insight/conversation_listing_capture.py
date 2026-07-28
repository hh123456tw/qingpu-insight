"""Capture 591 detail pages with a visible Chrome browser."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import requests

from qingpu_insight.conversation_listing_parser import (
    ListingPageVerificationRequired,
    ParsedListingDetail,
    has_listing_detail_content,
    parse_listing_detail,
)
from qingpu_insight.conversation_urls import (
    Initial591Url,
    Validated591DetailUrl,
    validate_final_591_url,
    validate_redirect_target,
)
from qingpu_insight.listing_capture import ChromeConfig, is_verification_page


@dataclass(frozen=True)
class CapturedListing:
    final_url: str
    detail: ParsedListingDetail


MAX_REDIRECTS = 3


class Safe591RedirectResolver:
    """Resolve 591 URLs without ever following an unvalidated Location header."""

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        self._session = session or requests.Session()
        if session is None:
            self._session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/126 Safari/537.36"
                )
            })
        self._timeout_seconds = timeout_seconds

    def resolve(self, initial_url: Initial591Url) -> Validated591DetailUrl:
        current = initial_url.request_url
        for redirect_count in range(MAX_REDIRECTS + 1):
            response = self._session.get(
                current,
                allow_redirects=False,
                stream=True,
                timeout=self._timeout_seconds,
            )
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    if redirect_count >= MAX_REDIRECTS:
                        raise RuntimeError(
                            f"Exceeded maximum redirects ({MAX_REDIRECTS})"
                        )
                    location = response.headers.get("Location")
                    if not location:
                        raise RuntimeError("591 redirect did not include Location")
                    target = urljoin(current, location)
                    validate_redirect_target(target)
                    current = target
                    continue
                # A direct detail page may return a verification/403 response
                # to a cookie-free preflight. With no Location header there is
                # no redirect target to approve; Chrome handles the page body.
                return validate_final_591_url(current)
            finally:
                response.close()
        raise RuntimeError(f"Exceeded maximum redirects ({MAX_REDIRECTS})")


class DetailPageBrowser:
    def __init__(
        self,
        driver_factory: Callable[[], Any],
        parser: Callable[..., ParsedListingDetail] = parse_listing_detail,
        clock: type[datetime] = datetime,
        config: ChromeConfig | None = None,
        redirect_resolver: (
            Callable[[Initial591Url], Validated591DetailUrl] | None
        ) = None,
    ):
        self._driver_factory = driver_factory
        self._parser = parser
        self._clock = clock
        self._config = config or ChromeConfig()
        self._redirect_resolver = (
            redirect_resolver or Safe591RedirectResolver().resolve
        )

    def _wait_for_page(
        self,
        driver: Any,
        *,
        listing_type: str,
    ) -> None:
        start = self._clock.now()
        content_ready_at: datetime | None = None
        while True:
            now = self._clock.now()
            elapsed = (now - start).total_seconds()
            if elapsed >= self._config.page_timeout_seconds:
                raise TimeoutError(
                    f"Page did not load content within {self._config.page_timeout_seconds}s"
                )
            html = driver.page_source
            if is_verification_page(html):
                return
            if has_listing_detail_content(html, listing_type=listing_type):
                if listing_type != "sale":
                    return
                if (
                    "detail-house-key" in html
                    and "detail-house-value" in html
                ):
                    return
                if content_ready_at is None:
                    content_ready_at = now
                elif (now - content_ready_at).total_seconds() >= 3:
                    return
            time.sleep(0.5)

    def capture(self, initial_url: Initial591Url) -> CapturedListing:
        validated = self._redirect_resolver(initial_url)
        driver = self._driver_factory()
        try:
            # Chrome only receives the already validated final detail URL.
            driver.get(validated.canonical_url)
            self._wait_for_page(
                driver,
                listing_type=validated.listing_type,
            )

            html = driver.page_source

            if is_verification_page(html):
                raise ListingPageVerificationRequired(
                    "Verification page detected after navigation"
                )

            final_url = validate_final_591_url(driver.current_url)
            if final_url.canonical_url != validated.canonical_url:
                raise RuntimeError("591 detail URL changed after safe preflight")

            detail = self._parser(
                html,
                canonical_url=final_url.canonical_url,
                listing_type=final_url.listing_type,
            )

            return CapturedListing(
                final_url=final_url.canonical_url,
                detail=detail,
            )
        finally:
            driver.quit()
