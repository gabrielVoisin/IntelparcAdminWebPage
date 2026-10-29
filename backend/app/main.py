"""Application entry point: `uvicorn app.main:app`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.v1 import api_router
from app.config import get_settings
from app.services.realtime import broadcaster
from app.web.routes import router as web_router

log = logging.getLogger("parking")


async def _alarm_loop(interval: int) -> None:
    from app.db import SessionLocal
    from app.services.alarm_engine import scan

    def run_once() -> int:
        with SessionLocal() as db:
            created = scan(db)
            db.commit()
            return created

    while True:
        try:
            created = await asyncio.to_thread(run_once)
            if created:
                log.info("alarm engine: %s new payment alarm(s)", created)
        except Exception:  # keep the loop alive, the error is logged
            log.exception("alarm engine failed")
        await asyncio.sleep(interval)


async def _watchdog_loop() -> None:
    """Communication watchdog of the cabinets (0.5 Hz): alarm when a cabinet stops signalling."""
    from app.db import SessionLocal
    from app.services import watchdog

    def run_once() -> int:
        with SessionLocal() as db:
            raised = watchdog.scan(db)
            db.commit()
            return raised

    while True:
        try:
            if await asyncio.to_thread(run_once):
                log.warning("watchdog: communication with a cabinet lost")
        except Exception:  # keep the loop alive, the error is logged
            log.exception("watchdog failed")
        await asyncio.sleep(watchdog.PERIOD_S)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    broadcaster.bind_loop(asyncio.get_running_loop())
    tasks = [asyncio.create_task(_alarm_loop(get_settings().alarm_scan_interval_s)), asyncio.create_task(_watchdog_loop())]
    yield
    for task in tasks:
        task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Parking management API",
        version=__version__,
        description=(
            "REST API shared by the web back office, the Android app (Bearer JWT) and the "
            "Raspberry Pi control cabinets (X-Controller-Key). OpenAPI spec: /openapi.json"
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True,
            allow_methods=["*"], allow_headers=["*"],
        )
    app.include_router(api_router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
