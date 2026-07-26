from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, render_template, request, session


@dataclass(frozen=True)
class AdminRuntime:
    job_service: object | None
    executor: object | None
    listing_update_service: object | None = None
    model_training_service: object | None = None
    model_observatory: object | None = None
    dashboard_service: object | None = None
    official_data_service: object | None = None
    model_release_service: object | None = None
    backup_service: object | None = None
    preview_service: object | None = None
    provider_ops_service: object | None = None
    secrets_store: object | None = None


ADMIN_JOB_TYPES = frozenset({
    "official_data_update", "listing_update", "model_training",
    "model_release", "backup_create", "restore_drill",
    "database_restore", "provider_smoke", "llm_benchmark",
})


def _admin_public_job(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "job_type": run.job_type,
        "status": run.status,
        "trigger": (
            run.trigger
            if run.trigger in {"manual", "scheduled", "web"}
            and len(run.trigger) <= 32
            else "redacted"
        ),
        "attempt": run.attempt,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "input_version": run.input_version,
        "output_version": run.output_version,
        "summary": run.summary,
        "error_code": run.error_code,
        "error_message": run.error_message,
    }


def create_admin_blueprint(runtime: AdminRuntime) -> Blueprint:
    bp = Blueprint("admin", __name__, url_prefix="")

    @bp.record_once
    def _store_runtime(state):
        state.app.extensions["qingpu_admin_runtime"] = runtime

    @bp.before_request
    def _restrict_to_local():
        try:
            remote_is_loopback = ip_address(request.remote_addr or "").is_loopback
            hostname = urlsplit(f"//{request.host}").hostname
        except ValueError:
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
        if not (remote_is_loopback and (hostname or "").lower() in {
            "localhost", "127.0.0.1", "::1",
        }):
            return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403

    @bp.get("/admin/", strict_slashes=False)
    def admin_index():
        return render_template("admin.html", csrf_token=session.get("_csrf_token", ""))

    @bp.get("/api/admin/overview")
    def admin_overview():
        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.dashboard_service is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503
        try:
            return jsonify(rt.dashboard_service.read())
        except Exception:
            err = {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}
            return jsonify({"error": err}), 503

    @bp.get("/api/admin/jobs")
    def admin_jobs():
        raw_limit = request.args.get("limit", "20")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": {"limit": "integer_1_to_100"}}
            return jsonify({"error": err}), 400
        if str(limit) != raw_limit or not 1 <= limit <= 100:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": {"limit": "integer_1_to_100"}}
            return jsonify({"error": err}), 400
        job_type = request.args.get("job_type")
        if job_type is not None and job_type not in ADMIN_JOB_TYPES:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": {"job_type": "unsupported"}}
            return jsonify({"error": err}), 400
        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.job_service is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503
        try:
            runs = rt.job_service.list_recent(limit, job_type)
        except Exception:
            err = {"code": "job_unavailable", "message": "工作歷史暫時無法取得。"}
            return jsonify({"error": err}), 503
        items = []
        for run in runs:
            item = _admin_public_job(run)
            if run.status == "pending":
                item["display_status"] = "queued"
            elif run.status == "failed" and run.error_code == "worker_interrupted":
                item["display_status"] = "interrupted"
            else:
                item["display_status"] = run.status
            item["info_url"] = f"/api/jobs/{run.run_id}"
            items.append(item)
        return jsonify({"items": items, "limit": limit})

    return bp
