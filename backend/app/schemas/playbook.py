from pydantic import BaseModel


class PlaybookSummary(BaseModel):
    name: str
    display_name: str
    description: str
    file_types: list[str]


class PlaybookDetail(BaseModel):
    name: str
    display_name: str
    description: str
    version: str
    file_types: list[str]
    detection_rules: list[dict]
