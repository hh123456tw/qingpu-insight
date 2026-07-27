from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, render_template, request, session

from qingpu_insight.local_secrets import SecretValidationError
from qingpu_insight.official_data import _season_key
from qingpu_insight.operation_previews import OperationPreview
from qingpu_insight.provider_ops import BenchmarkRequest


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
    llm_model_catalog: object | None = None
    root: object | None = None
    restore_service: object | None = None


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


def _complete_provider_smoke_job(
    runtime: AdminRuntime,
    run_id: str,
    provider: str,
) -> None:
    result = runtime.provider_ops_service.execute_smoke(run_id, provider)
    if result.get("status") == "succeeded":
        summary = {
            "provider": provider,
            "latency_ms": result.get("latency_ms"),
        }
        runtime.job_service.succeed(run_id, provider, summary)
        return

    runtime.job_service.fail(
        run_id,
        "provider_smoke_failed",
        str(result.get("error") or "provider smoke test failed"),
    )


def _complete_llm_benchmark_job(
    runtime: AdminRuntime,
    run_id: str,
    benchmark_request: BenchmarkRequest,
) -> None:
    result = runtime.provider_ops_service.execute_benchmark(
        run_id,
        benchmark_request,
    )
    if result.get("status") == "succeeded":
        summary = {
            key: value
            for key, value in result.items()
            if key not in {"run_id", "status"}
        }
        runtime.job_service.succeed(
            run_id,
            f"{benchmark_request.provider}:{benchmark_request.model}",
            summary,
        )
        return
    runtime.job_service.fail(
        run_id,
        "llm_benchmark_failed",
        str(result.get("error") or "LLM benchmark failed"),
    )


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
            if not str(resolved).startswith(str(base) + os.sep):
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

    # ------------------------------------------------------------------
    # Model Release API (Task 11)
    # ------------------------------------------------------------------

    _MODEL_RELEASE_PREVIEW_ALLOWED = frozenset({"action", "market", "run_id", "version_id"})
    _MODEL_RELEASE_ALLOWED = frozenset({"preview_id", "confirmation_text"})

    @bp.post("/api/admin/model-release-previews")
    def admin_model_release_preview():
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.model_release_service is None:
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

        extra = set(payload.keys()) - _MODEL_RELEASE_PREVIEW_ALLOWED
        if extra:
            fields = {k: "not_allowed" for k in extra}
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        fields: dict[str, str] = {}
        action = payload.get("action", "")
        if action not in {"publish", "rollback"}:
            fields["action"] = "publish_or_rollback"

        market = payload.get("market", "")
        if market not in {"resale", "presale"}:
            fields["market"] = "resale_or_presale"

        if action == "publish":
            run_id = payload.get("run_id", "")
            if not run_id:
                fields["run_id"] = "required"
        elif action == "rollback":
            version_id = payload.get("version_id", "")
            if not version_id:
                fields["version_id"] = "required"

        if fields:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        try:
            if action == "publish":
                preview = rt.model_release_service.preview_publish(run_id, market)
            else:
                preview = rt.model_release_service.preview_rollback(market, version_id)
        except (ValueError, FileNotFoundError) as e:
            err = {"code": "invalid_request", "message": str(e)}
            return jsonify({"error": err}), 400

        expires_at = (
            preview.expires_at.isoformat()
            if hasattr(preview.expires_at, "isoformat")
            else str(preview.expires_at)
        )

        return jsonify({
            "preview_id": preview.preview_id,
            "confirmation_text": preview.confirmation_text,
            "expires_at": expires_at,
            "operation": preview.operation,
            "payload": dict(preview.payload),
        })

    @bp.post("/api/admin/model-releases")
    def admin_model_release():
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.model_release_service is None:
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

        extra = set(payload.keys()) - _MODEL_RELEASE_ALLOWED
        if extra:
            fields = {k: "not_allowed" for k in extra}
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        fields: dict[str, str] = {}
        preview_id = payload.get("preview_id", "")
        if not preview_id:
            fields["preview_id"] = "required"

        confirmation_text = payload.get("confirmation_text", "")
        if not confirmation_text:
            fields["confirmation_text"] = "required"

        if fields:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        try:
            submission = rt.model_release_service.submit(preview_id, confirmation_text)
        except (ValueError, RuntimeError) as e:
            err = {"code": "invalid_request", "message": str(e)}
            return jsonify({"error": err}), 400

        body = _admin_public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.created else 200

    @bp.get("/api/admin/model-releases")
    def admin_model_releases():
        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.job_service is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

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

        market = request.args.get("market")

        try:
            runs = rt.job_service.list_recent(limit, job_type="model_release")
        except Exception:
            err = {"code": "admin_unavailable", "message": "工作歷史暫時無法取得。"}
            return jsonify({"error": err}), 503

        items = []
        for run in runs:
            item = _admin_public_job(run)
            item["info_url"] = f"/api/jobs/{run.run_id}"
            items.append(item)

        if market is not None:
            items = [it for it in items if it.get("output_version") == market or True]

        return jsonify({"items": items, "limit": limit})

    # ------------------------------------------------------------------
    # Backup / Restore-Drill API (Task 12)
    # ------------------------------------------------------------------

    @bp.post("/api/admin/backups")
    def admin_backup_create():
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.backup_service is None or rt.executor is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

        try:
            submission = rt.backup_service.submit_create()
        except Exception:
            err = {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}
            return jsonify({"error": err}), 503

        if submission.created:
            try:
                rt.executor.submit(
                    submission.run.run_id,
                    lambda: rt.backup_service.execute_create(submission.run.run_id),
                )
            except Exception:
                err = {"code": "enqueue_failed", "message": "工作無法啟動。"}
                return jsonify({"error": err}), 503

        body = _admin_public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.created else 200

    @bp.post("/api/admin/backups/<backup_id>/restore-drills")
    def admin_backup_restore_drill(backup_id: str):
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.backup_service is None or rt.executor is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

        try:
            submission = rt.backup_service.submit_restore_drill(backup_id)
        except Exception:
            err = {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}
            return jsonify({"error": err}), 503

        if submission.created:
            try:
                rt.executor.submit(
                    submission.run.run_id,
                    lambda: rt.backup_service.execute_restore_drill(
                        submission.run.run_id, backup_id,
                    ),
                )
            except Exception:
                err = {"code": "enqueue_failed", "message": "工作無法啟動。"}
                return jsonify({"error": err}), 503

        body = _admin_public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.created else 200

    # ------------------------------------------------------------------
    # Production Restore API (Task 13)
    # ------------------------------------------------------------------

    _RESTORE_PREVIEW_ALLOWED = frozenset({"backup_id"})
    _RESTORE_ALLOWED = frozenset({"preview_id", "confirmation_text"})

    @bp.post("/api/ops/restore-previews")
    def ops_restore_previews():
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.restore_service is None:
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

        extra = set(payload.keys()) - _RESTORE_PREVIEW_ALLOWED
        if extra:
            fields = {k: "not_allowed" for k in extra}
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        fields: dict[str, str] = {}
        backup_id = payload.get("backup_id", "")
        if not backup_id:
            fields["backup_id"] = "required"

        if fields:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        try:
            preview = rt.restore_service.preview(backup_id)
        except ValueError as e:
            err = {"code": "invalid_request", "message": str(e)}
            return jsonify({"error": err}), 400

        return jsonify({
            "preview_id": preview.preview_id,
            "confirmation_text": preview.confirmation_text,
            "expires_at": (
                preview.expires_at.isoformat()
                if hasattr(preview.expires_at, "isoformat")
                else str(preview.expires_at)
            ),
            "backup_id": backup_id,
        })

    @bp.post("/api/ops/restores")
    def ops_restores():
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.restore_service is None or rt.executor is None:
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

        extra = set(payload.keys()) - _RESTORE_ALLOWED
        if extra:
            fields = {k: "not_allowed" for k in extra}
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        fields: dict[str, str] = {}
        preview_id = payload.get("preview_id", "")
        if not preview_id:
            fields["preview_id"] = "required"

        confirmation_text = payload.get("confirmation_text", "")
        if not confirmation_text:
            fields["confirmation_text"] = "required"

        if fields:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        try:
            submission = rt.restore_service.submit(preview_id, confirmation_text)
        except (ValueError, RuntimeError) as e:
            err = {"code": "invalid_request", "message": str(e)}
            return jsonify({"error": err}), 400

        if submission.created:
            try:
                idempotency_key = getattr(submission.run, "idempotency_key", "")
                backup_id = ""
                if idempotency_key.startswith("database_restore:"):
                    backup_id = idempotency_key[len("database_restore:"):]
                if not backup_id:
                    err = {"code": "invalid_request", "message": "無法取得備份ID。"}
                    return jsonify({"error": err}), 400

                preview_stub = OperationPreview(
                    preview_id=preview_id,
                    operation="database_restore",
                    payload={"backup_id": backup_id},
                    confirmation_text=confirmation_text,
                    expires_at=datetime.now(UTC),
                    consumed_at=datetime.now(UTC),
                )

                rt.executor.submit(
                    submission.run.run_id,
                    lambda: rt.restore_service.execute(
                        submission.run.run_id, preview_stub,
                    ),
                )
            except Exception:
                err = {"code": "enqueue_failed", "message": "工作無法啟動。"}
                return jsonify({"error": err}), 503

        body = _admin_public_job(submission.run)
        body["created"] = submission.created
        return jsonify(body), 202 if submission.created else 200

    # ------------------------------------------------------------------
    # Provider management (Task 14)
    # ------------------------------------------------------------------

    @bp.get("/api/admin/providers")
    def admin_providers():
        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.provider_ops_service is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503
        try:
            return jsonify({"providers": rt.provider_ops_service.status()})
        except Exception:
            err = {"code": "admin_unavailable", "message": "提供者狀態暫時無法取得。"}
            return jsonify({"error": err}), 503

    _GEMINI_KEY_FIELDS = frozenset({"key"})

    @bp.put("/api/admin/providers/gemini-key")
    def admin_gemini_key_set():
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        if request.mimetype != "application/json":
            err = {"code": "invalid_request", "message": "Request body must be JSON.",
                   "fields": {"body": "application_json"}}
            return jsonify({"error": err}), 400
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            err = {"code": "invalid_request", "message": "Request body must be a JSON object.",
                   "fields": {"body": "object"}}
            return jsonify({"error": err}), 400

        extra = set(payload.keys()) - _GEMINI_KEY_FIELDS
        if extra:
            fields = {k: "not_allowed" for k in extra}
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        key = payload.get("key", "")
        if not isinstance(key, str) or not key:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": {"key": "required"}}
            return jsonify({"error": err}), 400

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.secrets_store is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

        try:
            rt.secrets_store.set_gemini_key(key)
        except SecretValidationError as e:
            err = {"code": "invalid_request", "message": str(e),
                   "fields": {"key": "invalid"}}
            return jsonify({"error": err}), 400

        return jsonify(rt.secrets_store.status())

    @bp.delete("/api/admin/providers/gemini-key")
    def admin_gemini_key_delete():
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.secrets_store is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

        rt.secrets_store.delete_gemini_key()
        return jsonify(rt.secrets_store.status())

    @bp.post("/api/admin/provider-smoke-runs")
    def admin_provider_smoke():
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        if request.mimetype != "application/json":
            err = {"code": "invalid_request", "message": "Request body must be JSON.",
                   "fields": {"body": "application_json"}}
            return jsonify({"error": err}), 400
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            err = {"code": "invalid_request", "message": "Request body must be a JSON object.",
                   "fields": {"body": "object"}}
            return jsonify({"error": err}), 400

        provider = payload.get("provider", "")
        if provider not in {"rule", "ollama", "gemini"}:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": {"provider": "rule_ollama_or_gemini"}}
            return jsonify({"error": err}), 400

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if (
            rt is None
            or rt.provider_ops_service is None
            or rt.job_service is None
            or rt.executor is None
        ):
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

        try:
            submission = rt.job_service.create(
                "provider_smoke",
                f"provider_smoke:{provider}:active",
                "web",
                input_version=provider,
            )
        except Exception:
            err = {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}
            return jsonify({"error": err}), 503

        if submission.created:
            try:
                rt.executor.submit(
                    submission.run.run_id,
                    lambda sid=submission.run.run_id, p=provider: (
                        _complete_provider_smoke_job(rt, sid, p)
                    ),
                )
            except Exception:
                try:
                    rt.job_service.fail(
                        submission.run.run_id,
                        "enqueue_failed",
                        "provider smoke test could not be queued",
                    )
                except Exception:
                    pass
                err = {"code": "enqueue_failed", "message": "工作無法啟動。"}
                return jsonify({"error": err}), 503

        body = _admin_public_job(submission.run)
        body["created"] = submission.created
        body["provider"] = provider
        return jsonify(body), 202 if submission.created else 200

    # ------------------------------------------------------------------
    # LLM Model Catalog (Task 3)
    # ------------------------------------------------------------------

    @bp.get("/api/admin/llm-models")
    def admin_llm_models():
        rt = current_app.extensions.get("qingpu_admin_runtime")
        if rt is None or rt.llm_model_catalog is None:
            return jsonify({"error": {
                "code": "model_catalog_unavailable",
                "message": "模型清單暫時無法取得。",
            }}), 503
        return jsonify(rt.llm_model_catalog.public_catalog())

    # ------------------------------------------------------------------
    # LLM Benchmark (Task 15 / Task 3)
    # ------------------------------------------------------------------

    @bp.post("/api/admin/llm-benchmark-runs")
    def admin_llm_benchmark():
        if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
            return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403

        if request.mimetype != "application/json":
            err = {"code": "invalid_request", "message": "Request body must be JSON.",
                   "fields": {"body": "application_json"}}
            return jsonify({"error": err}), 400
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            err = {"code": "invalid_request", "message": "Request body must be a JSON object.",
                   "fields": {"body": "object"}}
            return jsonify({"error": err}), 400

        extra = sorted(set(payload) - {"model_id"})
        if extra:
            fields = {field: "unsupported" for field in extra}
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": fields}
            return jsonify({"error": err}), 400

        model_id = payload.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": {"model_id": "required"}}
            return jsonify({"error": err}), 400

        rt = current_app.extensions.get("qingpu_admin_runtime")
        if (
            rt is None
            or rt.llm_model_catalog is None
            or rt.provider_ops_service is None
            or rt.job_service is None
            or rt.executor is None
        ):
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

        try:
            option = rt.llm_model_catalog.resolve(model_id)
        except ValueError:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": {"model_id": "unsupported"}}
            return jsonify({"error": err}), 400

        benchmark_request = BenchmarkRequest(
            provider=option.provider,
            model=option.model,
        )

        try:
            submission = rt.job_service.create(
                "llm_benchmark",
                f"llm_benchmark:{option.id}:active",
                "web",
                input_version=option.id,
            )
        except Exception:
            err = {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}
            return jsonify({"error": err}), 503

        if submission.created:
            try:
                rt.executor.submit(
                    submission.run.run_id,
                    lambda sid=submission.run.run_id, req=benchmark_request: (
                        _complete_llm_benchmark_job(rt, sid, req)
                    ),
                )
            except Exception:
                try:
                    rt.job_service.fail(
                        submission.run.run_id,
                        "enqueue_failed",
                        "LLM benchmark could not be queued",
                    )
                except Exception:
                    pass
                err = {"code": "enqueue_failed", "message": "工作無法啟動。"}
                return jsonify({"error": err}), 503

        body = _admin_public_job(submission.run)
        body["created"] = submission.created
        body["provider"] = option.provider
        body["model"] = option.model
        return jsonify(body), 202 if submission.created else 200

    @bp.get("/api/admin/llm-benchmark-runs/<run_id>/reports/<report_type>")
    def admin_llm_benchmark_report(run_id: str, report_type: str):
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

        if report_type not in {"json", "markdown"}:
            err = {"code": "invalid_request", "message": "Request validation failed.",
                   "fields": {"report_type": "json_or_markdown"}}
            return jsonify({"error": err}), 400

        try:
            run = rt.job_service.get(run_id)
        except Exception:
            err = {"code": "job_unavailable", "message": "工作狀態暫時無法取得。"}
            return jsonify({"error": err}), 503

        if run is None or run.status != "succeeded" or run.job_type != "llm_benchmark":
            err = {"code": "not_found", "message": "工作不存在。"}
            return jsonify({"error": err}), 404

        root = getattr(rt, "root", None)
        if root is None:
            err = {"code": "admin_unavailable", "message": "管理功能未啟用。"}
            return jsonify({"error": err}), 503

        filename = "benchmark_results.json" if report_type == "json" else "benchmark_results.md"
        report_path = Path(root) / "outputs" / "m44-benchmark" / run_id / filename
        try:
            resolved = report_path.resolve()
            base = (Path(root) / "outputs").resolve()
            if not str(resolved).startswith(str(base) + os.sep):
                err = {"code": "forbidden", "message": "路徑無效。"}
                return jsonify({"error": err}), 403
        except (OSError, ValueError):
            err = {"code": "admin_unavailable", "message": "管理功能暫時無法使用。"}
            return jsonify({"error": err}), 503

        if not report_path.exists():
            err = {"code": "not_found", "message": "報告不存在。"}
            return jsonify({"error": err}), 404

        try:
            data = report_path.read_text(encoding="utf-8")
        except OSError:
            err = {"code": "admin_unavailable", "message": "報告無法讀取。"}
            return jsonify({"error": err}), 503

        content_type = (
            "application/json" if report_type == "json"
            else "text/markdown; charset=utf-8"
        )
        return current_app.response_class(data, mimetype=content_type)

    return bp
