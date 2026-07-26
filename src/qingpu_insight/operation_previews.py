from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import pymysql

from qingpu_insight.job_repository import ConnectionFactory


class PreviewConfirmationMismatch(RuntimeError):
    """Confirmation text does not match the stored preview."""


class PreviewAlreadyConsumed(RuntimeError):
    """Preview has already been consumed."""


class PreviewExpired(RuntimeError):
    """Preview has expired and can no longer be consumed."""


@dataclass(frozen=True)
class OperationPreview:
    preview_id: str
    operation: Literal["model_publish", "model_rollback", "database_restore"]
    payload: dict[str, object]
    confirmation_text: str
    expires_at: datetime
    consumed_at: datetime | None


class OperationPreviewRepository(Protocol):
    def create(self, preview: OperationPreview) -> None: ...
    def consume(
        self, preview_id: str, confirmation_text: str, now: datetime
    ) -> OperationPreview: ...


class MySQLOperationPreviewRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _connection(self) -> Iterator[pymysql.Connection]:
        conn = self._connection_factory()
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _row_to_preview(row: dict[str, Any]) -> OperationPreview:
        raw_payload = row.get("payload")
        if isinstance(raw_payload, str):
            payload = json.loads(raw_payload)
        elif isinstance(raw_payload, dict):
            payload = raw_payload
        else:
            payload = {}
        return OperationPreview(
            preview_id=str(row["preview_id"]),
            operation=str(row["operation"]),
            payload=payload,
            confirmation_text=str(row["confirmation_text"]),
            expires_at=row["expires_at"],
            consumed_at=row.get("consumed_at"),
        )

    def create(self, preview: OperationPreview) -> None:
        with self._connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO operation_previews
                           (preview_id, operation, payload, confirmation_text,
                            expires_at, consumed_at)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (
                            preview.preview_id,
                            preview.operation,
                            json.dumps(preview.payload),
                            preview.confirmation_text,
                            preview.expires_at,
                            preview.consumed_at,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def consume(self, preview_id: str, confirmation_text: str, now: datetime) -> OperationPreview:
        with self._connection() as conn:
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        """UPDATE operation_previews
                           SET consumed_at = %s
                           WHERE preview_id = %s
                             AND confirmation_text = %s
                             AND consumed_at IS NULL
                             AND expires_at >= %s""",
                        (now, preview_id, confirmation_text, now),
                    )
                    if cursor.rowcount == 0:
                        cursor.execute(
                            "SELECT * FROM operation_previews WHERE preview_id = %s",
                            (preview_id,),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            raise ValueError(f"Preview {preview_id!r} not found")
                        if row["consumed_at"] is not None:
                            raise PreviewAlreadyConsumed(
                                f"Preview {preview_id!r} already consumed"
                            )
                        if row["expires_at"] < now:
                            raise PreviewExpired(
                                f"Preview {preview_id!r} expired at {row['expires_at']}"
                            )
                        raise PreviewConfirmationMismatch(
                            "Confirmation text does not match"
                        )
                    cursor.execute(
                        "SELECT * FROM operation_previews WHERE preview_id = %s",
                        (preview_id,),
                    )
                    row = cursor.fetchone()
                conn.commit()
            except (
                PreviewConfirmationMismatch,
                PreviewAlreadyConsumed,
                PreviewExpired,
                ValueError,
            ):
                conn.rollback()
                raise
            except Exception:
                conn.rollback()
                raise
        if row is None:
            raise ValueError(f"Preview {preview_id!r} not found")
        return self._row_to_preview(row)


class InMemoryOperationPreviewRepository:
    def __init__(self) -> None:
        self._previews: dict[str, OperationPreview] = {}

    def create(self, preview: OperationPreview) -> None:
        if preview.preview_id not in self._previews:
            self._previews[preview.preview_id] = preview

    def consume(self, preview_id: str, confirmation_text: str, now: datetime) -> OperationPreview:
        preview = self._previews.get(preview_id)
        if preview is None:
            raise ValueError(f"Preview {preview_id!r} not found")
        if preview.confirmation_text != confirmation_text:
            raise PreviewConfirmationMismatch("Confirmation text does not match")
        if preview.consumed_at is not None:
            raise PreviewAlreadyConsumed(
                f"Preview {preview_id!r} already consumed"
            )
        if preview.expires_at < now:
            raise PreviewExpired(
                f"Preview {preview_id!r} expired at {preview.expires_at}"
            )
        consumed = OperationPreview(
            preview_id=preview.preview_id,
            operation=preview.operation,
            payload=preview.payload,
            confirmation_text=preview.confirmation_text,
            expires_at=preview.expires_at,
            consumed_at=now,
        )
        self._previews[preview_id] = consumed
        return consumed


class OperationPreviewService:
    def __init__(
        self,
        repository: OperationPreviewRepository,
        clock: Callable[[], datetime] | None = None,
        make_uuid: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._make_uuid = make_uuid or (lambda: str(uuid.uuid4()))

    def create_for(
        self,
        operation: Literal["model_publish", "model_rollback", "database_restore"],
        payload: dict[str, object],
        confirmation_text: str,
        ttl_seconds: int = 300,
    ) -> OperationPreview:
        now = self._clock()
        preview = OperationPreview(
            preview_id=self._make_uuid(),
            operation=operation,
            payload=payload,
            confirmation_text=confirmation_text,
            expires_at=now + timedelta(seconds=ttl_seconds),
            consumed_at=None,
        )
        self._repository.create(preview)
        return preview

    def consume(self, preview_id: str, confirmation_text: str) -> OperationPreview:
        now = self._clock()
        return self._repository.consume(preview_id, confirmation_text, now)
