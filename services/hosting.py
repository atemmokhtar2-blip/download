"""Single-process ASGI host for the Download dashboard and Factory adapter."""

from contextlib import asynccontextmanager

import gradio as gr
from fastapi import FastAPI

from services import factory_api


def create_hosting_app(*, dashboard: gr.Blocks, dashboard_auth, temp_dir: str) -> FastAPI:
    """Mount Factory routes regardless of whether the optional dashboard is locked.

    The Telegram bot and its clone pollers keep running in the existing bot
    thread. This ASGI host only serves the dashboard and authenticated API.
    """
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await factory_api.initialize_shared_schema()
        yield

    web_app = FastAPI(title="Download Hosting", lifespan=lifespan)
    # Register the Factory API route objects before Gradio mounts its root
    # path. This preserves exact paths and handler dependencies while avoiding
    # a nested FastAPI router/app being swallowed by the root mount.
    web_app.router.routes.extend(factory_api.router.routes)

    return gr.mount_gradio_app(
        web_app,
        dashboard,
        path="/",
        auth=dashboard_auth,
        show_error=False,
        # The locked dashboard contains no file browser or local file content.
        allowed_paths=[temp_dir] if dashboard_auth else [],
    )
