from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from qingpu_insight.community_registry import CommunityMatch, CommunityRegistry
from qingpu_insight.conversation_repository import SnapshotRecord
from qingpu_insight.presentation import (
    format_total_price_wan,
    format_unit_price_wan,
    localize_confidence,
)


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
        community_registry: CommunityRegistry | None = None,
    ):
        self._valuation_service = valuation_service
        self._market_service = market_service
        self._dataset_version = dataset_version
        self._model_version = model_version
        self._community_registry = community_registry

    def build(self, *, snapshot: SnapshotRecord) -> ConversationEvidence:
        payload = dict(snapshot.structured_payload)
        facts = self._build_listing_facts(payload)

        limitations: list[str] = []

        community_match: CommunityMatch | None = None
        if self._community_registry is not None:
            lat = payload.get("latitude")
            lng = payload.get("longitude")
            try:
                twd97_x, twd97_y = (
                    (float(lng), float(lat))
                    if lat is not None and lng is not None
                    else (None, None)
                )
            except (ValueError, TypeError):
                twd97_x = twd97_y = None
            community_match = self._community_registry.match_listing(
                name=payload.get("community_name"),
                address=payload.get("address"),
                twd97_x=twd97_x,
                twd97_y=twd97_y,
            )

        if community_match is not None and community_match.community_id is not None:
            payload["community_id"] = community_match.community_id
            payload["community_known"] = community_match.community_id
            payload["community_match_method"] = community_match.method
            facts.append(
                EvidenceFact(
                    id="listing.community_match_method",
                    label="社區比對方式",
                    value=community_match.method,
                    source="591 詳細頁",
                    observed_at=payload.get("source_updated_text"),
                )
            )
            facts.append(
                EvidenceFact(
                    id="listing.community_known",
                    label="社區識別碼",
                    value=community_match.community_id,
                    source="591 詳細頁",
                    observed_at=payload.get("source_updated_text"),
                )
            )
        else:
            payload["community_id"] = None
            payload["community_known"] = "unknown"
            payload["community_match_method"] = "unknown"
            limitations.append("社區未識別，估價僅依賴區位/座標資訊")

        common_area_ratio = payload.get("common_area_ratio")
        if common_area_ratio is not None:
            payload["common_area_ratio"] = float(common_area_ratio)
            pct = round(float(common_area_ratio) * 100, 1)
            facts.append(
                EvidenceFact(
                    id="listing.common_area_ratio",
                    label="公設比",
                    value=f"{pct}%",
                    source="591 詳細頁",
                    observed_at=payload.get("source_updated_text"),
                )
            )
        else:
            payload["common_area_ratio"] = None

        valuation: dict | None = None
        comparables: list[dict] = []

        if payload.get("latitude") is None or payload.get("longitude") is None:
            limitations.append("缺少座標資訊，距離相關分析可能不準確")

        if self._valuation_service is not None:
            can_valuate, reason = self._can_valuate(payload)
            if can_valuate:
                if reason is not None:
                    limitations.append(reason)
                try:
                    raw = self._valuation_service(payload)
                    raw_comparables = raw.get("comparables")
                    if isinstance(raw_comparables, list):
                        comparables = list(raw_comparables)
                    valuation = {
                        **{
                            key: value
                            for key, value in raw.items()
                            if key != "comparables"
                        },
                        "model_version": raw.get("model_version", self._model_version),
                        "dataset_version": raw.get("dataset_version", self._dataset_version),
                    }
                    limitations.extend(raw.get("limitations", ()))
                except Exception:
                    limitations.append("估值模型暫時無法使用")
            else:
                limitations.append(reason)
        else:
            limitations.append("估值模型尚未啟用")

        if self._market_service is not None and not comparables:
            try:
                comparables = list(self._market_service(payload))
            except Exception:
                limitations.append("相似成交資料不足")

        if len(comparables) < 3:
            limitations.append("相似成交案例不足 3 筆，價格參考性有限")

        if valuation is not None:
            facts.extend(self._build_valuation_facts(valuation))
            asking_price = payload.get("total_price_twd")
            if asking_price is not None:
                facts.extend(self._build_asking_gap_facts(valuation, asking_price))
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
                    value=format_unit_price_wan(median),
                    source="實價登錄",
                )
            )
        dates = sorted(
            str(comp["transaction_date"]) for comp in comparables if comp.get("transaction_date")
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
        common_area_ratio = payload.get("common_area_ratio")
        if common_area_ratio is not None and not (0.0 <= common_area_ratio <= 0.70):
            return True, f"公設比 {common_area_ratio:.2f} 超出合理範圍"
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
            ("common_area_ratio_source", "listing.common_area_ratio_source", "公設比來源"),
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

        unit_price_low = payload.get("unit_price_low_twd_per_ping")
        unit_price_high = payload.get("unit_price_high_twd_per_ping")
        if unit_price_low is not None and unit_price_high is not None:
            facts.append(
                EvidenceFact(
                    id="listing.unit_price_range",
                    label="單價區間",
                    value=(
                        f"{format_unit_price_wan(unit_price_low)} 至 "
                        f"{format_unit_price_wan(unit_price_high)}"
                    ),
                    source="591 詳細頁",
                    observed_at=observed_at,
                )
            )

        area_low = payload.get("area_low_ping")
        area_high = payload.get("area_high_ping")
        if area_low is not None and area_high is not None:
            facts.append(
                EvidenceFact(
                    id="listing.area_range",
                    label="坪數區間",
                    value=f"{area_low} 至 {area_high} 坪",
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
            return format_total_price_wan(value)
        if key == "unit_price_twd_per_ping":
            return format_unit_price_wan(value)
        if key == "area_ping":
            return f"{value} 坪"
        if key == "age_years":
            return f"{value} 年"
        return str(value)

    @staticmethod
    def _build_valuation_facts(valuation: dict) -> list[EvidenceFact]:
        facts: list[EvidenceFact] = []
        mappings = [
            ("point_estimate_twd", "valuation.point", "估值點", format_total_price_wan),
            ("low_estimate_twd", "valuation.low", "估值下限", format_total_price_wan),
            ("high_estimate_twd", "valuation.high", "估值上限", format_total_price_wan),
            ("confidence", "valuation.confidence", "信心度", localize_confidence),
        ]
        for key, fact_id, label, formatter in mappings:
            value = valuation.get(key)
            if value is not None:
                formatted = formatter(value)
                if key == "confidence":
                    reasons = valuation.get("confidence_reasons")
                    if isinstance(reasons, list) and reasons:
                        first_reason = str(reasons[0]).strip()
                        if first_reason:
                            formatted = f"{formatted}（{first_reason}）"
                facts.append(
                    EvidenceFact(
                        id=fact_id,
                        label=label,
                        value=formatted,
                        source="估值模型",
                        observed_at=None,
                    )
                )
        building = valuation.get("estimated_building_price_twd")
        if building is not None:
            facts.append(
                EvidenceFact(
                    id="valuation.building",
                    label="房屋本體估值",
                    value=format_total_price_wan(building),
                    source="估值模型",
                )
            )
        parking = valuation.get("estimated_parking_price_twd")
        if parking is not None:
            facts.append(
                EvidenceFact(
                    id="valuation.parking",
                    label="車位估值",
                    value=format_total_price_wan(parking),
                    source="估值模型",
                )
            )
        total = valuation.get("estimated_total_price_twd")
        if total is not None:
            facts.append(
                EvidenceFact(
                    id="valuation.total",
                    label="總估值",
                    value=format_total_price_wan(total),
                    source="估值模型",
                )
            )

        low = valuation.get("low_estimate_twd")
        high = valuation.get("high_estimate_twd")
        if low is not None and high is not None:
            facts.append(
                EvidenceFact(
                    id="valuation.interval",
                    label="合理區間",
                    value=(f"{format_total_price_wan(low)} 至 {format_total_price_wan(high)}"),
                    source="估值模型",
                )
            )
        return facts

    @staticmethod
    def _build_asking_gap_facts(valuation: dict, asking_price_twd: int) -> list[EvidenceFact]:
        facts: list[EvidenceFact] = []
        point = valuation.get("point_estimate_twd")
        low = valuation.get("low_estimate_twd")
        high = valuation.get("high_estimate_twd")

        if point is not None and point > 0:
            gap = asking_price_twd - point

            if gap > 0:
                amount_text = f"高於估值中心 {format_total_price_wan(gap)}"
                pct = (gap / point) * 100
                pct_text = f"高於估值中心 {pct:.1f}%"
            elif gap < 0:
                amount_text = f"低於估值中心 {format_total_price_wan(abs(gap))}"
                pct = (abs(gap) / point) * 100
                pct_text = f"低於估值中心 {pct:.1f}%"
            else:
                amount_text = "與估值中心一致"
                pct_text = "與估值中心一致"

            facts.append(
                EvidenceFact(
                    id="valuation.asking_gap_amount",
                    label="開價與估值差距",
                    value=amount_text,
                    source="估值模型",
                    observed_at=None,
                )
            )
            facts.append(
                EvidenceFact(
                    id="valuation.asking_gap_percent",
                    label="開價與估值差距百分比",
                    value=pct_text,
                    source="估值模型",
                    observed_at=None,
                )
            )

        if low is not None and high is not None and point is not None:
            if low <= asking_price_twd <= high:
                position_text = "仍在合理區間內"
            elif asking_price_twd > high:
                position_text = "高於合理區間上限"
            else:
                position_text = "低於合理區間下限"
        else:
            position_text = "—"

        facts.append(
            EvidenceFact(
                id="valuation.asking_position",
                label="開價在估值區間的位置",
                value=position_text,
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
                ("price_twd", f"comparable.{rank}.price", "總價", format_total_price_wan),
                (
                    "unit_price_per_ping_twd",
                    f"comparable.{rank}.unit_price",
                    "單價",
                    format_unit_price_wan,
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
