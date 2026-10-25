"""Application factory and public health routes; no scheduler is started here."""

import argparse
import logging
import sys
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
from app.api.auth import PrivateApiHeadersMiddleware, protected_router, public_router, require_admin
from app.config import Settings
from app.contracts.dto import Error
from app.contracts.errors import AppError
from app.contracts.interfaces import AdapterFactory, Clock, SystemClock
from app.contracts.locks import AdvisoryLocks, LockBusy, LockLost
from app.contracts.worker import WorkerContext, WorkerRuntime
from app.db import Database, DatabaseNotReady
from app.registry.admin import AdminStore
from app.registry.authentication import AdministratorAuth

logger = logging.getLogger(__name__)


def _close_resources(db: Database | None, adapters: AdapterFactory | None) -> bool:
    failed = False
    for resource in (adapters, db):
        if resource is not None:
            try:
                resource.close()
            except Exception:
                logger.error("A process resource failed to close")
                failed = True
    return failed


def error_response(
    request_id: str,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=Error.model_validate(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "details": details or [],
                    "request_id": request_id,
                }
            }
        ).model_dump(mode="json"),
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
    *,
    clock: Clock | None = None,
    adapters: AdapterFactory | None = None,
    auth: AdministratorAuth | None = None,
) -> FastAPI:
    configuration = settings if settings is not None else Settings()
    db = database if database is not None else Database(configuration)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if _close_resources(db, adapters):
                raise RuntimeError("Application resources could not be closed") from None

    app = FastAPI(title="Syncthing Control Center", version=__version__, lifespan=lifespan)
    app.state.settings = configuration
    app.state.database = db
    app.state.clock = clock if clock is not None else SystemClock()
    app.state.adapters = adapters
    app.state.auth = (
        auth
        if auth is not None
        else AdministratorAuth(configuration, AdminStore(db), app.state.clock)
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(PrivateApiHeadersMiddleware)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(request.state.request_id, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Only fixed location categories are emitted, never raw input or validator messages.
        errors = exc.errors()
        locations = {
            str(error["loc"][0])
            for error in errors
            if error.get("loc") and error["loc"][0] in {"body", "path", "query", "header", "cookie"}
        }
        details = [{"field": loc, "message": "Invalid request value"} for loc in sorted(locations)]
        business_errors = {
            "invalid_timezone": "Unknown IANA timezone",
            "invalid_window": "Window boundaries must differ",
            "invalid_period": "Invalid analytics period",
        }
        for code, message in business_errors.items():
            if any(error["type"] == code for error in errors):
                return error_response(request.state.request_id, 422, code, message, details)
        return error_response(
            request.state.request_id, 422, "validation_error", "Request validation failed", details
        )

    @app.exception_handler(LockBusy)
    async def handle_lock_busy(request: Request, exc: LockBusy) -> JSONResponse:
        return error_response(
            request.state.request_id,
            409,
            "operation_in_progress",
            "Operation is already in progress",
        )

    @app.exception_handler(LockLost)
    async def handle_lock_lost(request: Request, exc: LockLost) -> JSONResponse:
        return error_response(
            request.state.request_id, 503, "service_unavailable", "Operation lock is unavailable"
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
    protected.include_router(protected_router)
    for router in routers:
        protected.include_router(router)
    app.include_router(protected)
    app.include_router(public_router)
    return app


def run_worker(
    runtime: WorkerRuntime | None,
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    clock: Clock | None = None,
    adapters: AdapterFactory | None = None,
) -> int:
    """Composition hook for A4. No B2 scheduler is imported or started implicitly.

    Passed database/factory resources transfer ownership to this call and are closed
    on every exit. The B2 loop must check context.ensure_active() before each cycle.
    """
    db = database
    result = 2
    try:
        if runtime is None or adapters is None:
            print("Worker runtime and adapter factory must be configured", file=sys.stderr)
        else:
            configuration = settings if settings is not None else Settings()
            if configuration.db_pool_size < 2:
                print("Worker requires a database pool size of at least 2", file=sys.stderr)
            else:
                db = db if db is not None else Database(configuration)
                db.check_ready()
                if db.engine is None:
                    raise DatabaseNotReady("database_not_configured")
                locks = AdvisoryLocks(db.engine)
                with locks.worker() as singleton:
                    context = WorkerContext(
                        configuration,
                        db,
                        clock if clock is not None else SystemClock(),
                        adapters,
                        locks,
                        singleton,
                    )
                    context.ensure_active()
                    runtime.run(context)
                result = 0
    except LockBusy:
        print("A worker is already running", file=sys.stderr)
        result = 3
    except (DatabaseNotReady, LockLost, SQLAlchemyError):
        print("Worker database or lock is unavailable", file=sys.stderr)
        result = 4
    except KeyboardInterrupt:
        result = 130
    except Exception:
        # Worker exceptions may contain URL, keys or raw responses. Do not echo them.
        print("Worker stopped after an unexpected failure", file=sys.stderr)
        result = 1
    finally:
        if _close_resources(db, adapters):
            result = 1
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Syncthing Control Center process entry points")
    parser.add_argument("mode", choices=["worker"])
    parser.parse_args(argv)
    # A4 supplies B2's runtime and A1's factory here after those modules exist.
    return run_worker(None)


if __name__ == "__main__":
    raise SystemExit(main())
