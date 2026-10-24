"""Application factory and public health routes; no scheduler is started here."""

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__
from app.api.auth import require_admin
from app.config import Settings
from app.contracts.errors import AppError
from app.db import Database, DatabaseNotReady

logger = logging.getLogger(__name__)


def error_response(
    request_id: str,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
                "request_id": request_id,
            }
        },
    )


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                message = {
                    **message,
                    "headers": [*headers, (b"x-request-id", request_id.encode("ascii"))],
                }
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            # Exception strings/tracebacks can contain upstream secrets or submitted values.
            logger.error("Unhandled request failure; request_id=%s", request_id)
            if started:
                raise RuntimeError(f"Response failed; request_id={request_id}") from None
            response = error_response(request_id, 500, "internal_error", "Internal server error")
            await response(scope, receive, send_with_id)


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    configuration = settings if settings is not None else Settings()
    db = database if database is not None else Database(configuration)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            db.close()

    app = FastAPI(title="Syncthing Control Center", version=__version__, lifespan=lifespan)
    app.state.settings = configuration
    app.state.database = db
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(request.state.request_id, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Only fixed location categories are emitted, never raw input or validator messages.
        locations = {
            str(error["loc"][0])
            for error in exc.errors()
            if error.get("loc") and error["loc"][0] in {"body", "path", "query", "header", "cookie"}
        }
        details = [{"field": loc, "message": "Invalid request value"} for loc in sorted(locations)]
        return error_response(
            request.state.request_id, 422, "validation_error", "Request validation failed", details
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "request_error")
        response = error_response(
            request.state.request_id, exc.status_code, code, "Request could not be processed"
        )
        if exc.status_code == 405 and exc.headers and "Allow" in exc.headers:
            response.headers["Allow"] = exc.headers["Allow"]
        return response

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return error_response(
            request.state.request_id, 503, "service_unavailable", "Database is unavailable"
        )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        try:
            db.check_ready()
        except DatabaseNotReady as exc:
            messages = {
                "database_not_configured": "Database is not configured",
                "database_unavailable": "Database is unavailable",
                "schema_not_ready": "Database migrations are not ready",
            }
            raise AppError(503, "service_unavailable", messages[exc.reason]) from None
        return {"status": "ok"}

    protected = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
    for router in routers:
        protected.include_router(router)
    app.include_router(protected)
    return app
