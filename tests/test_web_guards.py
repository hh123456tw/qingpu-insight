"""Every local-only route is guarded by the shared trusted-local + CSRF policy.

The routes are enumerated from ``app.url_map`` so a new admin, ops, job, report or
conversation route cannot be added without a guard.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pandas as pd
import pytest
from flask import Flask

from qingpu_insight.valuation import ModelRegistry
from qingpu_insight.valuation_store import FileValuationStore
from qingpu_insight.web import create_app
from qingpu_insight.web_routes import guards
from qingpu_insight.web_routes.guards import route_policy

LOCAL_PREFIXES = (
    "/api/admin",
    "/api/ops",
    "/api/conversations",
    "/api/conversation-models",
    "/api/jobs",
    "/api/reports",
    "/admin",
    "/assistant",
)
PUBLIC_PATHS = {
    "/",
    "/api/market/summary",
    "/api/market/trends",
    "/api/market/map-points",
    "/api/transactions",
    "/api/listings",
    "/api/listings/summary",
    "/api/listing-events",
    "/api/listing-radar",
    "/radar",
    "/api/valuations",
    "/api/valuations/<valuation_id>",
}
# Retired endpoint that answers 404 to every method; it performs nothing to guard.
TOMBSTONES = {"/api/ops/restore"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class _EmptyMarket:
    def load(self, filters):
        del filters
        return pd.DataFrame()


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return create_app(
        data_source=_EmptyMarket(),
        valuation_store=FileValuationStore(tmp_path / "valuations"),
        model_registry=ModelRegistry(tmp_path / "artifacts"),
    )


def _local_rules(app: Flask):
    return [
        rule
        for rule in app.url_map.iter_rules()
        if rule.endpoint != "static"
        and rule.rule not in TOMBSTONES
        and rule.rule.startswith(LOCAL_PREFIXES)
    ]


def _url(rule) -> str:
    return rule.rule.replace("<", "{").replace(">", "}").format(
        **{name: str(uuid.uuid4()) for name in rule.arguments}
    )


def _requests(app: Flask, methods: set[str]):
    for rule in _local_rules(app):
        for method in sorted((rule.methods or set()) & methods):
            yield rule, method, _url(rule)


def test_route_inventory_is_classified(app: Flask) -> None:
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or rule.rule in TOMBSTONES:
            continue
        assert rule.rule.startswith(LOCAL_PREFIXES) or rule.rule in PUBLIC_PATHS, (
            f"{rule.rule} is neither local-only nor listed as public"
        )


def test_every_local_route_declares_a_guard_policy(app: Flask) -> None:
    rules = _local_rules(app)
    assert len(rules) > 30
    unguarded = [rule.rule for rule in rules if route_policy(app, rule.endpoint) is None]
    assert unguarded == []


def test_public_routes_have_no_guard(app: Flask) -> None:
    for rule in app.url_map.iter_rules():
        if rule.rule in PUBLIC_PATHS:
            assert route_policy(app, rule.endpoint) is None, rule.rule


def test_every_mutating_admin_ops_conversation_route_rejects_remote_clients(
    app: Flask,
) -> None:
    client = app.test_client()
    checked = 0
    for rule, method, url in _requests(app, MUTATING_METHODS):
        for kwargs in (
            {"environ_base": {"REMOTE_ADDR": "10.0.0.2"}},
            {"base_url": "http://attacker.example"},
        ):
            response = client.open(url, method=method, json={}, **kwargs)
            assert response.status_code == 403, (method, rule.rule, kwargs)
            assert response.get_json()["error"]["code"] == "forbidden", (method, rule.rule)
        checked += 1
    prefixes = {rule.rule.split("/")[2] for rule, _, _ in _requests(app, MUTATING_METHODS)}
    assert {"admin", "ops", "conversations", "reports"} <= prefixes
    assert checked >= 20


def test_every_mutating_local_route_requires_the_csrf_token(app: Flask) -> None:
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "session-token"
    for rule, method, url in _requests(app, MUTATING_METHODS):
        for headers in ({}, {"X-Qingpu-CSRF": "wrong-token"}, {"X-Qingpu-CSRF": "токен"}):
            response = client.open(url, method=method, json={}, headers=headers)
            assert response.status_code == 403, (method, rule.rule, headers)
            assert response.get_json()["error"] == {
                "code": "csrf_mismatch",
                "message": "CSRF 驗證失敗。",
            }


def test_every_local_read_route_rejects_remote_clients(app: Flask) -> None:
    client = app.test_client()
    for rule, method, url in _requests(app, {"GET"}):
        response = client.open(url, method=method, environ_base={"REMOTE_ADDR": "10.0.0.2"})
        assert response.status_code == 403, rule.rule
        assert response.get_json()["error"] == {
            "code": "forbidden",
            "message": "僅允許本機存取。",
        }


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/api/admin/listing-updates", "僅限本機。"),
        ("/api/admin/model-training-runs", "僅限本機。"),
        ("/api/reports", "僅限本機。"),
        ("/api/admin/backups", "僅允許本機存取。"),
        ("/api/conversations", "僅允許本機存取。"),
    ],
)
def test_forbidden_messages_are_unchanged(app: Flask, path: str, message: str) -> None:
    response = app.test_client().post(path, json={}, environ_base={"REMOTE_ADDR": "10.0.0.2"})
    assert response.status_code == 403
    assert response.get_json()["error"] == {"code": "forbidden", "message": message}


def test_csrf_comparison_is_constant_time(app: Flask, monkeypatch) -> None:
    calls = []
    real = guards.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(guards.hmac, "compare_digest", spy)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "session-token"
    response = client.post(
        "/api/admin/backups", json={}, headers={"X-Qingpu-CSRF": "session-token"}
    )
    # Session signing also uses compare_digest; the CSRF check is the plain-token call.
    assert (b"session-token", b"session-token") in calls
    # The token matched, so the request reached the view (no backup service here).
    assert response.status_code == 503


def test_tombstone_still_answers_not_found(app: Flask) -> None:
    client = app.test_client()
    for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        assert client.open("/api/ops/restore", method=method).status_code == 404
