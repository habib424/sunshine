"""
Rules API: CRUD for YAML-backed validation rules.

Endpoints:
    GET  /api/rules/contracts          — list contracts with rule files
    GET  /api/rules/check-types        — list registered check types + schemas
    GET  /api/rules/{contract}         — get full rule set
    GET  /api/rules/{contract}/{id}    — get single rule
    PATCH /api/rules/{contract}/{id}   — update rule (enable/disable, severity, params)
    POST /api/rules/{contract}         — add a new rule
    DELETE /api/rules/{contract}/{id}  — remove a rule
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engine.checks import list_check_types
from app.engine.contracts.loader import (
    list_available_contracts,
    load_rules,
    reload_rules,
    save_rules,
)

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleUpdate(BaseModel):
    enabled: bool | None = None
    severity: str | None = None
    description: str | None = None
    params: dict | None = None


class RuleCreate(BaseModel):
    id: str
    issue_code: str
    description: str
    check_type: str
    severity: str = "error"
    scope: str = "line"
    enabled: bool = True
    phase: str = "line"
    params: dict = {}


@router.get("/contracts")
async def get_contracts():
    return list_available_contracts()


@router.get("/check-types")
async def get_check_types():
    return list_check_types()


@router.get("/{contract}")
async def get_rules(contract: str):
    try:
        ruleset = load_rules(contract)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No rules for contract '{contract}'")
    return ruleset.to_dict()


@router.get("/{contract}/{rule_id}")
async def get_rule(contract: str, rule_id: str):
    try:
        ruleset = load_rules(contract)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No rules for contract '{contract}'")
    rule = ruleset.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return rule


@router.patch("/{contract}/{rule_id}")
async def update_rule(contract: str, rule_id: str, body: RuleUpdate):
    try:
        ruleset = reload_rules(contract)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No rules for contract '{contract}'")

    rule = ruleset.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")

    if body.enabled is not None:
        rule["enabled"] = body.enabled
    if body.severity is not None:
        if body.severity not in ("error", "warning"):
            raise HTTPException(status_code=400, detail="severity must be 'error' or 'warning'")
        rule["severity"] = body.severity
    if body.description is not None:
        rule["description"] = body.description
    if body.params is not None:
        rule["params"] = body.params

    save_rules(ruleset)
    reload_rules(contract)
    return rule


@router.post("/{contract}")
async def create_rule(contract: str, body: RuleCreate):
    try:
        ruleset = reload_rules(contract)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No rules for contract '{contract}'")

    # Check for duplicate id
    if ruleset.get_rule(body.id):
        raise HTTPException(status_code=409, detail=f"Rule '{body.id}' already exists")

    # Validate check type exists
    try:
        from app.engine.checks import get_check_executor
        get_check_executor(body.check_type)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    new_rule = {
        "id": body.id,
        "issue_code": body.issue_code,
        "description": body.description,
        "check_type": body.check_type,
        "severity": body.severity,
        "scope": body.scope,
        "enabled": body.enabled,
        "phase": body.phase,
        "params": body.params,
    }
    ruleset.rules.append(new_rule)
    save_rules(ruleset)
    reload_rules(contract)
    return new_rule


@router.delete("/{contract}/{rule_id}")
async def delete_rule(contract: str, rule_id: str):
    try:
        ruleset = reload_rules(contract)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No rules for contract '{contract}'")

    original_count = len(ruleset.rules)
    ruleset.rules = [r for r in ruleset.rules if r["id"] != rule_id]
    if len(ruleset.rules) == original_count:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")

    save_rules(ruleset)
    reload_rules(contract)
    return {"status": "deleted", "rule_id": rule_id}
