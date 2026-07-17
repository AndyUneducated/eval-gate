"""FastAPI application factory + ASGI entrypoint."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from evalgate import __version__
from evalgate.api.deps import AuthDep, SessionDep
from evalgate.api.routers import (
    adversarial,
    badcase,
    dev,
    eval_sets,
    evals,
    otlp,
    shadow,
    traces,
)
from evalgate.core.config import get_settings
from evalgate.core.errors import EvalGateError
from evalgate.core.logging import configure_logging, get_logger
from evalgate.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("evalgate.api")
    log.info("api.startup", env=settings.env, version=__version__)
    yield
    # Release pooled DB connections on shutdown so redeploys don't leak them.
    await engine.dispose()
    log.info("api.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="EvalGate API",
        version=__version__,
        description="Eval-First LLMOps platform with a multi-axis PR CI gate.",
        lifespan=lifespan,
    )

    if settings.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        """Attach a request id to every response + bind it to structured logs.

        Honours an inbound ``X-Request-ID`` (trace propagation) or mints one.
        """
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-ID"] = request_id
        return response

    @app.middleware("http")
    async def _limit_body_size(request: Request, call_next):
        """Reject oversized bodies up front (memory-DoS guard on ingest)."""
        max_bytes = get_settings().max_request_bytes
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": f"request body exceeds {max_bytes} bytes"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "invalid Content-Length header"},
                )
        return await call_next(request)

    @app.exception_handler(EvalGateError)
    async def _handle_domain_error(_: Request, exc: EvalGateError) -> JSONResponse:
        """Map any raised domain error to its declared HTTP status in one place.

        Routers raise repository errors directly (no per-route ``try/except ...
        raise HTTPException``); the status code lives on the error class
        (``core.errors``), so the API and CLI can't drift."""
        return JSONResponse(status_code=exc.http_status, content={"detail": str(exc)})

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        """Liveness: process is up. Cheap, never touches the DB."""
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", tags=["meta"])
    async def readyz(session: SessionDep) -> JSONResponse:
        """Readiness: the DB is reachable. Load balancers should gate on this.

        Uses the injected session so it shares the app's engine (and the test
        override) rather than a second, divergent connection path.
        """
        try:
            await session.execute(text("SELECT 1"))
        except Exception as exc:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable", "detail": str(exc)},
            )
        return JSONResponse(content={"status": "ready", "version": __version__})

    # Every data route requires the API key (a no-op until one is configured).
    data = [AuthDep]
    app.include_router(traces.router, prefix="/v1", tags=["traces"], dependencies=data)
    app.include_router(otlp.router, prefix="/v1", tags=["traces"], dependencies=data)
    app.include_router(eval_sets.router, prefix="/v1", tags=["eval-sets"], dependencies=data)
    app.include_router(evals.router, prefix="/v1", tags=["evals", "runs"], dependencies=data)
    app.include_router(badcase.router, prefix="/v1", tags=["badcase"], dependencies=data)
    app.include_router(adversarial.router, prefix="/v1", tags=["adversarial"], dependencies=data)
    app.include_router(shadow.router, prefix="/v1", tags=["shadow"], dependencies=data)
    # Dev/seed routes never ship in a real deployment.
    if settings.dev_routes_enabled():
        app.include_router(dev.router, prefix="/v1", tags=["dev"], dependencies=data)
    return app


app = create_app()


def run() -> None:
    """`evalgate-api` console-script entrypoint (uvicorn dev server)."""
    import uvicorn

    uvicorn.run("evalgate.api.main:app", host="0.0.0.0", port=8000, reload=False)
