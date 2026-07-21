from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

ListingType = Literal["sale", "newhouse", "rental"]


@dataclass(frozen=True)
class CapturedPage:
    page_number: int
    url: str
    html: str
    accepted_count: int = 0
    rejected_count: int = 0
    representation: str = "unknown"
    schema_version: str = "unknown"


@dataclass(frozen=True)
class CaptureError:
    page_number: int
    code: str
    message: str


@dataclass
class CaptureBatch:
    batch_id: str
    source: str
    listing_type: ListingType
    started_at: datetime
    pages: list[CapturedPage] = field(default_factory=list)
    errors: list[CaptureError] = field(default_factory=list)
    reached_terminal_page: bool = False

    @property
    def is_complete(self) -> bool:
        return self.reached_terminal_page and not self.errors


class ListingSource(Protocol):
    def capture(self, listing_type: ListingType, max_pages: int) -> CaptureBatch: ...
