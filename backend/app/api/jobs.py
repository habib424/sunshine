import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.engine.pipeline import (
    PipelineStage,
    is_file_transform,
    is_rule_pipeline,
    run_export,
    run_file_transform,
    run_ingest,
    run_rule_pipeline,
    run_transform,
    run_validate,
)
from app.models.job import TransformationJob
from app.models.upload import UploadedFile
from app.models.validation_result import ValidationResult
from app.schemas.job import JobCreate, JobListResponse, JobResponse
from app.schemas.validation import ValidationIssue, ValidationSummary
from app.services.playbook_service import detect_file_type, get_playbook

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse)
async def create_job(body: JobCreate, db: AsyncSession = Depends(get_db)):
    try:
        get_playbook(body.playbook_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Playbook '{body.playbook_name}' not found")

    job = TransformationJob(
        id=str(uuid.uuid4()),
        playbook_name=body.playbook_name,
        status="pending",
    )
    db.add(job)

    # Link uploads to job
    for upload_id in body.upload_ids:
        upload = await db.get(UploadedFile, upload_id)
        if not upload:
            raise HTTPException(status_code=404, detail=f"Upload '{upload_id}' not found")
        upload.job_id = job.id

    await db.commit()
    await db.refresh(job)

    return _job_response(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TransformationJob).order_by(TransformationJob.created_at.desc()))
    jobs = result.scalars().all()
    return JobListResponse(
        jobs=[_job_response(j) for j in jobs],
        total=len(jobs),
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(TransformationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_response(job)


@router.post("/{job_id}/run", response_model=JobResponse)
async def run_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(TransformationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    playbook = get_playbook(job.playbook_name)

    # Get uploads for this job
    result = await db.execute(select(UploadedFile).where(UploadedFile.job_id == job_id))
    uploads = result.scalars().all()

    if not uploads:
        raise HTTPException(status_code=400, detail="No files attached to this job")

    try:
        job.status = "running"
        job.current_stage = PipelineStage.INGEST
        await db.commit()

        # Process each file
        for upload in uploads:
            file_path = Path(upload.storage_path)

            # Detect file type
            job.current_stage = PipelineStage.DETECT
            await db.commit()

            if not upload.file_type:
                detected, confidence = detect_file_type(playbook, upload.column_headers or [], upload.original_name)
                if detected:
                    upload.file_type = detected
                    await db.commit()
                else:
                    raise ValueError(f"Could not detect file type for '{upload.original_name}'")

            # Get file type config
            ft_config = playbook.get_file_type_config(upload.file_type)

            # Ingest + Transform (branched by transform_type)
            job.current_stage = PipelineStage.INGEST
            await db.commit()

            if is_file_transform(ft_config):
                # Whole-file multi-sheet transform — skips column-level ingest
                job.current_stage = PipelineStage.TRANSFORM
                await db.commit()
                transformed = run_file_transform(file_path, ft_config)
            else:
                df = run_ingest(file_path, ft_config)
                job.current_stage = PipelineStage.TRANSFORM
                await db.commit()
                transformed = run_transform(df, ft_config)

            # Validate
            job.current_stage = PipelineStage.VALIDATE
            await db.commit()
            issues = run_validate(transformed, ft_config)

            # Store validation results
            for issue in issues:
                vr = ValidationResult(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    upload_id=upload.id,
                    severity=issue["severity"],
                    row_number=issue.get("row_number"),
                    column_name=issue.get("column_name"),
                    message=issue["message"],
                    validator_name=issue["validator_name"],
                )
                db.add(vr)

            # Export
            job.current_stage = PipelineStage.EXPORT
            await db.commit()

            output_dir = settings.outputs_path / job_id
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / f"{upload.file_type}_{upload.original_name}"

            # Check for template
            template_name = ft_config.get("target_template")
            template_path = settings.playbooks_path / "_templates" / template_name if template_name else None

            run_export(transformed, output_file, template_path)
            upload.status = "completed"

        job.status = "completed"
        job.current_stage = None
        job.output_path = str(settings.outputs_path / job_id)
        job.updated_at = datetime.utcnow()
        await db.commit()

    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        job.updated_at = datetime.utcnow()
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))

    return _job_response(job)


@router.post("/run-direct")
async def run_direct(body: dict, db: AsyncSession = Depends(get_db)):
    """Run a file transform directly from an inline config (no pre-saved playbook needed)."""
    upload_id = body.get("upload_id")
    ft_config = body.get("playbook_config")
    output_filename = body.get("output_filename", "migration_output.xlsx")

    if not upload_id or not ft_config:
        raise HTTPException(status_code=400, detail="upload_id and playbook_config are required")

    upload = await db.get(UploadedFile, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    file_path = Path(upload.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    job_id = str(uuid.uuid4())
    job = TransformationJob(id=job_id, playbook_name="direct", status="running")
    db.add(job)
    upload.job_id = job_id
    await db.commit()

    try:
        if is_rule_pipeline(ft_config):
            transformed = run_rule_pipeline(file_path, ft_config.get("rules", []))
        else:
            transformed = run_file_transform(file_path, ft_config)

        output_dir = settings.outputs_path / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / output_filename
        run_export(transformed, output_file)

        job.status = "completed"
        job.output_path = str(output_dir)
        job.updated_at = datetime.utcnow()
        upload.status = "completed"
        await db.commit()
    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        job.updated_at = datetime.utcnow()
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))

    return {"job_id": job_id, "status": "completed", "output_filename": output_filename}


@router.get("/{job_id}/validation", response_model=ValidationSummary)
async def get_validation(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ValidationResult).where(ValidationResult.job_id == job_id))
    issues = result.scalars().all()

    return ValidationSummary(
        errors=sum(1 for i in issues if i.severity == "error"),
        warnings=sum(1 for i in issues if i.severity == "warning"),
        info=sum(1 for i in issues if i.severity == "info"),
        total=len(issues),
        issues=[ValidationIssue(
            id=i.id,
            severity=i.severity,
            row_number=i.row_number,
            column_name=i.column_name,
            message=i.message,
            validator_name=i.validator_name,
        ) for i in issues],
    )


@router.get("/{job_id}/export")
async def export_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(TransformationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Job is not completed yet")

    output_dir = Path(job.output_path)
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="Output files not found")

    files = list(output_dir.iterdir())
    if not files:
        raise HTTPException(status_code=404, detail="No output files generated")

    # Return the first file (for single-file jobs)
    return FileResponse(
        path=files[0],
        filename=files[0].name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _job_response(job: TransformationJob) -> JobResponse:
    return JobResponse(
        id=job.id,
        playbook_name=job.playbook_name,
        status=job.status,
        current_stage=job.current_stage,
        created_at=job.created_at,
        updated_at=job.updated_at,
        error_message=job.error_message,
        output_path=job.output_path,
    )
