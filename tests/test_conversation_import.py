from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from qingpu_insight.conversation_import import (
    ConversationImportService,
    ImportStage,
)
from qingpu_insight.conversation_listing_capture import CapturedListing
from qingpu_insight.conversation_listing_parser import (
    ListingDetailParseError,
    ListingPageVerificationRequired,
    ParsedListingDetail,
)
from qingpu_insight.conversation_repository import (
    ConversationAlreadyHasListing,
    ConversationListingRecord,
    ConversationRecord,
    EvidencePackRecord,
    SnapshotRecord,
)
from qingpu_insight.conversation_urls import (
    Initial591Url,
    Unsupported591Url,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _cid() -> str:
    return str(uuid4())


def _sample_detail(**overrides: Any) -> ParsedListingDetail:
    defaults: dict[str, Any] = dict(
        listing_type="sale",
        source_listing_id="12345",
        title="測試物件",
        total_price_twd=12_000_000,
        unit_price_twd_per_ping=400_000,
        area_ping=Decimal("30.5"),
        layout="3房2廳",
        address="台北市大安區",
        community_name="測試社區",
        builder_name="測試建商",
        building_type="大樓",
        floor="5F / 12F",
        total_floors=12,
        age_years=Decimal("5.5"),
        parking_type="坡道平面",
        latitude=Decimal("25.033"),
        longitude=Decimal("121.565"),
        source_updated_text="2024-01-15",
    )
    defaults.update(overrides)
    return ParsedListingDetail(**defaults)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRepository:
    def __init__(self) -> None:
        self.conversations: dict[str, dict[str, Any]] = {}
        self.listings: dict[str, dict[str, Any]] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.evidence_packs: dict[str, dict[str, Any]] = {}
        self.activated: list[tuple[str, str, int]] = []

    # -- conversations --

    def get_conversation(
        self, conversation_id: str
    ) -> ConversationRecord | None:
        conv = self.conversations.get(conversation_id)
        if conv is None:
            return None
        return ConversationRecord(**conv)

    def add_conversation(
        self,
        *,
        conversation_id: str,
        active_listing_id: str | None = None,
        active_evidence_revision: int | None = None,
    ) -> None:
        self.conversations[conversation_id] = dict(
            id=conversation_id,
            title="test",
            status="empty",
            default_provider="ollama",
            default_model="gpt-4",
            active_listing_id=active_listing_id,
            active_evidence_revision=active_evidence_revision,
            rolling_summary=None,
            created_at=_now(),
            updated_at=_now(),
            deleted_at=None,
        )

    # -- listings --

    def add_initial_listing(
        self, *, conversation_id: str
    ) -> ConversationListingRecord:
        for existing in self.listings.values():
            if (
                existing["conversation_id"] == conversation_id
                and existing["position"] == 1
            ):
                raise ConversationAlreadyHasListing(conversation_id)
        listing_id = _cid()
        now = _now()
        self.listings[listing_id] = dict(
            id=listing_id,
            conversation_id=conversation_id,
            position=1,
            listing_type=None,
            source_listing_id=None,
            canonical_url=None,
            created_at=now,
        )
        return ConversationListingRecord(
            id=listing_id,
            conversation_id=conversation_id,
            position=1,
            listing_type=None,
            source_listing_id=None,
            canonical_url=None,
            created_at=now,
        )

    def add_listing(
        self,
        *,
        listing_id: str,
        conversation_id: str,
        canonical_url: str | None = None,
    ) -> None:
        self.listings[listing_id] = dict(
            id=listing_id,
            conversation_id=conversation_id,
            position=1,
            listing_type="sale",
            source_listing_id="12345",
            canonical_url=canonical_url,
            created_at=_now(),
        )

    def get_listing(
        self, listing_id: str
    ) -> ConversationListingRecord | None:
        listing = self.listings.get(listing_id)
        if listing is None:
            return None
        return ConversationListingRecord(**listing)

    def get_initial_listing(
        self, conversation_id: str
    ) -> ConversationListingRecord | None:
        for listing in self.listings.values():
            if (
                listing["conversation_id"] == conversation_id
                and listing["position"] == 1
            ):
                return ConversationListingRecord(**listing)
        return None

    def update_listing(
        self,
        listing_id: str,
        *,
        listing_type: str | None,
        source_listing_id: str | None,
        canonical_url: str | None,
    ) -> None:
        listing = self.listings[listing_id]
        listing["listing_type"] = listing_type
        listing["source_listing_id"] = source_listing_id
        listing["canonical_url"] = canonical_url

    # -- snapshots --

    def append_snapshot(
        self, *, listing_id: str, source_url: str, payload: dict
    ) -> SnapshotRecord:
        snapshot_id = _cid()
        now = _now()
        existing = [
            s
            for s in self.snapshots.values()
            if s["listing_id"] == listing_id
        ]
        revision = max((s["revision"] for s in existing), default=0) + 1
        content_sha256 = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        self.snapshots[snapshot_id] = dict(
            id=snapshot_id,
            listing_id=listing_id,
            revision=revision,
            captured_at=now,
            source_url=source_url,
            payload=payload,
            content_sha256=content_sha256,
        )
        return SnapshotRecord(
            id=snapshot_id,
            conversation_listing_id=listing_id,
            revision=revision,
            captured_at=now,
            source_url=source_url,
            structured_payload=payload,
            content_sha256=content_sha256,
        )

    def get_snapshots(
        self, listing_id: str
    ) -> list[SnapshotRecord]:
        result = []
        for s in self.snapshots.values():
            if s["listing_id"] == listing_id:
                result.append(
                    SnapshotRecord(
                        id=s["id"],
                        conversation_listing_id=s["listing_id"],
                        revision=s["revision"],
                        captured_at=s["captured_at"],
                        source_url=s["source_url"],
                        structured_payload=s["payload"],
                        content_sha256=s["content_sha256"],
                    )
                )
        return sorted(result, key=lambda r: r.revision)

    # -- evidence packs --

    def append_evidence_pack(
        self, *, conversation_id: str, snapshot_id: str, pack: dict
    ) -> EvidencePackRecord:
        pack_id = _cid()
        now = _now()
        existing = [
            e
            for e in self.evidence_packs.values()
            if e["conversation_id"] == conversation_id
        ]
        revision = max((e["revision"] for e in existing), default=0) + 1
        self.evidence_packs[pack_id] = dict(
            id=pack_id,
            conversation_id=conversation_id,
            snapshot_id=snapshot_id,
            revision=revision,
            generated_at=now,
            pack=pack,
        )
        return EvidencePackRecord(
            id=pack_id,
            conversation_id=conversation_id,
            conversation_listing_snapshot_id=snapshot_id,
            revision=revision,
            generated_at=now,
            facts=pack["facts"],
            valuation=pack.get("valuation"),
            comparables=pack["comparables"],
            limitations=pack["limitations"],
        )

    def get_evidence_packs(
        self, conversation_id: str
    ) -> list[EvidencePackRecord]:
        result = []
        for e in self.evidence_packs.values():
            if e["conversation_id"] == conversation_id:
                pack = e["pack"]
                result.append(
                    EvidencePackRecord(
                        id=e["id"],
                        conversation_id=e["conversation_id"],
                        conversation_listing_snapshot_id=e["snapshot_id"],
                        revision=e["revision"],
                        generated_at=e["generated_at"],
                        facts=pack["facts"],
                        valuation=pack.get("valuation"),
                        comparables=pack["comparables"],
                        limitations=pack["limitations"],
                    )
                )
        return sorted(result, key=lambda r: r.revision)

    # -- evidence activation --

    def activate_evidence(
        self, *, conversation_id: str, listing_id: str, revision: int
    ) -> None:
        self.activated.append((conversation_id, listing_id, revision))
        conversation = self.conversations[conversation_id]
        conversation["active_listing_id"] = listing_id
        conversation["active_evidence_revision"] = revision
        conversation["status"] = "ready"

    def set_status(self, *, conversation_id: str, status: str) -> None:
        self.conversations[conversation_id]["status"] = status

    def set_title(self, *, conversation_id: str, title: str) -> None:
        self.conversations[conversation_id]["title"] = title


class FakeBrowser:
    def __init__(
        self,
        result: CapturedListing | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.captures: list[Initial591Url] = []

    def capture(self, initial_url: Initial591Url) -> CapturedListing:
        self.captures.append(initial_url)
        if self._error:
            raise self._error
        if self._result is None:
            raise RuntimeError("FakeBrowser has no result configured")
        return self._result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def detail() -> ParsedListingDetail:
    return _sample_detail()


@pytest.fixture
def detail_dict(detail: ParsedListingDetail) -> dict:
    """The serialized payload expected from import."""
    import dataclasses

    from qingpu_insight.conversation_import import _serialize_for_json

    return {
        k: _serialize_for_json(v)
        for k, v in dataclasses.asdict(detail).items()
    }


@pytest.fixture
def captured(detail: ParsedListingDetail) -> CapturedListing:
    return CapturedListing(
        final_url="https://sale.591.com.tw/home/house/detail/2/12345.html",
        detail=detail,
    )


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def browser(captured: CapturedListing) -> FakeBrowser:
    return FakeBrowser(result=captured)


@pytest.fixture
def service(
    repo: FakeRepository, browser: FakeBrowser
) -> ConversationImportService:
    return ConversationImportService(
        repository=repo,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInitialImport:
    def test_initial_import_success(
        self,
        service: ConversationImportService,
        repo: FakeRepository,
        browser: FakeBrowser,
        captured: CapturedListing,
        detail: ParsedListingDetail,
        detail_dict: dict,
    ) -> None:
        repo.add_conversation(conversation_id="conv-1")
        result = service.import_initial_listing(
            conversation_id="conv-1",
            raw_url="https://sale.591.com.tw/home/house/detail/2/12345.html",
        )

        assert result.outcome == "ready"
        assert result.conversation_id == "conv-1"
        assert len(result.listing_id) > 0
        assert len(result.snapshot_id) > 0
        assert result.snapshot_revision == 1
        assert result.evidence_revision == 1

        # Verify snapshot payload matches the detail
        snapshots = repo.get_snapshots(result.listing_id)
        assert len(snapshots) == 1
        assert snapshots[0].source_url == captured.final_url
        assert snapshots[0].structured_payload == detail_dict

        # Verify evidence was activated
        assert len(repo.activated) == 1
        assert repo.activated[0] == (
            "conv-1",
            result.listing_id,
            result.evidence_revision,
        )

    def test_url_rejection(
        self,
        service: ConversationImportService,
        repo: FakeRepository,
    ) -> None:
        repo.add_conversation(conversation_id="conv-1")
        with pytest.raises(Unsupported591Url):
            service.import_initial_listing(
                conversation_id="conv-1",
                raw_url="https://evil.com/phish",
            )

    def test_short_url_resolving_to_newhouse_is_rejected_before_mutation(
        self,
        repo: FakeRepository,
    ) -> None:
        repo.add_conversation(conversation_id="conv-1")
        captured = CapturedListing(
            final_url="https://newhouse.591.com.tw/456/detail",
            detail=_sample_detail(listing_type="newhouse"),
        )
        browser = FakeBrowser(result=captured)
        service = ConversationImportService(
            repository=repo,  # type: ignore[arg-type]
            browser=browser,  # type: ignore[arg-type]
        )

        with pytest.raises(Unsupported591Url, match="only sale listings"):
            service.import_initial_listing(
                conversation_id="conv-1",
                raw_url="https://591.to/abc123",
            )

        assert len(browser.captures) == 1
        assert repo.listings == {}
        assert repo.snapshots == {}
        assert repo.evidence_packs == {}

    def test_verification_needs_attention(
        self,
        repo: FakeRepository,
        captured: CapturedListing,
    ) -> None:
        repo.add_conversation(conversation_id="conv-1")
        err_browser = FakeBrowser(
            error=ListingPageVerificationRequired(
                "Verification page detected"
            )
        )
        svc = ConversationImportService(
            repository=repo,  # type: ignore[arg-type]
            browser=err_browser,  # type: ignore[arg-type]
        )
        result = svc.import_initial_listing(
            conversation_id="conv-1",
            raw_url="https://sale.591.com.tw/home/house/detail/2/12345.html",
        )
        assert result.outcome == "needs_attention"
        assert repo.conversations["conv-1"]["status"] == "needs_attention"

    def test_parse_failure(
        self,
        repo: FakeRepository,
        captured: CapturedListing,
    ) -> None:
        repo.add_conversation(conversation_id="conv-1")
        err_browser = FakeBrowser(
            error=ListingDetailParseError("could not parse detail")
        )
        svc = ConversationImportService(
            repository=repo,  # type: ignore[arg-type]
            browser=err_browser,  # type: ignore[arg-type]
        )
        with pytest.raises(ListingDetailParseError):
            svc.import_initial_listing(
                conversation_id="conv-1",
                raw_url="https://sale.591.com.tw/home/house/detail/2/12345.html",
            )

    def test_stage_callback(
        self,
        repo: FakeRepository,
        captured: CapturedListing,
    ) -> None:
        repo.add_conversation(conversation_id="conv-1")
        stages: list[tuple[str, ImportStage]] = []

        def cb(cid: str, stage: ImportStage) -> None:
            stages.append((cid, stage))

        svc = ConversationImportService(
            repository=repo,  # type: ignore[arg-type]
            browser=FakeBrowser(result=captured),
            stage_callback=cb,
        )
        svc.import_initial_listing(
            conversation_id="conv-1",
            raw_url="https://sale.591.com.tw/home/house/detail/2/12345.html",
        )
        assert stages == [
            ("conv-1", "validating_url"),
            ("conv-1", "opening_browser"),
            ("conv-1", "capturing_listing"),
            ("conv-1", "building_evidence"),
            ("conv-1", "ready"),
        ]


class TestRefresh:
    def test_refresh_appends_new_revision(
        self,
        repo: FakeRepository,
        captured: CapturedListing,
        detail_dict: dict,
    ) -> None:
        listing_id = _cid()
        repo.add_conversation(
            conversation_id="conv-1",
            active_listing_id=listing_id,
        )
        repo.add_listing(
            listing_id=listing_id,
            conversation_id="conv-1",
            canonical_url="https://sale.591.com.tw/home/house/detail/2/12345.html",
        )
        # Seed an existing snapshot and evidence pack
        snapshot = repo.append_snapshot(
            listing_id=listing_id,
            source_url="https://sale.591.com.tw/home/house/detail/2/12345.html",
            payload=detail_dict,
        )
        repo.append_evidence_pack(
            conversation_id="conv-1",
            snapshot_id=snapshot.id,
            pack={
                "facts": {"title": "test"},
                "valuation": None,
                "comparables": [],
                "limitations": [],
            },
        )

        svc = ConversationImportService(
            repository=repo,  # type: ignore[arg-type]
            browser=FakeBrowser(result=captured),
        )
        result = svc.refresh_listing(conversation_id="conv-1")

        assert result.outcome == "ready"
        assert result.listing_id == listing_id
        assert result.snapshot_revision == 2
        assert result.evidence_revision == 2

        snapshots = repo.get_snapshots(listing_id)
        assert len(snapshots) == 2
        assert snapshots[0].revision == 1
        assert snapshots[1].revision == 2
        assert snapshots[1].structured_payload == detail_dict

    def test_refresh_calls_stage_callback(
        self,
        repo: FakeRepository,
        captured: CapturedListing,
    ) -> None:
        listing_id = _cid()
        repo.add_conversation(
            conversation_id="conv-1",
            active_listing_id=listing_id,
        )
        repo.add_listing(
            listing_id=listing_id,
            conversation_id="conv-1",
            canonical_url="https://sale.591.com.tw/home/house/detail/2/12345.html",
        )
        stages: list[tuple[str, ImportStage]] = []

        def cb(cid: str, stage: ImportStage) -> None:
            stages.append((cid, stage))

        svc = ConversationImportService(
            repository=repo,  # type: ignore[arg-type]
            browser=FakeBrowser(result=captured),
            stage_callback=cb,
        )
        svc.refresh_listing(conversation_id="conv-1")

        assert stages == [
            ("conv-1", "validating_url"),
            ("conv-1", "opening_browser"),
            ("conv-1", "capturing_listing"),
            ("conv-1", "building_evidence"),
            ("conv-1", "ready"),
        ]
