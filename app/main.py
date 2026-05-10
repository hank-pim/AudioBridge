from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import create_api_router
from app.core.config_store import ConfigStore
from app.services.events import EventLog
from app.services.media import MediaController
from app.services.telemetry import TelemetryService


def create_app() -> FastAPI:
    app = FastAPI(title="Dante Bridge Endpoint", version="0.1.0")
    store = ConfigStore()
    telemetry = TelemetryService()
    events = EventLog()
    media = MediaController(telemetry=telemetry)
    events.append("info", "system", "endpoint service started")

    app.include_router(create_api_router(store, media, telemetry, events))

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
