from __future__ import annotations

import json
from collections.abc import Callable

import pymysql
import pymysql.cursors
import pymysql.err

from qingpu_insight.report_contracts import SavedBuyerReport

ConnectionFactory = Callable[[], pymysql.Connection]


class CorruptReportError(ValueError):
    """Raised when a persisted report cannot be validated as BuyerReportDraft."""


class MySQLReportRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def create(self, report: SavedBuyerReport) -> SavedBuyerReport:
        conn = self._connection_factory()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO buyer_reports
                       (report_id, request_hash, dataset_version, evidence_pack_id,
                        provider, model, content, fallback_reason,
                        validation_codes, latency_ms, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        report.report_id,
                        report.request_hash,
                        report.dataset_version,
                        report.evidence_pack_id,
                        report.provider,
                        report.model,
                        json.dumps(report.content, ensure_ascii=False),
                        report.fallback_reason,
                        json.dumps(list(report.validation_codes), ensure_ascii=False),
                        report.latency_ms,
                        report.created_at,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return report

    def get(self, report_id: str) -> SavedBuyerReport | None:
        conn = self._connection_factory()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM buyer_reports WHERE report_id = %s",
                    (report_id,),
                )
                row = cursor.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        if row is None:
            return None

        from qingpu_insight.report_contracts import BuyerReportDraft

        try:
            draft = BuyerReportDraft.model_validate(json.loads(row["content"]))
            content = draft.model_dump(mode="json")
        except Exception as e:
            raise CorruptReportError(
                f"report {report_id} validation failed: {e}"
            ) from e

        return SavedBuyerReport(
            report_id=str(row["report_id"]),
            request_hash=str(row["request_hash"]),
            dataset_version=str(row["dataset_version"]),
            evidence_pack_id=str(row["evidence_pack_id"]),
            provider=str(row["provider"]),
            model=str(row["model"]),
            content=content,
            fallback_reason=row["fallback_reason"],
            validation_codes=tuple(json.loads(row["validation_codes"])),
            latency_ms=float(row["latency_ms"]),
            created_at=str(row["created_at"]),
        )
