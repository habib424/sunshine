from datetime import datetime

from pydantic import BaseModel


class JobCreate(BaseModel):
    playbook_name: str
    upload_ids: list[str]
    mappings: dict | None = None


class JobResponse(BaseModel):
    id: str
    playbook_name: str
    status: str
    current_stage: str | None
    created_at: datetime
    updated_at: datetime
    error_message: str | None
    output_path: str | None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
