from __future__ import annotations

import uuid
from datetime import datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, render_template, request, session

from qingpu_insight.conversation_contracts import (
    ConversationCreateRequest,
    ListingImportRequest,
    ReplyCreateRequest,
)


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


def _require_mutation_auth() -> tuple | None:
    if not _is_trusted_local_request():
        return jsonify({"error": {"code": "forbidden", "message": "僅允許本機存取。"}}), 403
    if request.headers.get("X-Qingpu-CSRF", "") != session.get("_csrf_token", ""):
        return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403
    return None


def _conversation_to_json(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "title": record.title,
        "status": record.status,
        "default_provider": record.default_provider,
        "default_model": record.default_model,
        "active_evidence_revision": record.active_evidence_revision,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _message_to_json(msg: Any) -> dict[str, Any]:
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "sequence_no": msg.sequence_no,
        "role": msg.role,
        "content": msg.content,
        "evidence_revision": msg.evidence_revision,
        "provider": msg.provider,
        "model": msg.model,
        "citations": msg.citations,
        "created_at": msg.created_at.isoformat(),
    }


def create_conversation_blueprint(service, repository):
    bp = Blueprint("conversation", __name__, url_prefix="")

    @bp.route("/api/conversations", methods=["POST"])
    def create_conversation():
        auth_error = _require_mutation_auth()
        if auth_error:
            return auth_error
        if service is None:
            return jsonify({
                "error": {"code": "service_unavailable", "message": "對話功能未啟用。"}
            }), 503
        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                "error": {"code": "invalid_request", "message": "JSON required"}
            }), 400
        try:
            req = ConversationCreateRequest(**data)
        except Exception as e:
            return jsonify({
                "error": {"code": "invalid_request", "message": str(e)}
            }), 400
        record = service.create_conversation(provider=req.provider, model=req.model)
        return jsonify(_conversation_to_json(record)), 201

    @bp.route("/api/conversations", methods=["GET"])
    def list_conversations():
        if repository is None:
            return jsonify({
                "error": {"code": "service_unavailable", "message": "對話功能未啟用。"}
            }), 503
        raw_limit = request.args.get("limit", "20")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return jsonify({
                "error": {
                    "code": "invalid_request",
                    "message": "Request validation failed.",
                    "fields": {"limit": "integer"},
                }
            }), 400
        if limit < 1 or limit > 100:
            return jsonify({
                "error": {
                    "code": "invalid_request",
                    "message": "Request validation failed.",
                    "fields": {"limit": "integer_1_to_100"},
                }
            }), 400
        before = request.args.get("before")
        before_cursor = None
        if before:
            try:
                before_dt_str, before_id = before.split(",", 1)
                before_dt = datetime.fromisoformat(before_dt_str)
                before_cursor = (before_dt, before_id)
            except (ValueError, TypeError):
                return jsonify({
                    "error": {
                        "code": "invalid_request",
                        "message": "Request validation failed.",
                        "fields": {"before": "invalid_cursor"},
                    }
                }), 400
        records = repository.list_conversations(limit=limit, before=before_cursor)
        return jsonify({"items": [_conversation_to_json(r) for r in records], "limit": limit})

    @bp.route("/api/conversations/<conversation_id>", methods=["GET"])
    def get_conversation(conversation_id):
        if repository is None:
            return jsonify({
                "error": {"code": "service_unavailable", "message": "對話功能未啟用。"}
            }), 503
        record = repository.get_conversation(conversation_id)
        if record is None:
            return jsonify({
                "error": {"code": "not_found", "message": "對話不存在。"}
            }), 404
        return jsonify(_conversation_to_json(record))

    @bp.route("/api/conversations/<conversation_id>", methods=["DELETE"])
    def delete_conversation(conversation_id):
        auth_error = _require_mutation_auth()
        if auth_error:
            return auth_error
        if service is None:
            return jsonify({
                "error": {"code": "service_unavailable", "message": "對話功能未啟用。"}
            }), 503
        deleted = service.delete_conversation(conversation_id=conversation_id)
        if not deleted:
            return jsonify({
                "error": {"code": "not_found", "message": "對話不存在。"}
            }), 404
        return "", 204

    @bp.route("/api/conversations/<conversation_id>/listing", methods=["POST"])
    def import_listing(conversation_id):
        auth_error = _require_mutation_auth()
        if auth_error:
            return auth_error
        if service is None:
            return jsonify({
                "error": {"code": "service_unavailable", "message": "對話功能未啟用。"}
            }), 503
        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                "error": {"code": "invalid_request", "message": "JSON required"}
            }), 400
        try:
            req = ListingImportRequest(**data)
        except Exception as e:
            return jsonify({
                "error": {"code": "invalid_request", "message": str(e)}
            }), 400
        idempotency_key = str(uuid.uuid4())
        cmd = service.start_import(
            conversation_id=conversation_id,
            raw_url=req.url,
            idempotency_key=idempotency_key,
        )
        return jsonify({
            "run_id": cmd.run_id, "conversation_id": cmd.conversation_id
        }), 202

    @bp.route("/api/conversations/<conversation_id>/refresh", methods=["POST"])
    def refresh_listing(conversation_id):
        auth_error = _require_mutation_auth()
        if auth_error:
            return auth_error
        if service is None:
            return jsonify({
                "error": {"code": "service_unavailable", "message": "對話功能未啟用。"}
            }), 503
        idempotency_key = str(uuid.uuid4())
        cmd = service.start_refresh(
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        return jsonify({
            "run_id": cmd.run_id, "conversation_id": cmd.conversation_id
        }), 202

    @bp.route("/api/conversations/<conversation_id>/messages", methods=["GET"])
    def get_messages(conversation_id):
        if repository is None:
            return jsonify({
                "error": {"code": "service_unavailable", "message": "對話功能未啟用。"}
            }), 503
        raw_limit = request.args.get("limit", "50")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return jsonify({
                "error": {
                    "code": "invalid_request",
                    "message": "Request validation failed.",
                    "fields": {"limit": "integer"},
                }
            }), 400
        if limit < 1 or limit > 200:
            return jsonify({
                "error": {
                    "code": "invalid_request",
                    "message": "Request validation failed.",
                    "fields": {"limit": "integer_1_to_200"},
                }
            }), 400
        before_raw = request.args.get("before")
        before_sequence = None
        if before_raw:
            try:
                before_sequence = int(before_raw)
            except (ValueError, TypeError):
                return jsonify({
                    "error": {
                        "code": "invalid_request",
                        "message": "Request validation failed.",
                        "fields": {"before": "integer"},
                    }
                }), 400
        messages = repository.get_messages(
            conversation_id=conversation_id,
            limit=limit,
            before_sequence=before_sequence,
        )
        return jsonify({
            "items": [_message_to_json(m) for m in messages], "limit": limit
        })

    @bp.route("/api/conversations/<conversation_id>/replies", methods=["POST"])
    def create_reply(conversation_id):
        auth_error = _require_mutation_auth()
        if auth_error:
            return auth_error
        if service is None:
            return jsonify({
                "error": {"code": "service_unavailable", "message": "對話功能未啟用。"}
            }), 503
        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                "error": {"code": "invalid_request", "message": "JSON required"}
            }), 400
        try:
            req = ReplyCreateRequest(**data)
        except Exception as e:
            return jsonify({
                "error": {"code": "invalid_request", "message": str(e)}
            }), 400
        idempotency_key = str(uuid.uuid4())
        try:
            cmd = service.start_reply(
                conversation_id=conversation_id,
                question=req.content,
                provider=req.provider,
                model=req.model,
                evidence_revision=req.evidence_revision,
                idempotency_key=idempotency_key,
            )
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                return jsonify({
                    "error": {"code": "not_found", "message": "對話不存在。"}
                }), 404
            if "stale evidence revision" in msg:
                return jsonify({
                    "error": {"code": "stale_evidence", "message": "證據版本已過期，請重新整理。"}
                }), 409
            if "active reply already in progress" in msg:
                return jsonify({
                    "error": {"code": "busy", "message": "已有回覆正在生成。"}
                }), 409
            return jsonify({
                "error": {"code": "invalid_request", "message": msg}
            }), 400
        return jsonify({
            "run_id": cmd.run_id, "conversation_id": cmd.conversation_id
        }), 202

    @bp.route("/assistant/<conversation_id>")
    def assistant_page(conversation_id):
        return render_template("assistant.html", conversation_id=conversation_id)

    return bp
