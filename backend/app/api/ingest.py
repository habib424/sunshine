"""
Deterministic ingest API.

Endpoints for the new deterministic pipeline:
    GET  /api/ingest/intents          — list available intents (for UI dropdown)
    POST /api/ingest/analyze/{id}     — run deterministic analysis on an uploaded file
    POST /api/ingest/confirm/{id}     — confirm a proposed layout

These endpoints sit alongside the existing chat-based flow. They do NOT
replace it yet — that happens in slice 5 when we wire the full pipeline.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine.conservation import compare, snapshot
from app.engine.ingest.orchestrator import confirm_layout, ingest
from app.engine.intents import get_intent, list_intents
from app.models.upload import UploadedFile

import app.engine.validators  # noqa: F401 — register validators
from app.engine.registry import get_validator

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class IntentOption(BaseModel):
    name: str
    label: str
    description: str
    contract: str
    action: str


class AnalyzeRequest(BaseModel):
    intent: str


class ConfirmLayoutRequest(BaseModel):
    intent: str
    layout: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/intents", response_model=list[IntentOption])
async def get_intents():
    """Return available intents for the UI to display as choices."""
    return [IntentOption(**i) for i in list_intents()]


@router.post("/analyze/{upload_id}")
async def analyze_upload(
    upload_id: str,
    body: AnalyzeRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Run deterministic analysis on an uploaded file under a declared intent.

    Returns:
        - ingest result (layout, fingerprint, confidence, status)
        - contract validation issues (if layout was successfully applied)
        - conservation snapshot (for use after transform)
    """
    upload = await db.get(UploadedFile, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    file_path = Path(upload.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Run deterministic ingest
    try:
        result = ingest(file_path, body.intent)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # When the user explicitly asks to analyze, always try to apply the
    # layout — even at low confidence — and show what we find. The
    # confidence threshold exists for unattended pipeline runs; when a
    # human clicks "analyze", they want results, not to be blocked.
    if result.dataframe is None and result.layout.get("sheet") is not None:
        from app.engine.ingest.layout import apply_layout
        try:
            result.dataframe = apply_layout(file_path, result.layout)
        except Exception:
            pass  # If apply fails, we proceed with no DataFrame

    response = {
        "upload_id": upload_id,
        "filename": upload.original_name,
        **result.to_dict(),
    }

    # If we got a DataFrame, run contract validation and capture snapshot
    if result.dataframe is not None:
        intent_spec = get_intent(body.intent)

        # Contract validation
        validator = get_validator("journal_entry_contract")
        issues = validator(result.dataframe, {})
        response["validation_issues"] = issues
        response["issue_summary"] = _summarize_issues(issues)

        # Conservation snapshot (will be compared after transform)
        conserve_decls = intent_spec.get("conserve", [])
        if conserve_decls:
            snap = snapshot(result.dataframe, conserve_decls)
            response["conservation_snapshot"] = snap.to_dict()
    else:
        response["validation_issues"] = []
        response["issue_summary"] = {}

    return response


@router.post("/confirm/{upload_id}")
async def confirm_upload_layout(
    upload_id: str,
    body: ConfirmLayoutRequest,
    db: AsyncSession = Depends(get_db),
):
    """Confirm a proposed layout and persist it for future reuse."""
    upload = await db.get(UploadedFile, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    file_path = Path(upload.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    try:
        result = confirm_layout(
            file_path,
            body.intent,
            body.layout,
            confirmed_by="user",
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "upload_id": upload_id,
        "filename": upload.original_name,
        **result.to_dict(),
    }


def _summarize_issues(issues: list[dict]) -> dict:
    """Group issues by code with counts and severity."""
    summary: dict[str, dict] = {}
    for issue in issues:
        code = issue["issue_code"]
        if code not in summary:
            summary[code] = {
                "code": code,
                "severity": issue["severity"],
                "count": 0,
                "description": issue.get("message", ""),
            }
        summary[code]["count"] += 1
    return summary
