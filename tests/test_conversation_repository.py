from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from qingpu_insight.conversation_repository import (
    ConversationAlreadyHasListing,
    ConversationListingRecord,
    ConversationRecord,
    EvidencePackRecord,
    MessageRecord,
    MySQLConversationRepository,
    SnapshotRecord,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _cid() -> str:
    return str(uuid4())


def _conversation_row(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = dict(
        id=_cid(),
        title="test",
        status="empty",
        default_provider="ollama",
        default_model="gpt-4",
        active_listing_id=None,
        active_evidence_revision=None,
        rolling_summary=None,
        created_at=_now(),
        updated_at=_now(),
        deleted_at=None,
    )
    defaults.update(overrides)
    return defaults


class FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_rows: list[dict[str, Any] | None] = []
        self.rowcount = 1

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self.executed.append((sql, params))
        return self.rowcount

    def fetchone(self) -> dict[str, Any] | None:
        if not self.fetch_rows:
            return None
        return self.fetch_rows.pop(0)

    def fetchall(self) -> list[dict[str, Any]]:
        rows = [r for r in self.fetch_rows if r is not None]
        self.fetch_rows.clear()
        return rows

    def close(self) -> None:
        pass

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_class: object = None) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
def fake_conn() -> FakeConnection:
    return FakeConnection()


# ---------------------------------------------------------------------------
# create_conversation
# ---------------------------------------------------------------------------


def test_create_conversation_returns_record(fake_conn: FakeConnection) -> None:
    repo = MySQLConversationRepository(fake_conn)
    result = repo.create_conversation(provider="ollama", model="gpt-4", title="test-title")
    assert isinstance(result, ConversationRecord)
    assert result.title == "test-title"
    assert result.default_provider == "ollama"
    assert result.default_model == "gpt-4"
    assert result.status == "empty"
    assert result.active_listing_id is None
    assert result.active_evidence_revision is None
    assert result.rolling_summary is None
    assert result.deleted_at is None
    assert isinstance(result.id, str) and len(result.id) > 0
    sql = " ".join(fake_conn.cursor_instance.executed[0][0].lower().split())
    assert "insert into conversations" in sql
    assert fake_conn.commits == 1


def test_create_conversation_uses_default_title(fake_conn: FakeConnection) -> None:
    repo = MySQLConversationRepository(fake_conn)
    result = repo.create_conversation(provider="gemini", model="gemini-pro")
    assert result.title == "新的物件分析"
    assert result.default_provider == "gemini"
    assert result.default_model == "gemini-pro"


# ---------------------------------------------------------------------------
# get_conversation
# ---------------------------------------------------------------------------


def test_get_conversation_returns_record(fake_conn: FakeConnection) -> None:
    row = _conversation_row(id="conv-1", title="found")
    fake_conn.cursor_instance.fetch_rows = [row]
    repo = MySQLConversationRepository(fake_conn)
    result = repo.get_conversation("conv-1")
    assert result is not None
    assert result.id == "conv-1"
    assert result.title == "found"
    sql = " ".join(fake_conn.cursor_instance.executed[0][0].lower().split())
    assert "select" in sql and "from conversations" in sql and "where id = %s" in sql


def test_get_conversation_returns_none_when_missing(fake_conn: FakeConnection) -> None:
    fake_conn.cursor_instance.fetch_rows = []
    repo = MySQLConversationRepository(fake_conn)
    result = repo.get_conversation("missing")
    assert result is None


# ---------------------------------------------------------------------------
# list_conversations
# ---------------------------------------------------------------------------


def test_list_conversations_without_cursor(fake_conn: FakeConnection) -> None:
    rows = [_conversation_row(id="c1"), _conversation_row(id="c2")]
    fake_conn.cursor_instance.fetch_rows = rows
    repo = MySQLConversationRepository(fake_conn)
    result = repo.list_conversations(limit=10)
    assert len(result) == 2
    sql = " ".join(fake_conn.cursor_instance.executed[0][0].lower().split())
    assert "order by updated_at desc" in sql or "order by updated_at desc, id desc" in sql
    assert "%s" in sql  # parameterized
    assert "where deleted_at is null" in sql or "deleted_at" not in sql


def test_list_conversations_with_cursor(fake_conn: FakeConnection) -> None:
    before = (_now(), "c1")
    rows = [_conversation_row(id="c2")]
    fake_conn.cursor_instance.fetch_rows = rows
    repo = MySQLConversationRepository(fake_conn)
    result = repo.list_conversations(limit=5, before=before)
    assert len(result) == 1
    sql = " ".join(fake_conn.cursor_instance.executed[0][0].lower().split())
    assert "updated_at <" in sql and "id <" in sql


# ---------------------------------------------------------------------------
# add_initial_listing
# ---------------------------------------------------------------------------


def test_add_initial_listing_creates_position_one(fake_conn: FakeConnection) -> None:
    fake_conn.cursor_instance.fetch_rows = [None]  # no existing listing
    repo = MySQLConversationRepository(fake_conn)
    result = repo.add_initial_listing(conversation_id="conv-1")
    assert isinstance(result, ConversationListingRecord)
    assert result.conversation_id == "conv-1"
    assert result.position == 1
    assert result.listing_type is None
    assert result.source_listing_id is None
    assert result.canonical_url is None
    # Check FOR UPDATE was used on the existence check
    executed = fake_conn.cursor_instance.executed
    assert any("for update" in sql.lower() for sql, _ in executed)
    assert fake_conn.commits == 1


def test_add_initial_listing_raises_when_already_exists(fake_conn: FakeConnection) -> None:
    fake_conn.cursor_instance.fetch_rows = [{"id": "existing"}]
    repo = MySQLConversationRepository(fake_conn)
    with pytest.raises(ConversationAlreadyHasListing) as exc:
        repo.add_initial_listing(conversation_id="conv-1")
    assert "conv-1" in str(exc.value)


# ---------------------------------------------------------------------------
# append_snapshot
# ---------------------------------------------------------------------------


def test_append_snapshot_first_revision(fake_conn: FakeConnection) -> None:
    fake_conn.cursor_instance.fetch_rows = [None]  # MAX returns NULL
    repo = MySQLConversationRepository(fake_conn)
    payload = {"price": 1000, "url": "https://example.com"}
    result = repo.append_snapshot(
        listing_id="listing-1", source_url="https://example.com", payload=payload,
    )
    assert isinstance(result, SnapshotRecord)
    assert result.revision == 1
    assert result.source_url == "https://example.com"
    assert result.structured_payload == payload
    expected_sha = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    assert result.content_sha256 == expected_sha
    executed = fake_conn.cursor_instance.executed
    assert any("for update" in sql.lower() for sql, _ in executed)
    assert fake_conn.commits == 1


def test_append_snapshot_increments_revision(fake_conn: FakeConnection) -> None:
    fake_conn.cursor_instance.fetch_rows = [{"rev": 2}]  # MAX returns 2
    repo = MySQLConversationRepository(fake_conn)
    result = repo.append_snapshot(
        listing_id="listing-1", source_url="https://example.com", payload={},
    )
    assert result.revision == 3


# ---------------------------------------------------------------------------
# append_evidence_pack
# ---------------------------------------------------------------------------


def test_append_evidence_pack_first_revision(fake_conn: FakeConnection) -> None:
    fake_conn.cursor_instance.fetch_rows = [None]
    repo = MySQLConversationRepository(fake_conn)
    pack = dict(
        facts={"area": 30.0},
        valuation={"fair_value": 15_000_000},
        comparables=[{"id": "cmp-1"}],
        limitations=["small sample"],
    )
    result = repo.append_evidence_pack(conversation_id="conv-1", snapshot_id="snap-1", pack=pack)
    assert isinstance(result, EvidencePackRecord)
    assert result.revision == 1
    assert result.facts == {"area": 30.0}
    assert result.valuation == {"fair_value": 15_000_000}
    assert result.comparables == [{"id": "cmp-1"}]
    assert result.limitations == ["small sample"]
    executed = fake_conn.cursor_instance.executed
    assert any("for update" in sql.lower() for sql, _ in executed)
    assert fake_conn.commits == 1


def test_append_evidence_pack_increments_revision(fake_conn: FakeConnection) -> None:
    fake_conn.cursor_instance.fetch_rows = [{"rev": 5}]
    repo = MySQLConversationRepository(fake_conn)
    pack = dict(facts={}, valuation=None, comparables=[], limitations=[])
    result = repo.append_evidence_pack(conversation_id="conv-1", snapshot_id="snap-1", pack=pack)
    assert result.revision == 6


# ---------------------------------------------------------------------------
# activate_evidence
# ---------------------------------------------------------------------------


def test_activate_evidence_updates_conversation(fake_conn: FakeConnection) -> None:
    repo = MySQLConversationRepository(fake_conn)
    fake_conn.cursor_instance.rowcount = 1
    repo.activate_evidence(conversation_id="conv-1", listing_id="listing-1", revision=3)
    sql = " ".join(fake_conn.cursor_instance.executed[0][0].lower().split())
    assert "update conversations" in sql
    assert "active_listing_id" in sql
    assert "active_evidence_revision" in sql
    assert "where id = %s" in sql
    assert fake_conn.commits == 1


# ---------------------------------------------------------------------------
# append_message
# ---------------------------------------------------------------------------


def test_append_message_first_sequence(fake_conn: FakeConnection) -> None:
    fake_conn.cursor_instance.fetch_rows = [None]  # MAX returns NULL
    repo = MySQLConversationRepository(fake_conn)
    result = repo.append_message(
        conversation_id="conv-1",
        role="user",
        content="Hello",
        evidence_revision=None,
        provider=None,
        model=None,
        citations=[],
    )
    assert isinstance(result, MessageRecord)
    assert result.sequence_no == 1
    assert result.role == "user"
    assert result.content == "Hello"
    assert result.citations == []
    assert fake_conn.commits == 1


def test_append_message_increments_sequence(fake_conn: FakeConnection) -> None:
    fake_conn.cursor_instance.fetch_rows = [{"seq": 42}]
    repo = MySQLConversationRepository(fake_conn)
    result = repo.append_message(
        conversation_id="conv-1",
        role="assistant",
        content="Hi",
        evidence_revision=1,
        provider="ollama",
        model="gpt-4",
        citations=["src-1"],
    )
    assert result.sequence_no == 43
    assert result.evidence_revision == 1
    assert result.provider == "ollama"
    assert result.model == "gpt-4"
    assert result.citations == ["src-1"]


def test_two_messages_get_distinct_sequence_numbers(fake_conn: FakeConnection) -> None:
    repo = MySQLConversationRepository(fake_conn)
    fake_conn.cursor_instance.fetch_rows = [None]
    msg1 = repo.append_message(
        conversation_id="conv-1", role="user", content="first",
        evidence_revision=None, provider=None, model=None, citations=[],
    )
    fake_conn.cursor_instance.fetch_rows = [{"seq": 1}]
    msg2 = repo.append_message(
        conversation_id="conv-1", role="user", content="second",
        evidence_revision=None, provider=None, model=None, citations=[],
    )
    assert msg1.sequence_no == 1
    assert msg2.sequence_no == 2
    assert msg1.sequence_no != msg2.sequence_no


# ---------------------------------------------------------------------------
# get_messages
# ---------------------------------------------------------------------------


def test_get_messages_default_limit(fake_conn: FakeConnection) -> None:
    rows: list[dict[str, Any]] = [
        dict(id="m1", conversation_id="conv-1", sequence_no=1, role="user",
             content="hi", evidence_revision=None, provider=None, model=None,
             citations="[]", created_at=_now()),
    ]
    fake_conn.cursor_instance.fetch_rows = rows
    repo = MySQLConversationRepository(fake_conn)
    result = repo.get_messages(conversation_id="conv-1")
    assert len(result) == 1
    sql = " ".join(fake_conn.cursor_instance.executed[0][0].lower().split())
    assert "order by sequence_no desc" in sql or "order by" in sql
    assert "%s" in sql


def test_get_messages_with_before_sequence(fake_conn: FakeConnection) -> None:
    rows: list[dict[str, Any]] = [
        dict(id="m2", conversation_id="conv-1", sequence_no=2, role="assistant",
             content="there", evidence_revision=None, provider=None, model=None,
             citations="[]", created_at=_now()),
    ]
    fake_conn.cursor_instance.fetch_rows = rows
    repo = MySQLConversationRepository(fake_conn)
    result = repo.get_messages(conversation_id="conv-1", before_sequence=5)
    assert len(result) == 1
    sql = " ".join(fake_conn.cursor_instance.executed[0][0].lower().split())
    assert "sequence_no <" in sql


# ---------------------------------------------------------------------------
# set_status / set_rolling_summary / delete_conversation
# ---------------------------------------------------------------------------


def test_set_status_updates_status(fake_conn: FakeConnection) -> None:
    repo = MySQLConversationRepository(fake_conn)
    fake_conn.cursor_instance.rowcount = 1
    repo.set_status(conversation_id="conv-1", status="ready")
    sql = " ".join(fake_conn.cursor_instance.executed[0][0].lower().split())
    assert "update conversations" in sql
    assert "status = %s" in sql
    assert fake_conn.commits == 1


def test_set_rolling_summary_updates_summary(fake_conn: FakeConnection) -> None:
    repo = MySQLConversationRepository(fake_conn)
    fake_conn.cursor_instance.rowcount = 1
    repo.set_rolling_summary(conversation_id="conv-1", summary="conversation so far...")
    sql = " ".join(fake_conn.cursor_instance.executed[0][0].lower().split())
    assert "update conversations" in sql
    assert "rolling_summary = %s" in sql
    assert fake_conn.commits == 1


def test_delete_conversation_removes_row(fake_conn: FakeConnection) -> None:
    repo = MySQLConversationRepository(fake_conn)
    fake_conn.cursor_instance.rowcount = 1
    result = repo.delete_conversation("conv-1")
    assert result is True
    sql = " ".join(fake_conn.cursor_instance.executed[0][0].lower().split())
    assert "delete from conversations" in sql
    assert "where id = %s" in sql
    assert fake_conn.commits == 1


def test_delete_conversation_returns_false_when_missing(fake_conn: FakeConnection) -> None:
    repo = MySQLConversationRepository(fake_conn)
    fake_conn.cursor_instance.rowcount = 0
    result = repo.delete_conversation("missing")
    assert result is False


# ---------------------------------------------------------------------------
# repository error handling — rollback on exception
# ---------------------------------------------------------------------------


def test_repo_rolls_back_on_error(fake_conn: FakeConnection) -> None:
    repo = MySQLConversationRepository(fake_conn)

    class FakeError(Exception):
        pass

    original = fake_conn.cursor_instance.execute
    def broken_execute(sql: str, params: tuple[Any, ...] = ()) -> int:
        if "insert" in sql.lower():
            raise FakeError("db fail")
        return original(sql, params)

    fake_conn.cursor_instance.execute = broken_execute  # type: ignore[assignment]
    with pytest.raises(FakeError):
        repo.create_conversation(provider="ollama", model="gpt-4")
    assert fake_conn.rollbacks == 1
