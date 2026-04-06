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

    intent = (body or {}).get("intent", "convert_to_light_je")
    goal = (body or {}).get("goal", "journal_entry")

    # Run quick deterministic layout detection so the AI starts with
    # real knowledge about the file instead of guessing from scratch.
    layout_context = _build_layout_context(file_path, intent)

    try:
        result = create_session(file_path, goal=goal)

        # Build a grounded initial message that includes what the
        # detector already knows, so the AI doesn't re-guess.
        initial_msg = (
            f"I've uploaded '{upload.original_name}'.\n\n"
            f"My intent is: {intent}.\n\n"
        )
        if layout_context:
            initial_msg += (
                f"The deterministic layout detector has already analyzed the file "
                f"and found the following. Use these findings as facts — do NOT "
                f"re-guess or contradict them:\n\n{layout_context}\n\n"
                f"Based on these confirmed mappings, propose a transformation plan "
                f"that uses the detected columns. For any columns the detector "
                f"could NOT map, look at the actual data and propose specific "
                f"mappings with reasoning."
            )
        else:
            initial_msg += (
                f"Please analyze the file and propose a transformation plan."
            )

        initial = chat(result["session_id"], initial_msg)
        return {
            "session_id": result["session_id"],
            "sheet_names": result["sheet_names"],
            "message": initial["message"],
            "has_script": initial["has_script"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _build_layout_context(file_path: Path, intent: str) -> str:
    """Run the deterministic layout detector and format findings for the AI."""
    try:
        from app.engine.ingest.orchestrator import ingest
        result = ingest(file_path, intent)

        lines = []
        layout = result.layout
        lines.append(f"- Detected sheet: '{layout.get('sheet')}'")
        lines.append(f"- Header row: {layout.get('header_row')}")
        lines.append(f"- Confidence: {result.confidence}")

        roles = layout.get("column_roles", {})
        if roles:
            lines.append("- CONFIRMED column mappings (use these exactly):")
            for src_col, canonical in roles.items():
                lines.append(f"    '{src_col}' → {canonical}")

        missing = layout.get("missing_required", [])
        if missing:
            lines.append(f"- UNMAPPED required columns (you must find or derive): {missing}")

        unmapped = layout.get("unmapped_columns", [])
        if unmapped:
            lines.append(f"- Source columns with no assigned role: {unmapped}")
            lines.append(
                "  Look at these columns to find mappings for the unmapped "
                "required columns above. Examine actual data values, not just names."
            )

        return "\n".join(lines)
    except Exception:
        return ""


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
