from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.upload import UploadedFile
from app.schemas.upload import FilePreview, UploadResponse
from app.services.file_service import read_file_metadata, read_file_preview, save_upload

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("", response_model=list[UploadResponse])
async def upload_files(files: list[UploadFile], db: AsyncSession = Depends(get_db)):
    results = []
    for file in files:
        content = await file.read()
        file_id, dest_path = save_upload(content, file.filename)

        try:
            metadata = read_file_metadata(dest_path)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot read file '{file.filename}': {str(e)}")

        upload = UploadedFile(
            id=file_id,
            filename=dest_path.name,
            original_name=file.filename,
            status="uploaded",
            row_count=metadata["row_count"],
            column_headers=metadata["column_headers"],
            sheet_names=metadata["sheet_names"],
            storage_path=str(dest_path),
        )
        db.add(upload)
        results.append(upload)

    await db.commit()
    return [UploadResponse(
        id=u.id,
        filename=u.filename,
        original_name=u.original_name,
        file_type=u.file_type,
        status=u.status,
        uploaded_at=u.uploaded_at,
        row_count=u.row_count,
        column_headers=u.column_headers,
        sheet_names=u.sheet_names,
    ) for u in results]


@router.get("", response_model=list[UploadResponse])
async def list_uploads(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UploadedFile).order_by(UploadedFile.uploaded_at.desc()))
    uploads = result.scalars().all()
    return [UploadResponse(
        id=u.id,
        filename=u.filename,
        original_name=u.original_name,
        file_type=u.file_type,
        status=u.status,
        uploaded_at=u.uploaded_at,
        row_count=u.row_count,
        column_headers=u.column_headers,
        sheet_names=u.sheet_names,
    ) for u in uploads]


@router.get("/{upload_id}", response_model=UploadResponse)
async def get_upload(upload_id: str, db: AsyncSession = Depends(get_db)):
    upload = await db.get(UploadedFile, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    return UploadResponse(
        id=upload.id,
        filename=upload.filename,
        original_name=upload.original_name,
        file_type=upload.file_type,
        status=upload.status,
        uploaded_at=upload.uploaded_at,
        row_count=upload.row_count,
        column_headers=upload.column_headers,
        sheet_names=upload.sheet_names,
    )


@router.get("/{upload_id}/preview", response_model=FilePreview)
async def get_upload_preview(upload_id: str, db: AsyncSession = Depends(get_db)):
    upload = await db.get(UploadedFile, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    file_path = Path(upload.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    preview = read_file_preview(file_path)
    return FilePreview(
        id=upload.id,
        filename=upload.original_name,
        headers=preview["headers"],
        rows=preview["rows"],
        total_rows=upload.row_count or preview["total_rows"],
    )


@router.delete("/{upload_id}")
async def delete_upload(upload_id: str, db: AsyncSession = Depends(get_db)):
    upload = await db.get(UploadedFile, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    file_path = Path(upload.storage_path)
    if file_path.exists():
        file_path.unlink()

    await db.delete(upload)
    await db.commit()
    return {"status": "deleted"}
