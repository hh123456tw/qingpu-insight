"""Local buyer report APIs: ``POST /api/reports`` and ``GET /api/reports/<id>``."""

from __future__ import annotations

from threading import BoundedSemaphore

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from qingpu_insight.api_errors import ApiInputError
from qingpu_insight.evidence import UnknownCandidateError
from qingpu_insight.report_repository import CorruptReportError
from qingpu_insight.web_composition import ReportServices
from qingpu_insight.web_routes.guards import LOCAL_ONLY_LEGACY_MUTATION, guarded_blueprint

_REPORT_ALLOWED_FIELDS = frozenset({"candidate_ids", "budget_twd", "intended_use", "provider"})


def _parse_report_request() -> dict:
    if request.mimetype != "application/json":
        raise ApiInputError("Request body must be JSON.", {"body": "application_json"})
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiInputError("Request body must be a JSON object.", {"body": "object"})

    fields: dict[str, str] = {}

    extra = set(payload.keys()) - _REPORT_ALLOWED_FIELDS
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


def _report_json(record) -> dict:
    return {
        "report_id": record.report_id,
        "provider": record.provider,
        "model": record.model,
        "dataset_version": record.dataset_version,
        "evidence_pack_id": record.evidence_pack_id,
        "fallback_reason": record.fallback_reason,
        "content": record.content,
        "created_at": record.created_at,
    }


def _report_unavailable():
    return jsonify({"error": {"code": "report_unavailable", "message": "報告功能未啟用。"}}), 503


def create_reports_blueprint(report_services: ReportServices | None) -> Blueprint:
    bp = guarded_blueprint("reports", __name__, default_policy=LOCAL_ONLY_LEGACY_MUTATION)
    # One report at a time per app: generation calls a local or remote LLM.
    semaphore = BoundedSemaphore(1)

    @bp.post("/api/reports")
    def create_report():
        if report_services is None:
            return _report_unavailable()

        parsed = _parse_report_request()

        from qingpu_insight.report_contracts import ReportRequest

        try:
            report_request = ReportRequest(**parsed)
        except ValidationError as exc:
            # Field codes only; pydantic's message text echoes internals and input.
            fields = {
                ".".join(str(part) for part in item.get("loc", ())) or "body": "invalid"
                for item in exc.errors()
            }
            return jsonify(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "Request validation failed.",
                        "fields": fields,
                    }
                }
            ), 400

        if not semaphore.acquire(blocking=False):
            return jsonify({"error": {"code": "report_busy", "message": "已有報告正在產生。"}}), 429
        try:
            saved = report_services.service.generate(report_request)
        except UnknownCandidateError:
            return jsonify(
                {"error": {"code": "candidate_not_found", "message": "找不到指定物件。"}}
            ), 404
        except Exception:
            return jsonify({"error": {"code": "report_failed", "message": "報告產生失敗。"}}), 503
        finally:
            semaphore.release()

        return jsonify(_report_json(saved)), 201

    @bp.get("/api/reports/<report_id>")
    def get_report(report_id: str):
        if report_services is None:
            return _report_unavailable()

        try:
            record = report_services.repository.get(report_id)
        except CorruptReportError:
            return jsonify({"error": {"code": "report_corrupt", "message": "報告已毀損。"}}), 503
        if record is None:
            return jsonify({"error": {"code": "not_found", "message": "報告不存在。"}}), 404

        return jsonify(_report_json(record))

    return bp
