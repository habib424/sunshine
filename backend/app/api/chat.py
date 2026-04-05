"""Chat API: conversational transformation planning + execution."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat_transformer import chat, create_session, execute_script, get_session
from app.config import settings
from app.db.session import get_db
from app.models.job import TransformationJob
from app.models.upload import UploadedFile

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/start/{upload_id}")
async def start_chat(upload_id: str, body: dict = None, db: AsyncSession = Depends(get_db)):
    """Start a new chat session for an uploaded file."""
    upload = await db.get(UploadedFile, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    file_path = Path(upload.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    goal = (body or {}).get("goal", "journal_entry")

    try:
        result = create_session(file_path, goal=goal)
        # Send the initial message to get the AI's first analysis
        initial = chat(result["session_id"],
            f"I've uploaded '{upload.original_name}'. "
            f"My goal is to generate a Light journal entry upload file from this data. "
            f"Please analyze the file and propose a transformation plan."
        )
        return {
            "session_id": result["session_id"],
            "sheet_names": result["sheet_names"],
            "message": initial["message"],
            "has_script": initial["has_script"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/message/{session_id}")
async def send_message(session_id: str, body: dict):
    """Send a message in an existing chat session."""
    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        result = chat(session_id, message)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/execute/{session_id}")
async def execute_transform(session_id: str, body: dict = None, db: AsyncSession = Depends(get_db)):
    """Execute the generated script and produce the output file."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session["has_script"]:
        raise HTTPException(status_code=400, detail="No script generated yet")

    output_filename = (body or {}).get("output_filename", "migration_output.xlsx")
    job_id = str(uuid.uuid4())

    # Create job record
    job = TransformationJob(id=job_id, playbook_name="chat", status="running")
    db.add(job)
    await db.commit()

    output_dir = settings.outputs_path / job_id
    output_path = output_dir / output_filename

    try:
        result = execute_script(session_id, output_path)

        if result["success"]:
            job.status = "completed"
            job.output_path = str(output_dir)
        else:
            job.status = "failed"
            job.error_message = result.get("error", "Unknown error")

        await db.commit()

        return {
            "job_id": job_id,
            "success": result["success"],
            "error": result.get("error"),
            "rows": result.get("rows"),
            "columns": result.get("columns"),
            "preview": result.get("preview"),
        }
    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))
