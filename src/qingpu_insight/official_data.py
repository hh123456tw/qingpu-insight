from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol

import pandas as pd

from qingpu_insight.addresses import build_doorplate_frame, match_addresses
from qingpu_insight.archives import extract_taoyuan_tables
from qingpu_insight.config import get_settings
from qingpu_insight.downloads import (
    download_current_table,
    download_file,
    download_season,
    write_manifest,
)
from qingpu_insight.feasibility import evaluate_feasibility
from qingpu_insight.geo import assign_life_circle, station_points
from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import JobService, JobSubmission
from qingpu_insight.market_cleaning import build_market_dataset
from qingpu_insight.moi import read_moi_csv
from qingpu_insight.mysql_loader import _insert_market_rows
from qingpu_insight.reporting import write_report

SOURCES = (
    "https://data.gov.tw/dataset/77051",
    "https://data.gov.tw/dataset/157689",
    "https://www.tymetro.com.tw/tymetro-new/tw/_pages/travel-guide/A17",
    "https://www.tymetro.com.tw/tymetro-new/tw/_pages/travel-guide/A18",
    "https://www.tymetro.com.tw/tymetro-new/tw/_pages/travel-guide/A19",
)

_REFRESH_SQL = """INSERT INTO data_refreshes
(dataset_version, source_max_date, row_count, quality_report)
VALUES (%s, %s, %s, %s)"""


class OfficialDataError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class OfficialDataRequest:
    start_season: str
    end_season: str
    start_at: Literal["acquire", "analyse", "market_build", "mysql_publish"] = "acquire"
    trigger: str = "web"


@dataclass(frozen=True)
class OfficialDataResult:
    version: str
    row_count: int
    sha256: str
    minimum_date: str
    maximum_date: str
    quality_path: str


def replace_market_rows(
    connection: Any, frame: pd.DataFrame, version: str, batch_size: int = 1000
) -> int:
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM market_transactions")
        total = _insert_market_rows(connection, frame, batch_size)
        max_date = frame["transaction_date"].max()
        with connection.cursor() as cursor:
            cursor.execute(
                _REFRESH_SQL,
                (version, max_date, total, "{}"),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return total


def _transaction_files(raw_dir: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted(raw_dir.glob("seasons/*/h_lvr_land_[ab].csv")):
        files.append((path, "resale" if path.name.endswith("_a.csv") else "presale"))
    for path in sorted((raw_dir / "current").glob("h_lvr_land_[ab].csv")):
        files.append((path, "resale" if path.name.endswith("_a.csv") else "presale"))
    return files


def acquire_official(root: Path, start: str, end: str) -> None:
    settings = get_settings(root)
    manifest = settings.raw_dir / "manifest.json"
    errors: list[str] = []
    for season in _iter_seasons(start, end):
        archive = settings.raw_dir / "seasons" / f"{season}.zip"
        try:
            record = download_season(settings.sources.moi_base_url, season, archive)
            write_manifest([record], manifest)
            extract_taoyuan_tables(archive, settings.raw_dir / "seasons" / season)
        except Exception as error:
            errors.append(f"{season}: {error}")
    current = settings.raw_dir / "current"
    for name in ("h_lvr_land_a.csv", "h_lvr_land_b.csv"):
        try:
            record = download_current_table(settings.sources.moi_base_url, name, current / name)
            write_manifest([record], manifest)
        except Exception as error:
            errors.append(f"{name}: {error}")
    try:
        record = download_file(settings.sources.doorplate_url, settings.raw_dir / "doorplates.csv")
        write_manifest([record], manifest)
    except Exception as error:
        errors.append(f"doorplates.csv: {error}")
    if errors:
        raise RuntimeError("acquisition incomplete: " + "; ".join(errors))


def analyse_official(root: Path) -> str:
    settings = get_settings(root)
    files = _transaction_files(settings.raw_dir)
    if not files:
        raise FileNotFoundError("no MOI transaction CSV files found; run acquire first")
    frames = [read_moi_csv(path, kind) for path, kind in files]
    transactions = pd.concat(frames, ignore_index=True)
    business_columns = [column for column in transactions.columns if column != "source_file"]
    transactions = transactions.drop_duplicates(subset=business_columns)
    doorplates = build_doorplate_frame(settings.raw_dir / "doorplates.csv")
    located = match_addresses(transactions, doorplates)
    stations = station_points(settings.stations, doorplates)
    assigned = assign_life_circle(located, stations, settings.radius_m)
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    assigned.to_parquet(settings.processed_dir / "transactions.parquet", index=False)
    result = evaluate_feasibility(assigned, settings.thresholds)
    write_report(result, settings.report_dir, SOURCES)
    if result.decision != "GO":
        raise OfficialDataError("feasibility_no_go", "官方資料未通過可行性門檻。")
    return result.decision


def build_official_market(root: Path) -> OfficialDataResult:
    settings = get_settings(root)
    frame = pd.read_parquet(settings.processed_dir / "transactions.parquet")
    clean, quality = build_market_dataset(frame)
    output = settings.processed_dir / "market_transactions.parquet"
    quality_output = settings.report_dir / "m1-market-quality.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    quality_output.parent.mkdir(parents=True, exist_ok=True)
    clean.to_parquet(output, index=False)
    quality_output.write_text(
        json.dumps(quality.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    version = datetime.now(UTC).strftime("v%Y%m%d%H%M%S")
    sha256_hash = sha256(output.read_bytes()).hexdigest()
    min_date = clean["transaction_date"].min()
    max_date = clean["transaction_date"].max()
    return OfficialDataResult(
        version=version,
        row_count=len(clean),
        sha256=sha256_hash,
        minimum_date=str(min_date.date()),
        maximum_date=str(max_date.date()),
        quality_path=str(quality_output),
    )


def publish_official_market(
    root: Path,
    connection_factory: Callable[[], Any],
    result: OfficialDataResult,
) -> OfficialDataResult:
    settings = get_settings(root)
    path = settings.processed_dir / "market_transactions.parquet"
    frame = pd.read_parquet(path)
    connection = connection_factory()
    try:
        replace_market_rows(connection, frame, result.version)
    finally:
        connection.close()
    return result


STAGES = ("acquiring", "analysing", "building_market", "publishing_mysql", "verifying")

_IDEMPOTENCY_KEY = "official_data_update:active"


class OfficialDataRunner(Protocol):
    def acquire(self, start_season: str, end_season: str) -> None: ...
    def analyse(self) -> str: ...
    def build_market(self) -> OfficialDataResult: ...
    def publish_mysql(self, result: OfficialDataResult) -> OfficialDataResult: ...
    def verify(self, result: OfficialDataResult) -> OfficialDataResult: ...
    def verify_acquire_input(self) -> None: ...
    def verify_analyse_input(self) -> None: ...
    def verify_build_market_input(self) -> None: ...


class ProductionOfficialDataRunner:
    def __init__(self, root: Path, connection_factory: Callable[[], Any]) -> None:
        self._root = root
        self._connection_factory = connection_factory

    def acquire(self, start_season: str, end_season: str) -> None:
        acquire_official(self._root, start_season, end_season)

    def analyse(self) -> str:
        return analyse_official(self._root)

    def build_market(self) -> OfficialDataResult:
        return build_official_market(self._root)

    def publish_mysql(self, result: OfficialDataResult) -> OfficialDataResult:
        return publish_official_market(self._root, self._connection_factory, result)

    def verify(self, result: OfficialDataResult) -> OfficialDataResult:
        settings = get_settings(self._root)
        path = settings.processed_dir / "market_transactions.parquet"
        frame = pd.read_parquet(path)
        actual_sha = sha256(path.read_bytes()).hexdigest()
        if actual_sha != result.sha256:
            raise OfficialDataError("verification_failed", "SHA256 mismatch")
        if len(frame) != result.row_count:
            raise OfficialDataError(
                "verification_failed", "row count mismatch"
            )
        quality_path = Path(result.quality_path)
        if quality_path.exists():
            json.loads(quality_path.read_text(encoding="utf-8"))
        conn = self._connection_factory()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM market_transactions")
                (db_count,) = cursor.fetchone()
            if db_count != result.row_count:
                raise OfficialDataError(
                    "verification_failed",
                    f"MySQL row count {db_count} != expected {result.row_count}",
                )
        finally:
            conn.close()
        return result

    def verify_acquire_input(self) -> None:
        settings = get_settings(self._root)
        manifest = settings.raw_dir / "manifest.json"
        if not manifest.exists():
            raise OfficialDataError(
                "checkpoint_missing", "acquire manifest not found"
            )

    def verify_analyse_input(self) -> None:
        settings = get_settings(self._root)
        path = settings.processed_dir / "transactions.parquet"
        if not path.exists():
            raise OfficialDataError(
                "checkpoint_missing", "transactions.parquet not found"
            )

    def verify_build_market_input(self) -> None:
        settings = get_settings(self._root)
        path = settings.processed_dir / "market_transactions.parquet"
        if not path.exists():
            raise OfficialDataError(
                "checkpoint_missing", "market_transactions.parquet not found"
            )


class OfficialDataUpdateService:
    def __init__(
        self,
        job_service: JobService,
        runner: OfficialDataRunner,
        root: Path | None = None,
    ) -> None:
        self._job_service = job_service
        self._runner = runner
        self._root = root

    def submit(self, request: OfficialDataRequest) -> JobSubmission:
        return self._job_service.create(
            "official_data_update", _IDEMPOTENCY_KEY, request.trigger,
        )

    def handoff(
        self, submission: JobSubmission, request: OfficialDataRequest,
        executor: LocalJobExecutor,
    ) -> Future[None]:
        return executor.submit(
            submission.run.run_id,
            lambda: self.execute(submission.run.run_id, request),
        )

    def execute(self, run_id: str, request: OfficialDataRequest) -> OfficialDataResult:
        try:
            result = self._run_stages(run_id, request)
            if result.quality_path and self._root is not None:
                quality_path = Path(result.quality_path)
                if quality_path.exists():
                    admin_dir = self._root / "outputs" / "admin" / "official-data" / run_id
                    admin_dir.mkdir(parents=True, exist_ok=True)
                    target = admin_dir / "quality.json"
                    target.write_text(quality_path.read_text(encoding="utf-8"), encoding="utf-8")
            self._job_service.succeed(
                run_id, result.version, {"quality_report": "quality"},
            )
            return result
        except OfficialDataError as error:
            run = self._job_service.get(run_id)
            if run is not None and run.status == "running":
                self._job_service.fail(run_id, error.error_code, error.message)
            raise
        except Exception:
            self._job_service.fail(
                run_id, "official_update_failed", "official data update failed safely",
            )
            raise OfficialDataError(
                "official_update_failed", "official data update failed safely"
            ) from None

    def _run_stages(self, run_id: str, request: OfficialDataRequest) -> OfficialDataResult:
        _ALL = ("acquiring", "analysing", "building_market", "publishing_mysql", "verifying")
        start_map: dict[str, tuple[str, ...]] = {
            "acquire": _ALL,
            "analyse": _ALL[1:],
            "market_build": _ALL[2:],
            "mysql_publish": _ALL[3:],
        }
        checkpoint_map: dict[str, Callable[[], None]] = {
            "analyse": self._runner.verify_acquire_input,
            "market_build": self._runner.verify_analyse_input,
            "mysql_publish": self._runner.verify_build_market_input,
        }
        stages = start_map[request.start_at]
        if request.start_at in checkpoint_map:
            checkpoint_map[request.start_at]()

        completed: list[str] = []
        result: OfficialDataResult | None = None

        for stage in stages:
            if stage == "acquiring":
                self._runner.acquire(request.start_season, request.end_season)
            elif stage == "analysing":
                self._runner.analyse()
            elif stage == "building_market":
                result = self._runner.build_market()
            elif stage == "publishing_mysql":
                if result is None:
                    raise OfficialDataError("stage_order", "missing result from build_market")
                result = self._runner.publish_mysql(result)
            elif stage == "verifying":
                if result is None:
                    raise OfficialDataError("stage_order", "missing result from build_market")
                result = self._runner.verify(result)
            completed.append(stage)
            self._job_service.progress(run_id, {"stage": stage, "completed": list(completed)})

        assert result is not None
        return result


def _season_key(value: str) -> tuple[int, int]:
    year, quarter = value.upper().split("S", maxsplit=1)
    parsed = (int(year), int(quarter))
    if parsed[0] < 101 or parsed[1] not in (1, 2, 3, 4):
        raise ValueError(f"invalid ROC season: {value}")
    return parsed


def _iter_seasons(start: str, end: str) -> tuple[str, ...]:
    start_key = _season_key(start)
    end_key = _season_key(end)
    if start_key > end_key:
        raise ValueError("start season must not be after end season")
    values = []
    year, quarter = start_key
    while (year, quarter) <= end_key:
        values.append(f"{year}S{quarter}")
        quarter += 1
        if quarter == 5:
            year += 1
            quarter = 1
    return tuple(values)
