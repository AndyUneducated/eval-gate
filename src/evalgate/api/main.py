"""FastAPI application factory + ASGI entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from evalgate import __version__
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


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("evalgate.api")
    log.info("api.startup", env=settings.env, version=__version__)
    yield
    log.info("api.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="EvalGate API",
        version=__version__,
        description="Eval-First LLMOps platform with a multi-axis PR CI gate.",
        lifespan=lifespan,
    )

    @app.exception_handler(EvalGateError)
    async def _handle_domain_error(_: Request, exc: EvalGateError) -> JSONResponse:
        """Map any raised domain error to its declared HTTP status in one place.

        Routers raise repository errors directly (no per-route ``try/except ...
        raise HTTPException``); the status code lives on the error class
        (``core.errors``), so the API and CLI can't drift."""
        return JSONResponse(status_code=exc.http_status, content={"detail": str(exc)})

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(traces.router, prefix="/v1", tags=["traces"])
    app.include_router(otlp.router, prefix="/v1", tags=["traces"])
    app.include_router(eval_sets.router, prefix="/v1", tags=["eval-sets"])
    app.include_router(evals.router, prefix="/v1", tags=["evals", "runs"])
    app.include_router(badcase.router, prefix="/v1", tags=["badcase"])
    app.include_router(adversarial.router, prefix="/v1", tags=["adversarial"])
    app.include_router(shadow.router, prefix="/v1", tags=["shadow"])
    app.include_router(dev.router, prefix="/v1", tags=["dev"])
    return app


app = create_app()


def run() -> None:
    """`evalgate-api` console-script entrypoint (uvicorn dev server)."""
    import uvicorn

    uvicorn.run("evalgate.api.main:app", host="0.0.0.0", port=8000, reload=False)
