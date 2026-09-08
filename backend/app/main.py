import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from app.api.router import api_router
from app.config import PROJECT_ROOT, settings
from app.db.session import init_db

# Import to register transforms, validators, file transforms, and rules
import app.engine.transforms  # noqa: F401
import app.engine.validators  # noqa: F401
import app.engine.file_transforms.tb_opening_balance  # noqa: F401
import app.engine.open_ap  # noqa: F401
import app.engine.bills_ap  # noqa: F401
import app.engine.invoices_ar  # noqa: F401
import app.engine.rules.source_mapping  # noqa: F401
import app.engine.rules.unpivot_entities  # noqa: F401
import app.engine.rules.currency_lookup  # noqa: F401
import app.engine.rules.debit_credit_split  # noqa: F401
import app.engine.rules.filter_rows  # noqa: F401
import app.engine.rules.set_constant  # noqa: F401
import app.engine.rules.generate_id  # noqa: F401
import app.engine.rules.map_columns  # noqa: F401
import app.engine.rules.aggregate  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.uploads_path.mkdir(parents=True, exist_ok=True)
    settings.outputs_path.mkdir(parents=True, exist_ok=True)
    settings.temp_path.mkdir(parents=True, exist_ok=True)
    await init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    description="ERP Financial Data Migration Tool",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Paths that must stay reachable without a session: health checks, and the
# sign-in endpoints themselves.
_AUTH_EXEMPT_PREFIXES = ("/api/health", "/api/auth/")


class AuthRequiredMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated /api requests when Google sign-in is configured."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if (
            settings.auth_required
            and request.method != "OPTIONS"
            and path.startswith("/api")
            and not path.startswith(_AUTH_EXEMPT_PREFIXES)
            and not request.session.get("user")
        ):
            return JSONResponse(status_code=401, content={"detail": "Not signed in"})
        return await call_next(request)


# Middleware added later runs earlier, so SessionMiddleware (added last)
# parses the cookie before AuthRequiredMiddleware reads the session.
app.add_middleware(AuthRequiredMiddleware)
app.add_middleware(
    SessionMiddleware,
    # Without a configured key, sessions reset on restart — fine for dev.
    secret_key=settings.auth_secret_key or secrets.token_hex(32),
    same_site="lax",
    https_only=settings.session_https_only,
    max_age=14 * 24 * 3600,
)

app.include_router(api_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.app_name}


# In production the built frontend (frontend/dist) is served by this app, so
# one Render service handles everything on one origin. In dev the directory
# usually doesn't exist and Vite serves the frontend with its /api proxy.
_frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        candidate = (_frontend_dist / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(_frontend_dist.resolve())
        ):
            return FileResponse(candidate)
        return FileResponse(_frontend_dist / "index.html")
