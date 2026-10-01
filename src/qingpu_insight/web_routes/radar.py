"""Public, read-only 低估物件雷達: ``GET /radar`` and ``GET /api/listing-radar``.

Both only read the latest persisted radar batch; starting a radar run is an admin job
(``POST /api/admin/listing-radar-runs``).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from qingpu_insight.api_errors import ApiInputError
from qingpu_insight.listing_radar import (
    RADAR_CAVEATS,
    RADAR_STATIONS,
    ListingRadarStore,
    public_candidate,
)
from qingpu_insight.web_routes.errors import parse_limit, read_market_data
from qingpu_insight.web_routes.guards import guarded_blueprint

_SORTS = {
    # The stored rank already puts clean bargains before ones that need a human look.
    "score": lambda item: (item.get("rank") or 10**9,),
    "gap": lambda item: (item.get("gap_pct") or 0, item.get("rank") or 0),
    "asking": lambda item: (item.get("asking_price_twd") or 0, item.get("rank") or 0),
}


_VERDICTS = ("clear_below", "below_estimate", "needs_check")
_MARKET_KEYS = ("market_heat", "asking_index", "asking_drift", "negotiation")


def _radar_query() -> tuple[tuple[str, ...], int, str]:
    stations = tuple(
        value.strip()
        for raw in request.args.getlist("station")
        for value in raw.split(",")
        if value.strip()
    ) or RADAR_STATIONS
    if any(station not in RADAR_STATIONS for station in stations):
        raise ApiInputError("站點僅支援 A17、A18、A19。", {"station": "A17_A18_A19"})
    limit = parse_limit(request.args.get("limit", "20"))
    if limit is None:
        raise ApiInputError("筆數格式不正確。", {"limit": "integer_1_to_100"})
    sort = request.args.get("sort", "score")
    if sort not in _SORTS:
        raise ApiInputError("排序方式不支援。", {"sort": "score_gap_or_asking"})
    return stations, limit, sort


def _verdict_query() -> str | None:
    verdict = request.args.get("verdict") or None
    if verdict is not None and verdict not in _VERDICTS:
        raise ApiInputError(
            "判定篩選不支援。", {"verdict": "clear_below_below_estimate_needs_check"}
        )
    return verdict


def create_radar_blueprint(store: ListingRadarStore | None) -> Blueprint:
    bp = guarded_blueprint("radar", __name__)

    @bp.get("/radar")
    def radar_page():
        return render_template("radar.html")

    @bp.get("/api/listing-radar")
    def listing_radar_api():
        stations, limit, sort = _radar_query()
        verdict = _verdict_query()
        meta, frame = (None, None) if store is None else read_market_data(store.load_latest)
        body: dict[str, object] = {
            "batch": None,
            "items": [],
            "limit": limit,
            "sort": sort,
            "stations": list(stations),
            "caveats": list(RADAR_CAVEATS),
            "market": None,
        }
        if meta is None or frame is None or frame.empty or "rank" not in frame:
            return jsonify(body)
        ranked = frame[frame["rank"].notna()]
        if "station_code" in ranked:
            ranked = ranked[ranked["station_code"].isin(stations)]
        items = [public_candidate(row) for row in ranked.to_dict("records")]
        if verdict is not None:
            items = [item for item in items if item.get("verdict") == verdict]
        items.sort(key=_SORTS[sort])
        body["batch"] = {
            "radar_batch_id": meta.get("radar_batch_id"),
            "status": meta.get("status"),
            "finished_at": meta.get("finished_at"),
            "model_versions": meta.get("model_versions", []),
            "dataset_versions": meta.get("dataset_versions", []),
            "counts": meta.get("counts", {}),
        }
        body["market"] = {key: meta.get(key) for key in _MARKET_KEYS}
        body["items"] = items[:limit]
        return jsonify(body)

    return bp
