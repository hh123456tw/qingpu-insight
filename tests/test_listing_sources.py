from datetime import UTC, datetime

from qingpu_insight.listing_sources import CaptureBatch, CapturedPage


def test_capture_batch_is_complete_only_without_errors_and_with_terminal_page():
    page = CapturedPage(page_number=1, url="https://sale.591.com.tw/", html="<html/>")
    batch = CaptureBatch(
        batch_id="591-sale-20260721T120000Z",
        source="591",
        listing_type="sale",
        started_at=datetime(2026, 7, 21, 12, tzinfo=UTC),
        pages=[page],
        errors=[],
        reached_terminal_page=True,
    )
    assert batch.is_complete is True
