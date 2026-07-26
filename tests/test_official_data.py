from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

from qingpu_insight.jobs import ACTIVE_STATUSES, JobRun, JobService, JobStatus
from qingpu_insight.official_data import (
    OfficialDataError,
    OfficialDataRequest,
    OfficialDataResult,
    OfficialDataUpdateService,
    replace_market_rows,
)


def market_frame(n: int) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append(
            {
                "transaction_key": f"TK{i}",
                "transaction_type": "resale",
                "record_id": f"R{i}",
                "station_code": "A18",
                "transaction_date": pd.Timestamp("2025-01-01"),
                "building_area_sqm": 100.0,
                "building_area_ping": 30.25,
                "unit_price_sqm_twd": 200000.0,
                "unit_price_per_ping_twd": 600000.0,
                "total_price_twd": 18000000,
                "building_type": "住宅大樓",
                "bedrooms": 3,
                "living_rooms": 2,
                "bathrooms": 2,
                "building_age_years": 5.0,
                "station_distance_m": 500.0,
                "longitude": 121.2,
                "latitude": 25.0,
                "match_quality": "exact",
                "source_file": "test.csv",
                "floor": "5層",
                "total_floors": "15",
                "parking_type": "坡道平面",
                "parking_area_sqm": 10.0,
                "parking_price_twd": 2000000,
                "analysis_eligible": True,
            }
        )
    return pd.DataFrame(rows)


class FailingConnection:
    def __init__(self, fail_on_batch: int):
        self.fail_on_batch = fail_on_batch
        self.commits = 0
        self.rollbacks = 0
        self._batch_count = 0

    def cursor(self):
        return _FailingCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class _FailingCursor:
    def __init__(self, connection: FailingConnection):
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        pass

    def executemany(self, sql, rows):
        self._connection._batch_count += 1
        if self._connection._batch_count >= self._connection.fail_on_batch:
            raise RuntimeError(
                f"batch {self._connection.fail_on_batch} failed"
            )

    def close(self):
        pass


class RecordingConnection:
    def __init__(self):
        self.operation_names: list[str] = []

    def cursor(self):
        return _RecordingCursor(self)

    def commit(self):
        self.operation_names.append("commit")

    def rollback(self):
        pass

    def close(self):
        pass


class _RecordingCursor:
    def __init__(self, connection: RecordingConnection):
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        sql_upper = sql.strip().upper()
        if sql_upper.startswith("DELETE"):
            self._connection.operation_names.append("delete_market_rows")
        elif "DATA_REFRESHES" in sql_upper:
            self._connection.operation_names.append("insert_refresh")

    def executemany(self, sql, rows):
        self._connection.operation_names.append("insert_market_rows")

    def close(self):
        pass





class InMemoryJobRepository:
    def __init__(self) -> None:
        self._runs: dict[str, JobRun] = {}

    def create_or_get(self, run: JobRun) -> tuple[JobRun, bool]:
        existing = self.find_active_by_key(run.idempotency_key)
        if existing is not None:
            return existing, False
        self._runs[run.run_id] = run
        return run, True

    def get(self, run_id: str) -> JobRun | None:
        return self._runs.get(run_id)

    def find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        for run in self._runs.values():
            if run.idempotency_key == idempotency_key and run.status in ACTIVE_STATUSES:
                return run
        return None

    def list_recent(self, limit: int = 20, job_type: str | None = None) -> list[JobRun]:
        all_runs = reversed(list(self._runs.values()))
        if job_type is not None:
            all_runs = (r for r in all_runs if r.job_type == job_type)
        return list(all_runs)[:limit]

    def list_active(self, job_type: str) -> list[JobRun]:
        return [
            r for r in self._runs.values()
            if r.job_type == job_type and r.status in ACTIVE_STATUSES
        ]

    def update_summary(
        self, run_id: str, expected_status: JobStatus, summary: dict[str, object],
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != expected_status:
            return False
        self._runs[run_id] = replace(run, summary=summary)
        return True

    def transition(
        self, run_id: str, current_status: JobStatus, target_status: JobStatus,
        *,
        output_version: str | None = None, summary: dict[str, object] | None = None,
        error_code: str | None = None, error_message: str | None = None,
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != current_status:
            return False
        now = datetime.now(UTC)
        started_at = run.started_at
        finished_at = run.finished_at
        if target_status == "running":
            started_at = started_at or now
        elif target_status in {"succeeded", "failed", "skipped"}:
            finished_at = finished_at or now
        self._runs[run_id] = replace(
            run,
            status=target_status,
            started_at=started_at,
            finished_at=finished_at,
            attempt=run.attempt + (
                1 if run.status == "retry_wait" and target_status == "running" else 0
            ),
            output_version=output_version if output_version is not None else run.output_version,
            summary=summary if summary is not None else run.summary,
            error_code=error_code if error_code is not None else run.error_code,
            error_message=error_message if error_message is not None else run.error_message,
        )
        return True


class RecordingOfficialRunner:
    def __init__(self, fail_at: str | None = None, existing_inputs: bool = False):
        self.calls: list[str] = []
        self._fail_at = fail_at
        self._existing_inputs = existing_inputs

    def acquire(self, start_season: str, end_season: str) -> None:
        self.calls.append(f"acquire:{start_season}:{end_season}")
        if self._fail_at == "acquire":
            raise OfficialDataError("feasibility_no_go", "acquisition failed")

    def analyse(self) -> str:
        self.calls.append("analyse")
        if self._fail_at == "analyse":
            raise OfficialDataError("feasibility_no_go", "analysis failed")
        return "GO"

    def build_market(self) -> OfficialDataResult:
        self.calls.append("build_market")
        if self._fail_at == "build_market":
            raise OfficialDataError("feasibility_no_go", "build market failed")
        return OfficialDataResult(
            version="v1", row_count=2, sha256="abc",
            minimum_date="2025-01-01", maximum_date="2025-01-31",
            quality_path="",
        )

    def publish_mysql(self, result: OfficialDataResult) -> OfficialDataResult:
        self.calls.append("publish_mysql")
        if self._fail_at == "publish_mysql":
            raise OfficialDataError("feasibility_no_go", "publish failed")
        return result

    def verify(self, result: OfficialDataResult) -> OfficialDataResult:
        self.calls.append("verify")
        if self._fail_at == "verify":
            raise OfficialDataError("feasibility_no_go", "verify failed")
        return result

    def verify_acquire_input(self) -> None:
        self.calls.append("verify_acquire_input")

    def verify_analyse_input(self) -> None:
        self.calls.append("verify_analyse_input")

    def verify_build_market_input(self) -> None:
        self.calls.append("verify_build_market_input")


@pytest.fixture
def running_official_service(recwarn: Any) -> Any:
    def _build(runner: RecordingOfficialRunner) -> tuple[Any, Any, Any]:
        jobs = JobService(InMemoryJobRepository())
        service = OfficialDataUpdateService(jobs, runner)
        request = OfficialDataRequest("110S3", "115S2")
        run = service.submit(request).run
        jobs.start(run.run_id)
        return service, jobs, run
    return _build


def test_official_service_runs_fixed_stages_and_succeeds(running_official_service: Any) -> None:
    runner = RecordingOfficialRunner()
    service, jobs, run = running_official_service(runner)
    request = OfficialDataRequest("110S3", "115S2")
    result = service.execute(run.run_id, request)
    assert runner.calls == [
        "acquire:110S3:115S2", "analyse", "build_market",
        "publish_mysql", "verify",
    ]
    assert jobs.get(run.run_id).status == "succeeded"
    assert jobs.get(run.run_id).output_version == result.version


def test_official_service_stops_before_publish_on_no_go(running_official_service: Any) -> None:
    runner = RecordingOfficialRunner(fail_at="analyse")
    service, jobs, run = running_official_service(runner)
    with pytest.raises(OfficialDataError) as caught:
        service.execute(run.run_id, OfficialDataRequest("110S3", "115S2"))
    assert caught.value.error_code == "feasibility_no_go"
    assert "publish_mysql" not in runner.calls
    assert jobs.get(run.run_id).status == "failed"


def test_official_service_can_resume_only_from_fixed_checkpoint(
    running_official_service: Any,
) -> None:
    runner = RecordingOfficialRunner(existing_inputs=True)
    service, jobs, run = running_official_service(runner)
    request = OfficialDataRequest(
        "110S3", "115S2", start_at="market_build",
    )
    service.execute(run.run_id, request)
    assert runner.calls == [
        "verify_analyse_input", "build_market", "publish_mysql", "verify",
    ]


def test_replace_market_rows_rolls_back_delete_and_insert_together():
    connection = FailingConnection(fail_on_batch=2)
    with pytest.raises(RuntimeError):
        replace_market_rows(connection, market_frame(1500), "v-test")
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_replace_market_rows_records_refresh_only_after_rows():
    connection = RecordingConnection()
    count = replace_market_rows(connection, market_frame(2), "v-test")
    assert count == 2
    assert connection.operation_names == [
        "delete_market_rows",
        "insert_market_rows",
        "insert_refresh",
        "commit",
    ]
