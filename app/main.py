import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.db import engine
from app.modules.admin_ai.router import router as admin_ai_router
from app.modules.admin_auth.router import router as admin_auth_router
from app.modules.admin_content import activity, agendas, committees, countries, imports, topics
from app.modules.admin_content import settings as admin_settings
from app.modules.admin_insights.router import router as admin_insights_router
from app.modules.admin_live.router import router as admin_live_router
from app.modules.admin_notify.router import router as admin_notify_router
from app.modules.admin_students.router import router as admin_students_router
from app.modules.admin_system.router import router as admin_system_router
from app.modules.auth.router import router as auth_router
from app.modules.catalog.router import router as catalog_router
from app.modules.chanakya.router import router as chanakya_router
from app.modules.debates.router import router as debates_router
from app.modules.health.router import router as health_router
from app.modules.jobs.router import router as jobs_router
from app.modules.legal.router import router as legal_router
from app.modules.live.router import router as live_router
from app.modules.me.router import router as me_router
from app.modules.mun.router import router as mun_router
from app.web.contract import check_contract

log = logging.getLogger("sym")

ADMIN_DIST = Path(__file__).parent / "static" / "admin"
BODY_LIMIT = 1024 * 1024  # 1 MB of JSON is far more than any screen sends
UPLOAD_LIMIT = 26 * 1024 * 1024  # a recording (25 MB cap in the transcribe route) plus form overhead
UPLOAD_PATHS = {"/v1/mun/transcribe", "/admin/api/imports"}

ADMIN_CSP = (
    "default-src 'self'; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class Guard:
    """Refuses oversized requests before reading them, and adds security headers to every response."""

    def __init__(self, app: ASGIApp, *, deployed: bool) -> None:
        self.app, self.deployed = app, deployed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        limit = UPLOAD_LIMIT if path in UPLOAD_PATHS else BODY_LIMIT
        declared = next((value for key, value in scope["headers"] if key == b"content-length"), None)
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            too_large = JSONResponse(
                {"detail": {"message": "That request is too large.", "code": "too_large"}}, status_code=413
            )
            await too_large(scope, receive, send)
            return
        admin = path.startswith("/admin")

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                if admin:
                    headers.setdefault("Content-Security-Policy", ADMIN_CSP)
                    headers.setdefault("X-Frame-Options", "DENY")
                    headers.setdefault("Cache-Control", "no-store")
                if self.deployed:
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            await send(message)

        await self.app(scope, receive, with_headers)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.contract_check_on_startup:
        async with engine.connect() as conn:
            drift = await check_contract(conn)
        if drift:
            message = "Web tables changed: " + "; ".join(str(item) for item in drift)
            if settings.is_deployed:
                # Fail the deploy; Render keeps the previous version running.
                raise RuntimeError(message)
            log.warning(message)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SYM Mobile Backend",
        version=settings.version,
        lifespan=lifespan,
        # API docs only on a developer's machine, never on staging or production.
        docs_url="/docs" if not settings.is_deployed else None,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.is_deployed else None,
    )

    app.add_middleware(Guard, deployed=settings.is_deployed)

    # Matched in order: the app's routes first (the live room is polled every second), then the admin console.
    app.include_router(live_router)
    app.include_router(me_router)
    app.include_router(catalog_router)
    app.include_router(mun_router)
    app.include_router(debates_router)
    app.include_router(chanakya_router)
    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(jobs_router)
    app.include_router(legal_router)
    app.include_router(admin_auth_router)
    app.include_router(admin_system_router)
    for module in (committees, agendas, countries, topics, admin_settings, imports, activity):
        app.include_router(module.router)
    app.include_router(admin_students_router)
    app.include_router(admin_insights_router)
    app.include_router(admin_live_router)
    app.include_router(admin_ai_router)
    app.include_router(admin_notify_router)

    if settings.cors_origins:
        # Only the Expo web preview needs this; the phone app isn't a browser.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Device", "If-None-Match"],
            expose_headers=["ETag"],
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> Response:
        """The app gets one readable sentence; the admin console keeps FastAPI's field list."""
        if not request.url.path.startswith("/v1"):
            return await request_validation_exception_handler(request, error)
        first = error.errors()[0] if error.errors() else {}
        message = str(first.get("msg", "Check what you entered.")).removeprefix("Value error, ")
        location = [str(part) for part in first.get("loc", []) if part != "body"]
        return JSONResponse(
            {
                "detail": {
                    "message": message,
                    "code": "invalid_input",
                    "field": location[-1] if location else None,
                }
            },
            status_code=422,
        )

    @app.api_route("/admin/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def admin_api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"detail": "Not found."}, status_code=404)

    @app.api_route("/admin", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/admin/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def admin_console(rest: str = "") -> Response:
        """The admin console is a single-page app: real files are served, every other path gets index.html."""
        if not ADMIN_DIST.is_dir():
            return JSONResponse({"detail": "Admin console is not built. Run `npm run build` in admin/."}, 503)
        candidate = (ADMIN_DIST / rest).resolve()
        if rest and candidate.is_file() and candidate.is_relative_to(ADMIN_DIST.resolve()):
            response: Response = FileResponse(candidate)
            if rest.startswith("assets/"):
                # Vite fingerprints asset names, so they can be cached forever.
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response
        return FileResponse(ADMIN_DIST / "index.html")

    return app


app = create_app()
