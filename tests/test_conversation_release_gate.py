"""Deterministic release gate for the Conversation Assistant."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from qingpu_insight.conversation_import import ConversationImportService
from qingpu_insight.conversation_listing_parser import (
    ListingPageVerificationRequired,
)
from qingpu_insight.conversation_providers import ConversationProviderRegistry
from qingpu_insight.conversation_repository import (
    ConversationAlreadyHasListing,
    ConversationListingRecord,
    ConversationRecord,
    EvidencePackRecord,
    MessageRecord,
    SnapshotRecord,
)
from qingpu_insight.conversation_service import ConversationService
from qingpu_insight.conversation_urls import Initial591Url
from qingpu_insight.conversation_validation import (
    ChatAnswerDraft,
    PropertyClaim,
    validate_chat_answer,
)
from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import CONVERSATION_REPLY, JobRun, JobService, JobStatus

# ---------------------------------------------------------------------------
# In-memory fake repository
# ---------------------------------------------------------------------------

class FakeConversationRepository:
    """In-memory dict-based repository for deterministic testing."""

    def __init__(self) -> None:
        self.conversations: dict[str, ConversationRecord] = {}
        self.listings: dict[str, ConversationListingRecord] = {}
        self.snapshots: dict[str, SnapshotRecord] = {}
        self.evidence_packs: list[EvidencePackRecord] = []
        self.messages: dict[str, list[MessageRecord]] = {}

    def create_conversation(
        self, *, provider: str, model: str, title: str = "新的物件分析"
    ) -> ConversationRecord:
        now = datetime.now(UTC)
        cid = str(uuid4())
        record = ConversationRecord(
            id=cid, title=title, status="empty",
            default_provider=provider, default_model=model,
            active_listing_id=None, active_evidence_revision=None,
            rolling_summary=None, created_at=now, updated_at=now,
            deleted_at=None,
        )
        self.conversations[cid] = record
        return record

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        return self.conversations.get(conversation_id)

    def list_conversations(
        self, *, limit: int, before: tuple[datetime, str] | None = None
    ) -> list[ConversationRecord]:
        records = sorted(
            [c for c in self.conversations.values() if c.deleted_at is None],
            key=lambda c: (c.updated_at, c.id), reverse=True,
        )
        if before is not None:
            before_dt, before_id = before
            records = [
                c for c in records
                if c.updated_at < before_dt
                or (c.updated_at == before_dt and c.id < before_id)
            ]
        return records[:limit]

    def delete_conversation(self, conversation_id: str) -> bool:
        if conversation_id not in self.conversations:
            return False
        del self.conversations[conversation_id]
        self.listings = {
            k: v for k, v in self.listings.items()
            if v.conversation_id != conversation_id
        }
        self.messages.pop(conversation_id, None)
        return True

    def add_initial_listing(
        self, *, conversation_id: str
    ) -> ConversationListingRecord:
        if any(
            lst.conversation_id == conversation_id and lst.position == 1
            for lst in self.listings.values()
        ):
            raise ConversationAlreadyHasListing(conversation_id)
        listing_id = str(uuid4())
        now = datetime.now(UTC)
        record = ConversationListingRecord(
            id=listing_id, conversation_id=conversation_id,
            position=1, listing_type=None, source_listing_id=None,
            canonical_url=None, created_at=now,
        )
        self.listings[listing_id] = record
        return record

    def get_listing(self, listing_id: str) -> ConversationListingRecord | None:
        return self.listings.get(listing_id)

    def update_listing(
        self, listing_id: str, *, listing_type: str | None,
        source_listing_id: str | None, canonical_url: str | None,
    ) -> None:
        listing = self.listings.get(listing_id)
        if listing is None:
            return
        self.listings[listing_id] = ConversationListingRecord(
            id=listing.id, conversation_id=listing.conversation_id,
            position=listing.position,
            listing_type=listing_type,
            source_listing_id=source_listing_id,
            canonical_url=canonical_url,
            created_at=listing.created_at,
        )

    def append_snapshot(
        self, *, listing_id: str, source_url: str, payload: dict
    ) -> SnapshotRecord:
        snapshot_id = str(uuid4())
        now = datetime.now(UTC)
        content_sha256 = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        existing = [
            s for s in self.snapshots.values()
            if s.conversation_listing_id == listing_id
        ]
        prev_rev = max((s.revision for s in existing), default=0)
        record = SnapshotRecord(
            id=snapshot_id, conversation_listing_id=listing_id,
            revision=prev_rev + 1, captured_at=now,
            source_url=source_url, structured_payload=payload,
            content_sha256=content_sha256,
        )
        self.snapshots[snapshot_id] = record
        return record

    def append_evidence_pack(
        self, *, conversation_id: str, snapshot_id: str, pack: dict
    ) -> EvidencePackRecord:
        pack_id = str(uuid4())
        now = datetime.now(UTC)
        snapshot = self.snapshots.get(snapshot_id)
        if snapshot is None:
            raise ValueError(f"snapshot {snapshot_id} does not exist")
        if snapshot.conversation_listing_id not in self.listings:
            raise ValueError(f"snapshot {snapshot_id} has no listing")
        prev_eps = [
            ep for ep in self.evidence_packs
            if ep.conversation_id == conversation_id
        ]
        prev_rev = max((ep.revision for ep in prev_eps), default=0)
        record = EvidencePackRecord(
            id=pack_id, conversation_id=conversation_id,
            conversation_listing_snapshot_id=snapshot_id,
            revision=prev_rev + 1, generated_at=now,
            facts=pack["facts"], valuation=pack.get("valuation"),
            comparables=pack["comparables"],
            limitations=pack["limitations"],
        )
        self.evidence_packs.append(record)
        return record

    def get_evidence_pack(
        self, conversation_id: str, revision: int
    ) -> EvidencePackRecord | None:
        for ep in self.evidence_packs:
            if ep.conversation_id == conversation_id and ep.revision == revision:
                return ep
        return None

    def activate_evidence(
        self, *, conversation_id: str, listing_id: str, revision: int
    ) -> None:
        conv = self.conversations.get(conversation_id)
        if conv is None:
            return
        now = datetime.now(UTC)
        self.conversations[conversation_id] = ConversationRecord(
            id=conv.id, title=conv.title, status="ready",
            default_provider=conv.default_provider,
            default_model=conv.default_model,
            active_listing_id=listing_id,
            active_evidence_revision=revision,
            rolling_summary=conv.rolling_summary,
            created_at=conv.created_at, updated_at=now,
            deleted_at=conv.deleted_at,
        )

    def append_message(
        self, *, conversation_id: str, role: str, content: str,
        evidence_revision: int | None, provider: str | None,
        model: str | None, citations: list[str],
    ) -> MessageRecord:
        msg_id = str(uuid4())
        now = datetime.now(UTC)
        existing = self.messages.get(conversation_id, [])
        prev_seq = max((m.sequence_no for m in existing), default=0)
        record = MessageRecord(
            id=msg_id, conversation_id=conversation_id,
            sequence_no=prev_seq + 1, role=role, content=content,
            evidence_revision=evidence_revision, provider=provider,
            model=model, citations=citations, created_at=now,
        )
        if conversation_id not in self.messages:
            self.messages[conversation_id] = []
        self.messages[conversation_id].append(record)
        return record

    def get_messages(
        self, *, conversation_id: str, limit: int = 50,
        before_sequence: int | None = None,
    ) -> list[MessageRecord]:
        existing = self.messages.get(conversation_id, [])
        sorted_msgs = sorted(existing, key=lambda m: m.sequence_no, reverse=True)
        if before_sequence is not None:
            sorted_msgs = [
                m for m in sorted_msgs if m.sequence_no < before_sequence
            ]
        return sorted_msgs[:limit]

    def set_status(self, *, conversation_id: str, status: str) -> None:
        conv = self.conversations.get(conversation_id)
        if conv is None:
            return
        now = datetime.now(UTC)
        self.conversations[conversation_id] = ConversationRecord(
            id=conv.id, title=conv.title, status=status,
            default_provider=conv.default_provider,
            default_model=conv.default_model,
            active_listing_id=conv.active_listing_id,
            active_evidence_revision=conv.active_evidence_revision,
            rolling_summary=conv.rolling_summary,
            created_at=conv.created_at, updated_at=now,
            deleted_at=conv.deleted_at,
        )

    def set_rolling_summary(
        self, *, conversation_id: str, summary: str
    ) -> None:
        conv = self.conversations.get(conversation_id)
        if conv is None:
            return
        now = datetime.now(UTC)
        self.conversations[conversation_id] = ConversationRecord(
            id=conv.id, title=conv.title, status=conv.status,
            default_provider=conv.default_provider,
            default_model=conv.default_model,
            active_listing_id=conv.active_listing_id,
            active_evidence_revision=conv.active_evidence_revision,
            rolling_summary=summary,
            created_at=conv.created_at, updated_at=now,
            deleted_at=conv.deleted_at,
        )


# ---------------------------------------------------------------------------
# In-memory fake job repository
# ---------------------------------------------------------------------------

class FakeJobRepository:
    def __init__(self) -> None:
        self._runs: dict[str, JobRun] = {}

    def create_or_get(self, run: JobRun) -> tuple[JobRun, bool]:
        existing = self._find_active_by_key(run.idempotency_key)
        if existing is not None:
            return existing, False
        self._runs[run.run_id] = run
        return run, True

    def get(self, run_id: str) -> JobRun | None:
        return self._runs.get(run_id)

    def find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        for run in self._runs.values():
            if (
                run.idempotency_key == idempotency_key
                and run.status in ("pending", "running", "retry_wait")
            ):
                return run
        return None

    def _find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        for run in self._runs.values():
            if (
                run.idempotency_key == idempotency_key
                and run.status in ("pending", "running", "retry_wait")
            ):
                return run
        return None

    def list_recent(
        self, limit: int = 20, job_type: str | None = None
    ) -> list[JobRun]:
        runs = sorted(
            self._runs.values(),
            key=lambda r: r.started_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        if job_type is not None:
            runs = [r for r in runs if r.job_type == job_type]
        return runs[:limit]

    def list_active(self, job_type: str) -> list[JobRun]:
        return [
            r for r in self._runs.values()
            if r.job_type == job_type and r.status in ("pending", "running", "retry_wait")
        ]

    def update_summary(
        self, run_id: str, expected_status: JobStatus,
        summary: dict[str, object],
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != expected_status:
            return False
        self._runs[run_id] = JobRun(
            run_id=run.run_id, job_type=run.job_type,
            trigger=run.trigger, idempotency_key=run.idempotency_key,
            status=run.status, started_at=run.started_at,
            finished_at=run.finished_at, attempt=run.attempt,
            input_version=run.input_version,
            output_version=run.output_version, summary=summary,
            error_code=run.error_code, error_message=run.error_message,
        )
        return True

    def transition(
        self, run_id: str, current_status: JobStatus,
        target_status: JobStatus, *, output_version: str | None = None,
        summary: dict[str, object] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != current_status:
            return False
        now = datetime.now(UTC)
        started = run.started_at
        finished = run.finished_at
        if target_status == "running" and started is None:
            started = now
        if target_status in ("succeeded", "failed", "skipped", "needs_attention"):
            finished = now
        self._runs[run_id] = JobRun(
            run_id=run.run_id, job_type=run.job_type,
            trigger=run.trigger, idempotency_key=run.idempotency_key,
            status=target_status, started_at=started,
            finished_at=finished, attempt=run.attempt,
            input_version=run.input_version,
            output_version=output_version,
            summary=summary if summary is not None else run.summary,
            error_code=error_code, error_message=error_message,
        )
        return True


# ---------------------------------------------------------------------------
# Fake browser adapters
# ---------------------------------------------------------------------------

class FakeVerificationBrowser:
    """Browser that always triggers a verification page."""

    def capture(self, initial_url: Initial591Url) -> None:
        raise ListingPageVerificationRequired("模拟验证页面")


class FakeSuccessBrowser:
    """Browser that returns a static captured listing."""

    def capture(self, initial_url: Initial591Url) -> None:
        from decimal import Decimal

        from qingpu_insight.conversation_listing_capture import CapturedListing
        from qingpu_insight.conversation_listing_parser import ParsedListingDetail
        return CapturedListing(
            final_url="https://sale.591.com.tw/home/house/detail/2/123.html",
            detail=ParsedListingDetail(
                listing_type="sale",
                source_listing_id="123",
                title="測試住宅",
                total_price_twd=18800000,
                unit_price_twd_per_ping=578000,
                area_ping=Decimal("32.5"),
                layout="3房2廳2衛",
                address="測試路100號",
                community_name="測試社區",
                builder_name="測試建商",
                building_type="住宅大樓",
                floor="12",
                total_floors=15,
                age_years=Decimal("3.0"),
                parking_type="坡道平面",
                latitude=Decimal("25.033611"),
                longitude=Decimal("121.565000"),
                source_updated_text="2025-06-01 更新",
            ),
        )


# ---------------------------------------------------------------------------
# Release gate tests
# ---------------------------------------------------------------------------

class TestConversationReleaseGate:

    # ------------------------------------------------------------------
    # Scenario 1: Full lifecycle via repository
    # ------------------------------------------------------------------

    def test_full_conversation_lifecycle(self) -> None:
        """Create conversation → import → chat → verify evidence revision."""
        repo = FakeConversationRepository()

        # 1. Create conversation
        conv = repo.create_conversation(provider="rule", model="rule")
        assert conv.status == "empty"
        assert conv.default_provider == "rule"

        # 2. Add initial listing
        listing = repo.add_initial_listing(conversation_id=conv.id)
        assert listing.position == 1
        assert listing.conversation_id == conv.id

        # 3. Append snapshot revision 1
        snapshot_payload = {
            "title": "測試住宅",
            "total_price_twd": 18800000,
            "unit_price_twd_per_ping": 578000,
        }
        snapshot = repo.append_snapshot(
            listing_id=listing.id,
            source_url="https://sale.591.com.tw/home/house/detail/2/123.html",
            payload=snapshot_payload,
        )
        assert snapshot.revision == 1

        # 4. Append evidence pack revision 1
        pack = {
            "facts": {"price": 18800000, "unit_price": 578000},
            "valuation": None,
            "comparables": [],
            "limitations": [],
        }
        ep = repo.append_evidence_pack(
            conversation_id=conv.id, snapshot_id=snapshot.id, pack=pack,
        )
        assert ep.revision == 1

        # 5. Activate evidence revision 1
        repo.activate_evidence(
            conversation_id=conv.id, listing_id=listing.id, revision=ep.revision,
        )
        updated = repo.get_conversation(conv.id)
        assert updated is not None
        assert updated.active_evidence_revision == 1
        assert updated.active_listing_id == listing.id
        assert updated.status == "ready"

        # 6. Append user message
        user_msg = repo.append_message(
            conversation_id=conv.id, role="user",
            content="這個物件價格合理嗎？",
            evidence_revision=None, provider=None, model=None,
            citations=[],
        )
        assert user_msg.sequence_no == 1

        # 7. Validate a grounded answer using real validator
        draft = ChatAnswerDraft(
            answer="根據實價登錄，總價 1,880 萬元。",
            property_claims=[
                PropertyClaim(text="總價 1,880 萬元", fact_ids=["fact.price"]),
            ],
            general_guidance=["一般建議：購屋前應確認產權清楚。"],
            suggested_questions=["附近成交行情如何？"],
        )
        validated = validate_chat_answer(
            draft,
            available_fact_ids={"fact.price", "fact.unit_price"},
            evidence_revision=1,
        )
        assert validated.evidence_revision == 1
        assert "fact.price" in validated.citations

        # 8. Append assistant message with the validated answer
        asst_msg = repo.append_message(
            conversation_id=conv.id, role="assistant",
            content=validated.answer,
            evidence_revision=validated.evidence_revision,
            provider="rule", model="rule",
            citations=list(validated.citations),
        )
        assert asst_msg.sequence_no == 2
        assert asst_msg.evidence_revision == 1
        assert asst_msg.provider == "rule"

        # 9. Verify assistant message persisted with correct revision
        msgs = repo.get_messages(conversation_id=conv.id)
        assert len(msgs) == 2
        asst_msgs = [m for m in msgs if m.role == "assistant"]
        assert len(asst_msgs) == 1
        assert asst_msgs[0].evidence_revision == 1
        assert asst_msgs[0].citations == ["fact.price"]

    # ------------------------------------------------------------------
    # Scenario 1b: Snapshot revision 2, older answer still references rev 1
    # ------------------------------------------------------------------

    def test_revision_independence(self) -> None:
        """Older answer references revision 1 after refresh to revision 2."""
        repo = FakeConversationRepository()

        conv = repo.create_conversation(provider="rule", model="rule")
        listing = repo.add_initial_listing(conversation_id=conv.id)

        snap1 = repo.append_snapshot(
            listing_id=listing.id,
            source_url="https://sale.591.com.tw/home/house/detail/2/123.html",
            payload={"price": 1800},
        )
        ep1 = repo.append_evidence_pack(
            conversation_id=conv.id, snapshot_id=snap1.id,
            pack={"facts": {"p1": 1800}, "valuation": None,
                  "comparables": [], "limitations": []},
        )
        repo.activate_evidence(
            conversation_id=conv.id, listing_id=listing.id, revision=ep1.revision,
        )
        msg1 = repo.append_message(
            conversation_id=conv.id, role="assistant",
            content="Version 1 answer", evidence_revision=1,
            provider="rule", model="rule", citations=["f1"],
        )
        assert msg1.evidence_revision == 1

        snap2 = repo.append_snapshot(
            listing_id=listing.id,
            source_url="https://sale.591.com.tw/home/house/detail/2/123.html",
            payload={"price": 2000},
        )
        assert snap2.revision == 2
        ep2 = repo.append_evidence_pack(
            conversation_id=conv.id, snapshot_id=snap2.id,
            pack={"facts": {"p2": 2000}, "valuation": None,
                  "comparables": [], "limitations": []},
        )
        assert ep2.revision == 2
        repo.activate_evidence(
            conversation_id=conv.id, listing_id=listing.id, revision=ep2.revision,
        )

        conv2 = repo.get_conversation(conv.id)
        assert conv2 is not None
        assert conv2.active_evidence_revision == 2

        msgs = repo.get_messages(conversation_id=conv.id)
        assert len(msgs) == 1
        assert msgs[0].evidence_revision == 1

    # ------------------------------------------------------------------
    # Scenario 2: Verification → needs_attention
    # ------------------------------------------------------------------

    def test_verification_needs_attention(self) -> None:
        """Simulate verification page → import yields needs_attention."""
        repo = FakeConversationRepository()
        conv = repo.create_conversation(provider="rule", model="rule")

        browser = FakeVerificationBrowser()
        import_service = ConversationImportService(
            repository=repo, browser=browser,
        )

        result = import_service.import_initial_listing(
            conversation_id=conv.id,
            raw_url="https://sale.591.com.tw/home/house/detail/2/123.html",
        )
        assert result.outcome == "needs_attention"
        assert result.evidence_revision == 0

    # ------------------------------------------------------------------
    # Scenario 2b: needs_attention through service job flow
    # ------------------------------------------------------------------

    def test_verification_service_job_reaches_needs_attention(self) -> None:
        """Via ConversationService._run_import, job transitions to needs_attention."""
        repo = FakeConversationRepository()
        conv = repo.create_conversation(provider="rule", model="rule")

        browser = FakeVerificationBrowser()
        import_service = ConversationImportService(
            repository=repo, browser=browser,
        )

        fake_job_repo = FakeJobRepository()
        job_service = JobService(fake_job_repo)
        executor = LocalJobExecutor(job_service, max_workers=1)
        provider_registry = ConversationProviderRegistry()

        service = ConversationService(
            repository=repo, import_service=import_service,
            provider_registry=provider_registry,
            validator=validate_chat_answer,
            job_service=job_service, executor=executor,
        )

        submission = job_service.create(
            "conversation_import", "ik-verify", "manual",
        )
        job_service.start(submission.run.run_id)
        service._run_import(
            submission.run.run_id, conv.id,
            "https://sale.591.com.tw/home/house/detail/2/123.html",
        )

        run = job_service.get(submission.run.run_id)
        assert run is not None
        assert run.status == "needs_attention"
        executor.shutdown()

    # ------------------------------------------------------------------
    # Scenario 3: Invalid provider → no assistant message stored
    # ------------------------------------------------------------------

    def test_invalid_provider_no_assistant_message(self) -> None:
        """Two invalid provider outputs → no assistant message stored."""
        repo = FakeConversationRepository()

        conv = repo.create_conversation(provider="ollama", model="gemma3")
        listing = repo.add_initial_listing(conversation_id=conv.id)
        snapshot = repo.append_snapshot(
            listing_id=listing.id,
            source_url="https://sale.591.com.tw/home/house/detail/2/123.html",
            payload={"title": "test"},
        )
        ep = repo.append_evidence_pack(
            conversation_id=conv.id, snapshot_id=snapshot.id,
            pack={"facts": {"p1": 100}, "valuation": None,
                  "comparables": [], "limitations": []},
        )
        repo.activate_evidence(
            conversation_id=conv.id, listing_id=listing.id, revision=ep.revision,
        )
        conv_updated = repo.get_conversation(conv.id)
        assert conv_updated is not None
        assert conv_updated.active_evidence_revision == 1

        class InvalidProvider:
            def reply(
                self, *, model: str, question: str,
                context: object,
            ) -> ChatAnswerDraft:
                return ChatAnswerDraft(
                    answer="bad answer",
                    property_claims=[
                        PropertyClaim(text="bad", fact_ids=["unknown_fact"]),
                    ],
                    general_guidance=["一般建議：test"],
                    suggested_questions=[],
                )

        provider_registry = ConversationProviderRegistry()
        provider_registry.register("ollama", InvalidProvider())

        fake_job_repo = FakeJobRepository()
        job_service = JobService(fake_job_repo)
        executor = LocalJobExecutor(job_service, max_workers=1)

        service = ConversationService(
            repository=repo, import_service=MagicMock(),
            provider_registry=provider_registry,
            validator=validate_chat_answer,
            job_service=job_service, executor=executor,
        )

        submission = job_service.create(
            CONVERSATION_REPLY, "ik-invalid", "manual",
            input_version=conv.id,
        )
        job_service.start(submission.run.run_id)
        service._run_reply(
            submission.run.run_id, conv.id, "價格合理嗎？",
            "ollama", "gemma3", 1,
        )

        msgs = repo.get_messages(conversation_id=conv.id)
        assert len(msgs) == 1
        assert msgs[0].role == "user"

        run = job_service.get(submission.run.run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error_code == "validation_failed"
        executor.shutdown()

    # ------------------------------------------------------------------
    # Scenario 3b: Delete conversation → cascade cleanup
    # ------------------------------------------------------------------

    def test_delete_conversation_cascade(self) -> None:
        """Delete conversation removes all associated data."""
        repo = FakeConversationRepository()

        conv = repo.create_conversation(provider="rule", model="rule")
        listing = repo.add_initial_listing(conversation_id=conv.id)
        snapshot = repo.append_snapshot(
            listing_id=listing.id,
            source_url="https://sale.591.com.tw/home/house/detail/2/123.html",
            payload={"title": "test"},
        )
        repo.append_evidence_pack(
            conversation_id=conv.id, snapshot_id=snapshot.id,
            pack={"facts": {}, "valuation": None,
                  "comparables": [], "limitations": []},
        )
        repo.append_message(
            conversation_id=conv.id, role="user", content="hello",
            evidence_revision=None, provider=None, model=None, citations=[],
        )

        assert conv.id in repo.conversations
        assert any(
            lst.conversation_id == conv.id for lst in repo.listings.values()
        )
        assert conv.id in repo.messages

        deleted = repo.delete_conversation(conv.id)
        assert deleted is True

        assert conv.id not in repo.conversations
        assert not any(
            lst.conversation_id == conv.id for lst in repo.listings.values()
        )
        assert conv.id not in repo.messages

        deleted_again = repo.delete_conversation(conv.id)
        assert deleted_again is False

    # ------------------------------------------------------------------
    # Scenario 3c: Duplicate listing raises
    # ------------------------------------------------------------------

    def test_duplicate_listing_rejected(self) -> None:
        """Adding second listing to same conversation raises error."""
        repo = FakeConversationRepository()
        conv = repo.create_conversation(provider="rule", model="rule")
        repo.add_initial_listing(conversation_id=conv.id)
        with pytest.raises(ConversationAlreadyHasListing):
            repo.add_initial_listing(conversation_id=conv.id)
