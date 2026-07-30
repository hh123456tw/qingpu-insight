from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    session,
)
from werkzeug.exceptions import HTTPException

from qingpu_insight.conversation_contracts import (
    ConversationCreateRequest,
    ListingImportRequest,
    ReplyCreateRequest,
)
from qingpu_insight.conversation_presentation import (
    project_citation_details,
    project_price_summary,
)
from qingpu_insight.conversation_urls import (
    Unsupported591Url,
    parse_initial_591_url,
)

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


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


def _command_idempotency_key(command: str, conversation_id: str) -> str:
    supplied = request.headers.get("Idempotency-Key")
    token = supplied or str(uuid.uuid4())
    if not _IDEMPOTENCY_RE.fullmatch(token):
        raise ValueError("invalid idempotency key")
    raw = f"conversation:{command}:{conversation_id}:{token}".encode()
    return f"conversation:{command}:{hashlib.sha256(raw).hexdigest()}"


def _conversation_to_json(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "title": record.title,
        "status": record.status,
        "default_provider": record.default_provider,
        "default_model": record.default_model,
        "active_listing_id": record.active_listing_id,
        "active_evidence_revision": record.active_evidence_revision,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _evidence_to_json(ev: Any) -> dict[str, Any]:
    return {
        "id": ev.id,
        "conversation_id": ev.conversation_id,
        "revision": ev.revision,
        "generated_at": ev.generated_at.isoformat(),
        "facts": ev.facts,
        "valuation": ev.valuation,
        "comparables": ev.comparables,
        "limitations": ev.limitations,
    }


def _message_to_json(
    msg: Any,
    conversation: Any,
    evidence_pack: Any | None = None,
    *,
    include_price_summary: bool = True,
) -> dict[str, Any]:
    citation_details = (
        project_citation_details(evidence_pack, msg.citations)
        if evidence_pack is not None
        else []
    )
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "sequence_no": msg.sequence_no,
        "role": msg.role,
        "content": msg.content,
        "evidence_revision": msg.evidence_revision,
        "provider": msg.provider,
        "model": msg.model,
        "requested_provider": conversation.default_provider,
        "requested_model": conversation.default_model,
        "fallback_reason": getattr(msg, "fallback_reason", None),
        "citations": msg.citations,
        "citation_details": citation_details,
        "price_summary": (
            project_price_summary(evidence_pack)
            if include_price_summary and msg.role == "assistant" and evidence_pack is not None
            else None
        ),
        "created_at": msg.created_at.isoformat(),
    }


def create_conversation_blueprint(
    service,
    repository,
    *,
    catalog_getter: Callable[[], dict[str, Any]] | None = None,
):
    bp = Blueprint("conversation", __name__, url_prefix="")
    get_catalog = catalog_getter or (
        lambda: {
            "default_model": "",
            "gemini_configured": False,
            "items": [],
        }
    )

    @bp.errorhandler(Exception)
    def handle_conversation_error(error):
        if isinstance(error, HTTPException):
            return error
        current_app.logger.error("conversation request failed")
        return jsonify({
            "error": {
                "code": "conversation_unavailable",
                "message": "對話功能暫時無法使用。",
            }
        }), 503

    @bp.before_request
    def require_trusted_local_request():
        if not _is_trusted_local_request():
            return jsonify({
                "error": {
                    "code": "forbidden",
                    "message": "僅允許本機存取。",
                }
            }), 403
        return None

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
        record = service.create_conversation(model=req.model)
        return jsonify(_conversation_to_json(record)), 201

    @bp.route("/api/conversation-models", methods=["GET"])
    def get_conversation_models():
        return jsonify(get_catalog())

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

    @bp.route("/api/conversations/<conversation_id>/evidence", methods=["GET"])
    def get_evidence(conversation_id):
        if repository is None:
            return jsonify({
                "error": {"code": "service_unavailable", "message": "對話功能未啟用。"}
            }), 503
        record = repository.get_conversation(conversation_id)
        if record is None:
            return jsonify({
                "error": {"code": "not_found", "message": "對話不存在。"}
            }), 404
        if record.active_evidence_revision is None:
            return jsonify({
                "error": {"code": "no_evidence", "message": "尚無證據資料。"}
            }), 404
        evidence = repository.get_evidence_pack(
            conversation_id=conversation_id,
            revision=record.active_evidence_revision,
        )
        if evidence is None:
            return jsonify({
                "error": {"code": "not_found", "message": "證據資料不存在。"}
            }), 404
        return jsonify(_evidence_to_json(evidence))

    @bp.route("/api/conversations/<conversation_id>", methods=["DELETE"])
    def delete_conversation(conversation_id):
        auth_error = _require_mutation_auth()
        if auth_error:
            return auth_error
        if service is None:
            return jsonify({
                "error": {"code": "service_unavailable", "message": "對話功能未啟用。"}
            }), 503
        expected_confirmation = f"delete:{conversation_id}"
        if request.headers.get("X-Qingpu-Confirm") != expected_confirmation:
            return jsonify({
                "error": {
                    "code": "confirmation_required",
                    "message": "刪除確認不符。",
                }
            }), 400
        try:
            deleted = service.delete_conversation(
                conversation_id=conversation_id
            )
        except ValueError as error:
            if str(error) == "conversation busy":
                return jsonify({
                    "error": {
                        "code": "conversation_busy",
                        "message": "對話工作進行中，暫時無法刪除。",
                    }
                }), 409
            raise
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
            initial_url = parse_initial_591_url(req.url)
            if (
                initial_url.kind == "direct"
                and urlsplit(initial_url.request_url).hostname == "newhouse.591.com.tw"
            ):
                raise Unsupported591Url("only sale listings are supported")
            idempotency_key = _command_idempotency_key(
                "import", conversation_id
            )
        except Unsupported591Url as error:
            return jsonify({
                "error": {
                    "code": "unsupported_591_url",
                    "message": str(error),
                }
            }), 400
        except Exception as e:
            return jsonify({
                "error": {"code": "invalid_request", "message": str(e)}
            }), 400
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
        try:
            idempotency_key = _command_idempotency_key(
                "refresh", conversation_id
            )
        except ValueError as error:
            return jsonify({
                "error": {
                    "code": "invalid_request",
                    "message": str(error),
                }
            }), 400
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
        conversation = repository.get_conversation(conversation_id)
        if conversation is None:
            return jsonify({
                "error": {"code": "not_found", "message": "對話不存在。"}
            }), 404
        messages = repository.get_messages(
            conversation_id=conversation_id,
            limit=limit,
            before_sequence=before_sequence,
        )
        revisions = {
            message.evidence_revision
            for message in messages
            if message.evidence_revision is not None
        }
        evidence_by_revision = {
            revision: repository.get_evidence_pack(
                conversation_id=conversation_id,
                revision=revision,
            )
            for revision in revisions
        }
        first_assistant_seq = min(
            (m.sequence_no for m in messages if m.role == "assistant"),
            default=None,
        )
        return jsonify({
            "items": [
                _message_to_json(
                    message,
                    conversation,
                    evidence_by_revision.get(message.evidence_revision),
                    include_price_summary=(
                        message.sequence_no == first_assistant_seq
                    ),
                )
                for message in messages
            ],
            "limit": limit,
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
            idempotency_key = _command_idempotency_key(
                "reply", conversation_id
            )
        except Exception as e:
            return jsonify({
                "error": {"code": "invalid_request", "message": str(e)}
            }), 400
        try:
            cmd = service.start_reply(
                conversation_id=conversation_id,
                question=req.content,
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
        return render_template(
            "assistant.html",
            conversation_id=conversation_id,
            csrf_token=session.get("_csrf_token", ""),
        )

    return bp
