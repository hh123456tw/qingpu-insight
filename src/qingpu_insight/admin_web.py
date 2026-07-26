from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, render_template, request, session


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


def create_admin_blueprint(runtime: AdminRuntime) -> Blueprint:
    bp = Blueprint("admin", __name__, url_prefix="/admin")

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

    @bp.get("/", strict_slashes=False)
    def admin_index():
        return render_template("admin.html", csrf_token=session.get("_csrf_token", ""))

    return bp
