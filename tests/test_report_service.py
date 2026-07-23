from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from qingpu_insight.ollama_report_provider import ProviderError
from qingpu_insight.report_contracts import (
    BuyerReportDraft,
    EvidenceCandidate,
    EvidenceFact,
    EvidencePack,
    ReportClaim,
    ReportRequest,
)
from qingpu_insight.report_providers import ProviderResult, RuleReportProvider
from qingpu_insight.report_service import ReportService
from qingpu_insight.report_validation import ValidationIssue, ValidationResult


def _fact_id(version: str, cid: str, kind: str, unit: str) -> str:
    raw = f"{version}|{cid}|{kind}|{unit}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


_NOW = datetime.now(UTC).isoformat()
_VER = "v1"

_FID = {
    "asking_price": _fact_id(_VER, "c1", "asking_price", "twd"),
    "unit_price": _fact_id(_VER, "c1", "unit_price", "twd_per_ping"),
    "area": _fact_id(_VER, "c1", "area", "ping"),
    "building_age": _fact_id(_VER, "c1", "building_age", "years"),
    "station_distance": _fact_id(_VER, "c1", "station_distance", "m"),
    "model_interval": _fact_id(_VER, "c1", "model_interval", "twd"),
    "location_evidence": _fact_id(_VER, "c1", "location_evidence", "method"),
    "data_freshness": _fact_id(_VER, "c1", "data_freshness", "iso"),
}


def _mkfact(kind: str, value: str, unit: str, label: str) -> EvidenceFact:
    st = "valuation" if kind == "model_interval" else "listing"
    return EvidenceFact(
        fact_id=_FID[kind], kind=kind, label=label,
        value=value, unit=unit, source_type=st,
        source_version=_VER, observed_at=_NOW,
    )


_FACTS = (
    _mkfact("asking_price", "15000000", "twd", "Asking Price"),
    _mkfact("unit_price", "500000", "twd_per_ping", "Unit Price"),
    _mkfact("area", "30.00", "ping", "Building Area"),
    _mkfact("building_age", "5.0", "years", "Building Age"),
    _mkfact("station_distance", "A18 300m", "m", "Station Distance"),
    _mkfact("model_interval", "13000000-16000000", "twd", "Model Valuation Interval"),
    _mkfact("location_evidence", "structured_address", "method", "Location Method"),
    _mkfact("data_freshness", _NOW, "iso", "Data Freshness"),
)

_CANDIDATE = EvidenceCandidate(candidate_id="c1", listing_type="sale")

PACK = EvidencePack(
    pack_id="test-pack", dataset_version=_VER, generated_at=_NOW,
    candidates=(_CANDIDATE,), facts=_FACTS, limitations=(),
)

VALID_DRAFT = BuyerReportDraft(
    summary=ReportClaim(
        text="總價15000000元，面積30.00坪",
        fact_ids=(_FID["asking_price"], _FID["area"]),
        numeric_fact_ids=(_FID["asking_price"], _FID["area"]),
    ),
    advantages=(ReportClaim(
        text="交通便利",
        fact_ids=(_FID["station_distance"], _FID["location_evidence"]),
        numeric_fact_ids=(),
    ),),
    risks=(ReportClaim(
        text="屋齡5.0年",
        fact_ids=(_FID["building_age"],),
        numeric_fact_ids=(_FID["building_age"],),
    ),),
    negotiation=(ReportClaim(
        text="開價15000000元",
        fact_ids=(_FID["asking_price"],),
        numeric_fact_ids=(_FID["asking_price"],),
    ),),
    limitations=(ReportClaim(
        text="僅供參考",
        fact_ids=(_FID["location_evidence"],),
        numeric_fact_ids=(),
    ),),
)

INVALID_DRAFT = BuyerReportDraft(
    summary=ReportClaim(
        text="總價99999999元",
        fact_ids=(_FID["asking_price"],),
        numeric_fact_ids=(_FID["asking_price"],),
    ),
    advantages=(ReportClaim(
        text="交通便利",
        fact_ids=(_FID["station_distance"], _FID["location_evidence"]),
        numeric_fact_ids=(),
    ),),
    risks=(ReportClaim(
        text="屋齡5.0年",
        fact_ids=(_FID["building_age"],),
        numeric_fact_ids=(_FID["building_age"],),
    ),),
    negotiation=(ReportClaim(
        text="開價15000000元",
        fact_ids=(_FID["asking_price"],),
        numeric_fact_ids=(_FID["asking_price"],),
    ),),
    limitations=(ReportClaim(
        text="僅供參考",
        fact_ids=(_FID["location_evidence"],),
        numeric_fact_ids=(),
    ),),
)

REQUEST = ReportRequest(
    candidate_ids=("c1",), intended_use="self_use", provider="ollama",
)


class SequenceProvider:
    def __init__(self, results: list[ProviderResult]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, ...]] = []

    def generate(
        self, pack: EvidencePack, repair_codes: tuple[str, ...] = (),
    ) -> ProviderResult:
        self.calls.append(repair_codes)
        return self._results.pop(0)


def _make_validator_that_rejects_unsubstantiated() -> Any:
    def validating(draft: BuyerReportDraft, pack: EvidencePack) -> ValidationResult:
        issues: list[ValidationIssue] = []
        draft_data = draft.model_dump()
        summary = draft_data.get("summary", {})
        if "99999999" in summary.get("text", ""):
            issues.append(ValidationIssue(
                code="unsubstantiated_number", path="summary",
            ))
        return ValidationResult(valid=len(issues) == 0, issues=tuple(issues))
    return validating


def _make_service(
    ai_provider: Any = None,
    rule_provider: Any = None,
    repo: Any = None,
    validator: Any = None,
    builder: Any = None,
) -> ReportService:
    if builder is None:
        b = MagicMock()
        b.build.return_value = PACK
        builder = b
    if ai_provider is None:
        providers: dict[str, Any] = {}
    elif isinstance(ai_provider, SequenceProvider):
        providers = {"ollama": ai_provider, "gemini": ai_provider}
    else:
        providers = {"ollama": ai_provider, "gemini": ai_provider}
    if rule_provider is None:
        rule_provider = RuleReportProvider()
    if validator is None:
        v = MagicMock()
        v.return_value = ValidationResult(valid=True)
        validator = v
    if repo is None:
        r = MagicMock()
        r.create.side_effect = lambda x: x
        repo = r
    return ReportService(
        evidence_builder=builder, providers=providers,
        rule_provider=rule_provider, validator=validator, repository=repo,
    )


class TestReportService:
    def test_successful_ai_provider(self) -> None:
        ai = MagicMock()
        ai.generate.return_value = ProviderResult(
            provider="ollama", model="mock", draft=VALID_DRAFT, latency_ms=10,
        )
        repo = MagicMock()
        repo.create.side_effect = lambda x: x

        service = _make_service(ai_provider=ai, repo=repo)
        result = service.generate(REQUEST)

        assert result.provider == "ollama"
        assert result.fallback_reason is None
        ai.generate.assert_called_once()

    def test_ai_timeout_falls_back_to_rule(self) -> None:
        ai = MagicMock()
        ai.generate.side_effect = ProviderError("ollama_timeout")
        repo = MagicMock()
        repo.create.side_effect = lambda x: x

        service = _make_service(ai_provider=ai, repo=repo)
        saved = service.generate(REQUEST)

        assert saved.provider == "rule"
        assert saved.fallback_reason == "ollama_timeout"

    def test_ai_connection_error_falls_back_to_rule(self) -> None:
        ai = MagicMock()
        ai.generate.side_effect = ProviderError("ollama_connection_error")
        repo = MagicMock()
        repo.create.side_effect = lambda x: x

        service = _make_service(ai_provider=ai, repo=repo)
        saved = service.generate(REQUEST)

        assert saved.provider == "rule"
        assert saved.fallback_reason == "ollama_connection_error"

    def test_provider_unavailable_falls_back_to_rule(self) -> None:
        repo = MagicMock()
        repo.create.side_effect = lambda x: x

        service = _make_service(ai_provider=None, repo=repo)
        req = ReportRequest(
            candidate_ids=("c1",), intended_use="self_use", provider="gemini",
        )
        saved = service.generate(req)

        assert saved.provider == "rule"
        assert saved.fallback_reason == "provider_unavailable"

    def test_validation_error_triggers_repair(self) -> None:
        ai = MagicMock()
        ai.generate.return_value = ProviderResult(
            provider="ollama", model="mock", draft=INVALID_DRAFT, latency_ms=10,
        )
        repo = MagicMock()
        repo.create.side_effect = lambda x: x

        service = _make_service(
            ai_provider=ai, repo=repo,
            validator=_make_validator_that_rejects_unsubstantiated(),
        )
        service.generate(REQUEST)

        assert ai.generate.call_count == 2
        _, kwargs2 = ai.generate.call_args_list[1]
        assert kwargs2.get("repair_codes") == ("unsubstantiated_number",)

    def test_invalid_report_repairs_once_then_rules(self) -> None:
        ai = SequenceProvider([
            ProviderResult(provider="ollama", model="mock", draft=INVALID_DRAFT, latency_ms=10),
            ProviderResult(provider="ollama", model="mock", draft=INVALID_DRAFT, latency_ms=10),
        ])
        repo = MagicMock()
        repo.create.side_effect = lambda x: x

        service = _make_service(
            ai_provider=ai, repo=repo,
            validator=_make_validator_that_rejects_unsubstantiated(),
        )
        service.generate(REQUEST)
        assert ai.calls == [(), ("unsubstantiated_number",)]
        args, _ = repo.create.call_args
        saved_report = args[0]
        assert saved_report.provider == "rule"
        assert saved_report.fallback_reason == "validation_failed"

    def test_rule_failure_fails_request(self) -> None:
        ai = MagicMock()
        ai.generate.side_effect = ProviderError("ollama_timeout")

        bad_rule = MagicMock()
        bad_rule.generate.return_value = ProviderResult(
            provider="rule", model="rule", draft=INVALID_DRAFT, latency_ms=0,
        )
        repo = MagicMock()

        service = _make_service(
            ai_provider=ai, rule_provider=bad_rule, repo=repo,
            validator=_make_validator_that_rejects_unsubstantiated(),
        )
        with pytest.raises(ProviderError) as exc:
            service.generate(REQUEST)
        assert exc.value.code == "service_rule_failed"
        assert not repo.create.called

    def test_repository_failure_raised(self) -> None:
        ai = MagicMock()
        ai.generate.return_value = ProviderResult(
            provider="ollama", model="mock", draft=VALID_DRAFT, latency_ms=10,
        )
        repo = MagicMock()
        repo.create.side_effect = RuntimeError("db connection lost")

        service = _make_service(ai_provider=ai, repo=repo)
        with pytest.raises(RuntimeError, match="db connection lost"):
            service.generate(REQUEST)

    def test_rule_provider_direct(self) -> None:
        repo = MagicMock()
        repo.create.side_effect = lambda x: x

        service = _make_service(repo=repo)
        req = ReportRequest(
            candidate_ids=("c1",), intended_use="self_use", provider="rule",
        )
        saved = service.generate(req)

        assert saved.provider == "rule"
        assert saved.fallback_reason is None

    def test_latency_and_metadata_recorded(self) -> None:
        repo = MagicMock()
        repo.create.side_effect = lambda x: x

        service = _make_service(repo=repo)
        service.generate(REQUEST)

        assert repo.create.called
        args, _ = repo.create.call_args
        saved = args[0]
        assert saved.dataset_version == "v1"
        assert saved.evidence_pack_id == "test-pack"
        assert saved.latency_ms >= 0
        assert len(saved.report_id) > 0

    def test_request_hash(self) -> None:
        repo = MagicMock()
        repo.create.side_effect = lambda x: x

        service = _make_service(repo=repo)
        req = ReportRequest(
            candidate_ids=("c1",), intended_use="self_use", provider="ollama",
        )
        service.generate(req)

        args, _ = repo.create.call_args
        saved = args[0]
        expected_hash = hashlib.sha256(
            json.dumps(
                {"candidate_ids": ["c1"], "budget_twd": None, "intended_use": "self_use"},
                sort_keys=True,
            ).encode(),
        ).hexdigest()[:20]
        assert saved.request_hash == expected_hash
