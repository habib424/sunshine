"""Preview endpoint: runs a rule pipeline on an uploaded file and returns first N rows."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine.pipeline import run_rule_pipeline
from app.engine.rules import list_rule_types
from app.models.upload import UploadedFile

router = APIRouter(prefix="/api/preview", tags=["preview"])


@router.post("/{upload_id}")
async def preview_rules(upload_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    upload = await db.get(UploadedFile, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    file_path = Path(upload.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    rules = body.get("rules", [])
    limit = body.get("limit", 20)

    if not rules:
        return {"headers": [], "rows": [], "total_rows": 0, "errors": []}

    try:
        df = run_rule_pipeline(file_path, rules, limit=limit)
        headers = [str(c) for c in df.columns.tolist()]
        rows = df.fillna("").values.tolist()
        return {
            "headers": headers,
            "rows": rows,
            "total_rows": len(df),
            "errors": [],
        }
    except Exception as e:
        return {
            "headers": [],
            "rows": [],
            "total_rows": 0,
            "errors": [str(e)],
        }


@router.get("/rule-types")
async def get_rule_types():
    """Return all available rule types with their schemas."""
    return list_rule_types()
