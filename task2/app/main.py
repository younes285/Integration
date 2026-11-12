from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from urllib.parse import urlsplit

from app.config import Settings
from app.database import Base, make_database
from app.routes import admin, auth, users

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine, factory = make_database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="Google Identity + RBAC", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.app_origin).hostname])

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://accounts.google.com/gsi/client; "
            "style-src 'self' https://accounts.google.com/gsi/style; "
            "frame-src https://accounts.google.com; connect-src 'self' https://accounts.google.com; "
            "img-src 'self' https:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/config")
    def config():
        return {"google_client_id": settings.google_client_id}

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(admin.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
