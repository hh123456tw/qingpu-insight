from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from qingpu_insight.conversation_listing_capture import DetailPageBrowser
from qingpu_insight.conversation_listing_parser import (
    ListingDetailParseError,
    ListingPageVerificationRequired,
    ParsedListingDetail,
)
from qingpu_insight.conversation_repository import MySQLConversationRepository
from qingpu_insight.conversation_urls import parse_initial_591_url

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


def _build_evidence_pack(detail: ParsedListingDetail) -> dict:
    facts: dict[str, Any] = {
        "listing_type": detail.listing_type,
        "source_listing_id": detail.source_listing_id,
        "title": detail.title,
        "total_price_twd": detail.total_price_twd,
        "unit_price_twd_per_ping": detail.unit_price_twd_per_ping,
        "area_ping": str(detail.area_ping) if detail.area_ping is not None else None,
        "layout": detail.layout,
        "address": detail.address,
        "community_name": detail.community_name,
        "builder_name": detail.builder_name,
        "building_type": detail.building_type,
        "floor": detail.floor,
        "total_floors": detail.total_floors,
        "age_years": str(detail.age_years) if detail.age_years is not None else None,
        "parking_type": detail.parking_type,
        "latitude": str(detail.latitude) if detail.latitude is not None else None,
        "longitude": str(detail.longitude) if detail.longitude is not None else None,
        "source_updated_text": detail.source_updated_text,
    }
    return {
        "facts": facts,
        "valuation": None,
        "comparables": [],
        "limitations": [],
    }


class ConversationImportService:
    def __init__(
        self,
        *,
        repository: MySQLConversationRepository,
        browser: DetailPageBrowser,
        stage_callback: Callable[[str, ImportStage], None] | None = None,
    ):
        self._repository = repository
        self._browser = browser
        self._stage_callback = stage_callback

    def _notify(self, conversation_id: str, stage: ImportStage) -> None:
        if self._stage_callback:
            self._stage_callback(conversation_id, stage)

    def import_initial_listing(
        self, *, conversation_id: str, raw_url: str
    ) -> ListingImportResult:
        try:
            self._notify(conversation_id, "validating_url")
            initial = parse_initial_591_url(raw_url)

            self._notify(conversation_id, "opening_browser")
            captured = self._browser.capture(initial)

            self._notify(conversation_id, "capturing_listing")
            listing = self._repository.add_initial_listing(
                conversation_id=conversation_id,
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

            self._notify(conversation_id, "building_evidence")
            evidence = _build_evidence_pack(captured.detail)
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

            self._notify(conversation_id, "ready")
            return ListingImportResult(
                conversation_id=conversation_id,
                listing_id=listing.id,
                snapshot_id=snapshot.id,
                snapshot_revision=snapshot.revision,
                evidence_revision=ev.revision,
                outcome="ready",
            )
        except (ListingPageVerificationRequired, ListingDetailParseError):
            return ListingImportResult(
                conversation_id=conversation_id,
                listing_id="",
                snapshot_id="",
                snapshot_revision=0,
                evidence_revision=0,
                outcome="needs_attention",
            )

    def refresh_listing(self, *, conversation_id: str) -> ListingImportResult:
        try:
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

            self._notify(conversation_id, "validating_url")
            initial = parse_initial_591_url(listing.canonical_url)

            self._notify(conversation_id, "opening_browser")
            captured = self._browser.capture(initial)

            self._notify(conversation_id, "capturing_listing")
            payload = {
                k: _serialize_for_json(v)
                for k, v in dataclasses.asdict(captured.detail).items()
            }
            snapshot = self._repository.append_snapshot(
                listing_id=active_listing_id,
                source_url=captured.final_url,
                payload=payload,
            )

            self._notify(conversation_id, "building_evidence")
            evidence = _build_evidence_pack(captured.detail)
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

            self._notify(conversation_id, "ready")
            return ListingImportResult(
                conversation_id=conversation_id,
                listing_id=active_listing_id,
                snapshot_id=snapshot.id,
                snapshot_revision=snapshot.revision,
                evidence_revision=ev.revision,
                outcome="ready",
            )
        except (ListingPageVerificationRequired, ListingDetailParseError):
            return ListingImportResult(
                conversation_id=conversation_id,
                listing_id="",
                snapshot_id="",
                snapshot_revision=0,
                evidence_revision=0,
                outcome="needs_attention",
            )
