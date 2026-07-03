"""QuantumLedger FastAPI application."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .db import SessionLocal, bootstrap, engine

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "web" / "templates"
STATIC_DIR = APP_DIR / "web" / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    # Disable FastAPI's built-in Swagger UI / ReDoc so /docs and /redoc are free
    # for our own, human-friendly documentation site. (OpenAPI JSON stays at
    # /openapi.json for tooling.)
    app = FastAPI(title="QuantumLedger", version="0.1.0",
                  description="The vendor-neutral system of record for quantum.",
                  docs_url=None, redoc_url=None)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")

    from .api.v1 import auth_admin, compliance, growth, ingest, public, runs
    from .web import hardware as web_hardware
    from .web import reports as web_reports
    from .web import routes as web_routes
    from .web import seo as web_seo

    app.include_router(public.router)
    app.include_router(ingest.router)
    app.include_router(runs.router)
    app.include_router(compliance.router)
    app.include_router(auth_admin.router)
    app.include_router(growth.router)
    app.include_router(web_hardware.router)
    app.include_router(web_reports.router)
    app.include_router(web_seo.router)
    app.include_router(web_routes.router)

    # IndexNow key file (only when configured): GET /<key>.txt
    if settings.indexnow_key:
        from fastapi.responses import PlainTextResponse

        @app.get(f"/{settings.indexnow_key}.txt", include_in_schema=False)
        def indexnow_key_file() -> PlainTextResponse:
            return PlainTextResponse(settings.indexnow_key)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/api/v1/health", tags=["public"])
    def health():
        return {"status": "ok", "service": "quantumledger", "version": "0.1.0",
                "deployment": settings.deployment}

    @app.on_event("startup")
    def _startup():
        engine()
        s = SessionLocal()
        try:
            bootstrap(s)
        finally:
            s.close()

    return app


app = create_app()
