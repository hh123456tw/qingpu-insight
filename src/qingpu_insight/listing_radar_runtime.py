"""Wire the listing radar to real services: one visible Chrome, the official model, files.

``run_listing_radar`` is the single entry point used by ``qingpu-data listing-radar`` and
by the admin job (:class:`ListingRadarJobService`). Everything it touches can be injected
so tests never open Chrome or reach 591.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qingpu_insight.conversation_listing_parser import (
    ListingDelisted,
    ListingDetailParseError,
    ListingPageVerificationRequired,
)
from qingpu_insight.conversation_urls import Unsupported591Url
from qingpu_insight.jobs import JobService, JobSubmission
from qingpu_insight.listing_radar import (
    DEFAULT_DELAY_SECONDS,
    RADAR_JOB_TYPE,
    CaptureFn,
    ListingRadarRequest,
    ListingRadarRunner,
    ListingRadarStore,
    ProgressFn,
    RadarRunResult,
    ValuateFn,
    load_candidate_frame,
    select_candidates,
    write_radar_report,
)

RADAR_STORE_DIR = Path("data") / "processed" / "listing_radar"
RADAR_REPORT_DIR = Path("outputs") / "listing-radar"
MIN_POLITE_DELAY_SECONDS = 3.0


def radar_store(root: Path) -> ListingRadarStore:
    return ListingRadarStore(root / RADAR_STORE_DIR)


class _KeepAliveDriver:
    """Lets DetailPageBrowser 'quit' after each page while the real Chrome stays open."""

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    def __getattr__(self, name: str) -> Any:
        return getattr(self._driver, name)

    def quit(self) -> None:
        return None


class SharedChromeSession:
    """One Chrome window for the whole radar run; reopened only after it breaks."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._driver: Any = None
        self.opened = 0

    def driver(self) -> _KeepAliveDriver:
        if self._driver is None:
            self._driver = self._factory()
            self.opened += 1
        return _KeepAliveDriver(self._driver)

    def reset(self) -> None:
        driver, self._driver = self._driver, None
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    close = reset


def shared_session_capture(browser_factory: Callable[[Callable[[], Any]], Any],
                           session: SharedChromeSession) -> CaptureFn:
    """Capture through DetailPageBrowser, dropping the Chrome session after odd failures."""
    browser = browser_factory(session.driver)

    def capture(initial):
        try:
            return browser.capture(initial)
        except (
            ListingPageVerificationRequired,
            ListingDelisted,
            ListingDetailParseError,
            Unsupported591Url,
        ):
            raise
        except Exception:
            session.reset()
            raise

    return capture


@dataclass(frozen=True)
class RadarRunOptions:
    max_listings: int = 60
    refresh_hours: float = 24.0
    delay_seconds: tuple[float, float] = DEFAULT_DELAY_SECONDS
    page_timeout_seconds: int = 30
    profile_dir: str | None = None
    offline: bool = False

    def __post_init__(self) -> None:
        ListingRadarRequest(max_listings=self.max_listings, refresh_hours=self.refresh_hours)
        low, high = self.delay_seconds
        if low < MIN_POLITE_DELAY_SECONDS or high < low:
            raise ValueError(f"delay must be at least {MIN_POLITE_DELAY_SECONDS:g}s and ordered")
        if not 5 <= self.page_timeout_seconds <= 300:
            raise ValueError("page timeout must be between 5 and 300 seconds")


def _default_valuate(root: Path) -> ValuateFn:
    from qingpu_insight.conversation_valuation import valuate_listing_with_context
    from qingpu_insight.market_repository import repository_from_env
    from qingpu_insight.market_snapshot import ModelFrameCache
    from qingpu_insight.valuation import ModelRegistry

    data_source = repository_from_env(root)
    registry = ModelRegistry(root / "artifacts")
    snapshots = ModelFrameCache(data_source)
    return lambda payload: valuate_listing_with_context(
        data_source, registry, payload, snapshots=snapshots
    )


def _default_repository(root: Path) -> Any:
    from qingpu_insight.cli import create_listing_repository

    try:
        return create_listing_repository(root)
    except Exception:
        return None


def _chrome_session(options: RadarRunOptions) -> tuple[SharedChromeSession, CaptureFn]:
    from qingpu_insight.conversation_listing_capture import DetailPageBrowser
    from qingpu_insight.listing_capture import ChromeConfig, create_chrome

    config = ChromeConfig(
        headless=False,
        profile_dir=options.profile_dir,
        page_timeout_seconds=options.page_timeout_seconds,
        delay_seconds=options.delay_seconds,
    )
    session = SharedChromeSession(lambda: create_chrome(config))
    capture = shared_session_capture(
        lambda driver_factory: DetailPageBrowser(driver_factory=driver_factory, config=config),
        session,
    )
    return session, capture


def run_listing_radar(
    root: Path,
    options: RadarRunOptions,
    *,
    progress: ProgressFn | None = None,
    repository: Any = None,
    valuate: ValuateFn | None = None,
    capture: CaptureFn | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] | None = None,
) -> tuple[RadarRunResult, Path, Path]:
    """Select, capture, value, rank, persist and report one radar batch."""
    from qingpu_insight.config import get_settings

    settings = get_settings(root)
    repo = repository if repository is not None else _default_repository(root)
    frame, source = load_candidate_frame(
        repo, root / "data" / "processed" / "listing_snapshots.parquet"
    )
    candidates = select_candidates(frame, options.max_listings)
    if progress is not None:
        progress({"stage": "selected", "processed": 0, "total": len(candidates)})

    session: SharedChromeSession | None = None
    if capture is None and not options.offline and candidates:
        session, capture = _chrome_session(options)
    runner_kwargs: dict[str, Any] = {}
    if sleep is not None:
        runner_kwargs["sleep"] = sleep
    try:
        runner = ListingRadarRunner(
            store=radar_store(root),
            capture=None if options.offline else capture,
            valuate=valuate or _default_valuate(root),
            clock=clock,
            delay_seconds=options.delay_seconds,
            refresh_hours=options.refresh_hours,
            radius_m=settings.radius_m,
            **runner_kwargs,
        )
        result = runner.run(
            candidates, max_listings=options.max_listings, source=source, progress=progress
        )
    finally:
        if session is not None:
            session.close()
    json_path, md_path = write_radar_report(root / RADAR_REPORT_DIR, result)
    return result, json_path, md_path


# --------------------------------------------------------------------------- admin job


RadarRunFn = Callable[..., tuple[RadarRunResult, Path, Path]]

_FAILURE_MESSAGES = {
    "stopped_verification": (
        "verification_required",
        "591 顯示驗證頁，雷達已停止；已完成的物件已保存。請在 Chrome 手動完成驗證後再執行。",
    ),
    "stopped_failures": (
        "capture_failed",
        "連續多個物件頁面無法載入，雷達已停止；已完成的物件已保存。",
    ),
}


def radar_job_summary(result: RadarRunResult) -> dict[str, object]:
    return {
        "radar_batch_id": result.radar_batch_id,
        "radar_status": result.status,
        **{key: int(value) for key, value in result.counts.items()},
    }


class ListingRadarJobService:
    """Run the radar as a local admin job with progress in the job summary."""

    def __init__(self, job_service: JobService, run: RadarRunFn) -> None:
        self._job_service = job_service
        self._run = run

    def submit(self, request: ListingRadarRequest) -> JobSubmission:
        return self._job_service.create(
            RADAR_JOB_TYPE,
            f"{RADAR_JOB_TYPE}:active",
            request.trigger,
            summary={
                "stage": "queued",
                "max_listings": request.max_listings,
                "refresh_hours": request.refresh_hours,
            },
        )

    def execute(self, run_id: str, request: ListingRadarRequest) -> None:
        def progress(summary: dict[str, Any]) -> None:
            try:
                self._job_service.progress(run_id, summary)
            except Exception:
                pass

        options = RadarRunOptions(
            max_listings=request.max_listings, refresh_hours=request.refresh_hours
        )
        result, _, _ = self._run(options, progress=progress)
        summary = radar_job_summary(result)
        failure = _FAILURE_MESSAGES.get(result.status)
        if failure is None:
            self._job_service.succeed(run_id, output_version=result.radar_batch_id,
                                      summary=summary)
            return
        progress({"stage": "stopped", **summary})
        self._job_service.fail(run_id, *failure)
