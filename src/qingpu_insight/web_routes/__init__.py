"""Flask blueprints for the web app, grouped by domain.

`qingpu_insight.web.create_app` composes the services and registers these blueprints.
Route guards (trusted local + CSRF) live in :mod:`qingpu_insight.web_routes.guards`.
"""
