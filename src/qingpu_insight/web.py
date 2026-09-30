"""Flask app factory and ``qingpu-web`` entry point.

``create_app`` only composes: services come from :mod:`qingpu_insight.web_composition`
(or are injected by tests), routes live in the blueprints under
:mod:`qingpu_insight.web_routes` plus :mod:`qingpu_insight.admin_web` and
:mod:`qingpu_insight.conversation_web`.
"""

from __future__ import annotations

import os
import secrets
import uuid
from dataclasses import replace
from pathlib import Path
from threading import Lock, Thread

from dotenv import load_dotenv
from flask import Flask, session

from qingpu_insight.address_location import AddressLocator, LazyDoorplateIndex
from qingpu_insight.admin_web import AdminRuntime, create_admin_blueprint
from qingpu_insight.conversation_models import public_model_catalog
from qingpu_insight.conversation_web import create_conversation_blueprint
from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import JobService
from qingpu_insight.listing_repository import ListingRepository
from qingpu_insight.listing_update import ListingUpdateService
from qingpu_insight.market_repository import MarketDataSource, repository_from_env
from qingpu_insight.market_snapshot import ModelFrameCache
from qingpu_insight.valuation import ModelRegistry
from qingpu_insight.valuation_store import DEFAULT_MAX_VALUATION_RECORDS, FileValuationStore
from qingpu_insight.web_composition import (
    AdminServices,
    OpsServices,
    ReportServices,
    _UnavailableMarketDataSource,
    compose_admin_services,
    compose_conversation_runtime,
    compose_dashboard_service,
    compose_ops_services,
    compose_provider_runtime,
    compose_report_services,
)
from qingpu_insight.web_routes.errors import json_default, register_error_handlers
from qingpu_insight.web_routes.jobs import create_jobs_blueprint
from qingpu_insight.web_routes.market import create_market_blueprint
from qingpu_insight.web_routes.ops import create_ops_blueprint
from qingpu_insight.web_routes.pages import create_pages_blueprint
from qingpu_insight.web_routes.reports import create_reports_blueprint
from qingpu_insight.web_routes.valuation import create_valuation_blueprint

__all__ = ["AdminServices", "OpsServices", "ReportServices", "create_app", "main"]


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
    address_locator: AddressLocator | None = None,
) -> Flask:
    app = Flask(__name__)
    app.json.default = json_default
    configured_secret = os.environ.get("QINGPU_SECRET_KEY")
    app.secret_key = configured_secret or secrets.token_hex(32)

    if data_source is None and root is not None:
        try:
            data_source = repository_from_env(root)
        except Exception:
            app.logger.error("market data composition unavailable")
            data_source = _UnavailableMarketDataSource()

    store = valuation_store or FileValuationStore(
        Path.cwd() / "outputs" / "valuations", max_records=DEFAULT_MAX_VALUATION_RECORDS
    )
    registry = model_registry or ModelRegistry(Path.cwd() / "artifacts")
    if address_locator is None:
        address_locator = LazyDoorplateIndex(
            (root or Path.cwd()) / "data" / "raw" / "doorplates.csv"
        )
    app.extensions["qingpu_address_locator"] = address_locator

    admin_services = compose_admin_services(
        app,
        root,
        configured_secret,
        admin_services,
        job_service,
        listing_update_service,
        job_executor,
    )
    # One model frame per market data version, shared by the form and the assistant.
    snapshots = ModelFrameCache(data_source)
    providers = compose_provider_runtime(root)
    conversation = compose_conversation_runtime(
        app,
        root,
        data_source,
        registry,
        admin_services,
        providers,
        conversation_repository,
        conversation_service,
        snapshots=snapshots,
    )
    ops_services = compose_ops_services(app, root, ops_services)
    report_services = compose_report_services(
        app, root, report_services, report_service, report_repository
    )
    dashboard_service = compose_dashboard_service(app, root, admin_services, ops_services)

    shutdown_lock = Lock()
    shutdown_complete = False

    def shutdown_admin() -> None:
        nonlocal shutdown_complete
        with shutdown_lock:
            if shutdown_complete:
                return
            shutdown_complete = True
        if admin_services is not None:
            admin_services.executor.shutdown(wait=True)
        if conversation.owned_executor is not None:
            conversation.owned_executor.shutdown(wait=True)

    app.extensions["qingpu_admin_services"] = admin_services
    app.extensions["qingpu_admin_shutdown"] = shutdown_admin
    app.extensions["qingpu_conversation_service"] = conversation.service
    app.extensions["qingpu_conversation_repository"] = conversation.repository
    app.extensions["qingpu_conversation_job_service"] = conversation.job_service
    app.extensions["qingpu_conversation_executor"] = conversation.executor
    app.extensions["qingpu_report_services"] = report_services

    admin_runtime = AdminRuntime(
        job_service=admin_services.job_service if admin_services else None,
        executor=admin_services.executor if admin_services else None,
        provider_ops_service=providers.provider_ops_service,
        secrets_store=providers.secrets_store,
        llm_model_catalog=providers.llm_model_catalog,
        root=root,
    )
    if admin_services is not None:
        admin_runtime = replace(
            admin_runtime,
            listing_update_service=admin_services.listing_update_service,
            model_training_service=admin_services.model_training_service,
            model_observatory=admin_services.model_observatory,
            official_data_service=admin_services.official_data_service,
            model_release_service=admin_services.model_release_service,
            backup_service=admin_services.backup_job_service,
        )
    if dashboard_service is not None:
        admin_runtime = replace(admin_runtime, dashboard_service=dashboard_service)

    @app.before_request
    def ensure_session():
        if "_csrf_token" not in session:
            session["_csrf_token"] = str(uuid.uuid4())

    register_error_handlers(app)
    app.register_blueprint(create_admin_blueprint(admin_runtime))
    app.register_blueprint(
        create_conversation_blueprint(
            conversation.service,
            conversation.repository,
            catalog_getter=lambda: public_model_catalog(
                gemini_configured=providers.gemini_configured(),
                ollama_ready=providers.llm_model_catalog.ollama_model_ready("gemma4:e2b"),
            ),
        )
    )
    app.register_blueprint(create_pages_blueprint())
    app.register_blueprint(create_market_blueprint(data_source, listing_repo))
    app.register_blueprint(
        create_valuation_blueprint(snapshots, registry, store, address_locator)
    )
    app.register_blueprint(create_jobs_blueprint(admin_services))
    app.register_blueprint(create_reports_blueprint(report_services))
    app.register_blueprint(create_ops_blueprint(ops_services))
    return app


def _create_runtime_app(root: Path) -> Flask:
    load_dotenv(root / ".env", override=False)

    from qingpu_insight.cli import create_listing_repository

    listing_repo = create_listing_repository(root)
    locator = LazyDoorplateIndex(root / "data" / "raw" / "doorplates.csv")
    # Building the doorplate index takes seconds; do it before the first address.
    Thread(target=locator.warm, name="doorplate-index-warmup", daemon=True).start()
    return create_app(root=root, listing_repo=listing_repo, address_locator=locator)


def main() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    port = int(os.environ.get("QINGPU_PORT", "5000"))
    debug = os.environ.get("QINGPU_DEBUG", "") == "1"
    app = _create_runtime_app(Path.cwd())
    try:
        app.run(host="127.0.0.1", port=port, debug=debug)
    finally:
        app.extensions["qingpu_admin_shutdown"]()


if __name__ == "__main__":
    main()
