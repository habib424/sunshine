from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import settings
from app.db.session import init_db

# Import to register transforms, validators, file transforms, and rules
import app.engine.transforms  # noqa: F401
import app.engine.validators  # noqa: F401
import app.engine.file_transforms.tb_opening_balance  # noqa: F401
import app.engine.open_ap  # noqa: F401
import app.engine.bills_ap  # noqa: F401
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

app.include_router(api_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.app_name}
