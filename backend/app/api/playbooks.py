from fastapi import APIRouter, HTTPException

from app.schemas.playbook import PlaybookDetail, PlaybookSummary
from app.services.playbook_service import get_playbook, list_playbooks

router = APIRouter(prefix="/api/playbooks", tags=["playbooks"])


@router.get("", response_model=list[PlaybookSummary])
async def get_playbooks():
    playbooks = list_playbooks()
    return [PlaybookSummary(
        name=p.name,
        display_name=p.display_name,
        description=p.description,
        file_types=p.file_types,
    ) for p in playbooks]


@router.get("/{name}", response_model=PlaybookDetail)
async def get_playbook_detail(name: str):
    try:
        p = get_playbook(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Playbook '{name}' not found")

    return PlaybookDetail(
        name=p.name,
        display_name=p.display_name,
        description=p.description,
        version=p.version,
        file_types=p.file_types,
        detection_rules=p.detection_rules,
    )
