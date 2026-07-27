from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

from qingpu_insight.conversation_repository import (
    ConversationRecord,
    MessageRecord,
)
from qingpu_insight.conversation_service import ConversationCommand
from qingpu_insight.conversation_web import create_conversation_blueprint


@pytest.fixture
def service():
    return MagicMock()


@pytest.fixture
def repository():
    return MagicMock()


_TEMPLATE_DIR = str(Path(__file__).parent.parent / "src" / "qingpu_insight" / "templates")


@pytest.fixture
def conversation_app_no_service():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test-secret"
    app.register_blueprint(create_conversation_blueprint(None, None))
    return app.test_client()


@pytest.fixture
def conversation_app(service, repository):
    app = Flask(__name__, template_folder=_TEMPLATE_DIR)
    app.config["TESTING"] = True
    app.secret_key = "test-secret"
    app.register_blueprint(create_conversation_blueprint(service, repository))
    return app.test_client()


@pytest.fixture
def csrf_client(conversation_app):
    with conversation_app.session_transaction() as sess:
        sess["_csrf_token"] = "test-token"
    return conversation_app


@pytest.fixture
def sample_conversation():
    return ConversationRecord(
        id="conv-1",
        title="新的物件分析",
        status="empty",
        default_provider="ollama",
        default_model="gemma2:9b",
        active_listing_id=None,
        active_evidence_revision=None,
        rolling_summary=None,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, tzinfo=UTC),
        deleted_at=None,
    )


@pytest.fixture
def sample_messages():
    return [
        MessageRecord(
            id="msg-1",
            conversation_id="conv-1",
            sequence_no=1,
            role="user",
            content="Hello",
            evidence_revision=None,
            provider=None,
            model=None,
            citations=[],
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        MessageRecord(
            id="msg-2",
            conversation_id="conv-1",
            sequence_no=2,
            role="assistant",
            content="Hi there",
            evidence_revision=1,
            provider="ollama",
            model="gemma2:9b",
            citations=["fact-1"],
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        ),
    ]


# --- POST /api/conversations ---

class TestCreateConversation:
    def test_unsupported_method_returns_405(self, conversation_app):
        resp = conversation_app.patch("/api/conversations/conv-1")
        assert resp.status_code == 405

    def test_create_conversation_201(self, csrf_client, service, repository):
        service.create_conversation.return_value = ConversationRecord(
            id="conv-new",
            title="新的物件分析",
            status="empty",
            default_provider="ollama",
            default_model="gemma2:9b",
            active_listing_id=None,
            active_evidence_revision=None,
            rolling_summary=None,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, tzinfo=UTC),
            deleted_at=None,
        )
        response = csrf_client.post(
            "/api/conversations",
            json={"provider": "ollama", "model": "gemma2:9b"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 201
        data = response.get_json()
        assert data["id"] == "conv-new"
        assert data["status"] == "empty"
        assert data["default_provider"] == "ollama"
        service.create_conversation.assert_called_once_with(
            provider="ollama", model="gemma2:9b"
        )

    def test_create_conversation_400_missing_json(self, csrf_client):
        response = csrf_client.post(
            "/api/conversations",
            data=b"",
            content_type="application/json",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_request"

    def test_create_conversation_400_bad_provider(self, csrf_client):
        response = csrf_client.post(
            "/api/conversations",
            json={"provider": "unknown", "model": "gemma2:9b"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_request"

    def test_create_conversation_503_no_service(self):
        app = Flask(__name__, template_folder=_TEMPLATE_DIR)
        app.config["TESTING"] = True
        app.secret_key = "test-secret"
        app.register_blueprint(create_conversation_blueprint(None, MagicMock()))
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["_csrf_token"] = "test-token"
            response = client.post(
                "/api/conversations",
                json={"provider": "ollama", "model": "gemma2:9b"},
                headers={"X-Qingpu-CSRF": "test-token"},
            )
        assert response.status_code == 503


# --- GET /api/conversations ---

class TestListConversations:
    def test_list_conversations_200(
        self, conversation_app, service, repository, sample_conversation,
    ):
        repository.list_conversations.return_value = [sample_conversation]
        response = conversation_app.get("/api/conversations")
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == "conv-1"

    def test_list_conversations_with_cursor(
        self, conversation_app, repository, sample_conversation,
    ):
        repository.list_conversations.return_value = [sample_conversation]
        response = conversation_app.get(
            "/api/conversations?limit=10&before=2025-01-01T00:00:00%2B00:00,abc-123"
        )
        assert response.status_code == 200
        assert len(response.get_json()["items"]) == 1
        args, kwargs = repository.list_conversations.call_args
        assert kwargs["limit"] == 10
        assert kwargs["before"] is not None

    def test_list_conversations_invalid_cursor(self, conversation_app):
        response = conversation_app.get("/api/conversations?before=bad-cursor")
        assert response.status_code == 400

    def test_list_conversations_invalid_limit(self, conversation_app):
        response = conversation_app.get("/api/conversations?limit=abc")
        assert response.status_code == 400

    def test_list_conversations_503_no_repo(self):
        app = Flask(__name__, template_folder=_TEMPLATE_DIR)
        app.config["TESTING"] = True
        app.secret_key = "test-secret"
        app.register_blueprint(create_conversation_blueprint(MagicMock(), None))
        with app.test_client() as client:
            response = client.get("/api/conversations")
            assert response.status_code == 503


# --- GET /api/conversations/<id> ---

class TestGetConversation:
    def test_get_conversation_200(self, conversation_app, repository, sample_conversation):
        repository.get_conversation.return_value = sample_conversation
        response = conversation_app.get("/api/conversations/conv-1")
        assert response.status_code == 200
        assert response.get_json()["id"] == "conv-1"

    def test_get_conversation_404(self, conversation_app, repository):
        repository.get_conversation.return_value = None
        response = conversation_app.get("/api/conversations/missing")
        assert response.status_code == 404


# --- DELETE /api/conversations/<id> ---

class TestDeleteConversation:
    def test_delete_conversation_204(self, csrf_client, service):
        service.delete_conversation.return_value = True
        response = csrf_client.delete(
            "/api/conversations/conv-1",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 204

    def test_delete_conversation_404(self, csrf_client, service):
        service.delete_conversation.return_value = False
        response = csrf_client.delete(
            "/api/conversations/missing",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 404

    def test_delete_conversation_503_no_service(self, conversation_app_no_service):
        resp = conversation_app_no_service.delete("/api/conversations/conv-1")
        assert resp.status_code == 503


# --- POST /api/conversations/<id>/listing ---

class TestImportListing:
    def test_import_listing_202(self, csrf_client, service):
        service.start_import.return_value = ConversationCommand(
            run_id="run-1", conversation_id="conv-1"
        )
        response = csrf_client.post(
            "/api/conversations/conv-1/listing",
            json={"url": "https://example.com/listing/123"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        data = response.get_json()
        assert data["run_id"] == "run-1"
        assert data["conversation_id"] == "conv-1"

    def test_import_listing_400_bad_url(self, csrf_client):
        response = csrf_client.post(
            "/api/conversations/conv-1/listing",
            json={"url": ""},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_import_listing_503_no_service(self, conversation_app_no_service):
        resp = conversation_app_no_service.post(
            "/api/conversations/conv-1/listing", json={"url": "http://example.com"}
        )
        assert resp.status_code == 503


# --- POST /api/conversations/<id>/refresh ---

class TestRefreshListing:
    def test_refresh_listing_202(self, csrf_client, service):
        service.start_refresh.return_value = ConversationCommand(
            run_id="run-2", conversation_id="conv-1"
        )
        response = csrf_client.post(
            "/api/conversations/conv-1/refresh",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        data = response.get_json()
        assert data["run_id"] == "run-2"

    def test_refresh_listing_503_no_service(self, conversation_app_no_service):
        resp = conversation_app_no_service.post("/api/conversations/conv-1/refresh")
        assert resp.status_code == 503


# --- GET /api/conversations/<id>/messages ---

class TestGetMessages:
    def test_get_messages_200(self, conversation_app, repository, sample_messages):
        repository.get_messages.return_value = sample_messages
        response = conversation_app.get("/api/conversations/conv-1/messages")
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["items"]) == 2
        assert data["items"][0]["role"] == "user"

    def test_get_messages_default_limit(self, conversation_app, repository, sample_messages):
        repository.get_messages.return_value = sample_messages
        response = conversation_app.get("/api/conversations/conv-1/messages")
        assert response.status_code == 200
        args, kwargs = repository.get_messages.call_args
        assert kwargs["limit"] == 50

    def test_get_messages_with_before(self, conversation_app, repository, sample_messages):
        repository.get_messages.return_value = sample_messages
        response = conversation_app.get(
            "/api/conversations/conv-1/messages?before=5&limit=10"
        )
        assert response.status_code == 200
        args, kwargs = repository.get_messages.call_args
        assert kwargs["before_sequence"] == 5
        assert kwargs["limit"] == 10

    def test_get_messages_invalid_before(self, conversation_app):
        response = conversation_app.get(
            "/api/conversations/conv-1/messages?before=abc"
        )
        assert response.status_code == 400

    def test_get_messages_invalid_limit(self, conversation_app):
        response = conversation_app.get(
            "/api/conversations/conv-1/messages?limit=abc"
        )
        assert response.status_code == 400

    def test_get_messages_limit_out_of_range(self, conversation_app):
        response = conversation_app.get(
            "/api/conversations/conv-1/messages?limit=300"
        )
        assert response.status_code == 400


# --- POST /api/conversations/<id>/replies ---

class TestCreateReply:
    def test_create_reply_202(self, csrf_client, service):
        service.start_reply.return_value = ConversationCommand(
            run_id="run-3", conversation_id="conv-1"
        )
        response = csrf_client.post(
            "/api/conversations/conv-1/replies",
            json={
                "content": "What is the price?",
                "provider": "ollama",
                "model": "gemma2:9b",
                "evidence_revision": 1,
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        data = response.get_json()
        assert data["run_id"] == "run-3"

    def test_create_reply_409_stale(self, csrf_client, service):
        service.start_reply.side_effect = ValueError("stale evidence revision")
        response = csrf_client.post(
            "/api/conversations/conv-1/replies",
            json={
                "content": "What is the price?",
                "provider": "ollama",
                "model": "gemma2:9b",
                "evidence_revision": 1,
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 409
        data = response.get_json()
        assert data["error"]["code"] == "stale_evidence"

    def test_create_reply_409_busy(self, csrf_client, service):
        service.start_reply.side_effect = ValueError(
            "active reply already in progress for conversation"
        )
        response = csrf_client.post(
            "/api/conversations/conv-1/replies",
            json={
                "content": "What is the price?",
                "provider": "ollama",
                "model": "gemma2:9b",
                "evidence_revision": 1,
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 409
        data = response.get_json()
        assert data["error"]["code"] == "busy"

    def test_create_reply_400_missing_fields(self, csrf_client):
        response = csrf_client.post(
            "/api/conversations/conv-1/replies",
            json={"content": "Hello"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_create_reply_503_no_service(self, conversation_app_no_service):
        resp = conversation_app_no_service.post(
            "/api/conversations/conv-1/replies", json={"content": "Hi"}
        )
        assert resp.status_code == 503


# --- GET /assistant/<id> ---

class TestAssistantPage:
    def test_assistant_page_200(self, conversation_app):
        response = conversation_app.get("/assistant/conv-1")
        assert response.status_code == 200
        assert b"conv-1" in response.data


# --- Security ---

class TestSecurity:
    def test_security_loopback(self, csrf_client):
        response = csrf_client.post(
            "/api/conversations",
            json={"provider": "ollama", "model": "gemma2:9b"},
            environ_base={"REMOTE_ADDR": "10.0.0.2"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 403

    def test_security_host(self, csrf_client):
        response = csrf_client.post(
            "/api/conversations",
            json={"provider": "ollama", "model": "gemma2:9b"},
            base_url="http://attacker.example",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 403

    def test_security_csrf_missing(self, csrf_client):
        response = csrf_client.post(
            "/api/conversations",
            json={"provider": "ollama", "model": "gemma2:9b"},
        )
        assert response.status_code == 403

    def test_security_csrf_wrong(self, csrf_client):
        response = csrf_client.post(
            "/api/conversations",
            json={"provider": "ollama", "model": "gemma2:9b"},
            headers={"X-Qingpu-CSRF": "wrong-token"},
        )
        assert response.status_code == 403

    def test_get_endpoints_no_auth(self, conversation_app, repository, sample_conversation):
        repository.list_conversations.return_value = [sample_conversation]
        repository.get_conversation.return_value = sample_conversation
        repository.get_messages.return_value = []
        list_resp = conversation_app.get("/api/conversations")
        assert list_resp.status_code == 200
        get_resp = conversation_app.get("/api/conversations/conv-1")
        assert get_resp.status_code == 200
        msg_resp = conversation_app.get("/api/conversations/conv-1/messages")
        assert msg_resp.status_code == 200


# --- Wiring ---

def test_create_app_wires_conversation_blueprint():
    from qingpu_insight.web import create_app

    app = create_app()
    rules = [rule.endpoint for rule in app.url_map.iter_rules()]
    conversation_endpoints = [e for e in rules if e.startswith("conversation.")]
    assert len(conversation_endpoints) > 0
