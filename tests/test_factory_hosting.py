from __future__ import annotations

import gradio as gr

from services.hosting import create_hosting_app


def _demo():
    with gr.Blocks() as app:
        gr.Markdown("Dashboard")
    return app


def test_factory_routes_are_mounted_when_dashboard_password_is_missing():
    app = create_hosting_app(
        dashboard=_demo(),
        dashboard_auth=None,
        temp_dir="/tmp/download-test",
    )

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/health" in paths
    assert "/internal/factory/provision" in paths
    assert "/internal/factory/status/{request_id}" in paths


def test_factory_routes_remain_mounted_when_dashboard_auth_is_enabled():
    app = create_hosting_app(
        dashboard=_demo(),
        dashboard_auth=("admin", "strong-test-password"),
        temp_dir="/tmp/download-test",
    )

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/health" in paths
    assert "/internal/factory/provision" in paths
    assert "/internal/factory/status/{request_id}" in paths
