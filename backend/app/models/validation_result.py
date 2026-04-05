import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("transformation_jobs.id"))
    upload_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("uploaded_files.id"), nullable=True)
    severity: Mapped[str] = mapped_column(String(10))  # error, warning, info
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    validator_name: Mapped[str] = mapped_column(String(100))
