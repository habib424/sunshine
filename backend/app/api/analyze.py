from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.analyzer import analyze_file, build_playbook_config
from app.db.session import get_db
from app.models.upload import UploadedFile

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


@router.post("/{upload_id}")
async def analyze_upload(upload_id: str, db: AsyncSession = Depends(get_db)):
    upload = await db.get(UploadedFile, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    file_path = Path(upload.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    try:
        analysis = analyze_file(file_path)
        playbook_config = build_playbook_config(analysis)
        return {
            "upload_id": upload_id,
            "filename": upload.original_name,
            "analysis": analysis,
            "playbook_config": playbook_config,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
