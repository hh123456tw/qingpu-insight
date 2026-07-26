from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from qingpu_insight.evidence import EvidenceBuilder
from qingpu_insight.ollama_report_provider import ProviderError
from qingpu_insight.report_contracts import (
    BuyerReportDraft,
    EvidencePack,
    ReportRequest,
    SavedBuyerReport,
)
from qingpu_insight.report_providers import ProviderResult, ReportProvider
from qingpu_insight.report_repository import MySQLReportRepository
from qingpu_insight.report_validation import ValidationResult

ValidatorFn = Callable[[BuyerReportDraft, EvidencePack], ValidationResult]


class ReportService:
    def __init__(
        self,
        evidence_builder: EvidenceBuilder,
        providers: dict[str, ReportProvider],
        rule_provider: ReportProvider,
        validator: ValidatorFn,
        repository: MySQLReportRepository,
        provider_resolver: Callable[[str], ReportProvider | None] | None = None,
    ) -> None:
        self._evidence_builder = evidence_builder
        self._providers = providers
        self._rule_provider = rule_provider
        self._validator = validator
        self._repository = repository
        self._provider_resolver = provider_resolver

    def generate(self, request: ReportRequest) -> SavedBuyerReport:
        pack = self._evidence_builder.build(request)
        start = time.perf_counter()

        fallback_reason: str | None = None
        result: ProviderResult | None = None

        if request.provider == "rule":
            result = self._rule_provider.generate(pack)
            validation = self._validator(result.draft, pack)
            if not validation.valid:
                raise ProviderError("service_rule_failed")
        else:
            ai_provider = self._providers.get(request.provider)
            if ai_provider is None and self._provider_resolver is not None:
                ai_provider = self._provider_resolver(request.provider)

            if ai_provider is not None:
                try:
                    result = ai_provider.generate(pack)
                    validation = self._validator(result.draft, pack)
                    if not validation.valid:
                        codes = tuple(i.code for i in validation.issues)
                        try:
                            result = ai_provider.generate(pack, repair_codes=codes)
                            validation = self._validator(result.draft, pack)
                        except ProviderError as e:
                            fallback_reason = e.code
                            result = None

                        if result is not None and not validation.valid:
                            fallback_reason = "validation_failed"
                            result = None
                except ProviderError as e:
                    fallback_reason = e.code

        if result is None and request.provider != "rule":
            if fallback_reason is None:
                fallback_reason = "provider_unavailable"
            result = self._rule_provider.generate(pack)
            validation = self._validator(result.draft, pack)
            if not validation.valid:
                raise ProviderError("service_rule_failed")

        latency_ms = (time.perf_counter() - start) * 1000
        validation_codes = tuple(i.code for i in validation.issues)

        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "candidate_ids": sorted(request.candidate_ids),
                    "budget_twd": request.budget_twd,
                    "intended_use": request.intended_use,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:20]

        saved = SavedBuyerReport(
            report_id=str(uuid.uuid4()),
            request_hash=request_hash,
            dataset_version=pack.dataset_version,
            evidence_pack_id=pack.pack_id,
            provider=result.provider,
            model=result.model,
            content=result.draft.model_dump(mode="json"),
            fallback_reason=fallback_reason,
            validation_codes=validation_codes,
            latency_ms=latency_ms,
            created_at=datetime.now(UTC).isoformat(),
        )

        return self._repository.create(saved)
