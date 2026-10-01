"""Radar wiring: one shared Chrome, end-to-end run with fakes, the admin job and the CLI."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from qingpu_insight import cli
from qingpu_insight.conversation_listing_capture import DetailPageBrowser
from qingpu_insight.conversation_listing_parser import ListingPageVerificationRequired
from qingpu_insight.conversation_urls import Initial591Url, validate_final_591_url
from qingpu_insight.jobs import ACTIVE_STATUSES, JobRun, JobService
from qingpu_insight.listing_radar import ListingRadarRequest
from qingpu_insight.listing_radar_runtime import (
    ListingRadarJobService,
    RadarRunOptions,
    SharedChromeSession,
    radar_store,
    run_listing_radar,
    shared_session_capture,
)
from tests.fake_browser import FakeBrowser
from tests.test_listing_radar import _captured, _detail_html, _fake_valuate, _FakeCapture, _url


class _Repo:
    def __init__(self, ids: list[str]) -> None:
        self.frame = pd.DataFrame(
            [
                {
                    "listing_type": "sale",
                    "source_listing_id": listing_id,
                    "source_url": _url(listing_id),
                    "snapshot_at": f"2026-09-0{index + 1}T00:00:00Z",
                    "active": True,
                    "station_code": "A18",
                }
                for index, listing_id in enumerate(ids)
            ]
        )

    def load_current(self, listing_type=None):
        return self.frame


# --------------------------------------------------------------------------- Chrome reuse


def test_shared_chrome_session_opens_one_browser_for_many_pages() -> None:
    driver = FakeBrowser(pages=[_detail_html("1"), _detail_html("2")])
    created: list[FakeBrowser] = []

    def factory():
        created.append(driver)
        return driver

    session = SharedChromeSession(factory)
    capture = shared_session_capture(
        lambda driver_factory: DetailPageBrowser(
            driver_factory=driver_factory,
            redirect_resolver=lambda initial: validate_final_591_url(initial.request_url),
        ),
        session,
    )
    first = capture(Initial591Url(request_url=_url("1"), kind="direct"))
    second = capture(Initial591Url(request_url=_url("2"), kind="direct"))

    assert first.detail.source_listing_id == "1"
    assert second.detail.source_listing_id == "2"
    assert len(created) == 1
    assert "quit" not in driver.calls
    session.close()
    assert driver.calls.count("quit") == 1


def test_shared_session_is_dropped_after_an_unexpected_failure() -> None:
    drivers: list[FakeBrowser] = []

    def factory():
        drivers.append(FakeBrowser(pages=[_detail_html("1")]))
        return drivers[-1]

    class _Broken:
        def __init__(self, driver_factory):
            self._driver_factory = driver_factory

        def capture(self, initial):
            self._driver_factory()
            raise TimeoutError("page never loaded")

    session = SharedChromeSession(factory)
    capture = shared_session_capture(_Broken, session)
    with pytest.raises(TimeoutError):
        capture(Initial591Url(request_url=_url("1"), kind="direct"))
    assert drivers[0].calls == ["quit"]
    session.driver()
    assert session.opened == 2


def test_verification_does_not_drop_the_session() -> None:
    session = SharedChromeSession(lambda: FakeBrowser())

    class _Verify:
        def __init__(self, driver_factory):
            self._driver_factory = driver_factory

        def capture(self, initial):
            self._driver_factory()
            raise ListingPageVerificationRequired("captcha")

    capture = shared_session_capture(_Verify, session)
    with pytest.raises(ListingPageVerificationRequired):
        capture(Initial591Url(request_url=_url("1"), kind="direct"))
    assert session.opened == 1


def test_options_refuse_impolite_delays() -> None:
    with pytest.raises(ValueError):
        RadarRunOptions(delay_seconds=(1.0, 2.0))
    with pytest.raises(ValueError):
        RadarRunOptions(max_listings=0)


# --------------------------------------------------------------------------- end to end


def test_run_listing_radar_end_to_end_with_fakes(tmp_path: Path) -> None:
    capture = _FakeCapture({"1": _captured("1", price_wan="1,280"), "2": _captured("2")})
    progress: list[dict] = []

    result, json_path, md_path = run_listing_radar(
        tmp_path,
        RadarRunOptions(max_listings=10),
        progress=progress.append,
        repository=_Repo(["1", "2"]),
        valuate=_fake_valuate(),
        capture=capture,
        sleep=lambda _: None,
    )

    assert result.status == "completed"
    assert result.counts["valued"] == 2
    assert json_path.parent == tmp_path / "outputs" / "listing-radar"
    assert md_path.exists()
    meta, frame = radar_store(tmp_path).load_latest()
    assert meta["radar_batch_id"] == result.radar_batch_id
    assert meta["source"] == "listing_current"
    assert set(frame["source_listing_id"]) == {"1", "2"}
    assert progress[0]["stage"] == "selected"
    assert progress[-1]["processed"] == 2


def test_offline_run_never_builds_chrome(tmp_path: Path, monkeypatch) -> None:
    from qingpu_insight import listing_radar_runtime

    monkeypatch.setattr(
        listing_radar_runtime,
        "_chrome_session",
        lambda options: pytest.fail("offline radar must not open Chrome"),
    )
    result, _, _ = run_listing_radar(
        tmp_path,
        RadarRunOptions(offline=True),
        repository=_Repo(["1"]),
        valuate=_fake_valuate(),
    )
    assert result.counts["not_cached"] == 1


# --------------------------------------------------------------------------- admin job


class _MemoryJobs:
    def __init__(self) -> None:
        self.runs: dict[str, JobRun] = {}

    def create_or_get(self, run):
        for current in self.runs.values():
            if current.idempotency_key == run.idempotency_key and current.status in ACTIVE_STATUSES:
                return current, False
        self.runs[run.run_id] = run
        return run, True

    def get(self, run_id):
        return self.runs.get(run_id)

    def update_summary(self, run_id, expected_status, summary):
        run = self.runs[run_id]
        if run.status != expected_status:
            return False
        self.runs[run_id] = replace(run, summary=summary)
        return True

    def transition(self, run_id, current_status, target_status, *, output_version=None,
                   summary=None, error_code=None, error_message=None):
        run = self.runs[run_id]
        if run.status != current_status:
            return False
        self.runs[run_id] = replace(
            run,
            status=target_status,
            output_version=output_version or run.output_version,
            summary=summary if summary is not None else run.summary,
            error_code=error_code,
            error_message=error_message,
            finished_at=datetime.now(UTC),
        )
        return True


def _job_service(tmp_path: Path, outcomes: dict) -> tuple[JobService, ListingRadarJobService]:
    jobs = JobService(_MemoryJobs())

    def run(options, progress=None):
        return run_listing_radar(
            tmp_path,
            options,
            progress=progress,
            repository=_Repo(list(outcomes)),
            valuate=_fake_valuate(),
            capture=_FakeCapture(outcomes),
            sleep=lambda _: None,
        )

    return jobs, ListingRadarJobService(jobs, run)


def test_radar_job_reports_progress_and_succeeds(tmp_path: Path) -> None:
    jobs, service = _job_service(tmp_path, {"1": _captured("1")})
    submission = service.submit(ListingRadarRequest(max_listings=5, trigger="web"))
    assert submission.created
    assert submission.run.job_type == "listing_radar"
    assert service.submit(ListingRadarRequest()).created is False  # one radar at a time

    jobs.start(submission.run.run_id)
    service.execute(submission.run.run_id, ListingRadarRequest(max_listings=5))

    run = jobs.get(submission.run.run_id)
    assert run.status == "succeeded"
    assert run.output_version.startswith("radar-")
    assert run.summary["valued"] == 1


def test_radar_job_fails_cleanly_on_verification(tmp_path: Path) -> None:
    jobs, service = _job_service(
        # Newest first: listing 2 is captured, then listing 1 hits the verification page.
        tmp_path, {"1": ListingPageVerificationRequired("captcha"), "2": _captured("2")}
    )
    submission = service.submit(ListingRadarRequest())
    jobs.start(submission.run.run_id)
    service.execute(submission.run.run_id, ListingRadarRequest())

    run = jobs.get(submission.run.run_id)
    assert run.status == "failed"
    assert run.error_code == "verification_required"
    assert run.summary["valued"] == 1


# --------------------------------------------------------------------------- CLI


def test_cli_listing_radar_runs_one_command(tmp_path: Path, monkeypatch, capsys) -> None:
    calls: list[RadarRunOptions] = []

    def fake_run(root, options, **kwargs):
        calls.append(options)
        return run_listing_radar(
            root,
            options,
            repository=_Repo(["1"]),
            valuate=_fake_valuate(),
            capture=_FakeCapture({"1": _captured("1", price_wan="1,280")}),
            sleep=lambda _: None,
        )

    monkeypatch.setattr(cli, "run_listing_radar", fake_run)
    monkeypatch.chdir(tmp_path)

    code = cli.main(["listing-radar", "--max-listings", "5", "--refresh-hours", "12"])

    assert code == 0
    assert calls[0].max_listings == 5
    assert calls[0].refresh_hours == 12
    assert calls[0].delay_seconds == (3.0, 6.0)
    out = capsys.readouterr().out
    assert "radar-" in out
    report = json.loads(next((tmp_path / "outputs" / "listing-radar").glob("*.json")).read_text(
        encoding="utf-8"
    ))
    assert report["counts"]["valued"] == 1


def test_cli_listing_radar_exit_code_signals_verification(tmp_path, monkeypatch) -> None:
    def fake_run(root, options, **kwargs):
        return run_listing_radar(
            root,
            options,
            repository=_Repo(["1"]),
            valuate=_fake_valuate(),
            capture=_FakeCapture({"1": ListingPageVerificationRequired("captcha")}),
            sleep=lambda _: None,
        )

    monkeypatch.setattr(cli, "run_listing_radar", fake_run)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["listing-radar"]) == 2


def test_cli_rejects_impolite_delay(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["listing-radar", "--delay-min", "0.5", "--delay-max", "1"]) == 1
