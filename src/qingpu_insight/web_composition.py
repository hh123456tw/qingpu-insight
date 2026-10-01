"""Build the services behind the web app from the environment or injected fakes.

:func:`qingpu_insight.web.create_app` calls these helpers and hands the results to the
blueprints in :mod:`qingpu_insight.web_routes`. Production services need
``QINGPU_DATABASE_URL`` (and, for the job center, a strong ``QINGPU_SECRET_KEY``);
anything that cannot be built is logged and left unavailable so its routes answer 503.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from flask import Flask

from qingpu_insight.admin_dashboard import AdminDashboardService, ReadinessItem
from qingpu_insight.admin_web import ADMIN_JOB_TYPES
from qingpu_insight.backup_repository import MySQLBackupRepository
from qingpu_insight.config import get_settings
from qingpu_insight.conversation_valuation import listing_market_comparables, valuate_listing
from qingpu_insight.health import HealthService
from qingpu_insight.health_repository import MySQLHealthRepository
from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import JobService
from qingpu_insight.listing_update import ListingUpdateService
from qingpu_insight.llm_model_catalog import LlmModelCatalog
from qingpu_insight.local_secrets import LocalSecretsStore
from qingpu_insight.market_repository import MarketDataSource
from qingpu_insight.market_snapshot import ModelFrameCache
from qingpu_insight.official_data import (
    OfficialDataUpdateService,
    ProductionOfficialDataRunner,
)
from qingpu_insight.provider_ops import ProviderOpsService
from qingpu_insight.report_composition import create_report_runtime
from qingpu_insight.valuation import ModelRegistry
from qingpu_insight.web_benchmark_runner import ConfiguredWebBenchmarkRunner


def _parse_mysql_url_to_config() -> SimpleNamespace:
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
    listing_radar_service: object | None = None


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


def _latest_market_date(input_path: Path) -> pd.Timestamp | None:
    if not input_path.exists():
        return None
    dates = pd.read_parquet(input_path, columns=["transaction_date"])["transaction_date"]
    return pd.Timestamp(dates.max()) if not dates.empty else None


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
    )
    from qingpu_insight.source_version import GitSourceVersionProvider

    settings = get_settings(root)
    input_path = settings.processed_dir / "market_transactions.parquet"
    candidate_store = CandidateArtifactStore(root / "candidates")
    automl_registry = AutoMLControlRegistry()
    automl_output_store = AutoMLRunOutputStore(root)
    mts = ModelTrainingService(
        service.job_service,
        candidate_store,
        input_path,
        GitSourceVersionProvider(root),
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
        latest_data_date=lambda: _latest_market_date(input_path),
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

    from qingpu_insight.listing_radar_runtime import ListingRadarJobService, run_listing_radar

    radar_service = ListingRadarJobService(
        service.job_service,
        lambda options, progress=None: run_listing_radar(root, options, progress=progress),
    )

    return AdminServices(
        job_service=service.job_service,
        listing_update_service=service,
        executor=executor,
        model_training_service=mts,
        model_observatory=observatory,
        official_data_service=official_service,
        model_release_service=model_release_service,
        backup_job_service=_backup_job_svc,
        listing_radar_service=radar_service,
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


def compose_admin_services(
    app: Flask,
    root: Path | None,
    configured_secret: str | None,
    admin_services: AdminServices | None,
    job_service: JobService | None,
    listing_update_service: ListingUpdateService | None,
    job_executor: LocalJobExecutor | None,
) -> AdminServices | None:
    """Injected bundle, legacy injected trio, or production services behind a strong secret."""
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
    return admin_services


@dataclass(frozen=True)
class ProviderRuntime:
    """LLM provider settings that follow the local secrets file at request time."""

    secrets_store: LocalSecretsStore | None
    provider_ops_service: ProviderOpsService | None
    llm_model_catalog: LlmModelCatalog

    def runtime_env(self, name: str, default: str = "") -> str:
        current = (
            self.secrets_store.merged_env(os.environ)
            if self.secrets_store is not None
            else dict(os.environ)
        )
        return current.get(name, default)

    def ollama_base_url(self) -> str:
        return self.runtime_env("QINGPU_OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    def gemini_api_key(self) -> str | None:
        if self.secrets_store is None:
            return os.environ.get("QINGPU_GEMINI_API_KEY")
        return self.secrets_store.merged_env(os.environ).get("QINGPU_GEMINI_API_KEY")

    def gemini_configured(self) -> bool:
        return bool(self.runtime_env("QINGPU_GEMINI_API_KEY"))


def compose_provider_runtime(root: Path | None) -> ProviderRuntime:
    secrets_store: LocalSecretsStore | None = None
    provider_ops_service: ProviderOpsService | None = None
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

    # The catalog and benchmark runner read the runtime env lazily through this object.
    holder: dict[str, ProviderRuntime] = {}

    def runtime() -> ProviderRuntime:
        return holder["runtime"]

    llm_model_catalog = LlmModelCatalog(
        ollama_base_url_getter=lambda: runtime().ollama_base_url(),
        gemini_configured_getter=lambda: runtime().gemini_configured(),
    )
    holder["runtime"] = ProviderRuntime(
        secrets_store=secrets_store,
        provider_ops_service=provider_ops_service,
        llm_model_catalog=llm_model_catalog,
    )
    if provider_ops_service is not None:
        provider_ops_service.set_benchmark_runner(
            ConfiguredWebBenchmarkRunner(
                ollama_base_url_getter=lambda: runtime().ollama_base_url(),
                gemini_api_key_getter=lambda: runtime().runtime_env("QINGPU_GEMINI_API_KEY"),
            )
        )
    return holder["runtime"]


@dataclass(frozen=True)
class ConversationRuntime:
    service: object | None
    repository: object | None
    job_service: object | None
    executor: LocalJobExecutor | None
    owned_executor: LocalJobExecutor | None


def compose_conversation_runtime(
    app: Flask,
    root: Path | None,
    data_source: MarketDataSource | None,
    registry: ModelRegistry,
    admin_services: AdminServices | None,
    providers_runtime: ProviderRuntime,
    conversation_repository: object | None,
    conversation_service: object | None,
    *,
    snapshots: ModelFrameCache | None = None,
) -> ConversationRuntime:
    conv_repo = conversation_repository
    conv_service = conversation_service
    job_service = admin_services.job_service if admin_services is not None else None
    owned_executor: LocalJobExecutor | None = None
    if (
        root is None
        or not os.environ.get("QINGPU_DATABASE_URL")
        or (conv_repo is not None and conv_service is not None)
    ):
        return ConversationRuntime(conv_service, conv_repo, job_service, None, None)
    try:
        from qingpu_insight.cli import create_mysql_connection_factory
        from qingpu_insight.conversation_evidence import ConversationEvidenceBuilder
        from qingpu_insight.conversation_fallback import ConversationFallbackExecutor
        from qingpu_insight.conversation_import import ConversationImportService
        from qingpu_insight.conversation_listing_capture import DetailPageBrowser
        from qingpu_insight.conversation_providers import (
            ConversationProviderRegistry,
            GeminiConversationProvider,
            OllamaConversationProvider,
            RuleConversationProvider,
        )
        from qingpu_insight.conversation_repository import MySQLConversationRepository
        from qingpu_insight.conversation_service import ConversationService
        from qingpu_insight.conversation_validation import validate_chat_answer
        from qingpu_insight.job_repository import MySQLJobRepository
        from qingpu_insight.listing_capture import ChromeConfig, create_chrome

        connection_factory = create_mysql_connection_factory()
        _ensure_conversation_schema(root, connection_factory)
        conv_repo = conv_repo or MySQLConversationRepository(connection_factory)
        conversation_jobs = (
            admin_services.job_service
            if admin_services is not None
            else JobService(MySQLJobRepository(connection_factory))
        )
        job_service = conversation_jobs
        owned_executor = LocalJobExecutor(conversation_jobs)
        browser = DetailPageBrowser(
            driver_factory=lambda: create_chrome(ChromeConfig(headless=False))
        )
        market_service = (
            (lambda payload: listing_market_comparables(data_source, payload))
            if data_source is not None
            else None
        )
        valuation_service = (
            (lambda payload: valuate_listing(data_source, registry, payload, snapshots=snapshots))
            if data_source is not None
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
            OllamaConversationProvider(providers_runtime.ollama_base_url()),
        )
        providers.register(
            "gemini",
            GeminiConversationProvider(providers_runtime.gemini_api_key),
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
            executor=owned_executor,
        )
    except Exception:
        app.logger.error("conversation runtime unavailable")
        conv_repo = None
        conv_service = None
    return ConversationRuntime(conv_service, conv_repo, job_service, owned_executor, owned_executor)


def compose_ops_services(
    app: Flask, root: Path | None, ops_services: OpsServices | None
) -> OpsServices | None:
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
    return ops_services


def compose_report_services(
    app: Flask,
    root: Path | None,
    report_services: ReportServices | None,
    report_service: object | None,
    report_repository: object | None,
) -> ReportServices | None:
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
    return report_services


def compose_dashboard_service(
    app: Flask,
    root: Path | None,
    admin_services: AdminServices | None,
    ops_services: OpsServices | None,
) -> AdminDashboardService | None:
    if root is None:
        return None
    try:
        connection_factory = None
        if os.environ.get("QINGPU_DATABASE_URL"):
            from qingpu_insight.cli import create_mysql_connection_factory

            connection_factory = create_mysql_connection_factory()
        return _create_admin_dashboard_service(
            root,
            connection_factory,
            admin_services,
            ops_services,
        )
    except Exception:
        app.logger.warning("dashboard service composition failed")
        return None
