"""Local job center APIs: listing updates, job status and model training runs.

All routes are trusted-local; mutations also need the CSRF token (see
:mod:`qingpu_insight.web_routes.guards`).
"""

from __future__ import annotations

import re
import uuid

from flask import Blueprint, current_app, jsonify, redirect, request, send_file

from qingpu_insight.api_errors import ApiInputError
from qingpu_insight.jobs import ACTIVE_STATUSES, JobRun, redact_job_message
from qingpu_insight.listing_update import ListingUpdateAlreadyRunning, ListingUpdateRequest
from qingpu_insight.web_composition import AdminServices
from qingpu_insight.web_routes.errors import invalid_request, parse_limit
from qingpu_insight.web_routes.guards import LOCAL_ONLY_LEGACY_MUTATION, guarded_blueprint


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
    elif raw_markets != ["resale"]:
        fields["markets"] = "supported_values"

    market_order = ("resale",)
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


def public_job(run: JobRun) -> dict[str, object]:
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



def _admin_unavailable(message: str = "管理功能未啟用。"):
    return jsonify({"error": {"code": "admin_unavailable", "message": message}}), 503


def _not_found():
    return jsonify({"error": {"code": "not_found", "message": "工作不存在。"}}), 404


def _valid_run_id(run_id: str) -> bool:
    try:
        uuid.UUID(run_id)
    except (ValueError, AttributeError):
        return False
    return True


def create_jobs_blueprint(admin_services: AdminServices | None) -> Blueprint:
    bp = guarded_blueprint("jobs", __name__, default_policy=LOCAL_ONLY_LEGACY_MUTATION)

    @bp.post("/api/admin/listing-updates")
    def admin_listing_update():
        if admin_services is None:
            return _admin_unavailable()
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
            return _admin_unavailable("管理功能暫時無法使用。")

        if submission.created:
            try:
                admin_services.listing_update_service.handoff(
                    submission, request_obj, admin_services.executor
                )
            except Exception:
                return jsonify(
                    {"error": {"code": "enqueue_failed", "message": "工作無法啟動。"}}
                ), 503
        body = public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.run.status in {
            "pending",
            "running",
            "retry_wait",
        } else 200

    @bp.get("/api/jobs/<run_id>")
    def get_job(run_id: str):
        effective_job_service = (
            admin_services.job_service
            if admin_services is not None
            else current_app.extensions.get("qingpu_conversation_job_service")
        )
        if effective_job_service is None:
            return _admin_unavailable()
        if not _valid_run_id(run_id):
            return invalid_request({"run_id": "invalid_uuid"})
        try:
            run = effective_job_service.get(run_id)
        except Exception:
            return jsonify(
                {"error": {"code": "job_unavailable", "message": "工作狀態暫時無法取得。"}}
            ), 503
        if run is None:
            return _not_found()
        return jsonify(public_job(run))

    @bp.get("/api/jobs")
    def list_jobs():
        if admin_services is None:
            return _admin_unavailable()
        limit = parse_limit(request.args.get("limit", "20"))
        if limit is None:
            return invalid_request({"limit": "integer_1_to_100"})
        try:
            runs = admin_services.job_service.list_recent(limit)
        except Exception:
            return jsonify(
                {"error": {"code": "job_unavailable", "message": "工作歷史暫時無法取得。"}}
            ), 503
        return jsonify({"items": [public_job(run) for run in runs], "limit": limit})

    @bp.get("/admin/models")
    def admin_models_page():
        return redirect("/admin#models")

    def _observatory():
        if admin_services is None:
            return None
        return admin_services.model_observatory

    def _training_service():
        if admin_services is None:
            return None
        return admin_services.model_training_service

    @bp.get("/api/admin/models/status")
    def admin_models_status():
        observatory = _observatory()
        if observatory is None:
            return _admin_unavailable()
        return jsonify(observatory.status())

    @bp.get("/api/admin/model-training-runs")
    def admin_model_training_runs():
        observatory = _observatory()
        if observatory is None:
            return _admin_unavailable()
        limit = parse_limit(request.args.get("limit", "20"))
        if limit is None:
            return invalid_request({"limit": "integer_1_to_100"})
        try:
            runs = observatory.list_runs(limit)
        except Exception:
            return _admin_unavailable("工作歷史暫時無法取得。")
        return jsonify({"items": runs, "limit": limit})

    @bp.get("/api/admin/model-training-runs/<run_id>")
    def admin_model_training_run(run_id: str):
        observatory = _observatory()
        if observatory is None:
            return _admin_unavailable()
        if not _valid_run_id(run_id):
            return invalid_request({"run_id": "invalid_uuid"})
        try:
            run = observatory.get_run(run_id)
        except Exception:
            return _admin_unavailable("工作狀態暫時無法取得。")
        if run is None:
            return _not_found()
        return jsonify(run)

    @bp.post("/api/admin/model-training-runs")
    def admin_model_training_submit():
        training_service = _training_service()
        if training_service is None:
            return _admin_unavailable()
        request_obj = _parse_model_training_request()
        try:
            submission = training_service.submit(request_obj)
        except Exception:
            return _admin_unavailable("管理功能暫時無法使用。")

        if submission.created:
            try:
                training_service.handoff(submission, request_obj, admin_services.executor)
            except Exception:
                error = {"code": "enqueue_failed", "message": "工作無法啟動。"}
                return jsonify({"error": error}), 503

        body = public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.created else 200

    @bp.post("/api/admin/model-training-runs/<run_id>/stop")
    def admin_model_training_stop(run_id: str):
        training_service = _training_service()
        if training_service is None:
            return _admin_unavailable()
        if not _valid_run_id(run_id):
            return invalid_request({"run_id": "invalid_uuid"})
        try:
            run = admin_services.job_service.get(run_id)
        except Exception:
            return _admin_unavailable("管理功能暫時無法使用。")
        if run is None or run.job_type != "model_training":
            return _not_found()
        not_stoppable = {"error": {"code": "not_stoppable", "message": "此工作無法停止。"}}
        if run.status not in ACTIVE_STATUSES:
            return jsonify(not_stoppable), 409
        if training_service.request_stop(run_id):
            return jsonify({"run_id": run_id, "stop_requested": True}), 202
        return jsonify(not_stoppable), 409

    @bp.get("/api/admin/model-training-runs/<run_id>/reports/<report_type>")
    def admin_model_training_report(run_id: str, report_type: str):
        observatory = _observatory()
        if observatory is None:
            return _admin_unavailable()
        if not _valid_run_id(run_id):
            return invalid_request({"run_id": "invalid_uuid"})
        if not report_type.startswith("resale-"):
            return invalid_request({"report_type": "not_allowed"})
        try:
            path = observatory.report_path(run_id, report_type)
        except ValueError:
            return invalid_request({"report_type": "not_allowed"})

        if path.suffix == ".joblib":
            return invalid_request({"report_type": "not_downloadable"})

        try:
            return send_file(path, as_attachment=True, download_name=path.name)
        except FileNotFoundError:
            return jsonify({"error": {"code": "not_found", "message": "報告不存在。"}}), 404

    return bp
