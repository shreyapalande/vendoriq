"""One JSON error shape for every error response, plus the request-id middleware.

    {"error": {"code": "not_found", "message": "Not Found", "request_id": "3f2a..."}}

Every response also carries the same id in the X-Request-ID header, so a user's
error report can be matched to the server log line.
"""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")

REQUEST_ID_HEADER = "X-Request-ID"

# Machine-readable codes for common statuses. Anything else becomes "http_<status>".
CODES_BY_STATUS = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
}


class ApiError(StarletteHTTPException):
    """Raise this in endpoints to pick a specific error code, e.g.
    ApiError(409, "invalid_state", "Assessment is not awaiting review")."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail=message)
        self.code = code


def request_id_of(request: Request) -> str:
    # Set by the middleware; the fallback only matters if a request somehow skipped it.
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex


def error_response(request: Request, status: int, code: str, message: str, headers=None) -> JSONResponse:
    request_id = request_id_of(request)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
        # Set the header here too: a 500 is built outside the middleware, which never sees it.
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """HTTPException from endpoints, plus Starlette's own 404 (no route) and 405 (wrong method)."""
    code = getattr(exc, "code", None) or CODES_BY_STATUS.get(exc.status_code, f"http_{exc.status_code}")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return error_response(request, exc.status_code, code, message, headers=exc.headers)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Body/query/path validation failed. Lists the failing fields; never echoes input values."""
    problems = [f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return error_response(request, 422, "validation_error", "; ".join(problems) or "Invalid request")


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Anything not handled above: log the traceback, return a generic 500 with no details."""
    logger.exception("unhandled error (request_id=%s) %s %s", request_id_of(request), request.method, request.url.path)
    return error_response(request, 500, "internal_error", "Internal server error")


def install_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        # Always generated here; an incoming X-Request-ID is ignored, so clients
        # cannot put arbitrary text into our logs.
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
