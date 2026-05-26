"""Forme Studio — FastAPI entry point.

Boots the app, initialises the DB, wires CORS, and mounts module routers.
Each module exposes a single ``router`` attribute which is included here —
the main file is the only place that knows the full module list.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.db import init_db
from app.modules.packaging import router as packaging_router
from app.routes.health import router as health_router
from app.routes.settings import router as settings_router


def _configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup + shutdown hooks for the app.

    Lifespan replaces the deprecated ``@app.on_event("startup")`` pattern in
    FastAPI ≥0.93. We use it to create tables and log the ready banner.
    """
    settings = get_settings()
    init_db()
    log = structlog.get_logger(__name__)
    log.info(
        "forme_studio_ready",
        version=__version__,
        port=settings.port,
        workspaces_dir=str(settings.workspaces_dir),
        db=str(settings.db_path),
    )
    yield
    # nothing to tear down yet — DB engine releases on process exit


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.log_level)

    # Make sure storage roots exist before the DB tries to open the SQLite file.
    settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="Forme Studio API",
        version=__version__,
        description=(
            "Backend for Forme Studio — AI-assisted packaging & print design. "
            "Single-tenant pilot. Module #1: packaging."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(settings_router)
    app.include_router(packaging_router)

    return app


app = create_app()
