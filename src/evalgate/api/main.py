"""FastAPI application factory + ASGI entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from evalgate import __version__
from evalgate.api.routers import badcase, dev, eval_sets, evals, otlp, traces
from evalgate.core.config import get_settings
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

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(traces.router, prefix="/v1", tags=["traces"])
    app.include_router(otlp.router, prefix="/v1", tags=["traces"])
    app.include_router(eval_sets.router, prefix="/v1", tags=["eval-sets"])
    app.include_router(evals.router, prefix="/v1", tags=["evals", "runs"])
    app.include_router(badcase.router, prefix="/v1", tags=["badcase"])
    app.include_router(dev.router, prefix="/v1", tags=["dev"])
    return app


app = create_app()


def run() -> None:
    """`evalgate-api` console-script entrypoint (uvicorn dev server)."""
    import uvicorn

    uvicorn.run("evalgate.api.main:app", host="0.0.0.0", port=8000, reload=False)
