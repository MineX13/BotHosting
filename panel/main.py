"""
MineNodes Panel — FastAPI Entry Point

Runs the web panel alongside the Discord bot.
Shares the same database pool, encryption, and services.

Usage:
    python -m panel.main
"""

from __future__ import annotations

import asyncio
import platform
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config.settings import get_settings
from app.database.connection import create_pool, close_pool
from app.security.encryption import init_encryption
from app.services.process_service import ProcessService
from app.services.deployment_service import DeploymentService
from app.services.monitoring_service import MonitoringService
from app.utils.logging import setup_logging, get_logger

# Route imports
from panel.routes import bots, files, env, admin, account
from panel.auth import router as auth_router

logger = get_logger("panel")

# Shared service instances (populated at startup)
process_service: ProcessService | None = None
deployment_service: DeploymentService | None = None
monitoring_service: MonitoringService | None = None


def get_process_service() -> ProcessService:
    assert process_service is not None
    return process_service


def get_deployment_service() -> DeploymentService:
    assert deployment_service is not None
    return deployment_service


def get_monitoring_service() -> MonitoringService:
    assert monitoring_service is not None
    return monitoring_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    global process_service, deployment_service, monitoring_service

    settings = get_settings()

    # Init encryption
    init_encryption(settings.encryption_key)

    # Init database
    await create_pool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    logger.info("Database pool created")

    # Init services
    process_service = ProcessService()
    deployment_service = DeploymentService(process_service)
    monitoring_service = MonitoringService(process_service)

    # Recover existing processes
    recovered = await process_service.recover_processes()
    logger.info(f"Recovered {recovered} bot processes")

    # Start monitoring
    await monitoring_service.start()
    logger.info("Monitoring service started")

    yield

    # Shutdown
    await monitoring_service.stop()
    await close_pool()
    logger.info("Panel shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="MineNodes Panel",
        description="Pterodactyl-style bot hosting panel",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS for development (React dev server on :5173)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:8080", "*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    app.include_router(account.router, prefix="/api", tags=["account"])
    app.include_router(bots.router, prefix="/api", tags=["bots"])
    app.include_router(files.router, prefix="/api", tags=["files"])
    app.include_router(env.router, prefix="/api", tags=["env"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])

    # Serve React build in production
    frontend_dist = Path(__file__).parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """Serve the React SPA for all non-API routes."""
            file_path = frontend_dist / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(frontend_dist / "index.html"))

    return app


app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    setup_logging(level=settings.log_level, log_file="panel.log")
    logger.info("Starting MineNodes Panel", port=settings.panel_port)

    uvicorn.run(
        "panel.main:app",
        host="0.0.0.0",
        port=settings.panel_port,
        reload=False,
        log_level="info",
    )
