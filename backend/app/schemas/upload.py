from datetime import datetime

from pydantic import BaseModel


class UploadResponse(BaseModel):
    id: str
    filename: str
    original_name: str
    file_type: str | None
    status: str
    uploaded_at: datetime
    row_count: int | None
    column_headers: list[str] | None
    sheet_names: list[str] | None


class FilePreview(BaseModel):
    id: str
    filename: str
    headers: list[str]
    rows: list[list]
    total_rows: int
