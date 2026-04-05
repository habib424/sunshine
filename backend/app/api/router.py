from fastapi import APIRouter

from app.api.analyze import router as analyze_router
from app.api.chat import router as chat_router
from app.api.jobs import router as jobs_router
from app.api.playbooks import router as playbooks_router
from app.api.preview import router as preview_router
from app.api.uploads import router as uploads_router

api_router = APIRouter()
api_router.include_router(uploads_router)
api_router.include_router(playbooks_router)
api_router.include_router(jobs_router)
api_router.include_router(analyze_router)
api_router.include_router(chat_router)
api_router.include_router(preview_router)
