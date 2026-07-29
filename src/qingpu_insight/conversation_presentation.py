from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

_FACT_LABELS = {
    "listing.price": "591 開價",
    "listing.unit_price": "591 開價單價",
    "listing.area": "建物面積",
    "valuation.point": "模型估值",
    "valuation.interval": "模型估價區間",
    "valuation.asking_gap_amount": "開價與模型估值差額",
    "valuation.asking_gap_percent": "開價與模型估值差距",
    "valuation.asking_position": "開價位置",
    "valuation.confidence": "模型信心",
    "market.sample_size": "相似成交案例數",
    "market.median_unit_price": "附近成交單價中位數",
}


def _field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _facts(pack: Any) -> list[Mapping[str, Any]]:
    raw = _field(pack, "facts", [])
    if not isinstance(raw, list):
        return []
    return [fact for fact in raw if isinstance(fact, Mapping)]


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _price_fact_twd(pack: Any, fact_id: str) -> float | None:
    fact = next((item for item in _facts(pack) if item.get("id") == fact_id), None)
    if fact is None:
        return None
    value = str(fact.get("value", "")).strip().replace(",", "")
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*萬", value)
    if match:
        return _positive_number(float(match.group(1)) * 10_000)
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*元", value)
    if match:
        return _positive_number(match.group(1))
    return _positive_number(value)


def _rounded_twd(value: float) -> int:
    return int(round(value))


def project_price_summary(pack: Any) -> dict[str, Any] | None:
    valuation = _field(pack, "valuation")
    if not isinstance(valuation, Mapping):
        return None

    asking = _positive_number(valuation.get("asking_price_twd"))
    if asking is None:
        asking = _price_fact_twd(pack, "listing.price")
    low = _positive_number(valuation.get("low_estimate_twd"))
    point = _positive_number(valuation.get("point_estimate_twd"))
    high = _positive_number(valuation.get("high_estimate_twd"))
    if None in {asking, low, point, high}:
        return None

    assert asking is not None
    assert low is not None
    assert point is not None
    assert high is not None
    summary: dict[str, Any] = {
        "asking_twd": _rounded_twd(asking),
        "low_twd": _rounded_twd(low),
        "point_twd": _rounded_twd(point),
        "high_twd": _rounded_twd(high),
        "position": "inside",
        "gap_twd": None,
        "gap_percent": None,
        "confidence": valuation.get("confidence"),
        "confidence_reason": None,
    }

    reasons = valuation.get("confidence_reasons")
    if isinstance(reasons, list):
        summary["confidence_reason"] = next(
            (str(reason).strip() for reason in reasons if str(reason).strip()),
            None,
        )

    if not low <= point <= high:
        summary["position"] = "inconsistent"
        return summary
    if asking < low:
        gap = low - asking
        summary.update(
            position="below",
            gap_twd=_rounded_twd(gap),
            gap_percent=round(gap / low * 100, 1),
        )
    elif asking > high:
        gap = asking - high
        summary.update(
            position="above",
            gap_twd=_rounded_twd(gap),
            gap_percent=round(gap / high * 100, 1),
        )
    return summary


def project_citation_details(
    pack: Any,
    citation_ids: Iterable[str],
) -> list[dict[str, str]]:
    facts_by_id = {
        str(fact.get("id")): fact
        for fact in _facts(pack)
        if fact.get("id") is not None
    }
    details: list[dict[str, str]] = []
    for citation_id in citation_ids:
        fact = facts_by_id.get(citation_id)
        if fact is None:
            details.append(
                {
                    "id": citation_id,
                    "label": _FACT_LABELS.get(citation_id, citation_id),
                    "value": "",
                    "source": "",
                }
            )
            continue
        label = _FACT_LABELS.get(citation_id) or str(fact.get("label") or citation_id)
        details.append(
            {
                "id": citation_id,
                "label": label,
                "value": str(fact.get("value") or ""),
                "source": str(fact.get("source") or ""),
            }
        )
    return details
