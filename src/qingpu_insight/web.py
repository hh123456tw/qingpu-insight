from __future__ import annotations

import os
import re
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from threading import BoundedSemaphore, Lock
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, session
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import HTTPException

from qingpu_insight.admin_dashboard import AdminDashboardService, ReadinessItem
from qingpu_insight.admin_web import ADMIN_JOB_TYPES, AdminRuntime, create_admin_blueprint
from qingpu_insight.backup_repository import MySQLBackupRepository
from qingpu_insight.config import get_settings
from qingpu_insight.evidence import UnknownCandidateError
from qingpu_insight.geo import wgs84_to_twd97
from qingpu_insight.health import HealthService
from qingpu_insight.health_repository import MySQLHealthRepository
from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import ACTIVE_STATUSES, JobRun, JobService, redact_job_message
from qingpu_insight.listing_metrics import (
    ListingFilters,
    listing_summary,
    public_events,
    public_listings,
)
from qingpu_insight.listing_repository import ListingRepository
from qingpu_insight.listing_update import (
    ListingUpdateAlreadyRunning,
    ListingUpdateRequest,
    ListingUpdateService,
)
from qingpu_insight.llm_model_catalog import LlmModelCatalog
from qingpu_insight.local_secrets import LocalSecretsStore
from qingpu_insight.market_metrics import (
    MapBounds,
    MarketFilters,
    market_map_points,
    market_summary,
    market_trends,
    recent_transactions,
)
from qingpu_insight.market_repository import MarketDataSource, repository_from_env
from qingpu_insight.model_features import ValuationInput, build_model_frame
from qingpu_insight.official_data import (
    OfficialDataUpdateService,
    ProductionOfficialDataRunner,
)
from qingpu_insight.provider_ops import ProviderOpsService
from qingpu_insight.report_composition import create_report_runtime
from qingpu_insight.report_repository import CorruptReportError
from qingpu_insight.valuation import ModelRegistry, valuate
from qingpu_insight.valuation_store import FileValuationStore
from qingpu_insight.web_benchmark_runner import ConfiguredWebBenchmarkRunner


def _parse_mysql_url_to_config() -> SimpleNamespace:
    import os
    from urllib import parse as urlparse

    url = os.environ.get("QINGPU_DATABASE_URL")
    if not url:
        raise RuntimeError("QINGPU_DATABASE_URL is required")
    parsed = urlparse.urlparse(url)
    return SimpleNamespace(
        mysql_host=parsed.hostname or "localhost",
        mysql_port=parsed.port or 3306,
        mysql_user=urlparse.unquote(parsed.username or ""),
        mysql_password=urlparse.unquote(parsed.password or ""),
        mysql_database=parsed.path.lstrip("/"),
    )


class ApiInputError(Exception):
    def __init__(
        self,
        message: str,
        fields: dict[str, str] | None = None,
        *,
        code: str = "invalid_request",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.fields = fields or {}
        self.code = code


@dataclass(frozen=True)
class AdminServices:
    """Immutable production/injected dependencies for the local job center."""

    job_service: JobService
    listing_update_service: ListingUpdateService
    executor: LocalJobExecutor
    model_training_service: object | None = None
    model_observatory: object | None = None
    official_data_service: object | None = None
    model_release_service: object | None = None
    backup_job_service: object | None = None


@dataclass(frozen=True)
class ReportServices:
    """Immutable injected dependencies for buyer report generation."""

    service: object  # ReportService duck type
    repository: object  # ReportRepository duck type


@dataclass(frozen=True)
class OpsServices:
    """Immutable production/injected dependencies for ops endpoints."""

    health_service: HealthService | None = None
    health_repository: MySQLHealthRepository | None = None
    backup_repository: MySQLBackupRepository | None = None


class _UnavailableMarketDataSource:
    def load(self, filters):
        del filters
        raise RuntimeError("market data unavailable")


def _strong_admin_secret(secret: str | None) -> bool:
    if not secret or len(secret) < 32:
        return False
    lowered = secret.casefold()
    if (
        any(
            marker in lowered
            for marker in (
                "dev-secret",
                "change-me",
                "changeme",
                "placeholder",
                "at-least-32",
                "random-characters",
                "password",
                "letmein",
                "example",
                "sample",
                "0123456789",
                "1234567890",
                "abcdefghijklmnopqrstuvwxyz",
                "zyxwvutsrqponmlkjihgfedcba",
                "qwertyuiop",
                "asdfghjkl",
            )
        )
        or "<" in secret
        or ">" in secret
    ):
        return False
    if lowered in (lowered + lowered)[1:-1]:
        return False
    if re.fullmatch(r"[0-9a-fA-F]{64,128}", secret):
        return len(set(lowered)) >= 8
    character_classes = sum(
        (
            any(char.islower() for char in secret),
            any(char.isupper() for char in secret),
            any(char.isdigit() for char in secret),
            any(not char.isalnum() for char in secret),
        )
    )
    return character_classes >= 3 and len(set(secret)) >= 12


def _create_production_admin_services(
    root: Path,
    *,
    connection_factory=None,
    source_factory=None,
    executor_factory=None,
) -> AdminServices:
    # Task 3 owns URL parsing and visible-Selenium dependency construction.
    from qingpu_insight.cli import (
        _create_listing_update_service,
        create_mysql_connection_factory,
    )

    operation_connection_factory = (
        connection_factory or create_mysql_connection_factory()
    )
    service_kwargs = {
        "connection_factory": operation_connection_factory,
    }
    if source_factory is not None:
        service_kwargs["source_factory"] = source_factory
    service = _create_listing_update_service(root, **service_kwargs)
    build_executor = executor_factory or LocalJobExecutor
    executor = build_executor(service.job_service)

    from qingpu_insight.automl_control import AutoMLControlRegistry
    from qingpu_insight.automl_outputs import AutoMLRunOutputStore
    from qingpu_insight.model_artifacts import CandidateArtifactStore
    from qingpu_insight.model_observatory import ModelObservatory
    from qingpu_insight.model_training_service import (
        ModelTrainingService,
        SourceVersionProvider,
    )

    settings = get_settings(root)
    input_path = settings.processed_dir / "market_transactions.parquet"
    candidate_store = CandidateArtifactStore(root / "candidates")
    automl_registry = AutoMLControlRegistry()
    automl_output_store = AutoMLRunOutputStore(root)
    mts = ModelTrainingService(
        service.job_service,
        candidate_store,
        input_path,
        SourceVersionProvider("unknown", True),
        automl_registry=automl_registry,
        automl_output_store=automl_output_store,
    )
    from qingpu_insight.model_release import OfficialModelStore as _OfficialModelStore

    official_store = _OfficialModelStore(root / "artifacts")
    observatory = ModelObservatory(
        root / "artifacts",
        candidate_store,
        mts,
        service.job_service,
        input_path=input_path,
        official_store=official_store,
        automl_output_store=automl_output_store,
    )

    for jt in ADMIN_JOB_TYPES:
        for interrupted in service.job_service.recover_interrupted(jt):
            if jt == "model_training":
                candidate_store.discard_staging(interrupted.run_id)

    official_runner = ProductionOfficialDataRunner(root, connection_factory)
    official_service = OfficialDataUpdateService(service.job_service, official_runner, root)

    from qingpu_insight.model_release import ModelReleaseService
    from qingpu_insight.model_release_repository import MySQLModelReleaseRepository
    from qingpu_insight.operation_previews import (
        MySQLOperationPreviewRepository,
        OperationPreviewService,
    )

    release_repo = MySQLModelReleaseRepository(
        operation_connection_factory
    )
    preview_repo = MySQLOperationPreviewRepository(
        operation_connection_factory
    )
    preview_service = OperationPreviewService(repository=preview_repo)
    model_release_service = ModelReleaseService(
        official_store=official_store,
        release_repository=release_repo,
        preview_service=preview_service,
        job_service=service.job_service,
        candidate_store=candidate_store,
        artifact_dir=root / "artifacts",
    )

    _backup_job_svc = None
    try:
        from qingpu_insight.backup_repository import MySQLBackupRepository
        from qingpu_insight.backups import BackupJobService, BackupService, RealRunner
        from qingpu_insight.cli import (
            create_mysql_connection_factory as _create_mysql_connection_factory,
        )

        _backup_dir = root / "outputs" / "backups"
        _backup_dir.mkdir(parents=True, exist_ok=True)
        _bk_cf = connection_factory or _create_mysql_connection_factory()
        _backup_repo = MySQLBackupRepository(_bk_cf)
        _mysql_config = _parse_mysql_url_to_config()
        _backup_svc = BackupService(_mysql_config, RealRunner(), _backup_repo, _backup_dir)
        _backup_job_svc = BackupJobService(service.job_service, _backup_svc)
    except Exception:
        pass

    return AdminServices(
        job_service=service.job_service,
        listing_update_service=service,
        executor=executor,
        model_training_service=mts,
        model_observatory=observatory,
        official_data_service=official_service,
        model_release_service=model_release_service,
        backup_job_service=_backup_job_svc,
    )


def _create_admin_dashboard_service(
    root: Path | None,
    connection_factory: object | None,
    admin_services: AdminServices | None,
    ops_services: OpsServices | None,
) -> AdminDashboardService | None:
    import shutil
    from pathlib import Path as _Path

    probes: dict[str, object] = {}

    if connection_factory is not None:

        def _mysql_probe() -> ReadinessItem:
            try:
                conn = connection_factory()
                conn.ping()
                conn.close()
                return ReadinessItem("mysql", "ready", "MySQL 連線正常。", {"reachable": True})
            except Exception:
                return ReadinessItem("mysql", "blocked", "MySQL 無法連線。", {"reachable": False})

        probes["mysql"] = _mysql_probe

    # Only show dependencies that are part of the normal project workflow.
    # Selenium manages ChromeDriver on demand, while the MySQL command-line
    # tools are optional implementation details of backup/restore operations.
    for binary_name, code in (("ollama", "ollama"),):

        def _make_binary_probe(
            name: str = binary_name, probe_code: str = code
        ) -> Callable[[], ReadinessItem]:
            def _probe() -> ReadinessItem:
                found = shutil.which(name)
                if found:
                    return ReadinessItem(probe_code, "ready", f"{name} 可用。", {"path": found})
                return ReadinessItem(
                    probe_code,
                    "warning",
                    f"找不到 {name}。",
                    {"path": None},
                )

            return _probe

        probes[code] = _make_binary_probe()

    for dir_key, dir_path in (
        ("data_dir", root / "data" if root else _Path()),
        ("candidates_dir", root / "candidates" if root else _Path()),
    ):

        def _make_dir_probe(
            key: str = dir_key, path: _Path = dir_path
        ) -> Callable[[], ReadinessItem]:
            def _probe() -> ReadinessItem:
                if path.exists():
                    return ReadinessItem(key, "ready", "目錄存在。", {"path": str(path)})
                return ReadinessItem(
                    key,
                    "warning",
                    "目錄不存在。",
                    {"path": str(path)},
                )

            return _probe

        probes[dir_key] = _make_dir_probe()

    jobs = admin_services.job_service if admin_services is not None else None
    health_repo = ops_services.health_repository if ops_services is not None else None
    backup_repo = ops_services.backup_repository if ops_services is not None else None
    model_obs = admin_services.model_observatory if admin_services is not None else None

    return AdminDashboardService(
        probes=probes,
        jobs=jobs,
        health_repository=health_repo,
        backup_repository=backup_repo,
        model_observatory=model_obs,
    )


def parse_filters(args: MultiDict[str, str]) -> MarketFilters:
    transaction_type = args.get("transaction_type", "resale")
    if transaction_type == "presale":
        raise ApiInputError("目前僅支援中古屋市場。", {"transaction_type": "resale_only"})
    stations = tuple(args.getlist("station")) or ("A17", "A18", "A19")
    try:
        date_from = (
            pd.to_datetime(args.get("date_from"), errors="raise") if args.get("date_from") else None
        )
    except (TypeError, ValueError):
        raise ApiInputError("日期格式不正確。", {"date_from": "invalid"}) from None
    try:
        date_to = (
            pd.to_datetime(args.get("date_to"), errors="raise") if args.get("date_to") else None
        )
    except (TypeError, ValueError):
        raise ApiInputError("日期格式不正確。", {"date_to": "invalid"}) from None
    try:
        area_ping_min = float(args["area_ping_min"]) if args.get("area_ping_min") else None
        area_ping_max = float(args["area_ping_max"]) if args.get("area_ping_max") else None
        bedrooms = tuple(int(value) for value in args.getlist("bedrooms"))
    except (TypeError, ValueError):
        raise ApiInputError("篩選條件格式不正確。", {"filters": "invalid"}) from None
    try:
        return MarketFilters(
            transaction_type=transaction_type,
            station_codes=stations,
            date_from=date_from,
            date_to=date_to,
            area_ping_min=area_ping_min,
            area_ping_max=area_ping_max,
            building_types=tuple(args.getlist("building_type")),
            bedrooms=bedrooms,
        )
    except ValueError:
        fields: dict[str, str] = {}
        if transaction_type != "resale":
            fields["transaction_type"] = "resale_only"
        if not stations or not set(stations) <= {"A17", "A18", "A19"}:
            fields["station"] = "A17_A18_or_A19"
        if area_ping_min is not None and area_ping_min < 0:
            fields["area_ping_min"] = "non_negative"
        if area_ping_max is not None and area_ping_max < 0:
            fields["area_ping_max"] = "non_negative"
        if (
            area_ping_min is not None
            and area_ping_max is not None
            and area_ping_min >= 0
            and area_ping_max >= 0
            and area_ping_min > area_ping_max
        ):
            fields["area_ping_min"] = "must_not_exceed_area_ping_max"
            fields["area_ping_max"] = "must_not_be_less_than_area_ping_min"
        raise ApiInputError("篩選條件無效。", fields or {"filters": "invalid"}) from None


def _parse_map_view(args: MultiDict[str, str]) -> tuple[int, MapBounds | None]:
    raw_zoom = args.get("zoom", "14")
    try:
        zoom = int(raw_zoom)
    except (TypeError, ValueError):
        raise ApiInputError("地圖縮放層級格式不正確。", {"zoom": "integer_10_to_19"}) from None
    if str(zoom) != raw_zoom or not 10 <= zoom <= 19:
        raise ApiInputError("地圖縮放層級無效。", {"zoom": "integer_10_to_19"})

    names = ("south", "west", "north", "east")
    values = [args.get(name) for name in names]
    if not any(value not in (None, "") for value in values):
        return zoom, None
    if any(value in (None, "") for value in values):
        raise ApiInputError("地圖邊界不完整。", {"bounds": "all_or_none"})
    try:
        bounds = MapBounds(*(float(value) for value in values if value is not None))
    except (TypeError, ValueError):
        raise ApiInputError("地圖邊界無效。", {"bounds": "ordered_finite_numbers"}) from None
    return zoom, bounds


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, pd.Timestamp | pd.Timedelta):
        return None if pd.isna(obj) else obj.isoformat()
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def parse_valuation_payload(payload: dict[str, Any]) -> ValuationInput:
    transaction_type = str(payload.get("transaction_type", "resale"))
    if transaction_type == "presale":
        raise ApiInputError(
            "目前已停止預售屋估價。",
            {"transaction_type": "resale_only"},
            code="presale_valuation_disabled",
        )
    required = (
        "station_code",
        "building_area_ping",
        "station_distance_m",
        "building_type",
        "bedrooms",
        "living_rooms",
        "bathrooms",
        "floor",
        "total_floors",
    )
    missing = {name: "required" for name in required if payload.get(name) in (None, "")}
    if missing:
        raise ApiInputError("請完整填寫估價條件。", missing)
    parking_type = payload.get("parking_type", "")
    parking_area = float(payload.get("parking_area_ping", 0))
    if not parking_type:
        parking_area = 0
    elif parking_area <= 0:
        raise ApiInputError(
            "有車位時請填寫大於 0 的車位面積。",
            {"parking_area_ping": "positive_when_parking_selected"},
        )
    try:
        return ValuationInput(
            transaction_type=transaction_type,
            station_code=str(payload["station_code"]),
            building_area_ping=float(payload["building_area_ping"]),
            station_distance_m=float(payload["station_distance_m"]),
            building_type=str(payload["building_type"]),
            bedrooms=int(payload["bedrooms"]),
            living_rooms=int(payload["living_rooms"]),
            bathrooms=int(payload["bathrooms"]),
            building_age_years=float(payload["building_age_years"])
            if payload.get("building_age_years") is not None
            else None,
            floor=int(payload["floor"]),
            total_floors=int(payload["total_floors"]),
            parking_type=parking_type,
            parking_area_ping=parking_area,
            asking_total_price_twd=int(payload["asking_total_price_twd"])
            if payload.get("asking_total_price_twd")
            else None,
        )
    except (KeyError, TypeError, ValueError):
        raise ApiInputError("估價條件格式不正確。", {"valuation": "invalid"}) from None


def _is_trusted_local_request() -> bool:
    try:
        remote_is_loopback = ip_address(request.remote_addr or "").is_loopback
        hostname = urlsplit(f"//{request.host}").hostname
    except ValueError:
        return False
    return remote_is_loopback and (hostname or "").lower() in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def _invalid_request(fields: dict[str, str]):
    return jsonify(
        {
            "error": {
                "code": "invalid_request",
                "message": "Request validation failed.",
                "fields": fields,
            }
        }
    ), 400


def _parse_listing_update_request() -> ListingUpdateRequest:
    if request.mimetype != "application/json":
        raise ApiInputError("Request body must be JSON.", {"body": "application_json"})
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiInputError("Request body must be a JSON object.", {"body": "object"})

    fields: dict[str, str] = {}
    types = payload.get("types", ["sale"])
    if not isinstance(types, list):
        fields["types"] = "array"
    elif not types:
        fields["types"] = "non_empty"
    elif any(not isinstance(item, str) for item in types):
        fields["types"] = "string_items"
    elif len(set(types)) != len(types):
        fields["types"] = "unique"
    elif any(item != "sale" for item in types):
        fields["types"] = "supported_values"

    max_pages = payload.get("max_pages", 10)
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        fields["max_pages"] = "integer_1_to_100"

    trigger = payload.get("trigger", "manual")
    if (
        not isinstance(trigger, str)
        or not trigger.strip()
        or len(trigger) > 32
        or trigger.strip() not in {"manual", "scheduled", "web"}
    ):
        fields["trigger"] = "supported_value"
    if fields:
        raise ApiInputError("Request validation failed.", fields)
    return ListingUpdateRequest(types=tuple(types), max_pages=max_pages, trigger=trigger.strip())


def _parse_model_training_request() -> object:
    from qingpu_insight.model_training_service import ModelTrainingRequest
    from qingpu_insight.model_tuning import TuningValidationError, parse_tuning_plan

    if request.mimetype != "application/json":
        raise ApiInputError("Request body must be JSON.", {"body": "application_json"})
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiInputError("Request body must be a JSON object.", {"body": "object"})

    fields: dict[str, str] = {}

    extra = set(payload.keys()) - {"markets", "tuning"}
    for k in sorted(extra):
        fields[k] = "not_allowed"

    raw_markets = payload.get("markets")
    if raw_markets is None:
        fields["markets"] = "required"
    elif not isinstance(raw_markets, list):
        fields["markets"] = "array"
    elif not raw_markets:
        fields["markets"] = "non_empty"
    elif any(not isinstance(m, str) for m in raw_markets):
        fields["markets"] = "string_items"
    elif len(set(raw_markets)) != len(raw_markets):
        fields["markets"] = "unique"
    elif any(m not in {"resale", "presale"} for m in raw_markets):
        fields["markets"] = "supported_values"

    market_order = ("resale", "presale")
    ordered = [m for m in market_order if m in raw_markets] if isinstance(raw_markets, list) else []

    try:
        tuning_plan = parse_tuning_plan(
            tuple(ordered),
            payload.get("tuning"),
        )
    except TuningValidationError as exc:
        for key, value in exc.fields.items():
            fields[f"tuning.{key}"] = value

    if fields:
        raise ApiInputError("Request validation failed.", fields)

    return ModelTrainingRequest(
        markets=tuple(ordered),
        tuning_plan=tuning_plan,
    )


def _public_job(run: JobRun) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "job_type": run.job_type,
        "status": run.status,
        "trigger": (
            run.trigger
            if run.trigger in {"manual", "scheduled", "web"} and len(run.trigger) <= 32
            else "redacted"
        ),
        "attempt": run.attempt,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "input_version": run.input_version,
        "output_version": run.output_version,
        "summary": _safe_public_value(run.summary),
        "error_code": run.error_code,
        "error_message": (_safe_public_text(run.error_message) if run.error_message else None),
    }


_UNSAFE_SUMMARY_KEY = re.compile(
    r"(?i)(password|secret|token|credential|database_url|db_url|sql|query|html|phone|traceback)"
)
_SQL_TEXT = re.compile(
    r"(?is)\b(select\s+.+\s+from|insert\s+into|update\s+.+\s+set|"
    r"delete\s+from|alter\s+table|create\s+table|drop\s+table)\b"
)
_DATABASE_URL_TEXT = re.compile(
    r"(?ix)(?:\b(?:mysql|mariadb|postgres(?:ql)?)"
    r"(?:\+[a-z0-9_.-]+)?://\S+|"
    r"\bQINGPU_DATABASE_URL\b\s*[:=]\s*\S+)"
)


def _safe_public_text(value: str) -> str:
    if _DATABASE_URL_TEXT.search(value):
        return "redacted"
    redacted = redact_job_message(value)
    lowered = redacted.lower()
    if (
        "<html" in lowered
        or "<!doctype" in lowered
        or "traceback (most recent call last)" in lowered
        or _SQL_TEXT.search(redacted)
    ):
        return "redacted"
    return redacted


def _safe_public_value(value):
    if isinstance(value, dict):
        return {
            str(key): (
                "redacted" if _UNSAFE_SUMMARY_KEY.search(str(key)) else _safe_public_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_public_value(item) for item in value]
    if isinstance(value, str):
        return _safe_public_text(value)
    return value


def _conversation_market_comparables(
    data_source: MarketDataSource,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    transaction_type = "presale" if payload.get("listing_type") == "newhouse" else "resale"
    area = pd.to_numeric(payload.get("area_ping"), errors="coerce")
    filters = MarketFilters(
        transaction_type=transaction_type,
        area_ping_min=float(area * 0.7) if pd.notna(area) else None,
        area_ping_max=float(area * 1.3) if pd.notna(area) else None,
    )
    frame = data_source.load(filters).copy()
    if frame.empty:
        return []

    latitude = pd.to_numeric(payload.get("latitude"), errors="coerce")
    longitude = pd.to_numeric(payload.get("longitude"), errors="coerce")
    frame["_distance_m"] = np.nan
    if pd.notna(latitude) and pd.notna(longitude):
        target_lat = np.radians(float(latitude))
        target_lon = np.radians(float(longitude))
        row_lat = np.radians(pd.to_numeric(frame["latitude"], errors="coerce"))
        row_lon = np.radians(pd.to_numeric(frame["longitude"], errors="coerce"))
        delta_lat = row_lat - target_lat
        delta_lon = row_lon - target_lon
        haversine = (
            np.sin(delta_lat / 2) ** 2
            + np.cos(target_lat) * np.cos(row_lat) * np.sin(delta_lon / 2) ** 2
        )
        frame["_distance_m"] = (
            6_371_000
            * 2
            * np.arctan2(
                np.sqrt(haversine),
                np.sqrt(1 - haversine),
            )
        )

    frame["_transaction_date"] = pd.to_datetime(
        frame["transaction_date"],
        errors="coerce",
    )
    frame = frame.sort_values(
        ["_distance_m", "_transaction_date"],
        ascending=[True, False],
        na_position="last",
    ).head(10)

    comparables: list[dict[str, Any]] = []
    for rank, (_, row) in enumerate(frame.iterrows(), start=1):
        date = row.get("_transaction_date")
        distance = row.get("_distance_m")
        comparables.append(
            {
                "rank": rank,
                "price_twd": (
                    int(row["total_price_twd"]) if pd.notna(row.get("total_price_twd")) else None
                ),
                "unit_price_per_ping_twd": (
                    int(row["unit_price_per_ping_twd"])
                    if pd.notna(row.get("unit_price_per_ping_twd"))
                    else None
                ),
                "distance_m": (int(round(float(distance))) if pd.notna(distance) else None),
                "transaction_date": (date.date().isoformat() if pd.notna(date) else None),
                "station_code": row.get("station_code"),
                "station_distance_m": (
                    float(row["station_distance_m"])
                    if pd.notna(row.get("station_distance_m"))
                    else None
                ),
                "selection_reason": "面積相近，依距離與日期排序",
            }
        )
    return comparables


_CONVERSATION_LAYOUT_RE = re.compile(
    r"(?P<bedrooms>\d+)\s*房.*?"
    r"(?P<living_rooms>\d+)\s*廳.*?"
    r"(?P<bathrooms>\d+)\s*衛"
)
_CONVERSATION_FLOOR_RE = re.compile(r"(?P<floor>\d+)\s*[Ff]")
_CONVERSATION_PARKING_AREA_RE = re.compile(
    r"(?P<area>\d+(?:\s*\.\s*\d+)?)\s*坪"
)


def _conversation_model_building_type(
    raw_building_type: object,
    total_floors: int,
) -> str:
    value = str(raw_building_type or "").strip()
    if not value:
        raise ValueError("building type unavailable")
    if "公寓" in value:
        return "公寓(5樓含以下無電梯)"
    if "華廈" in value:
        return "華廈(10層含以下有電梯)"
    if "住宅大樓" in value:
        return "住宅大樓(11層含以上有電梯)"
    if "電梯大樓" in value:
        if total_floors <= 10:
            return "華廈(10層含以下有電梯)"
        return "住宅大樓(11層含以上有電梯)"
    return value


def _conversation_parking(
    raw_parking: object,
) -> tuple[str, float]:
    value = str(raw_parking or "").strip()
    if not value or "無車位" in value:
        return "", 0
    area_match = _CONVERSATION_PARKING_AREA_RE.search(value)
    if area_match is None:
        return "", 0
    area = float(re.sub(r"\s+", "", area_match.group("area")))
    if area <= 0:
        return "", 0
    if "機械" in value:
        parking_type = "坡道機械"
    elif "平面" in value:
        parking_type = "坡道平面"
    else:
        parking_type = "其他"
    return parking_type, area


def _conversation_valuation(
    data_source: MarketDataSource,
    registry: ModelRegistry,
    payload: dict[str, Any],
) -> dict[str, Any]:
    transaction_type = "presale" if payload.get("listing_type") == "newhouse" else "resale"
    layout = _CONVERSATION_LAYOUT_RE.search(str(payload.get("layout") or ""))
    floor_match = _CONVERSATION_FLOOR_RE.search(str(payload.get("floor") or ""))
    comparables = _conversation_market_comparables(data_source, payload)
    nearest = next(
        (
            item
            for item in comparables
            if item.get("station_code") in {"A17", "A18", "A19"}
            and item.get("station_distance_m") is not None
            and item.get("distance_m") is not None
        ),
        None,
    )
    if layout is None or floor_match is None or nearest is None:
        raise ValueError("listing lacks required valuation features")
    if int(nearest["distance_m"]) > 300:
        raise ValueError("no nearby transaction for station inference")

    total_floors = int(payload["total_floors"])
    floor = int(floor_match.group("floor"))
    total_area = float(payload["area_ping"])
    parking_type, parking_area = _conversation_parking(
        payload.get("parking_type")
    )
    area = total_area - parking_area
    if area <= 0:
        raise ValueError("parking area must be smaller than total area")
    building_type = _conversation_model_building_type(
        payload.get("building_type"),
        total_floors,
    )
    age = None if transaction_type == "presale" else float(payload["age_years"])
    longitude = payload.get("longitude")
    latitude = payload.get("latitude")
    coordinates = (
        wgs84_to_twd97(float(longitude), float(latitude))
        if longitude is not None and latitude is not None
        else (None, None)
    )
    valuation_input = ValuationInput(
        transaction_type=transaction_type,
        station_code=str(nearest["station_code"]),
        station_distance_m=float(nearest["station_distance_m"]),
        building_area_ping=area,
        building_type=building_type,
        bedrooms=int(layout.group("bedrooms")),
        living_rooms=int(layout.group("living_rooms")),
        bathrooms=int(layout.group("bathrooms")),
        building_age_years=age,
        floor=floor,
        total_floors=total_floors,
        parking_type=parking_type,
        parking_area_ping=parking_area,
        asking_total_price_twd=(
            int(payload["total_price_twd"]) if payload.get("total_price_twd") is not None else None
        ),
        twd97_x=coordinates[0],
        twd97_y=coordinates[1],
    )
    market = data_source.load(MarketFilters(transaction_type=transaction_type))
    if market.empty:
        raise ValueError("market data unavailable")
    latest_data_date = pd.Timestamp(market["transaction_date"].max())
    model_frame = build_model_frame(market, transaction_type)
    result = valuate(
        valuation_input,
        registry,
        model_frame,
        latest_data_date=latest_data_date,
    )
    low, high = result["interval_total_price_twd"]
    model = result.get("model") or {}
    limitations = [
        f"捷運生活圈與距離由最近一筆實價登錄推定（該成交距物件約 {nearest['distance_m']} 公尺）"
    ]
    if payload.get("parking_type") and not parking_type:
        limitations.append(
            "591 未提供可驗證車位坪數，本次未另加車位估值"
        )
    elif parking_type:
        limitations.append(
            f"房屋坪數已從建物總坪數扣除車位 {parking_area:g} 坪"
        )
    return {
        "point_estimate_twd": result["estimated_total_price_twd"],
        "estimated_building_price_twd": result.get("estimated_building_price_twd"),
        "estimated_parking_price_twd": result.get("estimated_parking_price_twd"),
        "low_estimate_twd": low,
        "high_estimate_twd": high,
        "confidence": result["confidence"],
        "confidence_reasons": result.get("confidence_reasons", []),
        "asking_price_assessment": result.get("asking_price_assessment"),
        "model_version": model.get("version", "unknown"),
        "dataset_version": result.get("data_date", "unknown"),
        "comparables": result.get("comparables", []),
        "limitations": limitations,
    }


def _ensure_conversation_schema(
    root: Path,
    connection_factory,
) -> None:
    migration_paths = (
        root / "database" / "008_conversation_assistant_schema.sql",
        root / "database" / "009_conversation_fallback_metadata.sql",
    )
    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            for migration_path in migration_paths:
                sql = migration_path.read_text(encoding="utf-8")
                statements = [
                    statement.strip() for statement in sql.split(";") if statement.strip()
                ]
                for statement in statements:
                    cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_app(
    data_source: MarketDataSource | None = None,
    root: Path | None = None,
    valuation_store: FileValuationStore | None = None,
    model_registry: ModelRegistry | None = None,
    listing_repo: ListingRepository | None = None,
    job_service: JobService | None = None,
    listing_update_service: ListingUpdateService | None = None,
    job_executor: LocalJobExecutor | None = None,
    admin_services: AdminServices | None = None,
    ops_services: OpsServices | None = None,
    report_services: ReportServices | None = None,
    report_service: object | None = None,
    report_repository: object | None = None,
    conversation_service: object | None = None,
    conversation_repository: object | None = None,
) -> Flask:
    app = Flask(__name__)
    app.json.default = _json_default
    configured_secret = os.environ.get("QINGPU_SECRET_KEY")
    app.secret_key = configured_secret or secrets.token_hex(32)

    if data_source is None and root is not None:
        try:
            data_source = repository_from_env(root)
        except Exception:
            app.logger.error("market data composition unavailable")
            data_source = _UnavailableMarketDataSource()

    ds = data_source
    store = valuation_store or FileValuationStore(Path.cwd() / "outputs" / "valuations")
    registry = model_registry or ModelRegistry(Path.cwd() / "artifacts")
    lr = listing_repo
    injected_legacy = (job_service, listing_update_service, job_executor)
    if admin_services is None and any(item is not None for item in injected_legacy):
        if all(item is not None for item in injected_legacy):
            admin_services = AdminServices(
                job_service=job_service,
                listing_update_service=listing_update_service,
                executor=job_executor,
            )
        else:
            raise ValueError("admin dependencies must be injected as a complete bundle")
    if (
        admin_services is None
        and root is not None
        and os.environ.get("QINGPU_DATABASE_URL")
        and _strong_admin_secret(configured_secret)
    ):
        try:
            admin_services = _create_production_admin_services(root)
        except Exception:
            app.logger.error("listing update admin composition unavailable")

    shutdown_lock = Lock()
    shutdown_complete = False
    conversation_owned_executor: LocalJobExecutor | None = None
    conversation_executor: LocalJobExecutor | None = None

    def shutdown_admin() -> None:
        nonlocal shutdown_complete
        with shutdown_lock:
            if shutdown_complete:
                return
            shutdown_complete = True
        if admin_services is not None:
            admin_services.executor.shutdown(wait=True)
        if conversation_owned_executor is not None:
            conversation_owned_executor.shutdown(wait=True)

    app.extensions["qingpu_admin_services"] = admin_services
    app.extensions["qingpu_admin_shutdown"] = shutdown_admin

    secrets_store: LocalSecretsStore | None = None
    provider_ops_service: ProviderOpsService | None = None
    resolved_env = dict(os.environ)
    if root is not None:
        secrets_store = LocalSecretsStore(root / "instance" / "secrets.env")
        from qingpu_insight.report_composition import create_dynamic_provider_resolver
        from qingpu_insight.report_providers import RuleReportProvider

        rule_provider = RuleReportProvider()
        resolved_env = secrets_store.merged_env(os.environ)
        provider_resolver = create_dynamic_provider_resolver(secrets_store, os.environ)
        provider_ops_service = ProviderOpsService(
            rule_provider=rule_provider,
            provider_factory=provider_resolver,
            env=resolved_env,
        )

    def get_runtime_env(name: str, default: str = "") -> str:
        current = (
            secrets_store.merged_env(os.environ) if secrets_store is not None else dict(os.environ)
        )
        return current.get(name, default)

    def get_ollama_base_url() -> str:
        return get_runtime_env(
            "QINGPU_OLLAMA_BASE_URL",
            "http://127.0.0.1:11434",
        )

    llm_model_catalog = LlmModelCatalog(
        ollama_base_url_getter=get_ollama_base_url,
        gemini_configured_getter=lambda: bool(get_runtime_env("QINGPU_GEMINI_API_KEY")),
    )
    if provider_ops_service is not None:
        provider_ops_service.set_benchmark_runner(
            ConfiguredWebBenchmarkRunner(
                ollama_base_url_getter=get_ollama_base_url,
                gemini_api_key_getter=lambda: get_runtime_env("QINGPU_GEMINI_API_KEY"),
            )
        )

    def get_gemini_api_key() -> str | None:
        if secrets_store is None:
            return os.environ.get("QINGPU_GEMINI_API_KEY")
        return secrets_store.merged_env(os.environ).get("QINGPU_GEMINI_API_KEY")

    if admin_services is not None:
        admin_runtime = AdminRuntime(
            job_service=admin_services.job_service,
            executor=admin_services.executor,
            listing_update_service=admin_services.listing_update_service,
            model_training_service=admin_services.model_training_service,
            model_observatory=admin_services.model_observatory,
            official_data_service=admin_services.official_data_service,
            model_release_service=admin_services.model_release_service,
            backup_service=admin_services.backup_job_service,
            provider_ops_service=provider_ops_service,
            secrets_store=secrets_store,
            llm_model_catalog=llm_model_catalog,
            root=root,
        )
    else:
        admin_runtime = AdminRuntime(
            job_service=None,
            executor=None,
            provider_ops_service=provider_ops_service,
            secrets_store=secrets_store,
            llm_model_catalog=llm_model_catalog,
            root=root,
        )
    app.register_blueprint(create_admin_blueprint(admin_runtime))

    from qingpu_insight.conversation_web import create_conversation_blueprint

    conv_repo = conversation_repository
    conv_service = conversation_service
    conversation_job_service = admin_services.job_service if admin_services is not None else None
    if (
        root is not None
        and os.environ.get("QINGPU_DATABASE_URL")
        and (conv_repo is None or conv_service is None)
    ):
        try:
            from qingpu_insight.cli import create_mysql_connection_factory
            from qingpu_insight.conversation_evidence import (
                ConversationEvidenceBuilder,
            )
            from qingpu_insight.conversation_fallback import (
                ConversationFallbackExecutor,
            )
            from qingpu_insight.conversation_import import (
                ConversationImportService,
            )
            from qingpu_insight.conversation_listing_capture import (
                DetailPageBrowser,
            )
            from qingpu_insight.conversation_providers import (
                ConversationProviderRegistry,
                GeminiConversationProvider,
                OllamaConversationProvider,
                RuleConversationProvider,
            )
            from qingpu_insight.conversation_repository import MySQLConversationRepository
            from qingpu_insight.conversation_service import ConversationService
            from qingpu_insight.conversation_validation import (
                validate_chat_answer,
            )
            from qingpu_insight.job_repository import MySQLJobRepository
            from qingpu_insight.listing_capture import (
                ChromeConfig,
                create_chrome,
            )

            connection_factory = create_mysql_connection_factory()
            _ensure_conversation_schema(root, connection_factory)
            conv_repo = conv_repo or MySQLConversationRepository(connection_factory)
            conversation_jobs = (
                admin_services.job_service
                if admin_services is not None
                else JobService(MySQLJobRepository(connection_factory))
            )
            conversation_job_service = conversation_jobs
            conversation_owned_executor = LocalJobExecutor(conversation_jobs)
            conversation_executor = conversation_owned_executor
            browser = DetailPageBrowser(
                driver_factory=lambda: create_chrome(ChromeConfig(headless=False))
            )
            market_service = (
                (lambda payload: _conversation_market_comparables(ds, payload))
                if ds is not None
                else None
            )
            valuation_service = (
                (
                    lambda payload: _conversation_valuation(
                        ds,
                        registry,
                        payload,
                    )
                )
                if ds is not None
                else None
            )
            evidence_builder = ConversationEvidenceBuilder(
                valuation_service=valuation_service,
                market_service=market_service,
            )
            import_service = ConversationImportService(
                repository=conv_repo,
                browser=browser,
                evidence_builder=evidence_builder,
            )
            providers = ConversationProviderRegistry()
            providers.register("rule", RuleConversationProvider())
            providers.register(
                "ollama",
                OllamaConversationProvider(get_ollama_base_url()),
            )
            providers.register(
                "gemini",
                GeminiConversationProvider(get_gemini_api_key),
            )
            reply_executor = ConversationFallbackExecutor(
                provider_registry=providers,
                validator=validate_chat_answer,
            )
            conv_service = conv_service or ConversationService(
                repository=conv_repo,
                import_service=import_service,
                provider_registry=providers,
                reply_executor=reply_executor,
                validator=validate_chat_answer,
                job_service=conversation_jobs,
                executor=conversation_executor,
            )
        except Exception:
            app.logger.error("conversation runtime unavailable")
            conv_repo = None
            conv_service = None

    app.extensions["qingpu_conversation_service"] = conv_service
    app.extensions["qingpu_conversation_repository"] = conv_repo
    app.extensions["qingpu_conversation_job_service"] = conversation_job_service
    app.extensions["qingpu_conversation_executor"] = conversation_executor
    from qingpu_insight.conversation_models import public_model_catalog

    app.register_blueprint(
        create_conversation_blueprint(
            conv_service,
            conv_repo,
            catalog_getter=lambda: public_model_catalog(
                gemini_configured=bool(get_runtime_env("QINGPU_GEMINI_API_KEY")),
                ollama_ready=llm_model_catalog.ollama_model_ready("gemma4:e2b"),
            ),
        )
    )

    @app.before_request
    def ensure_session():
        if "_csrf_token" not in session:
            session["_csrf_token"] = str(uuid.uuid4())

    @app.errorhandler(ApiInputError)
    def handle_api_input_error(error: ApiInputError):
        return jsonify(
            {
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "fields": error.fields,
                }
            }
        ), 400

    @app.errorhandler(Exception)
    def handle_unhandled(error: Exception):
        if isinstance(error, HTTPException):
            return error
        app.logger.exception("unhandled error serving request")
        return jsonify(
            {
                "error": {
                    "code": "market_data_unavailable",
                    "message": "無法取得市場資料，請稍後再試。",
                    "fields": None,
                }
            }
        ), 503

    @app.get("/")
    def index():
        return render_template("index.html", csrf_token=session.get("_csrf_token", ""))

    @app.get("/api/market/summary")
    def summary_api():
        filters = parse_filters(request.args)
        return jsonify(market_summary(ds.load(filters), filters))

    @app.get("/api/market/trends")
    def trends_api():
        filters = parse_filters(request.args)
        return jsonify({"items": market_trends(ds.load(filters), filters)})

    @app.get("/api/market/map-points")
    def map_points_api():
        filters = parse_filters(request.args)
        zoom, bounds = _parse_map_view(request.args)
        return jsonify(
            market_map_points(
                ds.load(filters),
                filters,
                zoom=zoom,
                bounds=bounds,
            )
        )

    @app.get("/api/transactions")
    def transactions_api():
        filters = parse_filters(request.args)
        try:
            limit = min(max(int(request.args.get("limit", "20")), 1), 100)
        except (TypeError, ValueError):
            raise ApiInputError("筆數格式不正確。", {"limit": "integer_1_to_100"}) from None
        return jsonify(
            {
                "items": recent_transactions(ds.load(filters), filters, limit),
                "limit": limit,
            }
        )

    # ------------------------------------------------------------------
    # Listing intelligence (M3)
    # ------------------------------------------------------------------

    def _listing_filters_from_args() -> ListingFilters:
        listing_type = request.args.get("listing_type", "sale")
        if listing_type != "sale":
            raise ApiInputError("僅支援中古屋刊登資料。", {"listing_type": "supported_value"})
        stations = tuple(request.args.getlist("station")) or ("A17", "A18", "A19")
        try:
            limit = min(max(int(request.args.get("limit", "100")), 1), 100)
        except (TypeError, ValueError):
            raise ApiInputError("筆數格式不正確。", {"limit": "integer_1_to_100"}) from None
        return ListingFilters(
            listing_type=listing_type,
            station_codes=stations,
            limit=limit,
        )

    def _publicly_visible_listings(df: pd.DataFrame) -> pd.DataFrame:
        required = {"location_eligible", "active"}
        if not required.issubset(df.columns):
            return df.iloc[0:0]
        return df[df["location_eligible"].eq(True) & df["active"].eq(True)]

    @app.get("/api/listings/summary")
    def listing_summary_api():
        filters = _listing_filters_from_args()
        if lr is None:
            err = {"code": "listing_data_unavailable", "message": "刊登資料未啟用。"}
            return jsonify({"error": err}), 503
        df = _publicly_visible_listings(lr.load_current(filters.listing_type))
        return jsonify(listing_summary(df, filters))

    @app.get("/api/listings")
    def listings_api():
        filters = _listing_filters_from_args()
        if lr is None:
            err = {"code": "listing_data_unavailable", "message": "刊登資料未啟用。"}
            return jsonify({"error": err}), 503
        df = _publicly_visible_listings(lr.load_current(filters.listing_type))
        items = public_listings(df, filters)
        return jsonify({"items": items, "limit": filters.limit})

    @app.get("/api/listing-events")
    def listing_events_api():
        filters = _listing_filters_from_args()
        if lr is None:
            err = {"code": "listing_data_unavailable", "message": "刊登資料未啟用。"}
            return jsonify({"error": err}), 503
        df = lr.load_events(filters.listing_type)
        events = public_events(df, filters)
        return jsonify({"items": events, "limit": filters.limit})

    @app.post("/api/valuations")
    def create_valuation():
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                raise ApiInputError("Request body must be a JSON object.", {"body": "object"})
            input_ = parse_valuation_payload(payload)
        except ApiInputError as error:
            return jsonify(
                {
                    "error": {
                        "code": error.code,
                        "message": error.message,
                        "fields": error.fields,
                    }
                }
            ), 400

        market = ds.load(MarketFilters(transaction_type=input_.transaction_type))
        latest_data_date = (
            pd.Timestamp(market["transaction_date"].max()) if not market.empty else None
        )
        market_model = build_model_frame(market, input_.transaction_type)

        result = valuate(input_, registry, market_model, latest_data_date=latest_data_date)
        result["valuation_id"] = str(uuid.uuid4())
        store.save_with_id(result["valuation_id"], result)
        return jsonify(result), 201

    @app.get("/api/valuations/<valuation_id>")
    def get_valuation(valuation_id: str):
        record = store.get(valuation_id)
        if record is None:
            return jsonify(
                {"error": {"code": "not_found", "message": "估價記錄不存在。", "fields": None}}
            ), 404
        return jsonify(record)

    # ------------------------------------------------------------------
    # Admin API (M4.2)
    # ------------------------------------------------------------------

    @app.post("/api/admin/listing-updates")
    def admin_listing_update():
        unauthorized = _require_trusted_local_post()
        if unauthorized:
            return unauthorized
        if admin_services is None:
            return jsonify(
                {"error": {"code": "admin_unavailable", "message": "管理功能未啟用。"}}
            ), 503
        try:
            request_obj = _parse_listing_update_request()
            submission = admin_services.listing_update_service.submit(request_obj)
        except ApiInputError:
            raise
        except ListingUpdateAlreadyRunning:
            return jsonify(
                {"error": {"code": "already_running", "message": "已有更新工作執行中。"}}
            ), 409
        except Exception:
            return jsonify(
                {"error": {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}}
            ), 503

        if submission.created:
            try:
                admin_services.listing_update_service.handoff(
                    submission, request_obj, admin_services.executor
                )
            except Exception:
                return jsonify(
                    {"error": {"code": "enqueue_failed", "message": "工作無法啟動。"}}
                ), 503
        body = _public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.run.status in {
            "pending",
            "running",
            "retry_wait",
        } else 200

    @app.get("/api/jobs/<run_id>")
    def get_job(run_id: str):
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        effective_job_service = (
            admin_services.job_service
            if admin_services is not None
            else app.extensions.get("qingpu_conversation_job_service")
        )
        if effective_job_service is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503
        try:
            uuid.UUID(run_id)
        except (ValueError, AttributeError):
            return _invalid_request({"run_id": "invalid_uuid"})
        try:
            run = effective_job_service.get(run_id)
        except Exception:
            return jsonify(
                {"error": {"code": "job_unavailable", "message": "工作狀態暫時無法取得。"}}
            ), 503
        if run is None:
            return jsonify({"error": {"code": "not_found", "message": "工作不存在。"}}), 404
        return jsonify(_public_job(run))

    # ------------------------------------------------------------------
    # Model Admin page (M5 / Task 7)
    # ------------------------------------------------------------------

    @app.get("/admin/models")
    def admin_models_page():
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        return redirect("/admin#models")

    # ------------------------------------------------------------------
    # Model Admin API (M5)
    # ------------------------------------------------------------------

    @app.get("/api/admin/models/status")
    def admin_models_status():
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        if admin_services is None or admin_services.model_observatory is None:
            error = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": error}), 503
        return jsonify(admin_services.model_observatory.status())

    @app.get("/api/admin/model-training-runs")
    def admin_model_training_runs():
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        if admin_services is None or admin_services.model_observatory is None:
            error = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": error}), 503
        raw_limit = request.args.get("limit", "20")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return _invalid_request({"limit": "integer_1_to_100"})
        if str(limit) != raw_limit or not 1 <= limit <= 100:
            return _invalid_request({"limit": "integer_1_to_100"})
        try:
            runs = admin_services.model_observatory.list_runs(limit)
        except Exception:
            error = {"code": "admin_unavailable", "message": "工作歷史暫時無法取得。"}
            return jsonify({"error": error}), 503
        return jsonify({"items": runs, "limit": limit})

    @app.get("/api/admin/model-training-runs/<run_id>")
    def admin_model_training_run(run_id: str):
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        if admin_services is None or admin_services.model_observatory is None:
            error = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": error}), 503
        try:
            uuid.UUID(run_id)
        except (ValueError, AttributeError):
            return _invalid_request({"run_id": "invalid_uuid"})
        try:
            run = admin_services.model_observatory.get_run(run_id)
        except Exception:
            error = {"code": "admin_unavailable", "message": "工作狀態暫時無法取得。"}
            return jsonify({"error": error}), 503
        if run is None:
            return jsonify({"error": {"code": "not_found", "message": "工作不存在。"}}), 404
        return jsonify(run)

    @app.post("/api/admin/model-training-runs")
    def admin_model_training_submit():
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅限本機。"}}), 403
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403
        if admin_services is None or admin_services.model_training_service is None:
            error = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": error}), 503
        try:
            request_obj = _parse_model_training_request()
        except ApiInputError:
            raise

        try:
            submission = admin_services.model_training_service.submit(request_obj)
        except Exception:
            error = {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}
            return jsonify({"error": error}), 503

        if submission.created:
            try:
                admin_services.model_training_service.handoff(
                    submission,
                    request_obj,
                    admin_services.executor,
                )
            except Exception:
                error = {"code": "enqueue_failed", "message": "工作無法啟動。"}
                return jsonify({"error": error}), 503

        body = _public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.created else 200

    @app.post("/api/admin/model-training-runs/<run_id>/stop")
    def admin_model_training_stop(run_id: str):
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅限本機。"}}), 403
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403
        if admin_services is None or admin_services.model_training_service is None:
            error = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": error}), 503
        try:
            uuid.UUID(run_id)
        except (ValueError, AttributeError):
            return _invalid_request({"run_id": "invalid_uuid"})
        try:
            run = admin_services.job_service.get(run_id)
        except Exception:
            return jsonify(
                {"error": {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}}
            ), 503
        if run is None:
            return jsonify({"error": {"code": "not_found", "message": "工作不存在。"}}), 404
        if run.job_type != "model_training":
            return jsonify({"error": {"code": "not_found", "message": "工作不存在。"}}), 404
        if run.status not in ACTIVE_STATUSES:
            return jsonify({"error": {"code": "not_stoppable", "message": "此工作無法停止。"}}), 409
        accepted = admin_services.model_training_service.request_stop(run_id)
        if accepted:
            return jsonify({"run_id": run_id, "stop_requested": True}), 202
        return jsonify({"error": {"code": "not_stoppable", "message": "此工作無法停止。"}}), 409

    @app.get("/api/admin/model-training-runs/<run_id>/reports/<report_type>")
    def admin_model_training_report(run_id: str, report_type: str):
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        if admin_services is None or admin_services.model_observatory is None:
            error = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": error}), 503
        try:
            uuid.UUID(run_id)
        except (ValueError, AttributeError):
            return _invalid_request({"run_id": "invalid_uuid"})
        try:
            path = admin_services.model_observatory.report_path(run_id, report_type)
        except ValueError:
            return _invalid_request({"report_type": "not_allowed"})

        if path.suffix == ".joblib":
            return _invalid_request({"report_type": "not_downloadable"})

        try:
            return send_file(path, as_attachment=True, download_name=path.name)
        except FileNotFoundError:
            return jsonify({"error": {"code": "not_found", "message": "報告不存在。"}}), 404

    @app.get("/api/jobs")
    def list_jobs():
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        if admin_services is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503
        raw_limit = request.args.get("limit", "20")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return _invalid_request({"limit": "integer_1_to_100"})
        if str(limit) != raw_limit or not 1 <= limit <= 100:
            return _invalid_request({"limit": "integer_1_to_100"})
        try:
            runs = admin_services.job_service.list_recent(limit)
        except Exception:
            return jsonify(
                {"error": {"code": "job_unavailable", "message": "工作歷史暫時無法取得。"}}
            ), 503
        return jsonify({"items": [_public_job(run) for run in runs], "limit": limit})

    # ------------------------------------------------------------------
    # Ops API (M4.3)
    # ------------------------------------------------------------------

    if ops_services is None and root is not None and os.environ.get("QINGPU_DATABASE_URL"):
        try:
            from qingpu_insight.cli import create_mysql_connection_factory

            factory = create_mysql_connection_factory()
            ops_services = OpsServices(
                health_repository=MySQLHealthRepository(factory),
                backup_repository=MySQLBackupRepository(factory),
            )
        except Exception:
            app.logger.warning("ops services composition failed")

    # ------------------------------------------------------------------
    # Report API (M4.4)
    # ------------------------------------------------------------------

    if report_services is None and root is not None and os.environ.get("QINGPU_DATABASE_URL"):
        try:
            from qingpu_insight.cli import create_mysql_connection_factory

            factory = create_mysql_connection_factory()
            runtime = create_report_runtime(factory, root, os.environ)
            report_services = ReportServices(
                service=runtime.service,
                repository=runtime.repository,
            )
        except Exception:
            app.logger.warning("report services composition failed")

    if report_services is None and report_service is not None and report_repository is not None:
        report_services = ReportServices(service=report_service, repository=report_repository)

    app.extensions["qingpu_report_services"] = report_services

    # ------------------------------------------------------------------
    # Dashboard / readiness service (M4.2)
    # ------------------------------------------------------------------

    dashboard_service: AdminDashboardService | None = None
    if root is not None:
        try:
            _connection_factory = None
            if os.environ.get("QINGPU_DATABASE_URL"):
                from qingpu_insight.cli import create_mysql_connection_factory

                _connection_factory = create_mysql_connection_factory()
            dashboard_service = _create_admin_dashboard_service(
                root,
                _connection_factory,
                admin_services,
                ops_services,
            )
        except Exception:
            app.logger.warning("dashboard service composition failed")

    if dashboard_service is not None:
        from dataclasses import replace

        admin_runtime = replace(admin_runtime, dashboard_service=dashboard_service)
        if "qingpu_admin_runtime" in app.extensions:
            app.extensions["qingpu_admin_runtime"] = admin_runtime

    _REPORT_SEMAPHORE = BoundedSemaphore(1)

    def _require_trusted_local_post():
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅限本機。"}}), 403
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403
        return None

    _REPORT_ALLOWED_FIELDS = frozenset({"candidate_ids", "budget_twd", "intended_use", "provider"})

    def _parse_report_request() -> dict:
        if request.mimetype != "application/json":
            raise ApiInputError("Request body must be JSON.", {"body": "application_json"})
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ApiInputError("Request body must be a JSON object.", {"body": "object"})

        fields: dict[str, str] = {}

        extra = set(payload.keys()) - _REPORT_ALLOWED_FIELDS
        if extra:
            for k in extra:
                fields[k] = "not_allowed"

        candidate_ids = payload.get("candidate_ids")
        if not isinstance(candidate_ids, list) or not candidate_ids:
            fields["candidate_ids"] = "required"
        elif len(candidate_ids) > 5:
            fields["candidate_ids"] = "max_5"
        elif not all(isinstance(c, str) for c in candidate_ids):
            fields["candidate_ids"] = "string_items"

        provider = payload.get("provider")
        if not provider:
            fields["provider"] = "required"
        elif provider not in ("rule", "ollama", "gemini"):
            fields["provider"] = "unsupported"

        intended_use = payload.get("intended_use")
        if not intended_use:
            fields["intended_use"] = "required"
        elif intended_use not in ("self_use", "rental_reference"):
            fields["intended_use"] = "unsupported"

        budget_twd = payload.get("budget_twd")
        if budget_twd is not None and (not isinstance(budget_twd, int) or budget_twd < 0):
            fields["budget_twd"] = "invalid"

        if fields:
            raise ApiInputError("Request validation failed.", fields)

        return {
            "candidate_ids": tuple(candidate_ids),
            "budget_twd": budget_twd,
            "intended_use": intended_use,
            "provider": provider,
        }

    @app.post("/api/reports")
    def create_report():
        unauthorized = _require_trusted_local_post()
        if unauthorized:
            return unauthorized
        if report_services is None:
            return jsonify(
                {"error": {"code": "report_unavailable", "message": "報告功能未啟用。"}}
            ), 503

        try:
            parsed = _parse_report_request()
        except ApiInputError:
            raise

        from qingpu_insight.report_contracts import ReportRequest

        try:
            request = ReportRequest(**parsed)
        except Exception as exc:
            return jsonify(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "Request validation failed.",
                        "fields": {"_schema": str(exc)},
                    }
                }
            ), 400

        if not _REPORT_SEMAPHORE.acquire(blocking=False):
            return jsonify({"error": {"code": "report_busy", "message": "已有報告正在產生。"}}), 429
        try:
            saved = report_services.service.generate(request)
        except UnknownCandidateError:
            return jsonify(
                {"error": {"code": "candidate_not_found", "message": "找不到指定物件。"}}
            ), 404
        except Exception:
            return jsonify({"error": {"code": "report_failed", "message": "報告產生失敗。"}}), 503
        finally:
            _REPORT_SEMAPHORE.release()

        return jsonify(
            {
                "report_id": saved.report_id,
                "provider": saved.provider,
                "model": saved.model,
                "dataset_version": saved.dataset_version,
                "evidence_pack_id": saved.evidence_pack_id,
                "fallback_reason": saved.fallback_reason,
                "content": saved.content,
                "created_at": saved.created_at,
            }
        ), 201

    @app.get("/api/reports/<report_id>")
    def get_report(report_id: str):
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        if report_services is None:
            return jsonify(
                {"error": {"code": "report_unavailable", "message": "報告功能未啟用。"}}
            ), 503

        try:
            record = report_services.repository.get(report_id)
        except CorruptReportError:
            return jsonify({"error": {"code": "report_corrupt", "message": "報告已毀損。"}}), 503
        if record is None:
            return jsonify({"error": {"code": "not_found", "message": "報告不存在。"}}), 404

        return jsonify(
            {
                "report_id": record.report_id,
                "provider": record.provider,
                "model": record.model,
                "dataset_version": record.dataset_version,
                "evidence_pack_id": record.evidence_pack_id,
                "fallback_reason": record.fallback_reason,
                "content": record.content,
                "created_at": record.created_at,
            }
        )

    @app.get("/api/ops/health")
    def ops_health():
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        if ops_services is None or ops_services.health_repository is None:
            return jsonify(
                {"error": {"code": "ops_unavailable", "message": "維運功能未啟用。"}}
            ), 503
        try:
            latest = ops_services.health_repository.latest()
            if latest is None:
                return jsonify(
                    {"error": {"code": "no_health_data", "message": "尚無健康檢查記錄。"}}
                ), 404
            return jsonify(
                {
                    "status": latest.status,
                    "checked_at": latest.checked_at.isoformat(),
                    "items": [
                        {
                            "code": item.code,
                            "status": item.status,
                            "summary": item.summary,
                            "value": item.value,
                            "unit": item.unit,
                        }
                        for item in latest.items
                    ],
                }
            )
        except Exception:
            return jsonify(
                {"error": {"code": "ops_unavailable", "message": "維運功能暫時無法使用。"}}
            ), 503

    @app.get("/api/ops/backups")
    def ops_backups():
        if not _is_trusted_local_request():
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        if ops_services is None or ops_services.backup_repository is None:
            return jsonify(
                {"error": {"code": "ops_unavailable", "message": "維運功能未啟用。"}}
            ), 503
        raw_limit = request.args.get("limit", "20")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return _invalid_request({"limit": "integer_1_to_100"})
        if str(limit) != raw_limit or not 1 <= limit <= 100:
            return _invalid_request({"limit": "integer_1_to_100"})
        try:
            records = ops_services.backup_repository.list_recent(limit)
        except Exception:
            return jsonify(
                {"error": {"code": "ops_unavailable", "message": "備份記錄暫時無法取得。"}}
            ), 503
        return jsonify(
            {
                "items": [
                    {
                        "backup_id": r.backup_id,
                        "status": r.status,
                        "sha256": r.sha256,
                        "size_bytes": r.size_bytes,
                        "created_at": r.created_at.isoformat(),
                        "restore_status": r.restore_status,
                        "restore_checked_at": (
                            r.restore_checked_at.isoformat() if r.restore_checked_at else None
                        ),
                    }
                    for r in records
                ],
                "limit": limit,
            }
        )

    @app.post("/api/ops/backups")
    def ops_backups_post():
        rt = app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.backup_service is None or rt.executor is None:
            return jsonify(
                {"error": {"code": "ops_unavailable", "message": "維運功能未啟用。"}}
            ), 503
        try:
            submission = rt.backup_service.submit_create()
        except Exception:
            return jsonify(
                {"error": {"code": "ops_unavailable", "message": "維運功能暫時無法使用。"}}
            ), 503
        if submission.created:
            try:
                rt.executor.submit(
                    submission.run.run_id,
                    lambda: rt.backup_service.execute_create(submission.run.run_id),
                )
            except Exception:
                return jsonify(
                    {"error": {"code": "enqueue_failed", "message": "工作無法啟動。"}}
                ), 503
        body = _public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.created else 200

    @app.post("/api/ops/backups/<backup_id>/restore-drills")
    def ops_restore_drill(backup_id: str):
        rt = app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.backup_service is None or rt.executor is None:
            return jsonify(
                {"error": {"code": "ops_unavailable", "message": "維運功能未啟用。"}}
            ), 503
        try:
            submission = rt.backup_service.submit_restore_drill(backup_id)
        except Exception:
            return jsonify(
                {"error": {"code": "ops_unavailable", "message": "維運功能暫時無法使用。"}}
            ), 503
        if submission.created:
            try:
                rt.executor.submit(
                    submission.run.run_id,
                    lambda: rt.backup_service.execute_restore_drill(
                        submission.run.run_id,
                        backup_id,
                    ),
                )
            except Exception:
                return jsonify(
                    {"error": {"code": "enqueue_failed", "message": "工作無法啟動。"}}
                ), 503
        body = _public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.created else 200

    @app.route("/api/ops/restore", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def ops_restore():
        return jsonify({"error": {"code": "not_found", "message": "路由不存在。"}}), 404

    return app


def _create_runtime_app(root: Path) -> Flask:
    load_dotenv(root / ".env", override=False)

    from qingpu_insight.cli import create_listing_repository

    listing_repo = create_listing_repository(root)
    return create_app(root=root, listing_repo=listing_repo)


def main() -> None:
    port = int(os.environ.get("QINGPU_PORT", "5000"))
    debug = os.environ.get("QINGPU_DEBUG", "") == "1"
    app = _create_runtime_app(Path.cwd())
    try:
        app.run(host="127.0.0.1", port=port, debug=debug)
    finally:
        app.extensions["qingpu_admin_shutdown"]()


if __name__ == "__main__":
    main()
