"""Chat API: conversational transformation planning + execution."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat_transformer import chat, create_session, execute_script, get_session
from app.config import settings
from app.db.session import get_db
from app.engine.deferrals import DEFERRAL_INTENTS
from app.engine.fx_adjustment import FX_ADJUSTMENT_INTENTS
from app.engine.open_ap import OPEN_AP_INTENTS
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
        result = create_session(file_path, goal=goal, intent=intent)

        # Build a grounded initial message whose task matches the intent.
        initial_msg = f"I've uploaded '{upload.original_name}'.\n\n"

        if layout_context:
            initial_msg += (
                f"The deterministic layout detector has already analyzed the file "
                f"and found the following. Use these findings as facts — do NOT "
                f"re-guess or contradict them:\n\n{layout_context}\n\n"
            )

        initial_msg += _intent_instruction(intent)

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
    if (
        intent == "reconcile_je_to_gl"
        or intent in DEFERRAL_INTENTS
        or intent in OPEN_AP_INTENTS
        or intent in FX_ADJUSTMENT_INTENTS
    ):
        return ""  # These paths use their own multi-sheet analysis.

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


def _intent_instruction(intent: str) -> str:
    """Return the task instruction for the AI based on the user's intent."""
    if intent == "validate_je":
        return (
            "My intent is to VALIDATE this file — NOT to transform it.\n\n"
            "Check the file against journal entry rules:\n"
            "1. For each entry (same entry ID), do all lines have the same date, currency, and business partner?\n"
            "2. Does each entry balance to zero (sum of debits = sum of credits)?\n"
            "3. Are all GL accounts valid?\n"
            "4. Are there any missing required fields?\n"
            "5. Are there any data quality issues (invalid dates, unknown currencies, etc.)?\n\n"
            "Report what you find as a structured validation report. "
            "Do NOT propose a transformation plan. Do NOT generate a script. "
            "Just tell me what's right and what's wrong with this file."
        )
    elif intent == "reconcile_je_to_gl":
        return (
            "My intent is to RECONCILE journal entries against a trial balance or GL extract.\n\n"
            "This workbook contains two datasets:\n"
            "1. A sheet with journal entry line items (debits and credits per account)\n"
            "2. A sheet with trial balance or GL summary balances per account\n\n"
            "Please:\n"
            "1. Identify which sheet is the JE data and which is the TB/GL data\n"
            "2. Identify how account codes appear in each sheet "
            "(column name, embedded in descriptions, etc.)\n"
            "3. Propose a reconciliation plan that:\n"
            "   - Aggregates JE data to net balance per account code\n"
            "   - Extracts account code and balance from the TB/GL sheet\n"
            "   - Joins on account code and computes differences\n"
            "   - Flags each account as Matched, Difference, JE Only, or TB Only\n"
            "4. Show me the plan before generating any script\n\n"
            "The output should be a reconciliation report DataFrame."
        )
    elif intent in DEFERRAL_INTENTS:
        direction = "deferred revenue" if "revenue" in intent else "deferred cost / prepayment"
        return (
            f"My intent is to MIGRATE {direction} balances into the Light JE upload format.\n\n"
            "Use the target Light JE layout as the destination, identify the source schedule and any reference extracts, "
            "ask only for missing facts, and execute using deterministic rules once the required facts are available."
        )
    elif intent in OPEN_AP_INTENTS:
        return (
            "My intent is to UPLOAD open accounts payable into Light.\n\n"
            "Identify the AP source (flat ledger or aging detail report), exclude section "
            "headers, per-vendor subtotals and grand totals, net vendor payments against "
            "outstanding invoices, reuse Light Posting reference lines when the workbook "
            "carries them, ask only for missing facts, and execute using deterministic "
            "rules once the required facts are available."
        )
    elif intent in FX_ADJUSTMENT_INTENTS:
        return (
            "My intent is to post FX CURRENCY ADJUSTMENTS so the booked account-currency "
            "amounts align with the real bank balances.\n\n"
            "Compare the booked balances from the trial balance with the real bank balances, "
            "post each difference against the bank clearing account with Local and Group "
            "Currency FX Rate overridden to 0, ask only for missing facts, and execute using "
            "deterministic rules once the required facts are available."
        )
    else:
        # Default: convert_to_light_je
        return (
            "My intent is to CONVERT this file to Light.inc journal entry format.\n\n"
            "Based on the confirmed column mappings, propose a transformation plan "
            "that uses the detected columns. For any columns the detector "
            "could NOT map, look at the actual data and propose specific "
            "mappings with reasoning."
        )


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
