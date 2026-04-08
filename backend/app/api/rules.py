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

import json

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


class GenerateRequest(BaseModel):
    contract: str
    description: str


@router.post("/generate")
async def generate_rule_endpoint(body: GenerateRequest):
    """Use AI to generate a rule definition from a plain-language description."""
    try:
        ruleset = load_rules(body.contract)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No rules for contract '{body.contract}'")

    check_types = list_check_types()
    existing_ids = [r["id"] for r in ruleset.rules]
    existing_codes = [r["issue_code"] for r in ruleset.rules]

    prompt = _build_generate_prompt(
        body.description,
        body.contract,
        ruleset.canonical_columns,
        check_types,
        existing_ids,
        existing_codes,
    )

    try:
        from app.ai.client import get_client
        client = get_client()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        rule = _extract_json(text)
        if not rule:
            raise HTTPException(status_code=422, detail="AI did not produce a valid rule definition")
        explanation = text.split("```")[0].strip() if "```" in text else ""
        return {"rule": rule, "explanation": explanation}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {str(e)}")


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



# -- Helper functions for AI rule generation --


def _build_generate_prompt(
    description: str,
    contract: str,
    canonical_columns: dict,
    check_types: dict,
    existing_ids: list,
    existing_codes: list,
) -> str:
    types_desc = "\n".join(
        f"  - {name}: {info['description']}\n    params schema: {json.dumps(info['schema'])}"
        for name, info in check_types.items()
    )
    columns_desc = "\n".join(f"  - {col}: {desc}" for col, desc in canonical_columns.items())

    return f"""You are a validation rule generator for an ERP data migration tool.

The user wants to add a new validation rule described as:
"{description}"

Available check types (you MUST use one of these):
{types_desc}

Available canonical columns in the data:
{columns_desc}

Existing rule IDs (do NOT reuse): {existing_ids}
Existing issue codes (do NOT reuse): {existing_codes}

Generate a rule definition as a JSON object with these fields:
- id: kebab-case identifier (e.g., "max-entry-lines")
- issue_code: uppercase with prefix (e.g., "JE-MAX-LINES")
- description: plain-language description of what the rule checks
- check_type: one of the available check types above
- severity: "error" or "warning"
- scope: "file", "entry", or "line"
- enabled: true
- phase: "structural", "line", or "group"
- params: parameters matching the check_type's schema

Respond with a brief explanation of what the rule does, then the JSON in a ```json code block. Nothing else."""


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from AI response text."""
    import re
    # Try ```json block first
    match = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try bare JSON object
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None
