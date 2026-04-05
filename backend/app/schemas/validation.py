from pydantic import BaseModel


class ValidationIssue(BaseModel):
    id: str
    severity: str
    row_number: int | None
    column_name: str | None
    message: str
    validator_name: str


class ValidationSummary(BaseModel):
    errors: int
    warnings: int
    info: int
    total: int
    issues: list[ValidationIssue]
