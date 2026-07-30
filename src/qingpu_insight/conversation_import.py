from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from qingpu_insight.conversation_evidence import (
    ConversationEvidence,
    ConversationEvidenceBuilder,
)
from qingpu_insight.conversation_listing_capture import DetailPageBrowser
from qingpu_insight.conversation_listing_parser import (
    ListingPageVerificationRequired,
)
from qingpu_insight.conversation_repository import (
    ConversationAlreadyHasListing,
    MySQLConversationRepository,
)
from qingpu_insight.conversation_urls import Unsupported591Url, parse_initial_591_url

ImportStage = Literal[
    "validating_url", "opening_browser", "capturing_listing",
    "building_evidence", "ready",
]


@dataclass(frozen=True)
class ListingImportResult:
    conversation_id: str
    listing_id: str
    snapshot_id: str
    snapshot_revision: int
    evidence_revision: int
    outcome: Literal["ready", "needs_attention"]


def _serialize_for_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value


def _serialize_evidence(evidence: ConversationEvidence) -> dict:
    return {
        "facts": [dataclasses.asdict(fact) for fact in evidence.facts],
        "valuation": evidence.valuation,
        "comparables": list(evidence.comparables),
        "limitations": list(evidence.limitations),
    }


class ConversationImportService:
    def __init__(
        self,
        *,
        repository: MySQLConversationRepository,
        browser: DetailPageBrowser,
        evidence_builder: ConversationEvidenceBuilder | None = None,
        stage_callback: Callable[[str, ImportStage], None] | None = None,
    ):
        self._repository = repository
        self._browser = browser
        self._evidence_builder = evidence_builder or ConversationEvidenceBuilder()
        self._stage_callback = stage_callback

    def _notify(
        self,
        conversation_id: str,
        stage: ImportStage,
        callback: Callable[[str, ImportStage], None] | None = None,
    ) -> None:
        selected = callback or self._stage_callback
        if selected:
            selected(conversation_id, stage)

    def import_initial_listing(
        self,
        *,
        conversation_id: str,
        raw_url: str,
        stage_callback: Callable[[str, ImportStage], None] | None = None,
    ) -> ListingImportResult:
        try:
            def notify(stage: ImportStage) -> None:
                self._notify(
                    conversation_id,
                    stage,
                    stage_callback,
                )

            notify("validating_url")
            initial = parse_initial_591_url(raw_url)

            notify("opening_browser")
            captured = self._browser.capture(initial)

            notify("capturing_listing")
            if captured.detail.listing_type != "sale":
                raise Unsupported591Url("only sale listings are supported")
            try:
                listing = self._repository.add_initial_listing(
                    conversation_id=conversation_id,
                )
            except ConversationAlreadyHasListing:
                listing = self._repository.get_initial_listing(conversation_id)
                if listing is None:
                    raise
                if (
                    listing.canonical_url is not None
                    and listing.canonical_url != captured.final_url
                ):
                    raise ValueError(
                        "conversation is already bound to another listing"
                    ) from None
            self._repository.update_listing(
                listing.id,
                listing_type=captured.detail.listing_type,
                source_listing_id=captured.detail.source_listing_id,
                canonical_url=captured.final_url,
            )
            self._repository.set_title(
                conversation_id=conversation_id,
                title=captured.detail.title,
            )
            payload = {
                k: _serialize_for_json(v)
                for k, v in dataclasses.asdict(captured.detail).items()
            }
            snapshot = self._repository.append_snapshot(
                listing_id=listing.id,
                source_url=captured.final_url,
                payload=payload,
            )

            notify("building_evidence")
            evidence = _serialize_evidence(
                self._evidence_builder.build(snapshot=snapshot)
            )
            ev = self._repository.append_evidence_pack(
                conversation_id=conversation_id,
                snapshot_id=snapshot.id,
                pack=evidence,
            )
            self._repository.activate_evidence(
                conversation_id=conversation_id,
                listing_id=listing.id,
                revision=ev.revision,
            )
            self._repository.set_status(
                conversation_id=conversation_id,
                status="ready",
            )

            notify("ready")
            return ListingImportResult(
                conversation_id=conversation_id,
                listing_id=listing.id,
                snapshot_id=snapshot.id,
                snapshot_revision=snapshot.revision,
                evidence_revision=ev.revision,
                outcome="ready",
            )
        except ListingPageVerificationRequired:
            self._repository.set_status(
                conversation_id=conversation_id,
                status="needs_attention",
            )
            return ListingImportResult(
                conversation_id=conversation_id,
                listing_id="",
                snapshot_id="",
                snapshot_revision=0,
                evidence_revision=0,
                outcome="needs_attention",
            )

    def refresh_listing(
        self,
        *,
        conversation_id: str,
        stage_callback: Callable[[str, ImportStage], None] | None = None,
    ) -> ListingImportResult:
        try:
            def notify(stage: ImportStage) -> None:
                self._notify(
                    conversation_id,
                    stage,
                    stage_callback,
                )

            conversation = self._repository.get_conversation(conversation_id)
            if conversation is None:
                raise ValueError(f"conversation not found: {conversation_id}")
            active_listing_id = conversation.active_listing_id
            if active_listing_id is None:
                raise ValueError(
                    f"conversation {conversation_id} has no active listing"
                )

            listing = self._repository.get_listing(active_listing_id)
            if listing is None:
                raise ValueError(f"listing not found: {active_listing_id}")
            if listing.canonical_url is None:
                raise ValueError(
                    f"listing {active_listing_id} has no canonical_url"
                )

            notify("validating_url")
            initial = parse_initial_591_url(listing.canonical_url)

            notify("opening_browser")
            captured = self._browser.capture(initial)

            notify("capturing_listing")
            payload = {
                k: _serialize_for_json(v)
                for k, v in dataclasses.asdict(captured.detail).items()
            }
            snapshot = self._repository.append_snapshot(
                listing_id=active_listing_id,
                source_url=captured.final_url,
                payload=payload,
            )

            notify("building_evidence")
            evidence = _serialize_evidence(
                self._evidence_builder.build(snapshot=snapshot)
            )
            ev = self._repository.append_evidence_pack(
                conversation_id=conversation_id,
                snapshot_id=snapshot.id,
                pack=evidence,
            )
            self._repository.activate_evidence(
                conversation_id=conversation_id,
                listing_id=active_listing_id,
                revision=ev.revision,
            )
            self._repository.set_status(
                conversation_id=conversation_id,
                status="ready",
            )

            notify("ready")
            return ListingImportResult(
                conversation_id=conversation_id,
                listing_id=active_listing_id,
                snapshot_id=snapshot.id,
                snapshot_revision=snapshot.revision,
                evidence_revision=ev.revision,
                outcome="ready",
            )
        except ListingPageVerificationRequired:
            self._repository.set_status(
                conversation_id=conversation_id,
                status="needs_attention",
            )
            return ListingImportResult(
                conversation_id=conversation_id,
                listing_id="",
                snapshot_id="",
                snapshot_revision=0,
                evidence_revision=0,
                outcome="needs_attention",
            )
