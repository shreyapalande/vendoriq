"""The FastAPI application. Run with: uvicorn app.main:app --reload (from backend/)."""

import logging

from fastapi import FastAPI

from app.api import auth, health, vendors
from app.config import get_settings
from app.errors import install_error_handling


def create_app() -> FastAPI:
    """Build a fresh app. Tests call this to get an app they can add routes to."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = FastAPI(title="VendorIQ API", version="0.1.0")
    install_error_handling(app)
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
    app.include_router(vendors.router, prefix="/api/v1", tags=["vendors"])
    return app


app = create_app()
