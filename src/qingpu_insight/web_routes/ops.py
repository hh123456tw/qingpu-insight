"""Local ops APIs: latest health check and backup history."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from qingpu_insight.web_composition import OpsServices
from qingpu_insight.web_routes.errors import invalid_request, parse_limit
from qingpu_insight.web_routes.guards import LOCAL_ONLY, guarded_blueprint, local_only


def _ops_unavailable(message: str = "維運功能未啟用。"):
    return jsonify({"error": {"code": "ops_unavailable", "message": message}}), 503


def create_ops_blueprint(ops_services: OpsServices | None) -> Blueprint:
    bp = guarded_blueprint("ops", __name__)

    @bp.get("/api/ops/health")
    @local_only(LOCAL_ONLY)
    def ops_health():
        if ops_services is None or ops_services.health_repository is None:
            return _ops_unavailable()
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
            return _ops_unavailable("維運功能暫時無法使用。")

    @bp.get("/api/ops/backups")
    @local_only(LOCAL_ONLY)
    def ops_backups():
        if ops_services is None or ops_services.backup_repository is None:
            return _ops_unavailable()
        limit = parse_limit(request.args.get("limit", "20"))
        if limit is None:
            return invalid_request({"limit": "integer_1_to_100"})
        try:
            records = ops_services.backup_repository.list_recent(limit)
        except Exception:
            return _ops_unavailable("備份記錄暫時無法取得。")
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

    # Retired endpoint: restores go through a preview + confirmation (see admin_web).
    @bp.route("/api/ops/restore", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def ops_restore():
        return jsonify({"error": {"code": "not_found", "message": "路由不存在。"}}), 404

    return bp
