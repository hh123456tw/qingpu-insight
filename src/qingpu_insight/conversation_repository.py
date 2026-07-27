from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pymysql
import pymysql.cursors

ConnectionFactory = Callable[[], pymysql.Connection]


class ConversationAlreadyHasListing(RuntimeError):
    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"conversation {conversation_id!r} already has an initial listing")


@dataclass(frozen=True)
class ConversationRecord:
    id: str
    title: str
    status: str
    default_provider: str
    default_model: str
    active_listing_id: str | None
    active_evidence_revision: int | None
    rolling_summary: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True)
class ConversationListingRecord:
    id: str
    conversation_id: str
    position: int
    listing_type: str | None
    source_listing_id: str | None
    canonical_url: str | None
    created_at: datetime


@dataclass(frozen=True)
class SnapshotRecord:
    id: str
    conversation_listing_id: str
    revision: int
    captured_at: datetime
    source_url: str
    structured_payload: dict
    content_sha256: str


@dataclass(frozen=True)
class EvidencePackRecord:
    id: str
    conversation_id: str
    conversation_listing_snapshot_id: str
    revision: int
    generated_at: datetime
    facts: dict
    valuation: dict | None
    comparables: list
    limitations: list


@dataclass(frozen=True)
class MessageRecord:
    id: str
    conversation_id: str
    sequence_no: int
    role: str
    content: str
    evidence_revision: int | None
    provider: str | None
    model: str | None
    citations: list[str]
    created_at: datetime
    fallback_reason: str | None = None


class MySQLConversationRepository:
    def __init__(self, connection: pymysql.Connection | ConnectionFactory) -> None:
        if callable(connection):
            self._connection_factory = connection
            self._close_connections = True
        else:
            self._connection_factory = lambda: connection
            self._close_connections = False

    @contextmanager
    def _connection(self) -> Iterator[pymysql.Connection]:
        connection = self._connection_factory()
        try:
            yield connection
        finally:
            if self._close_connections:
                close = getattr(connection, "close", None)
                if close is not None:
                    close()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_conversation(row: dict[str, Any]) -> ConversationRecord:
        return ConversationRecord(
            id=str(row["id"]),
            title=str(row["title"]),
            status=str(row["status"]),
            default_provider=str(row["default_provider"]),
            default_model=str(row["default_model"]),
            active_listing_id=row.get("active_listing_id"),
            active_evidence_revision=row.get("active_evidence_revision"),
            rolling_summary=row.get("rolling_summary"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row.get("deleted_at"),
        )

    @staticmethod
    def _row_to_message(row: dict[str, Any]) -> MessageRecord:
        raw = row.get("citations", "[]")
        if isinstance(raw, str):
            citations = json.loads(raw)
        elif isinstance(raw, (list, tuple)):
            citations = list(raw)
        else:
            citations = []
        return MessageRecord(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            sequence_no=int(row["sequence_no"]),
            role=str(row["role"]),
            content=str(row["content"]),
            evidence_revision=row.get("evidence_revision"),
            provider=row.get("provider"),
            model=row.get("model"),
            citations=citations,
            created_at=row["created_at"],
            fallback_reason=row.get("fallback_reason"),
        )

    # ------------------------------------------------------------------
    # conversation CRUD
    # ------------------------------------------------------------------

    def create_conversation(
        self, *, provider: str, model: str, title: str = "新的物件分析"
    ) -> ConversationRecord:
        now = datetime.now(UTC)
        cid = str(uuid4())
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO conversations
                           (id, title, status, default_provider, default_model,
                            active_listing_id, active_evidence_revision, rolling_summary,
                            created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (cid, title, "empty", provider, model,
                         None, None, None,
                         now, now),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return ConversationRecord(
            id=cid,
            title=title,
            status="empty",
            default_provider=provider,
            default_model=model,
            active_listing_id=None,
            active_evidence_revision=None,
            rolling_summary=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM conversations WHERE id = %s",
                        (conversation_id,),
                    )
                    row = cursor.fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if row is None:
            return None
        return self._row_to_conversation(row)

    def list_conversations(
        self, *, limit: int, before: tuple[datetime, str] | None = None
    ) -> list[ConversationRecord]:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    if before is not None:
                        before_dt, before_id = before
                        cursor.execute(
                            """SELECT * FROM conversations
                               WHERE deleted_at IS NULL
                                 AND (updated_at < %s OR (updated_at = %s AND id < %s))
                               ORDER BY updated_at DESC, id DESC
                               LIMIT %s""",
                            (before_dt, before_dt, before_id, limit),
                        )
                    else:
                        cursor.execute(
                            """SELECT * FROM conversations
                               WHERE deleted_at IS NULL
                               ORDER BY updated_at DESC, id DESC
                               LIMIT %s""",
                            (limit,),
                        )
                    rows = cursor.fetchall()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return [self._row_to_conversation(row) for row in rows]

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM conversations WHERE id = %s",
                        (conversation_id,),
                    )
                    affected = cursor.rowcount
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return affected == 1

    # ------------------------------------------------------------------
    # listing
    # ------------------------------------------------------------------

    def add_initial_listing(self, *, conversation_id: str) -> ConversationListingRecord:
        listing_id = str(uuid4())
        now = datetime.now(UTC)
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        """SELECT id FROM conversation_listings
                           WHERE conversation_id = %s AND position = 1
                           FOR UPDATE""",
                        (conversation_id,),
                    )
                    existing = cursor.fetchone()
                    if existing is not None:
                        raise ConversationAlreadyHasListing(conversation_id)
                    cursor.execute(
                        """INSERT INTO conversation_listings
                           (id, conversation_id, position, created_at)
                           VALUES (%s, %s, %s, %s)""",
                        (listing_id, conversation_id, 1, now),
                    )
                connection.commit()
            except ConversationAlreadyHasListing:
                connection.rollback()
                raise
            except Exception:
                connection.rollback()
                raise
        return ConversationListingRecord(
            id=listing_id,
            conversation_id=conversation_id,
            position=1,
            listing_type=None,
            source_listing_id=None,
            canonical_url=None,
            created_at=now,
        )

    # ------------------------------------------------------------------
    # listing read / update
    # ------------------------------------------------------------------

    def get_listing(self, listing_id: str) -> ConversationListingRecord | None:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM conversation_listings WHERE id = %s",
                        (listing_id,),
                    )
                    row = cursor.fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if row is None:
            return None
        return ConversationListingRecord(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            position=int(row["position"]),
            listing_type=row.get("listing_type"),
            source_listing_id=row.get("source_listing_id"),
            canonical_url=row.get("canonical_url"),
            created_at=row["created_at"],
        )

    def get_initial_listing(
        self, conversation_id: str
    ) -> ConversationListingRecord | None:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        """SELECT * FROM conversation_listings
                           WHERE conversation_id = %s AND position = 1""",
                        (conversation_id,),
                    )
                    row = cursor.fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if row is None:
            return None
        return ConversationListingRecord(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            position=int(row["position"]),
            listing_type=row.get("listing_type"),
            source_listing_id=row.get("source_listing_id"),
            canonical_url=row.get("canonical_url"),
            created_at=row["created_at"],
        )

    def update_listing(
        self,
        listing_id: str,
        *,
        listing_type: str | None,
        source_listing_id: str | None,
        canonical_url: str | None,
    ) -> None:
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """UPDATE conversation_listings
                           SET listing_type = %s,
                               source_listing_id = %s,
                               canonical_url = %s
                           WHERE id = %s""",
                        (listing_type, source_listing_id, canonical_url, listing_id),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError(f"listing not found: {listing_id}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------

    def append_snapshot(self, *, listing_id: str, source_url: str, payload: dict) -> SnapshotRecord:
        snapshot_id = str(uuid4())
        now = datetime.now(UTC)
        content_sha256 = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        """SELECT MAX(revision) AS rev
                           FROM conversation_listing_snapshots
                           WHERE conversation_listing_id = %s
                           FOR UPDATE""",
                        (listing_id,),
                    )
                    row = cursor.fetchone()
                    prev_rev = row["rev"] if row and row["rev"] is not None else 0
                    new_revision = int(prev_rev) + 1
                    cursor.execute(
                        """INSERT INTO conversation_listing_snapshots
                           (id, conversation_listing_id, revision, captured_at,
                            source_url, structured_payload, content_sha256)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (snapshot_id, listing_id, new_revision, now,
                         source_url, json.dumps(payload, sort_keys=True, ensure_ascii=False),
                         content_sha256),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return SnapshotRecord(
            id=snapshot_id,
            conversation_listing_id=listing_id,
            revision=new_revision,
            captured_at=now,
            source_url=source_url,
            structured_payload=payload,
            content_sha256=content_sha256,
        )

    # ------------------------------------------------------------------
    # evidence packs
    # ------------------------------------------------------------------

    def append_evidence_pack(
        self, *, conversation_id: str, snapshot_id: str, pack: dict
    ) -> EvidencePackRecord:
        pack_id = str(uuid4())
        now = datetime.now(UTC)
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        """SELECT cl.conversation_id
                           FROM conversation_listing_snapshots cls
                           JOIN conversation_listings cl ON cls.conversation_listing_id = cl.id
                           WHERE cls.id = %s""",
                        (snapshot_id,),
                    )
                    snap_row = cursor.fetchone()
                    if not snap_row or snap_row["conversation_id"] != conversation_id:
                        raise ValueError(
                            f"snapshot {snapshot_id} does not belong to"
                            f" conversation {conversation_id}"
                        )
                    cursor.execute(
                        """SELECT MAX(ep.revision) AS rev
                           FROM conversation_evidence_packs ep
                           WHERE ep.conversation_id = %s
                           FOR UPDATE""",
                        (conversation_id,),
                    )
                    row = cursor.fetchone()
                    prev_rev = row["rev"] if row and row["rev"] is not None else 0
                    new_revision = int(prev_rev) + 1
                    valuation_json = (
                        json.dumps(pack["valuation"], sort_keys=True, ensure_ascii=False)
                        if pack.get("valuation") is not None else None
                    )
                    cursor.execute(
                        """INSERT INTO conversation_evidence_packs
                           (id, conversation_id, conversation_listing_snapshot_id,
                            revision, generated_at, facts, valuation, comparables, limitations)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (pack_id, conversation_id, snapshot_id,
                         new_revision, now,
                         json.dumps(pack["facts"], sort_keys=True, ensure_ascii=False),
                         valuation_json,
                         json.dumps(pack["comparables"], sort_keys=True, ensure_ascii=False),
                         json.dumps(pack["limitations"], sort_keys=True, ensure_ascii=False)),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return EvidencePackRecord(
            id=pack_id,
            conversation_id=conversation_id,
            conversation_listing_snapshot_id=snapshot_id,
            revision=new_revision,
            generated_at=now,
            facts=pack["facts"],
            valuation=pack.get("valuation"),
            comparables=pack["comparables"],
            limitations=pack["limitations"],
        )

    # ------------------------------------------------------------------
    # activate evidence
    # ------------------------------------------------------------------

    def get_evidence_pack(self, conversation_id: str, revision: int) -> EvidencePackRecord | None:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        """SELECT * FROM conversation_evidence_packs
                           WHERE conversation_id = %s AND revision = %s""",
                        (conversation_id, revision),
                    )
                    row = cursor.fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if row is None:
            return None
        return EvidencePackRecord(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            conversation_listing_snapshot_id=str(row["conversation_listing_snapshot_id"]),
            revision=int(row["revision"]),
            generated_at=row["generated_at"],
            facts=json.loads(row["facts"]) if isinstance(row["facts"], str) else row["facts"],
            valuation=(
                json.loads(row["valuation"])
                if isinstance(row.get("valuation"), str) and row["valuation"] is not None
                else row.get("valuation")
            ),
            comparables=(
                json.loads(row["comparables"])
                if isinstance(row["comparables"], str)
                else row["comparables"]
            ),
            limitations=(
                json.loads(row["limitations"])
                if isinstance(row["limitations"], str)
                else row["limitations"]
            ),
        )

    def activate_evidence(
        self, *, conversation_id: str, listing_id: str, revision: int
    ) -> None:
        now = datetime.now(UTC)
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        """SELECT ep.id
                           FROM conversation_evidence_packs ep
                           JOIN conversation_listing_snapshots cls
                             ON cls.id = ep.conversation_listing_snapshot_id
                           JOIN conversation_listings cl
                             ON cl.id = cls.conversation_listing_id
                           WHERE ep.conversation_id = %s
                             AND ep.revision = %s
                             AND cl.id = %s
                             AND cl.conversation_id = %s
                           FOR UPDATE""",
                        (
                            conversation_id,
                            revision,
                            listing_id,
                            conversation_id,
                        ),
                    )
                    if cursor.fetchone() is None:
                        raise ValueError(
                            "evidence revision does not belong to active listing"
                        )
                    cursor.execute(
                        """UPDATE conversations
                           SET active_listing_id = %s,
                               active_evidence_revision = %s,
                               status = 'ready',
                               updated_at = %s
                           WHERE id = %s""",
                        (listing_id, revision, now, conversation_id),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError(
                            f"conversation not found: {conversation_id}"
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------

    def append_message(
        self, *, conversation_id: str, role: str, content: str,
        evidence_revision: int | None, provider: str | None,
        model: str | None, citations: list[str],
        fallback_reason: str | None = None,
    ) -> MessageRecord:
        msg_id = str(uuid4())
        now = datetime.now(UTC)
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        """SELECT MAX(sequence_no) AS seq
                           FROM conversation_messages
                           WHERE conversation_id = %s
                           FOR UPDATE""",
                        (conversation_id,),
                    )
                    row = cursor.fetchone()
                    prev_seq = row["seq"] if row and row["seq"] is not None else 0
                    new_seq = int(prev_seq) + 1
                    cursor.execute(
                        """INSERT INTO conversation_messages
                           (id, conversation_id, sequence_no, role, content,
                            evidence_revision, provider, model, fallback_reason,
                            citations, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (msg_id, conversation_id, new_seq, role, content,
                         evidence_revision, provider, model, fallback_reason,
                         json.dumps(citations, sort_keys=True, ensure_ascii=False),
                         now),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return MessageRecord(
            id=msg_id,
            conversation_id=conversation_id,
            sequence_no=new_seq,
            role=role,
            content=content,
            evidence_revision=evidence_revision,
            provider=provider,
            model=model,
            citations=citations,
            created_at=now,
            fallback_reason=fallback_reason,
        )

    def get_messages(
        self, *, conversation_id: str, limit: int = 50, before_sequence: int | None = None
    ) -> list[MessageRecord]:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    if before_sequence is not None:
                        cursor.execute(
                            """SELECT * FROM conversation_messages
                               WHERE conversation_id = %s AND sequence_no < %s
                               ORDER BY sequence_no DESC
                               LIMIT %s""",
                            (conversation_id, before_sequence, limit),
                        )
                    else:
                        cursor.execute(
                            """SELECT * FROM conversation_messages
                               WHERE conversation_id = %s
                               ORDER BY sequence_no DESC
                               LIMIT %s""",
                            (conversation_id, limit),
                        )
                    rows = cursor.fetchall()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return [self._row_to_message(row) for row in rows]

    # ------------------------------------------------------------------
    # status / summary
    # ------------------------------------------------------------------

    def set_status(self, *, conversation_id: str, status: str) -> None:
        now = datetime.now(UTC)
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE conversations SET status = %s, updated_at = %s WHERE id = %s",
                        (status, now, conversation_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def set_title(self, *, conversation_id: str, title: str) -> None:
        normalized = " ".join(title.split())[:160]
        if not normalized:
            return
        now = datetime.now(UTC)
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """UPDATE conversations
                           SET title = %s, updated_at = %s
                           WHERE id = %s""",
                        (normalized, now, conversation_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def set_rolling_summary(self, *, conversation_id: str, summary: str) -> None:
        now = datetime.now(UTC)
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE conversations"
                        " SET rolling_summary = %s, updated_at = %s"
                        " WHERE id = %s",
                        (summary, now, conversation_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
