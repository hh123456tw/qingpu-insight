from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from qingpu_insight.report_contracts import (
    BuyerReportDraft,
    EvidenceFact,
    EvidencePack,
    ReportClaim,
)


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model: str
    draft: BuyerReportDraft
    latency_ms: float
    raw_usage: dict = field(default_factory=dict)


class ReportProvider(Protocol):
    def generate(
        self, pack: EvidencePack, repair_codes: tuple[str, ...] = ()
    ) -> ProviderResult:
        ...


class RuleReportProvider:
    def generate(
        self, pack: EvidencePack, repair_codes: tuple[str, ...] = ()
    ) -> ProviderResult:
        start = time.perf_counter()
        draft = self._build_draft(pack)
        elapsed = (time.perf_counter() - start) * 1000
        return ProviderResult(
            provider="rule", model="rule", draft=draft, latency_ms=elapsed
        )

    def _build_draft(self, pack: EvidencePack) -> BuyerReportDraft:
        facts_by_id = {f.fact_id: f for f in pack.facts}
        cids = [c.candidate_id for c in pack.candidates]
        cid = cids[0] if cids else "unknown"

        summary_text = self._build_summary(cid, pack.facts, facts_by_id)
        advantages = self._build_advantages(pack.facts, facts_by_id)
        risks = self._build_risks(pack.facts, facts_by_id)
        negotiation = self._build_negotiation(pack.facts, facts_by_id)
        limitations = self._build_limitations(
            pack.limitations, pack.facts, facts_by_id
        )

        return BuyerReportDraft(
            summary=summary_text,
            advantages=tuple(advantages),
            risks=tuple(risks),
            negotiation=tuple(negotiation),
            limitations=tuple(limitations),
        )

    def _fact_by_kind(
        self, facts: tuple[EvidenceFact, ...], kind: str
    ) -> EvidenceFact | None:
        for f in facts:
            if f.kind == kind:
                return f
        return None

    def _build_summary(
        self,
        cid: str,
        facts: tuple[EvidenceFact, ...],
        facts_by_id: dict[str, EvidenceFact],
    ) -> ReportClaim:
        parts = [f"物件 {cid}"]
        fact_ids: list[str] = []
        numeric_ids: list[str] = []

        asking = self._fact_by_kind(facts, "asking_price")
        if asking:
            parts.append(f"開價 {asking.value} 元")
            fact_ids.append(asking.fact_id)
            numeric_ids.append(asking.fact_id)

        area = self._fact_by_kind(facts, "area")
        if area:
            parts.append(f"面積 {area.value} 坪")
            fact_ids.append(area.fact_id)
            numeric_ids.append(area.fact_id)

        unit_price = self._fact_by_kind(facts, "unit_price")
        if unit_price:
            parts.append(f"單價 {unit_price.value} 元/坪")
            fact_ids.append(unit_price.fact_id)
            numeric_ids.append(unit_price.fact_id)

        age = self._fact_by_kind(facts, "building_age")
        if age:
            parts.append(f"屋齡 {age.value} 年")
            fact_ids.append(age.fact_id)
            numeric_ids.append(age.fact_id)

        station = self._fact_by_kind(facts, "station_distance")
        if station:
            parts.append(f"車站距離 {station.value}")
            fact_ids.append(station.fact_id)

        loc = self._fact_by_kind(facts, "location_evidence")
        if loc:
            fact_ids.append(loc.fact_id)

        text = "，".join(parts) + "。"

        if not fact_ids and facts:
            fact_ids.append(facts[0].fact_id)
        return ReportClaim(
            text=text,
            fact_ids=tuple(fact_ids),
            numeric_fact_ids=tuple(numeric_ids),
        )

    def _build_advantages(
        self,
        facts: tuple[EvidenceFact, ...],
        facts_by_id: dict[str, EvidenceFact],
    ) -> list[ReportClaim]:
        claims: list[ReportClaim] = []

        loc = self._fact_by_kind(facts, "location_evidence")
        station = self._fact_by_kind(facts, "station_distance")
        if loc and station and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text=f"位於 {station.value}，位置明確且交通便利。",
                    fact_ids=(loc.fact_id, station.fact_id),
                    numeric_fact_ids=(),
                )
            )
        elif loc and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text="此物件位置可明確定位，區位條件佳。",
                    fact_ids=(loc.fact_id,),
                    numeric_fact_ids=(),
                )
            )

        nearby = self._fact_by_kind(facts, "nearby_transactions_summary")
        if nearby and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text=f"附近有 {nearby.value}，可作為價格參考。",
                    fact_ids=(nearby.fact_id,),
                    numeric_fact_ids=(),
                )
            )

        unit_price = self._fact_by_kind(facts, "unit_price")
        if unit_price and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text=f"單價每坪 {unit_price.value} 元，價格透明。",
                    fact_ids=(unit_price.fact_id,),
                    numeric_fact_ids=(unit_price.fact_id,),
                )
            )

        freshness = self._fact_by_kind(facts, "data_freshness")
        if freshness and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text="資料來源為近期更新，資訊具時效性。",
                    fact_ids=(freshness.fact_id,),
                    numeric_fact_ids=(),
                )
            )

        if not claims:
            claims.append(
                ReportClaim(
                    text="物件資訊已整理提供。",
                    fact_ids=tuple(f.fact_id for f in facts[:1]),
                    numeric_fact_ids=(),
                )
            )
        return claims

    def _build_risks(
        self,
        facts: tuple[EvidenceFact, ...],
        facts_by_id: dict[str, EvidenceFact],
    ) -> list[ReportClaim]:
        claims: list[ReportClaim] = []
        lid = tuple(f.fact_id for f in facts[:1])

        station = self._fact_by_kind(facts, "station_distance")
        if not station:
            claims.append(
                ReportClaim(
                    text="目前無車站距離資訊，通勤便利性需自行評估。",
                    fact_ids=lid,
                    numeric_fact_ids=(),
                )
            )

        age = self._fact_by_kind(facts, "building_age")
        if age and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text=f"屋齡 {age.value} 年，建議留意屋況維護與管線狀態。",
                    fact_ids=(age.fact_id,),
                    numeric_fact_ids=(age.fact_id,),
                )
            )

        nearby = self._fact_by_kind(facts, "nearby_transactions_summary")
        if not nearby and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text="附近無近期官方成交資料，市場參考有限。",
                    fact_ids=lid,
                    numeric_fact_ids=(),
                )
            )
        elif nearby and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text="市場成交行情可能隨時間波動，僅供參考。",
                    fact_ids=(nearby.fact_id,),
                    numeric_fact_ids=(),
                )
            )

        model_iv = self._fact_by_kind(facts, "model_interval")
        if model_iv and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text=f"模型估值區間為 {model_iv.value} 元，實際成交受市場因素影響。",
                    fact_ids=(model_iv.fact_id,),
                    numeric_fact_ids=(model_iv.fact_id,),
                )
            )

        if not claims:
            claims.append(
                ReportClaim(
                    text="市場存在不確定性，建議多方比較。",
                    fact_ids=lid,
                    numeric_fact_ids=(),
                )
            )
        return claims

    def _build_negotiation(
        self,
        facts: tuple[EvidenceFact, ...],
        facts_by_id: dict[str, EvidenceFact],
    ) -> list[ReportClaim]:
        claims: list[ReportClaim] = []
        lid = tuple(f.fact_id for f in facts[:1])

        model_iv = self._fact_by_kind(facts, "model_interval")
        asking = self._fact_by_kind(facts, "asking_price")

        if model_iv and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text=f"模型估值區間 {model_iv.value} 元，可作為議價參考範圍。",
                    fact_ids=(model_iv.fact_id,),
                    numeric_fact_ids=(model_iv.fact_id,),
                )
            )

        nearby = self._fact_by_kind(facts, "nearby_transactions_summary")
        if nearby and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text=f"附近 {nearby.value}，可比較近期成交價格。",
                    fact_ids=(nearby.fact_id,),
                    numeric_fact_ids=(),
                )
            )

        if asking and model_iv and len(claims) < 3:
            claims.append(
                ReportClaim(
                    text=f"開價 {asking.value} 元，可參考模型估值低端進行議價。",
                    fact_ids=(asking.fact_id, model_iv.fact_id),
                    numeric_fact_ids=(asking.fact_id, model_iv.fact_id),
                )
            )

        if not claims:
            claims.append(
                ReportClaim(
                    text="建議參考周邊相似物件開價進行議價。",
                    fact_ids=lid,
                    numeric_fact_ids=(),
                )
            )
        return claims

    def _build_limitations(
        self,
        pack_limitations: tuple[str, ...],
        facts: tuple[EvidenceFact, ...],
        facts_by_id: dict[str, EvidenceFact],
    ) -> list[ReportClaim]:
        claims: list[ReportClaim] = []

        for lim in pack_limitations:
            claims.append(
                ReportClaim(
                    text=lim,
                    fact_ids=tuple(f.fact_id for f in facts[:1]),
                    numeric_fact_ids=(),
                )
            )

        freshness = self._fact_by_kind(facts, "data_freshness")
        if not freshness:
            lid = tuple(f.fact_id for f in facts[:1])
            claims.append(
                ReportClaim(
                    text="資料時間無法確認，資訊時效性不足。",
                    fact_ids=lid,
                    numeric_fact_ids=(),
                )
            )

        if not claims:
            lid = tuple(f.fact_id for f in facts[:1])
            claims.append(
                ReportClaim(
                    text="本報告僅依現有資料產出，實際情況以現場為準。",
                    fact_ids=lid,
                    numeric_fact_ids=(),
                )
            )
        return claims


class MockReportProvider:
    def __init__(self, draft: BuyerReportDraft, latency_ms: float = 0) -> None:
        self._draft = draft
        self._latency_ms = latency_ms

    def generate(
        self, pack: EvidencePack, repair_codes: tuple[str, ...] = ()
    ) -> ProviderResult:
        return ProviderResult(
            provider="mock",
            model="mock",
            draft=self._draft,
            latency_ms=self._latency_ms,
        )
