from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, render_template, request, session

from qingpu_insight.official_data import _season_key


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
    root: object | None = None


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

    @bp.post("/api/admin/official-data-updates")
    def admin_official_data_update():
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.official_data_service is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

        if request.mimetype != "application/json":
            err = {"code": "invalid_request", "message": "Request body must be JSON.",
                   "fields": {"body": "application_json"}}
            return jsonify({"error": err}), 400
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            err = {"code": "invalid_request", "message": "Request body must be a JSON object.",
                   "fields": {"body": "object"}}
            return jsonify({"error": err}), 400

        _ALLOWED_FIELDS = frozenset({"start_season", "end_season", "start_at"})
        fields: dict[str, str] = {}
        extra = set(payload.keys()) - _ALLOWED_FIELDS
        for k in extra:
            fields[k] = "not_allowed"

        start_season = payload.get("start_season", "")
        end_season = payload.get("end_season", "")
        if not start_season:
            fields["start_season"] = "required"
        else:
            try:
                _season_key(start_season)
            except ValueError:
                fields["start_season"] = "invalid"

        if not end_season:
            fields["end_season"] = "required"
        else:
            try:
                _season_key(end_season)
            except ValueError:
                fields["end_season"] = "invalid"

        start_at = payload.get("start_at", "acquire")
        if start_at not in {"acquire", "analyse", "market_build", "mysql_publish"}:
            fields["start_at"] = "unsupported"

        if fields:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        if rt.dashboard_service is not None:
            try:
                dashboard = rt.dashboard_service.read()
            except Exception:
                err = {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}
                return jsonify({"error": err}), 503
            if not dashboard.get("mutation_ready"):
                err = {"code": "mutation_not_ready", "message": "系統尚無法進行資料異動。"}
                return jsonify({"error": err}), 409

        from qingpu_insight.official_data import OfficialDataRequest

        request_obj = OfficialDataRequest(
            start_season=start_season,
            end_season=end_season,
            start_at=start_at,
        )

        try:
            submission = rt.official_data_service.submit(request_obj)
        except Exception:
            err = {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}
            return jsonify({"error": err}), 503

        if submission.created:
            try:
                rt.official_data_service.handoff(submission, request_obj, rt.executor)
            except Exception:
                err = {"code": "enqueue_failed", "message": "工作無法啟動。"}
                return jsonify({"error": err}), 503

        body = _admin_public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.created else 200

    @bp.get("/api/admin/official-data-updates/<run_id>/reports/quality")
    def admin_official_data_quality(run_id: str):
        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.job_service is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

        try:
            uuid.UUID(run_id)
        except (ValueError, AttributeError):
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": {"run_id": "invalid_uuid"}}
            return jsonify({"error": err}), 400

        try:
            run = rt.job_service.get(run_id)
        except Exception:
            err = {"code": "job_unavailable", "message": "工作狀態暫時無法取得。"}
            return jsonify({"error": err}), 503

        if run is None or run.status != "succeeded" or run.job_type != "official_data_update":
            err = {"code": "not_found", "message": "工作不存在。"}
            return jsonify({"error": err}), 404

        root = getattr(rt, "root", None)
        if root is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

        quality_path = Path(root) / "outputs" / "admin" / "official-data" / run_id / "quality.json"
        try:
            resolved = quality_path.resolve()
            base = (Path(root) / "outputs" / "admin").resolve()
            if not str(resolved).startswith(str(base)):
                err = {"code": "forbidden", "message": "路徑無效。"}
                return jsonify({"error": err}), 403
        except (OSError, ValueError):
            err = {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}
            return jsonify({"error": err}), 503

        if not quality_path.exists():
            err = {"code": "not_found", "message": "品質報告不存在。"}
            return jsonify({"error": err}), 404

        try:
            data = json.loads(quality_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            err = {"code": "admin_unavailable", "message": "品質報告無法讀取。"}
            return jsonify({"error": err}), 503

        return jsonify(data)

    return bp
