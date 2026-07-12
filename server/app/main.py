"""Provenova FastAPI application."""

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
    app = FastAPI(title="Provenova", version="0.1.0",
                  description="The vendor-neutral system of record for quantum.",
                  docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        # Mark the cookie Secure whenever we serve over HTTPS (prod), so it can't
        # ride a plaintext downgrade. Stays off for local http:// dev.
        https_only=settings.base_url.lower().startswith("https"),
        # Stateless signed cookie — cap its lifetime (was Starlette's 14-day
        # default). Revocation before expiry is via Account.token_version.
        max_age=60 * 60 * 12,
    )

    # Canonical-host 301s: fold the legacy public host + www onto the canonical
    # host (from QL_BASE_URL). No-op until QL_BASE_URL is the new domain — the
    # host==canonical guard prevents any redirect loop before the cutover.
    from urllib.parse import urlparse

    from fastapi.responses import RedirectResponse

    _canonical_host = (urlparse(settings.base_url).hostname or "").lower()
    _legacy_hosts = {"quantumledger.ben.gy", f"www.{_canonical_host}"}

    @app.middleware("http")
    async def _canonical_host_redirect(request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if _canonical_host and host != _canonical_host and host in _legacy_hosts:
            target = request.url.replace(scheme="https", netloc=_canonical_host)
            return RedirectResponse(str(target), status_code=301)
        return await call_next(request)

    # Security response headers. Applied app-wide here (not just in the reverse
    # proxy) because production runs on Fly.io directly, bypassing Caddy.
    _serve_https = settings.base_url.lower().startswith("https")

    @app.middleware("http")
    async def _security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # Restrict framing by default, but never clobber a route that set its own
        # CSP — the embed card deliberately uses `frame-ancestors *`.
        if "content-security-policy" not in response.headers:
            response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        if _serve_https:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response

    # CSRF defense-in-depth: reject a cross-origin Origin on cookie-authenticated
    # state-changing requests. Complements SameSite=Lax (which already blocks
    # cross-site cookie POSTs) and is template-free. A missing Origin is allowed
    # (older browsers / non-browser clients rely on the SameSite cookie); Bearer/
    # API requests carry no session cookie and are exempt.
    from starlette.responses import JSONResponse

    _unsafe_methods = {"POST", "PUT", "PATCH", "DELETE"}

    @app.middleware("http")
    async def _csrf_origin_check(request, call_next):
        if request.method in _unsafe_methods and request.cookies.get("session"):
            origin = request.headers.get("origin")
            if origin:
                o_host = (urlparse(origin).hostname or "").lower()
                req_host = (request.headers.get("host") or "").split(":")[0].lower()
                if o_host and req_host and o_host != req_host:
                    return JSONResponse({"detail": "cross-origin request blocked"},
                                        status_code=403)
        return await call_next(request)

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

    # Browser-facing 404s get the branded page; API paths and non-HTML clients
    # keep the JSON error body.
    from fastapi.exception_handlers import http_exception_handler
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request, exc):
        wants_html = "text/html" in (request.headers.get("accept") or "")
        if exc.status_code == 404 and wants_html and not request.url.path.startswith("/api/"):
            from .web.routes import templates

            return templates.TemplateResponse(
                request, "404.html",
                {"principal": None, "settings": settings, "detail": exc.detail if exc.detail != "Not Found" else None},
                status_code=404)
        return await http_exception_handler(request, exc)

    @app.get("/api/v1/health", tags=["public"])
    def health():
        return {"status": "ok", "service": "provenova", "version": "0.1.0",
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
