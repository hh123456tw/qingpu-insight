"""Group the same flat listed by several agents into one property.

591 shows one row per agent listing, and a popular flat in 青埔 is often listed by
twenty agents at once, each spelling the community differently (鼎藏文星 / 鼎藏麗星 /
鼎藏). Community names are therefore not used to link listings; the physical
attributes are: station, floor and building height, then area, asking price and age
within tight tolerances. Two flats on the same floor of the same building with the
same area and price would merge, which is rare and only understates supply.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

AREA_TOLERANCE_PING = 0.3
AREA_TOLERANCE_RATIO = 0.01
PRICE_TOLERANCE_RATIO = 0.05
AGE_TOLERANCE_YEARS = 2.0


@dataclass(frozen=True)
class PropertyGroup:
    """One physical property: a representative listing and every listing of it."""

    property_key: str
    representative_id: str
    listing_ids: tuple[str, ...]
    min_price_twd: float | None
    max_price_twd: float | None

    @property
    def duplicate_count(self) -> int:
        return len(self.listing_ids) - 1


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _listing_id(listing: Mapping[str, Any]) -> str:
    return str(listing.get("source_listing_id") or listing.get("id") or "")


def _block(listing: Mapping[str, Any]) -> tuple[str, str, str] | None:
    station = str(listing.get("station_code") or "")
    floor = str(listing.get("floor_text") or listing.get("floor") or "").replace(" ", "")
    total = listing.get("total_floors")
    if not station or not floor:
        return None
    return station, floor, str(total if total is not None else "")


def same_property(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """True when two listings in the same block describe the same flat."""
    area_a, area_b = _number(a.get("area_ping")), _number(b.get("area_ping"))
    price_a, price_b = _number(a.get("asking_price_twd")), _number(b.get("asking_price_twd"))
    if area_a is None or area_b is None or price_a is None or price_b is None:
        return False
    area_tolerance = max(AREA_TOLERANCE_PING, AREA_TOLERANCE_RATIO * max(area_a, area_b))
    if abs(area_a - area_b) > area_tolerance:
        return False
    if abs(price_a - price_b) > PRICE_TOLERANCE_RATIO * min(price_a, price_b):
        return False
    age_a, age_b = _number(a.get("building_age_years")), _number(b.get("building_age_years"))
    return age_a is None or age_b is None or abs(age_a - age_b) <= AGE_TOLERANCE_YEARS


def _sort_id(listing_id: str) -> tuple[int, int | str]:
    return (0, int(listing_id)) if listing_id.isdigit() else (1, listing_id)


def group_properties(listings: Iterable[Mapping[str, Any]]) -> dict[str, PropertyGroup]:
    """Map every listing id to its property group (singletons included).

    The representative is the cheapest listing, so a buyer sees the best asking price;
    the property key is the smallest listing id, so it is stable while that listing lives.
    """
    by_id: dict[str, Mapping[str, Any]] = {}
    for listing in listings:
        listing_id = _listing_id(listing)
        if listing_id and listing_id not in by_id:
            by_id[listing_id] = listing

    parent = {listing_id: listing_id for listing_id in by_id}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    blocks: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for listing_id, listing in by_id.items():
        block = _block(listing)
        if block is not None:
            blocks[block].append(listing_id)
    for members in blocks.values():
        members.sort(key=lambda i: _number(by_id[i].get("area_ping")) or 0.0)
        for index, first in enumerate(members):
            area_first = _number(by_id[first].get("area_ping")) or 0.0
            for second in members[index + 1 :]:
                area_second = _number(by_id[second].get("area_ping")) or 0.0
                if area_second - area_first > max(
                    AREA_TOLERANCE_PING, AREA_TOLERANCE_RATIO * area_second
                ):
                    break
                if same_property(by_id[first], by_id[second]):
                    parent[find(second)] = find(first)

    clusters: dict[str, list[str]] = defaultdict(list)
    for listing_id in by_id:
        clusters[find(listing_id)].append(listing_id)

    groups: dict[str, PropertyGroup] = {}
    for members in clusters.values():
        members.sort(key=_sort_id)
        prices = [p for p in (_number(by_id[m].get("asking_price_twd")) for m in members) if p]
        representative = min(
            members,
            key=lambda m: (_number(by_id[m].get("asking_price_twd")) or math.inf, _sort_id(m)),
        )
        group = PropertyGroup(
            property_key=f"p{members[0]}",
            representative_id=representative,
            listing_ids=tuple(members),
            min_price_twd=min(prices) if prices else None,
            max_price_twd=max(prices) if prices else None,
        )
        for member in members:
            groups[member] = group
    return groups


def unique_property_count(groups: Mapping[str, PropertyGroup]) -> int:
    return len({group.property_key for group in groups.values()})
