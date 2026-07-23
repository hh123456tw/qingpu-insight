from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pymysql
import pytest

from qingpu_insight.report_contracts import SavedBuyerReport
from qingpu_insight.report_repository import MySQLReportRepository

_NOW = datetime.now(UTC).isoformat()

_VALID_CONTENT = {
    "summary": {"text": "總價15000000元", "fact_ids": ["abc123"], "numeric_fact_ids": ["abc123"]},
    "advantages": [{"text": "交通便利", "fact_ids": ["def456"], "numeric_fact_ids": []}],
    "risks": [{"text": "屋齡5年", "fact_ids": ["ghi789"], "numeric_fact_ids": ["ghi789"]}],
    "negotiation": [
        {"text": "開價15000000元", "fact_ids": ["abc123"], "numeric_fact_ids": ["abc123"]},
    ],
    "limitations": [{"text": "僅供參考", "fact_ids": ["def456"], "numeric_fact_ids": []}],
}

_SAMPLE_REPORT = SavedBuyerReport(
    report_id="rep-001", request_hash="hash123", dataset_version="v1",
    evidence_pack_id="pack-001", provider="rule", model="rule",
    content=_VALID_CONTENT, fallback_reason=None, validation_codes=(),
    latency_ms=12.5, created_at=_NOW,
)


def _make_mock_conn() -> MagicMock:
    conn = MagicMock(spec=pymysql.Connection)
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    return conn


class TestMySQLReportRepository:
    def test_create_inserts_row(self) -> None:
        conn = _make_mock_conn()
        factory = MagicMock(return_value=conn)
        repo = MySQLReportRepository(factory)

        result = repo.create(_SAMPLE_REPORT)

        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.execute.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert "INSERT INTO" in sql or "insert into" in sql
        assert "buyer_reports" in sql

        args = cursor.execute.call_args[0][1]
        assert args[0] == "rep-001"
        assert args[4] == "rule"
        conn.commit.assert_called_once()
        assert result.report_id == "rep-001"

    def test_get_returns_report(self) -> None:
        conn = _make_mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {
            "report_id": "rep-001",
            "request_hash": "hash123",
            "dataset_version": "v1",
            "evidence_pack_id": "pack-001",
            "provider": "rule",
            "model": "rule",
            "content": json.dumps(_VALID_CONTENT, ensure_ascii=False),
            "fallback_reason": None,
            "validation_codes": "[]",
            "latency_ms": 12.5,
            "created_at": _NOW,
        }

        factory = MagicMock(return_value=conn)
        repo = MySQLReportRepository(factory)

        result = repo.get("rep-001")
        assert result is not None
        assert result.report_id == "rep-001"
        assert result.provider == "rule"
        assert result.content == _VALID_CONTENT

    def test_get_nonexistent_returns_none(self) -> None:
        conn = _make_mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None

        factory = MagicMock(return_value=conn)
        repo = MySQLReportRepository(factory)

        result = repo.get("nonexistent")
        assert result is None

    def test_get_with_fallback_reason(self) -> None:
        conn = _make_mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {
            "report_id": "rep-002",
            "request_hash": "hash456",
            "dataset_version": "v1",
            "evidence_pack_id": "pack-002",
            "provider": "rule",
            "model": "rule",
            "content": json.dumps(_VALID_CONTENT, ensure_ascii=False),
            "fallback_reason": "ollama_timeout",
            "validation_codes": json.dumps(["unsubstantiated_number"]),
            "latency_ms": 25.0,
            "created_at": _NOW,
        }

        factory = MagicMock(return_value=conn)
        repo = MySQLReportRepository(factory)

        result = repo.get("rep-002")
        assert result is not None
        assert result.fallback_reason == "ollama_timeout"
        assert result.validation_codes == ("unsubstantiated_number",)

    def test_connection_lifecycle_create(self) -> None:
        conn = _make_mock_conn()
        factory = MagicMock(return_value=conn)
        repo = MySQLReportRepository(factory)

        repo.create(_SAMPLE_REPORT)
        factory.assert_called_once()
        conn.close.assert_called_once()

    def test_connection_lifecycle_get(self) -> None:
        conn = _make_mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None

        factory = MagicMock(return_value=conn)
        repo = MySQLReportRepository(factory)

        repo.get("test")
        factory.assert_called_once()
        conn.close.assert_called_once()

    def test_rollback_on_error(self) -> None:
        conn = _make_mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = pymysql.err.OperationalError("deadlock")

        factory = MagicMock(return_value=conn)
        repo = MySQLReportRepository(factory)

        with pytest.raises(pymysql.err.OperationalError):
            repo.create(_SAMPLE_REPORT)
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    def test_duplicate_create(self) -> None:
        conn = _make_mock_conn()
        factory = MagicMock(return_value=conn)
        repo = MySQLReportRepository(factory)

        repo.create(_SAMPLE_REPORT)

        conn2 = _make_mock_conn()
        cursor2 = conn2.cursor.return_value.__enter__.return_value
        cursor2.execute.side_effect = pymysql.err.IntegrityError("duplicate")
        factory.return_value = conn2

        with pytest.raises(pymysql.err.IntegrityError):
            repo.create(_SAMPLE_REPORT)
