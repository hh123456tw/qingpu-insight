from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from qingpu_insight.conversation_repository import SnapshotRecord


@dataclass(frozen=True)
class EvidenceFact:
    id: str
    label: str
    value: str
    source: str
    observed_at: str | None = None
    kind: str = ""


@dataclass(frozen=True)
class ConversationEvidence:
    facts: tuple[EvidenceFact, ...]
    valuation: dict | None
    comparables: tuple[dict, ...]
    limitations: tuple[str, ...]


ValuationService = Callable[[dict], dict]
MarketService = Callable[[dict], list[dict]]


class ConversationEvidenceBuilder:
    def __init__(
        self,
        *,
        valuation_service: ValuationService | None = None,
        market_service: MarketService | None = None,
        dataset_version: str = "unknown",
        model_version: str = "unknown",
    ):
        self._valuation_service = valuation_service
        self._market_service = market_service
        self._dataset_version = dataset_version
        self._model_version = model_version

    def build(self, *, snapshot: SnapshotRecord) -> ConversationEvidence:
        payload = snapshot.structured_payload
        facts = self._build_listing_facts(payload)
        valuation: dict | None = None
        comparables: list[dict] = []
        limitations: list[str] = []

        if payload.get("latitude") is None or payload.get("longitude") is None:
            limitations.append("缺少座標資訊，距離相關分析可能不準確")

        if self._valuation_service is not None:
            can_valuate, reason = self._can_valuate(payload)
            if can_valuate:
                try:
                    raw = self._valuation_service(payload)
                    valuation = {
                        **raw,
                        "model_version": raw.get(
                            "model_version", self._model_version
                        ),
                        "dataset_version": raw.get(
                            "dataset_version", self._dataset_version
                        ),
                    }
                    limitations.extend(raw.get("limitations", ()))
                except Exception:
                    limitations.append("估值模型暫時無法使用")
            else:
                limitations.append(reason)
        else:
            limitations.append("估值模型尚未啟用")

        if self._market_service is not None:
            try:
                comparables = list(self._market_service(payload))
            except Exception:
                limitations.append("相似成交資料不足")

        if len(comparables) < 3:
            limitations.append("相似成交案例不足 3 筆，價格參考性有限")

        if valuation is not None:
            facts.extend(self._build_valuation_facts(valuation))
        if len(comparables) > 10:
            comparables = comparables[:10]

        if comparables:
            facts.extend(self._build_comparable_facts(comparables))
            facts.extend(self._build_market_summary_facts(comparables))

        return ConversationEvidence(
            facts=tuple(facts),
            valuation=valuation,
            comparables=tuple(comparables),
            limitations=tuple(limitations),
        )

    @staticmethod
    def _build_market_summary_facts(
        comparables: list[dict],
    ) -> list[EvidenceFact]:
        facts = [
            EvidenceFact(
                id="market.sample_size",
                label="相似成交筆數",
                value=str(len(comparables)),
                source="實價登錄",
            )
        ]
        unit_prices = sorted(
            int(comp["unit_price_per_ping_twd"])
            for comp in comparables
            if comp.get("unit_price_per_ping_twd") is not None
        )
        if unit_prices:
            middle = len(unit_prices) // 2
            median = (
                unit_prices[middle]
                if len(unit_prices) % 2
                else (unit_prices[middle - 1] + unit_prices[middle]) // 2
            )
            facts.append(
                EvidenceFact(
                    id="market.median_unit_price",
                    label="相似成交單價中位數",
                    value=f"{median:,} 元/坪",
                    source="實價登錄",
                )
            )
        dates = sorted(
            str(comp["transaction_date"])
            for comp in comparables
            if comp.get("transaction_date")
        )
        if dates:
            facts.append(
                EvidenceFact(
                    id="market.period",
                    label="相似成交資料期間",
                    value=f"{dates[0]} 至 {dates[-1]}",
                    source="實價登錄",
                )
            )
        return facts

    def _can_valuate(self, payload: dict) -> tuple[bool, str | None]:
        floor = payload.get("floor")
        total_floors = payload.get("total_floors")
        if floor is not None and total_floors is not None:
            try:
                f = int(floor)
                tf = int(total_floors)
                if f > tf:
                    return False, f"樓層資料不一致（{f}/{tf}），無法進行精確估價"
            except (ValueError, TypeError):
                pass
        return True, None

    def _build_listing_facts(self, payload: dict) -> list[EvidenceFact]:
        facts: list[EvidenceFact] = []
        observed_at: str | None = payload.get("source_updated_text")

        mappings = [
            ("title", "listing.title", "物件名稱"),
            ("total_price_twd", "listing.price", "開價總價"),
            ("unit_price_twd_per_ping", "listing.unit_price", "單價"),
            ("area_ping", "listing.area", "建物面積"),
            ("layout", "listing.layout", "格局"),
            ("address", "listing.address", "地址"),
            ("community_name", "listing.community", "社區名稱"),
            ("builder_name", "listing.builder", "建商"),
            ("building_type", "listing.building_type", "建築類型"),
            ("floor", "listing.floor", "樓層"),
            ("age_years", "listing.age", "屋齡"),
            ("parking_type", "listing.parking", "車位類型"),
        ]
        for key, fact_id, label in mappings:
            value = payload.get(key)
            if value is not None:
                facts.append(
                    EvidenceFact(
                        id=fact_id,
                        label=label,
                        value=self._format_value(key, value),
                        source="591 詳細頁",
                        observed_at=observed_at,
                    )
                )

        lat = payload.get("latitude")
        lng = payload.get("longitude")
        if lat is not None and lng is not None:
            facts.append(
                EvidenceFact(
                    id="listing.location",
                    label="座標",
                    value=f"{lat}, {lng}",
                    source="591 詳細頁",
                    observed_at=observed_at,
                )
            )

        return facts

    @staticmethod
    def _format_value(key: str, value: Any) -> str:
        if key == "total_price_twd":
            return f"{value:,} 元"
        if key == "unit_price_twd_per_ping":
            return f"{value:,} 元/坪"
        if key == "area_ping":
            return f"{value} 坪"
        if key == "age_years":
            return f"{value} 年"
        return str(value)

    @staticmethod
    def _build_valuation_facts(valuation: dict) -> list[EvidenceFact]:
        facts: list[EvidenceFact] = []
        mappings = [
            ("point_estimate_twd", "valuation.point", "估值點", lambda v: f"{v:,} 元"),
            ("low_estimate_twd", "valuation.low", "估值下限", lambda v: f"{v:,} 元"),
            ("high_estimate_twd", "valuation.high", "估值上限", lambda v: f"{v:,} 元"),
            ("confidence", "valuation.confidence", "信心度", str),
        ]
        for key, fact_id, label, formatter in mappings:
            value = valuation.get(key)
            if value is not None:
                facts.append(
                    EvidenceFact(
                        id=fact_id,
                        label=label,
                        value=formatter(value),
                        source="估值模型",
                        observed_at=None,
                    )
                )
        return facts

    @staticmethod
    def _build_comparable_facts(comparables: list[dict]) -> list[EvidenceFact]:
        facts: list[EvidenceFact] = []
        for comp in comparables:
            rank = comp.get("rank")
            if rank is None:
                continue
            mappings = [
                ("price_twd", f"comparable.{rank}.price", "總價", lambda v: f"{v:,} 元"),
                (
                    "unit_price_per_ping_twd",
                    f"comparable.{rank}.unit_price",
                    "單價",
                    lambda v: f"{v:,} 元/坪",
                ),
                ("distance_m", f"comparable.{rank}.distance", "距離", lambda v: f"{v} 公尺"),
                ("transaction_date", f"comparable.{rank}.date", "交易日期", str),
            ]
            for key, fact_id, label, formatter in mappings:
                value = comp.get(key)
                if value is not None:
                    facts.append(
                        EvidenceFact(
                            id=fact_id,
                            label=label,
                            value=formatter(value),
                            source="實價登錄",
                            observed_at=None,
                        )
                    )
        return facts
