from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from qingpu_insight.report_contracts import (
    BuyerReportDraft,
    EvidenceCandidate,
    EvidenceFact,
    EvidencePack,
    ReportClaim,
)
from qingpu_insight.report_validation import ValidationResult, validate_report


def _fact_id(version: str, cid: str, kind: str, unit: str) -> str:
    raw = f"{version}|{cid}|{kind}|{unit}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


_NOW = datetime.now(UTC).isoformat()
_VER = "v1"

_C1_ASKING = _fact_id(_VER, "c1", "asking_price", "twd")
_C1_UNIT_PRICE = _fact_id(_VER, "c1", "unit_price", "twd_per_ping")
_C1_AREA = _fact_id(_VER, "c1", "area", "ping")
_C1_AGE = _fact_id(_VER, "c1", "building_age", "years")
_C1_STATION = _fact_id(_VER, "c1", "station_distance", "m")
_C1_MODEL = _fact_id(_VER, "c1", "model_interval", "twd")
_C1_FRESHNESS = _fact_id(_VER, "c1", "data_freshness", "iso")
_C1_LOC = _fact_id(_VER, "c1", "location_evidence", "method")
_C1_TXNS = _fact_id(_VER, "c1", "nearby_transactions_summary", "count")

_NONEXISTENT = "aaaaaaaaaaaaaaaaaaaa"

_FACTS = (
    EvidenceFact(
        fact_id=_C1_ASKING,
        kind="asking_price",
        label="Asking Price",
        value="15000000",
        unit="twd",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_C1_UNIT_PRICE,
        kind="unit_price",
        label="Unit Price",
        value="500000",
        unit="twd_per_ping",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_C1_AREA,
        kind="area",
        label="Building Area",
        value="30.00",
        unit="ping",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_C1_AGE,
        kind="building_age",
        label="Building Age",
        value="5.0",
        unit="years",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_C1_STATION,
        kind="station_distance",
        label="Station Distance",
        value="A18 300m",
        unit="m",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_C1_MODEL,
        kind="model_interval",
        label="Model Valuation Interval",
        value="13000000-16000000",
        unit="twd",
        source_type="valuation",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_C1_LOC,
        kind="location_evidence",
        label="Location Method",
        value="structured_address",
        unit="method",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
)

_CANDIDATE = EvidenceCandidate(candidate_id="c1", listing_type="sale")

PACK = EvidencePack(
    pack_id="test-pack",
    dataset_version=_VER,
    generated_at=_NOW,
    candidates=(_CANDIDATE,),
    facts=_FACTS,
    limitations=(),
)


def _valid_draft(
    summary: ReportClaim | None = None,
    advantages: tuple[ReportClaim, ...] | None = None,
    risks: tuple[ReportClaim, ...] | None = None,
    negotiation: tuple[ReportClaim, ...] | None = None,
    limitations: tuple[ReportClaim, ...] | None = None,
) -> BuyerReportDraft:
    if summary is None:
        summary = ReportClaim(
            text="此物件位於A18站，總價15000000元，面積30.00坪，屋齡5.0年",
            fact_ids=(_C1_STATION, _C1_ASKING, _C1_AREA, _C1_AGE, _C1_LOC),
            numeric_fact_ids=(_C1_ASKING, _C1_AREA, _C1_AGE),
        )
    if advantages is None:
        advantages = (
            ReportClaim(
                text="位於A18站附近，交通便利",
                fact_ids=(_C1_STATION, _C1_LOC),
                numeric_fact_ids=(),
            ),
        )
    if risks is None:
        risks = (
            ReportClaim(
                text="屋齡5.0年，需留意維護狀況",
                fact_ids=(_C1_AGE,),
                numeric_fact_ids=(_C1_AGE,),
            ),
        )
    if negotiation is None:
        negotiation = (
            ReportClaim(
                text="開價15000000元，模型估值低端13000000元，可嘗試議價",
                fact_ids=(_C1_ASKING, _C1_MODEL),
                numeric_fact_ids=(_C1_ASKING, _C1_MODEL),
            ),
        )
    if limitations is None:
        limitations = (
            ReportClaim(
                text="缺少附近成交參考資料",
                fact_ids=(_C1_LOC,),
                numeric_fact_ids=(),
            ),
        )
    return BuyerReportDraft(
        summary=summary,
        advantages=advantages,
        risks=risks,
        negotiation=negotiation,
        limitations=limitations,
    )


class TestValidateReport:
    def test_valid_report_passes(self) -> None:
        draft = _valid_draft()
        result = validate_report(draft, PACK)
        assert result.valid, f"expected valid, got issues: {result.issues}"
        assert not result.issues

    def test_unknown_fact_id_in_fact_ids(self) -> None:
        claim = ReportClaim(
            text="測試",
            fact_ids=(_NONEXISTENT,),
            numeric_fact_ids=(),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        codes = [(i.code, i.fact_id) for i in result.issues]
        assert any(code == "unknown_fact_id" and _NONEXISTENT in (fid or "")
                   for code, fid in codes)

    def test_unknown_fact_id_in_numeric_fact_ids(self) -> None:
        claim = ReportClaim(
            text="測試",
            fact_ids=(),
            numeric_fact_ids=(_NONEXISTENT,),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        codes = [(i.code, i.fact_id) for i in result.issues]
        assert any(code == "unknown_fact_id" and _NONEXISTENT in (fid or "")
                   for code, fid in codes)

    def test_unsubstantiated_number(self) -> None:
        claim = ReportClaim(
            text="總價99999999元",
            fact_ids=(_C1_ASKING,),
            numeric_fact_ids=(_C1_ASKING,),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        assert any(i.code == "unsubstantiated_number" for i in result.issues)

    def test_unsubstantiated_number_with_wan(self) -> None:
        claim = ReportClaim(
            text="總價9999萬",
            fact_ids=(_C1_ASKING,),
            numeric_fact_ids=(_C1_ASKING,),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        assert any(i.code == "unsubstantiated_number" for i in result.issues)

    def test_sensitive_phone(self) -> None:
        claim = ReportClaim(
            text="請聯繫0912345678",
            fact_ids=(_C1_LOC,),
            numeric_fact_ids=(),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        assert any(i.code == "sensitive_content" for i in result.issues)

    def test_sensitive_email(self) -> None:
        claim = ReportClaim(
            text="請寄至test@example.com",
            fact_ids=(_C1_LOC,),
            numeric_fact_ids=(),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        assert any(i.code == "sensitive_content" for i in result.issues)

    def test_sensitive_html(self) -> None:
        claim = ReportClaim(
            text="<script>alert('xss')</script>",
            fact_ids=(_C1_LOC,),
            numeric_fact_ids=(),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        assert any(i.code == "sensitive_content" for i in result.issues)

    def test_sensitive_db_url(self) -> None:
        claim = ReportClaim(
            text="mysql://user:pass@localhost:3306/db",
            fact_ids=(_C1_LOC,),
            numeric_fact_ids=(),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        assert any(i.code == "sensitive_content" for i in result.issues)

    def test_sensitive_db_url_with_driver_suffix(self) -> None:
        claim = ReportClaim(
            text="mysql+pymysql://user:pass@localhost:3306/db",
            fact_ids=(_C1_LOC,),
            numeric_fact_ids=(),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        assert any(i.code == "sensitive_content" for i in result.issues)

    def test_sensitive_db_url_postgres(self) -> None:
        claim = ReportClaim(
            text="postgresql://user:pass@localhost:5432/db",
            fact_ids=(_C1_LOC,),
            numeric_fact_ids=(),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        assert any(i.code == "sensitive_content" for i in result.issues)

    def test_limitation_without_numbers_passes(self) -> None:
        claim = ReportClaim(
            text="資料僅供參考",
            fact_ids=(_C1_LOC,),
            numeric_fact_ids=(),
        )
        draft = _valid_draft(limitations=(claim,))
        result = validate_report(draft, PACK)
        assert result.valid, f"expected valid, got: {result.issues}"

    def test_unit_mismatch_ping_text_with_twd_fact(self) -> None:
        claim = ReportClaim(
            text="面積約30.00坪",
            fact_ids=(_C1_AREA,),
            numeric_fact_ids=(_C1_AREA,),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert result.valid, (
            f"area fact with 坪 text should be valid, got: {result.issues}"
        )

    def test_unit_mismatch_detected(self) -> None:
        claim = ReportClaim(
            text="面積約30.00坪",
            fact_ids=(_C1_ASKING,),
            numeric_fact_ids=(_C1_ASKING,),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        assert any(i.code == "unit_mismatch" for i in result.issues)

    def test_results_struct(self) -> None:
        result = validate_report(_valid_draft(), PACK)
        assert isinstance(result, ValidationResult)
        assert result.valid is True
        assert isinstance(result.issues, tuple)

    def test_number_without_numeric_fact_id_is_rejected(self) -> None:
        claim = ReportClaim(
            text="總價99999999元",
            fact_ids=(_C1_ASKING,),
            numeric_fact_ids=(),
        )
        draft = _valid_draft(summary=claim)
        result = validate_report(draft, PACK)
        assert not result.valid
        assert any(i.code == "missing_numeric_fact_reference" for i in result.issues)
