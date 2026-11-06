"""GET /api/v1/health: is the API up, and does the database answer?"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.db import get_session

logger = logging.getLogger("app.health")
router = APIRouter()


@router.get("/health")
def health(session: Session = Depends(get_session)) -> JSONResponse:
    """200 if the database answers SELECT 1, otherwise 503.

    A status report rather than an error, so the 503 body is not the error shape.
    The database error is logged, never returned.
    """
    try:
        session.exec(text("SELECT 1")).one()
    except SQLAlchemyError:
        logger.exception("health check: database did not answer")
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "unavailable"})
    return JSONResponse(status_code=200, content={"status": "ok", "database": "ok"})
