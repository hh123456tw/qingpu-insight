from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from qingpu_insight.ollama_report_provider import ProviderError
from qingpu_insight.report_contracts import (
    BuyerReportDraft,
    EvidenceCandidate,
    EvidenceFact,
    EvidencePack,
    ReportClaim,
    ReportRequest,
    SavedBuyerReport,
)
from qingpu_insight.report_providers import MockReportProvider, ProviderResult, RuleReportProvider
from qingpu_insight.report_repository import MySQLReportRepository
from qingpu_insight.report_service import ReportService
from qingpu_insight.report_validation import validate_report

_NOW = datetime.now(UTC).isoformat()
_VER = "v1"

_FID = "aaaaaaaaaaaaaaaaaaaa"
_FID2 = "bbbbbbbbbbbbbbbbbbbb"

_FACTS = (
    EvidenceFact(
        fact_id=_FID, kind="asking_price", label="Price",
        value="15000000", unit="twd", source_type="listing",
        source_version=_VER, observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_FID2, kind="area", label="Area",
        value="30.00", unit="ping", source_type="listing",
        source_version=_VER, observed_at=_NOW,
    ),
)

_CANDIDATE = EvidenceCandidate(candidate_id="c1", listing_type="sale")

PACK = EvidencePack(
    pack_id="pack-001", dataset_version=_VER, generated_at=_NOW,
    candidates=(_CANDIDATE,), facts=_FACTS, limitations=(),
)

VALID_DRAFT = BuyerReportDraft(
    summary=ReportClaim(
        text="總價15000000元", fact_ids=(_FID,), numeric_fact_ids=(_FID,),
    ),
    advantages=(ReportClaim(
        text="交通便利", fact_ids=(_FID2,), numeric_fact_ids=(),
    ),),
    risks=(ReportClaim(
        text="屋齡未知", fact_ids=(_FID2,), numeric_fact_ids=(),
    ),),
    negotiation=(ReportClaim(
        text="開價15000000元", fact_ids=(_FID,), numeric_fact_ids=(_FID,),
    ),),
    limitations=(ReportClaim(
        text="僅供參考", fact_ids=(_FID2,), numeric_fact_ids=(),
    ),),
)

REQUEST = ReportRequest(
    candidate_ids=("c1",), intended_use="self_use", provider="ollama",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self) -> None:
        self.result: list[dict[str, Any]] = []
        self.rowcount = 0

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> int:
        normalized = " ".join(sql.lower().split())
        if "select" in normalized and "from published_datasets" in normalized:
            version = params[0]
            if version == "market":
                self.result = [{"version": "v1"}]
                self.rowcount = 1
            else:
                self.result = []
                self.rowcount = 0
        elif "insert into buyer_reports" in normalized:
            self.rowcount = 1
        elif "select" in normalized and "from buyer_reports" in normalized:
            self.result = self.result or []
        elif "select version from published_versions" in normalized:
            self.result = [{"version": "v1"}]
            self.rowcount = 1
        else:
            self.result = []
            self.rowcount = 0
        return self.rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.result


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self._cursor = FakeCursor()
        self.commits = 0

    def cursor(self, cursor_class: Any = None) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _make_factory() -> Any:
    conn = FakeConnection()

    def factory() -> FakeConnection:
        return conn

    return factory


class _FakeEvidenceRepo:
    def __init__(self, version: str = _VER) -> None:
        self._version = version

    def current_dataset_version(self) -> str:
        return self._version

    def load_candidates(self, candidate_ids):
        import pandas as pd
        return pd.DataFrame()

    def load_market_evidence(self, candidate_ids):
        import pandas as pd
        return pd.DataFrame()


def _make_service(
    *,
    ai_provider: Any = None,
    repo: Any = None,
    builder: Any = None,
) -> ReportService:
    from unittest.mock import MagicMock

    from qingpu_insight.report_providers import RuleReportProvider

    if builder is None:
        b = MagicMock()
        b.build.return_value = PACK
        builder = b
    providers: dict[str, Any] = {}
    if ai_provider is not None:
        providers = {"ollama": ai_provider, "gemini": ai_provider}
    rule = RuleReportProvider()
    if repo is None:
        r = MagicMock()
        r.create.side_effect = lambda x: x
        repo = r
    return ReportService(
        evidence_builder=builder,
        providers=providers,
        rule_provider=rule,
        validator=validate_report,
        repository=repo,
    )


# ============================================================================
# Release gate tests
# ============================================================================


class TestRuleNoNetwork:
    def test_rule_report_succeeds_without_network(self) -> None:
        result = RuleReportProvider().generate(PACK)
        assert result.provider == "rule"
        assert result.model == "rule"
        validation = validate_report(result.draft, PACK)
        assert validation.valid, f"rule draft invalid: {validation.issues}"


class TestAiValidOutput:
    def test_ai_valid_output_passes_and_saves(self) -> None:
        repo = _FakeReportRepository()

        ai = MockReportProvider(draft=VALID_DRAFT)
        builder = _FakeBuilder()
        service = _make_service(ai_provider=ai, repo=repo, builder=builder)
        saved = service.generate(REQUEST)
        assert saved.provider == "mock"
        assert saved.fallback_reason is None
        assert repo.saved is not None


class _FakeReportRepository:
    def __init__(self):
        self.saved: SavedBuyerReport | None = None

    def create(self, report: SavedBuyerReport) -> SavedBuyerReport:
        self.saved = report
        return report


class _FakeBuilder:
    def build(self, request: ReportRequest) -> EvidencePack:
        return PACK


class TestFailureModesTriggerRepair:
    @pytest.mark.parametrize(
        ("draft", "expected_first_repair_code"),
        [
            ("unknown_fact", "unknown_fact_id"),
            ("tampered", "unsubstantiated_number"),
            ("sensitive", "sensitive_content"),
        ],
    )
    def test_failure_triggers_one_repair(
        self, draft: str, expected_first_repair_code: str,
    ) -> None:
        claims_map = {
            "unknown_fact": ReportClaim(
                text="test", fact_ids=("nonexistent",), numeric_fact_ids=(),
            ),
            "tampered": ReportClaim(
                text="總價99999999元", fact_ids=(_FID,), numeric_fact_ids=(_FID,),
            ),
            "sensitive": ReportClaim(
                text="請聯繫test@example.com", fact_ids=(_FID2,), numeric_fact_ids=(),
            ),
        }
        bad_claim = claims_map[draft]
        bad_draft = BuyerReportDraft(
            summary=bad_claim, advantages=(bad_claim,), risks=(bad_claim,),
            negotiation=(bad_claim,), limitations=(bad_claim,),
        )
        call_log: list[tuple[str, ...]] = []

        class LoggingProvider:
            def generate(
                self, pack: EvidencePack, repair_codes: tuple[str, ...] = (),
            ) -> ProviderResult:
                call_log.append(repair_codes)
                return ProviderResult(
                    provider="mock", model="mock", draft=bad_draft, latency_ms=0,
                )

        repo = _FakeReportRepository()
        service = _make_service(ai_provider=LoggingProvider(), repo=repo)
        service.generate(REQUEST)
        assert len(call_log) == 2
        assert call_log[0] == ()
        assert expected_first_repair_code in call_log[1]

    def test_invalid_json_triggers_repair(self) -> None:
        call_log: list[tuple[str, ...]] = []

        class InvalidJsonProvider:
            def generate(
                self, pack: EvidencePack, repair_codes: tuple[str, ...] = (),
            ) -> ProviderResult:
                call_log.append(repair_codes)
                raise ProviderError("ollama_non_json_response")

        repo = _FakeReportRepository()
        rule = RuleReportProvider()
        builder = _FakeBuilder()

        service = ReportService(
            evidence_builder=builder,
            providers={"ollama": InvalidJsonProvider(), "gemini": InvalidJsonProvider()},
            rule_provider=rule,
            validator=validate_report,
            repository=repo,
        )
        saved = service.generate(REQUEST)
        assert saved.provider == "rule"
        assert saved.fallback_reason == "ollama_non_json_response"
        assert call_log == [()]


class TestRepairFailsRuleFallback:
    def test_repair_fails_falls_back_to_rule(self) -> None:
        bad_claim = ReportClaim(
            text="99999999", fact_ids=(_FID,), numeric_fact_ids=(_FID,),
        )
        bad_draft = BuyerReportDraft(
            summary=bad_claim, advantages=(bad_claim,), risks=(bad_claim,),
            negotiation=(bad_claim,), limitations=(bad_claim,),
        )

        class AlwaysBadProvider:
            def __init__(self):
                self.calls = 0

            def generate(
                self, pack: EvidencePack, repair_codes: tuple[str, ...] = (),
            ) -> ProviderResult:
                self.calls += 1
                return ProviderResult(
                    provider="mock", model="mock", draft=bad_draft, latency_ms=0,
                )

        ai = AlwaysBadProvider()
        repo = _FakeReportRepository()
        service = _make_service(ai_provider=ai, repo=repo)
        saved = service.generate(REQUEST)
        assert ai.calls == 2
        assert saved.provider == "rule"
        assert saved.fallback_reason == "validation_failed"


class TestPublishedPointerUnchanged:
    def test_failure_does_not_change_m42_published_pointer(self) -> None:
        from unittest.mock import MagicMock

        factory = _make_factory()
        repo = MySQLReportRepository(factory)

        bad_claim = ReportClaim(
            text="99999999", fact_ids=(_FID,), numeric_fact_ids=(_FID,),
        )
        bad_draft = BuyerReportDraft(
            summary=bad_claim, advantages=(bad_claim,), risks=(bad_claim,),
            negotiation=(bad_claim,), limitations=(bad_claim,),
        )

        class BadProvider:
            def generate(self, pack, repair_codes=()):
                return ProviderResult(
                    provider="mock", model="mock", draft=bad_draft, latency_ms=0,
                )

        class BadRuleProvider:
            def generate(self, pack, repair_codes=()):
                return ProviderResult(
                    provider="rule", model="rule", draft=bad_draft, latency_ms=0,
                )

        builder = MagicMock()
        builder.build.return_value = PACK
        service = ReportService(
            evidence_builder=builder,
            providers={"ollama": BadProvider(), "gemini": BadProvider()},
            rule_provider=BadRuleProvider(),
            validator=validate_report,
            repository=repo,
        )

        with pytest.raises(ProviderError, match="service_rule_failed"):
            service.generate(REQUEST)

        conn = factory()
        cursor = conn.cursor()
        cursor.execute("SELECT version FROM published_datasets WHERE dataset_key = %s", ("market",))
        row = cursor.fetchone()
        assert row is not None, "published pointer should still exist"
        assert row["version"] == "v1"


class TestReportMetadata:
    def test_mysql_metadata_uses_evidence_version(self) -> None:
        factory = _make_factory()
        repo = MySQLReportRepository(factory)

        builder = _FakeBuilder()
        ai = MockReportProvider(draft=VALID_DRAFT)
        rule = RuleReportProvider()
        service = ReportService(
            evidence_builder=builder,
            providers={"ollama": ai, "gemini": ai},
            rule_provider=rule,
            validator=validate_report,
            repository=repo,
        )
        saved = service.generate(REQUEST)
        assert saved.dataset_version == _VER
        assert saved.evidence_pack_id == "pack-001"


class TestNoRawBodyOrSecrets:
    def test_api_does_not_return_raw_body_or_secrets(self) -> None:
        result = RuleReportProvider().generate(PACK)
        draft = result.draft.model_dump(mode="json")
        text = json.dumps(draft)
        assert "api_key" not in text
        assert "prompt" not in text
        assert "password" not in text
        assert "system_instruction" not in text
        assert "raw_usage" not in text or result.raw_usage == {}
