import math
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.engine.reconciliation import (
    classify_reconciliation_file,
    reconcile_light_journal_to_trial_balances,
    write_reconciliation_workbook,
)
from app.models.job import TransformationJob
from app.models.upload import UploadedFile

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])


class ReconciliationRunRequest(BaseModel):
    journal_upload_id: str
    tb_upload_ids: list[str] = Field(min_length=1)
    tolerance: float = Field(default=0.01, ge=0)
    entity_mappings: dict[str, str] | None = None


class ReconciliationRunResponse(BaseModel):
    job_id: str
    status: str
    output_filename: str
    summary: dict[str, Any]
    entity_summary: list[dict[str, Any]]
    account_details: list[dict[str, Any]]
    journal_meta: dict[str, Any]
    trial_balance_mappings: list[dict[str, Any]]
    file_classifications: list[dict[str, Any]] = []


class ReconciliationIdentifyRequest(BaseModel):
    upload_ids: list[str] = Field(min_length=1)


class ReconciliationAutoRunRequest(BaseModel):
    upload_ids: list[str] = Field(min_length=1)
    tolerance: float = Field(default=0.01, ge=0)
    entity_mappings: dict[str, str] | None = None


class ReconciliationIdentifyResponse(BaseModel):
    file_classifications: list[dict[str, Any]]
    journal_upload_ids: list[str]
    tb_upload_ids: list[str]


@router.post("/run", response_model=ReconciliationRunResponse)
async def run_reconciliation(
    body: ReconciliationRunRequest,
    db: AsyncSession = Depends(get_db),
):
    journal_upload = await _get_upload(body.journal_upload_id, db)
    tb_uploads = [await _get_upload(upload_id, db) for upload_id in body.tb_upload_ids]

    journal_path = Path(journal_upload.storage_path)
    tb_paths = [Path(upload.storage_path) for upload in tb_uploads]
    _ensure_exists(journal_path, journal_upload.original_name)
    for upload, path in zip(tb_uploads, tb_paths, strict=True):
        _ensure_exists(path, upload.original_name)

    job_id = str(uuid.uuid4())
    job = TransformationJob(
        id=job_id,
        playbook_name="reconciliation",
        status="running",
        current_stage="reconcile",
    )
    db.add(job)
    await db.commit()

    try:
        overrides = _entity_overrides_by_file(body.entity_mappings or {}, tb_uploads)
        source_names = _source_names([journal_upload], tb_uploads)
        result = reconcile_light_journal_to_trial_balances(
            journal_path,
            tb_paths,
            tolerance=body.tolerance,
            entity_overrides=overrides,
            source_names=source_names,
        )
        _apply_display_filenames(result, [journal_upload], tb_uploads)

        output_dir = settings.outputs_path / job_id
        output_filename = "light_trial_balance_reconciliation.xlsx"
        output_file = output_dir / output_filename
        write_reconciliation_workbook(result, output_file)

        job.status = "completed"
        job.current_stage = None
        job.output_path = str(output_dir)
        job.updated_at = datetime.utcnow()
        await db.commit()

        response = {
            "job_id": job_id,
            "status": "completed",
            "output_filename": output_filename,
            "summary": result["summary"],
            "entity_summary": result["entity_summary"],
            "account_details": result["account_details"],
            "journal_meta": result["journal_meta"],
            "trial_balance_mappings": result["trial_balance_mappings"],
            "file_classifications": [],
        }
        return _jsonable(response)
    except Exception as exc:
        job.status = "failed"
        job.current_stage = None
        job.error_message = str(exc)
        job.updated_at = datetime.utcnow()
        await db.commit()
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/identify", response_model=ReconciliationIdentifyResponse)
async def identify_reconciliation_files(
    body: ReconciliationIdentifyRequest,
    db: AsyncSession = Depends(get_db),
):
    uploads = [await _get_upload(upload_id, db) for upload_id in body.upload_ids]
    classifications = _classify_uploads(uploads)
    return _classification_response(uploads, classifications)


@router.post("/auto-run", response_model=ReconciliationRunResponse)
async def auto_run_reconciliation(
    body: ReconciliationAutoRunRequest,
    db: AsyncSession = Depends(get_db),
):
    uploads = [await _get_upload(upload_id, db) for upload_id in body.upload_ids]
    classifications = _classify_uploads(uploads)
    journal_uploads = [
        upload for upload, classification in zip(uploads, classifications, strict=True)
        if classification["kind"] == "journal"
    ]
    tb_uploads = [
        upload for upload, classification in zip(uploads, classifications, strict=True)
        if classification["kind"] == "trial_balance"
    ]

    if not journal_uploads:
        raise HTTPException(status_code=400, detail="No Light journal file was identified in this upload batch")
    if not tb_uploads:
        raise HTTPException(status_code=400, detail="No trial balance file was identified in this upload batch")

    journal_paths = [Path(upload.storage_path) for upload in journal_uploads]
    tb_paths = [Path(upload.storage_path) for upload in tb_uploads]
    for upload, path in zip(journal_uploads + tb_uploads, journal_paths + tb_paths, strict=True):
        _ensure_exists(path, upload.original_name)

    job_id = str(uuid.uuid4())
    job = TransformationJob(
        id=job_id,
        playbook_name="reconciliation",
        status="running",
        current_stage="reconcile",
    )
    db.add(job)
    await db.commit()

    try:
        overrides = _entity_overrides_by_file(body.entity_mappings or {}, tb_uploads)
        source_names = _source_names(journal_uploads, tb_uploads)
        result = reconcile_light_journal_to_trial_balances(
            journal_paths,
            tb_paths,
            tolerance=body.tolerance,
            entity_overrides=overrides,
            source_names=source_names,
        )
        _apply_display_filenames(result, journal_uploads, tb_uploads)

        output_dir = settings.outputs_path / job_id
        output_filename = "light_trial_balance_reconciliation.xlsx"
        output_file = output_dir / output_filename
        write_reconciliation_workbook(result, output_file)

        job.status = "completed"
        job.current_stage = None
        job.output_path = str(output_dir)
        job.updated_at = datetime.utcnow()
        await db.commit()

        return _jsonable({
            "job_id": job_id,
            "status": "completed",
            "output_filename": output_filename,
            "summary": result["summary"],
            "entity_summary": result["entity_summary"],
            "account_details": result["account_details"],
            "journal_meta": result["journal_meta"],
            "trial_balance_mappings": result["trial_balance_mappings"],
            "file_classifications": classifications,
        })
    except Exception as exc:
        job.status = "failed"
        job.current_stage = None
        job.error_message = str(exc)
        job.updated_at = datetime.utcnow()
        await db.commit()
        raise HTTPException(status_code=500, detail=str(exc))


async def _get_upload(upload_id: str, db: AsyncSession) -> UploadedFile:
    upload = await db.get(UploadedFile, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail=f"Upload '{upload_id}' not found")
    return upload


def _ensure_exists(path: Path, original_name: str) -> None:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File '{original_name}' was not found on disk")


def _classify_uploads(uploads: list[UploadedFile]) -> list[dict[str, Any]]:
    classifications: list[dict[str, Any]] = []
    for upload in uploads:
        path = Path(upload.storage_path)
        _ensure_exists(path, upload.original_name)
        classification = classify_reconciliation_file(path, source_name=upload.original_name)
        classification["upload_id"] = upload.id
        classifications.append(classification)
    return classifications


def _classification_response(
    uploads: list[UploadedFile],
    classifications: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "file_classifications": classifications,
        "journal_upload_ids": [
            upload.id for upload, classification in zip(uploads, classifications, strict=True)
            if classification["kind"] == "journal"
        ],
        "tb_upload_ids": [
            upload.id for upload, classification in zip(uploads, classifications, strict=True)
            if classification["kind"] == "trial_balance"
        ],
    }


def _source_names(
    journal_uploads: list[UploadedFile],
    tb_uploads: list[UploadedFile],
) -> dict[str, str]:
    names: dict[str, str] = {}
    for upload in journal_uploads + tb_uploads:
        path = Path(upload.storage_path)
        names[path.name] = upload.original_name
        names[str(path)] = upload.original_name
    return names


def _entity_overrides_by_file(
    mappings: dict[str, str],
    uploads: list[UploadedFile],
) -> dict[str, str]:
    """Allow UI mappings keyed by upload id, stored filename, or original filename."""
    by_file: dict[str, str] = {}
    for upload in uploads:
        entity = (
            mappings.get(upload.id)
            or mappings.get(upload.filename)
            or mappings.get(upload.original_name)
            or mappings.get(str(Path(upload.storage_path)))
        )
        if entity:
            path = Path(upload.storage_path)
            by_file[path.name] = entity
            by_file[str(path)] = entity
            by_file[upload.original_name] = entity
    return by_file


def _apply_display_filenames(
    result: dict[str, Any],
    journal_uploads: list[UploadedFile],
    tb_uploads: list[UploadedFile],
) -> None:
    names = {}
    for upload in journal_uploads + tb_uploads:
        names[Path(upload.storage_path).name] = upload.original_name

    summary = result["summary"]
    summary["journal_file"] = names.get(summary["journal_file"], summary["journal_file"])
    if "journal_files" in summary:
        summary["journal_files"] = [
            names.get(filename, filename) for filename in summary["journal_files"]
        ]
        summary["journal_file"] = ", ".join(summary["journal_files"])
    summary["trial_balance_files"] = [
        names.get(filename, filename) for filename in summary["trial_balance_files"]
    ]

    for mapping in result["trial_balance_mappings"]:
        mapping["source_file"] = names.get(mapping["source_file"], mapping["source_file"])

    for detail in result["account_details"]:
        detail["tb_source_file"] = _display_source_list(detail.get("tb_source_file", ""), names)

    details_df = result.get("details_df")
    if details_df is not None and "tb_source_file" in details_df.columns:
        details_df["tb_source_file"] = details_df["tb_source_file"].apply(
            lambda value: _display_source_list(value, names)
        )

    journal_lines_df = result.get("journal_lines_df")
    if journal_lines_df is not None and "source_file" in journal_lines_df.columns:
        journal_lines_df["source_file"] = journal_lines_df["source_file"].apply(
            lambda value: names.get(str(value), value)
        )

    for tab in result.get("tb_tabs", []):
        tab["source_file"] = names.get(tab["source_file"], tab["source_file"])
        tab_df = tab.get("df")
        if tab_df is not None and "tb_source_file" in tab_df.columns:
            tab_df["tb_source_file"] = tab_df["tb_source_file"].apply(
                lambda value: _display_source_list(value, names)
            )


def _display_source_list(value: Any, names: dict[str, str]) -> str:
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    return ", ".join(names.get(part, part) for part in parts)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item"):
        return _jsonable(value.item())
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value
