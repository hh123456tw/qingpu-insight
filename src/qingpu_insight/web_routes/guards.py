"""Trusted-local and CSRF guards shared by every blueprint.

A route opts in with ``@local_only()``; a blueprint created by :func:`guarded_blueprint`
may also give all of its routes a default policy. One ``before_request`` hook per
blueprint enforces the policy of the matched endpoint, so the checks run before any
view code (including for HEAD/OPTIONS) and exactly the same way everywhere:

1. the request must come from a loopback address to ``localhost``/``127.0.0.1``/``::1``,
   otherwise ``403 forbidden``;
2. unsafe methods (POST/PUT/PATCH/DELETE) must also carry the session CSRF token in the
   ``X-Qingpu-CSRF`` header (compared with :func:`hmac.compare_digest`), otherwise
   ``403 csrf_mismatch``.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, TypeVar
from urllib.parse import urlsplit

from flask import Blueprint, Flask, current_app, jsonify, request, session

LOCAL_ONLY_MESSAGE = "僅允許本機存取。"
# Historical wording used by the job, model-training and report mutations.
LOCAL_MUTATION_MESSAGE = "僅限本機。"
CSRF_HEADER = "X-Qingpu-CSRF"
CSRF_SESSION_KEY = "_csrf_token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_GUARD_ATTRIBUTE = "_qingpu_guard_policy"
_BLUEPRINT_DEFAULT_ATTRIBUTE = "qingpu_guard_default"

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class GuardPolicy:
    """Trusted local access, plus a CSRF token for unsafe methods."""

    forbidden_message: str = LOCAL_ONLY_MESSAGE
    mutation_forbidden_message: str = LOCAL_ONLY_MESSAGE

    def message_for(self, method: str) -> str:
        if method in SAFE_METHODS:
            return self.forbidden_message
        return self.mutation_forbidden_message


LOCAL_ONLY = GuardPolicy()
LOCAL_ONLY_LEGACY_MUTATION = GuardPolicy(mutation_forbidden_message=LOCAL_MUTATION_MESSAGE)


def is_trusted_local_request() -> bool:
    try:
        remote_is_loopback = ip_address(request.remote_addr or "").is_loopback
        hostname = urlsplit(f"//{request.host}").hostname
    except ValueError:
        return False
    return remote_is_loopback and (hostname or "").lower() in _LOCAL_HOSTNAMES


def csrf_token_matches() -> bool:
    supplied = str(request.headers.get(CSRF_HEADER, ""))
    expected = str(session.get(CSRF_SESSION_KEY, ""))
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def forbidden_response(message: str = LOCAL_ONLY_MESSAGE):
    return jsonify({"error": {"code": "forbidden", "message": message}}), 403


def csrf_mismatch_response():
    return jsonify({"error": {"code": "csrf_mismatch", "message": "CSRF 驗證失敗。"}}), 403


def check_policy(policy: GuardPolicy):
    """Return the 403 response for the current request, or None when it may proceed."""
    if not is_trusted_local_request():
        return forbidden_response(policy.message_for(request.method))
    if request.method not in SAFE_METHODS and not csrf_token_matches():
        return csrf_mismatch_response()
    return None


def local_only(policy: GuardPolicy = LOCAL_ONLY) -> Callable[[F], F]:
    """Mark a view as trusted-local (and CSRF-protected for unsafe methods)."""

    def decorate(view: F) -> F:
        setattr(view, _GUARD_ATTRIBUTE, policy)
        return view

    return decorate


def route_policy(app: Flask, endpoint: str) -> GuardPolicy | None:
    """The policy enforced for ``endpoint``: its own, else its blueprint's default."""
    view = app.view_functions.get(endpoint)
    policy = getattr(view, _GUARD_ATTRIBUTE, None)
    if policy is not None:
        return policy
    blueprint_name = endpoint.rpartition(".")[0]
    blueprint = app.blueprints.get(blueprint_name) if blueprint_name else None
    return getattr(blueprint, _BLUEPRINT_DEFAULT_ATTRIBUTE, None)


def _enforce_route_policy():
    if request.endpoint is None:
        return None
    policy = route_policy(current_app, request.endpoint)
    if policy is None:
        return None
    return check_policy(policy)


def guarded_blueprint(
    name: str,
    import_name: str,
    *,
    default_policy: GuardPolicy | None = None,
    **kwargs: Any,
) -> Blueprint:
    """A blueprint whose routes are checked by :func:`_enforce_route_policy`."""
    bp = Blueprint(name, import_name, **kwargs)
    setattr(bp, _BLUEPRINT_DEFAULT_ATTRIBUTE, default_policy)
    bp.before_request(_enforce_route_policy)
    return bp
