"""Public HTML pages served outside the admin and assistant blueprints."""

from __future__ import annotations

from flask import Blueprint, render_template, session

from qingpu_insight.web_routes.guards import guarded_blueprint


def create_pages_blueprint() -> Blueprint:
    bp = guarded_blueprint("pages", __name__)

    @bp.get("/")
    def index():
        return render_template("index.html", csrf_token=session.get("_csrf_token", ""))

    return bp
