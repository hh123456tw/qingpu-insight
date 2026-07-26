from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from qingpu_insight.operation_previews import (
    InMemoryOperationPreviewRepository,
    OperationPreviewService,
    PreviewAlreadyConsumed,
    PreviewConfirmationMismatch,
    PreviewExpired,
)

RUN_ID = "test-run-id"


@pytest.fixture
def clock() -> datetime:
    return datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def make_uuid() -> str:
    return "test-preview-id"


@pytest.fixture
def service(clock: datetime, make_uuid: str) -> OperationPreviewService:
    repo = InMemoryOperationPreviewRepository()
    return OperationPreviewService(
        repository=repo,
        clock=lambda: clock,
        make_uuid=lambda: make_uuid,
    )


class TestCreateFor:
    def test_returns_valid_preview(
        self, service: OperationPreviewService, clock: datetime,
    ) -> None:
        preview = service.create_for(
            "model_publish",
            {"market": "resale"},
            "confirm",
            ttl_seconds=300,
        )
        assert preview.preview_id == "test-preview-id"
        assert preview.operation == "model_publish"
        assert preview.payload == {"market": "resale"}
        assert preview.confirmation_text == "confirm"
        assert preview.expires_at == clock + timedelta(seconds=300)
        assert preview.consumed_at is None

    def test_returns_valid_preview_with_custom_ttl(
        self, service: OperationPreviewService, clock: datetime,
    ) -> None:
        preview = service.create_for(
            "model_rollback",
            {"market": "presale"},
            "rollback confirm",
            ttl_seconds=60,
        )
        assert preview.expires_at == clock + timedelta(seconds=60)
        assert preview.operation == "model_rollback"

    def test_database_restore_operation(
        self, service: OperationPreviewService,
    ) -> None:
        preview = service.create_for(
            "database_restore",
            {"backup_id": "bak-001"},
            "restore confirm",
        )
        assert preview.operation == "database_restore"
        assert preview.payload == {"backup_id": "bak-001"}


class TestConsume:
    def test_requires_exact_confirmation_text(
        self, service: OperationPreviewService,
    ) -> None:
        preview = service.create_for(
            "model_publish",
            {"market": "resale", "run_id": RUN_ID},
            "發布 resale " + RUN_ID,
            ttl_seconds=300,
        )
        with pytest.raises(PreviewConfirmationMismatch):
            service.consume(preview.preview_id, "發布 resale")
        consumed = service.consume(preview.preview_id, "發布 resale " + RUN_ID)
        assert consumed.consumed_at is not None

    def test_is_single_use(
        self, service: OperationPreviewService,
    ) -> None:
        preview = service.create_for(
            "model_publish",
            {"market": "resale", "run_id": RUN_ID},
            "發布 resale " + RUN_ID,
            ttl_seconds=300,
        )
        service.consume(preview.preview_id, "發布 resale " + RUN_ID)
        with pytest.raises(PreviewAlreadyConsumed):
            service.consume(preview.preview_id, "發布 resale " + RUN_ID)

    def test_expired_preview_raises_error(
        self, clock: datetime, make_uuid: str,
    ) -> None:
        repo = InMemoryOperationPreviewRepository()
        svc = OperationPreviewService(
            repo, clock=lambda: clock, make_uuid=lambda: make_uuid,
        )
        preview = svc.create_for(
            "model_publish",
            {"market": "resale"},
            "confirm",
            ttl_seconds=0,
        )
        svc_advanced = OperationPreviewService(
            repo, clock=lambda: clock + timedelta(seconds=1),
        )
        with pytest.raises(PreviewExpired):
            svc_advanced.consume(preview.preview_id, "confirm")

    def test_service_uses_injected_clock(
        self, service: OperationPreviewService, clock: datetime,
    ) -> None:
        preview = service.create_for(
            "model_publish",
            {"market": "resale"},
            "confirm",
            ttl_seconds=300,
        )
        assert preview.expires_at == clock + timedelta(seconds=300)

    def test_consume_returns_preview_with_consumed_at(
        self, service: OperationPreviewService,
    ) -> None:
        preview = service.create_for(
            "model_publish",
            {"market": "resale"},
            "confirm",
        )
        consumed = service.consume(preview.preview_id, "confirm")
        assert consumed.consumed_at is not None
        assert consumed.preview_id == preview.preview_id
        assert consumed.operation == preview.operation
