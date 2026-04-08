"""
Conversational transformation planner.

Maintains a chat session about a specific file + goal.
The AI proposes transformation steps, the user refines via conversation,
then the AI generates and executes a Python script.
"""

import json
import re
import traceback
import uuid
from pathlib import Path

import pandas as pd

from app.ai.client import get_client
from app.engine.target_schemas import TARGET_SCHEMAS

# In-memory session store: session_id -> {messages, file_path, file_structure, plan}
_sessions: dict[str, dict] = {}

SYSTEM_PROMPT = """You are Sunshine, an expert ERP financial data migration assistant. You help users transform source ERP files into Light.inc upload format.

You are working with a specific uploaded file. Here is its structure:

{file_structure}

The target format is the Light.inc journal entry upload with these columns:
{target_columns}

{validation_rules}

Your job:
1. Analyze the file and propose a clear, numbered transformation plan
2. Listen to the user's feedback and adjust the plan
3. When the user approves, generate a Python script to execute the transformation

RULES FOR THE PLAN:
- Write the plan as numbered steps in plain English
- Be specific about which sheets, columns, and values you'll use
- Include any assumptions you're making
- Always end by asking if the user wants to adjust anything

RULES FOR THE SCRIPT (when user says "run it", "go", "execute", "looks good", etc.):
- Generate a complete, self-contained Python function
- The function signature MUST be: def transform(file_path: str) -> "pd.DataFrame"
- Import pandas inside the function
- Read the Excel file using pd.read_excel with engine="openpyxl"
- Return a DataFrame with the exact target columns
- Handle edge cases (NaN values, type conversion, missing data)
- Do NOT print anything, just return the DataFrame
- CRITICAL: The output MUST satisfy the validation rules listed above. Do NOT drop rows. Do NOT change numeric totals. Preserve every row from the source.

When generating the script, wrap it in a ```python code block.

IMPORTANT: Only generate the script when the user explicitly approves the plan. Until then, just discuss and refine the plan."""


def _get_validation_rules_context() -> str:
    """Build a summary of active validation rules for the AI system prompt."""
    try:
        from app.engine.contracts.loader import load_rules
        ruleset = load_rules("journal_entry")
        lines = [
            "ACTIVE VALIDATION RULES (the output will be checked against these):"
        ]
        for rule in ruleset.enabled_rules:
            lines.append(
                f"  - [{rule['severity'].upper()}] {rule['issue_code']}: "
                f"{rule['description']} (scope: {rule['scope']})"
            )
        return "\n".join(lines)
    except Exception:
        return ""


def _read_file_structure(file_path: Path) -> dict:
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, nrows=15, engine="openpyxl")
    structure = {}
    for name, df in sheets.items():
        rows = []
        for i, row in df.iterrows():
            rows.append([str(v) if pd.notna(v) else None for v in row.tolist()])
            if i >= 8:
                break
        structure[name] = {"rows": rows, "num_cols": len(df.columns), "total_rows": len(df)}
    return structure


def create_session(file_path: Path, goal: str = "journal_entry") -> dict:
    """Create a new chat session for a file."""
    session_id = str(uuid.uuid4())
    file_structure = _read_file_structure(file_path)
    target = TARGET_SCHEMAS.get("light_journal_entry", {})

    system = SYSTEM_PROMPT.format(
        file_structure=json.dumps(file_structure, indent=2),
        target_columns=json.dumps(target.get("columns", []), indent=2),
        validation_rules=_get_validation_rules_context(),
    )

    _sessions[session_id] = {
        "file_path": str(file_path),
        "file_structure": file_structure,
        "messages": [],
        "system": system,
        "plan": None,
        "script": None,
        "goal": goal,
    }

    return {"session_id": session_id, "sheet_names": list(file_structure.keys())}


def chat(session_id: str, user_message: str) -> dict:
    """Send a message in the chat session and get AI response."""
    if session_id not in _sessions:
        raise ValueError(f"Session '{session_id}' not found")

    session = _sessions[session_id]
    client = get_client()

    # Add user message
    session["messages"].append({"role": "user", "content": user_message})

    # Call Claude
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=session["system"],
        messages=session["messages"],
    )

    assistant_text = response.content[0].text
    session["messages"].append({"role": "assistant", "content": assistant_text})

    # Check if response contains a Python script
    script = _extract_script(assistant_text)
    if script:
        session["script"] = script

    return {
        "message": assistant_text,
        "has_script": script is not None,
        "session_id": session_id,
    }


def execute_script(session_id: str, output_path: Path) -> dict:
    """Execute the generated script and save the output."""
    if session_id not in _sessions:
        raise ValueError(f"Session '{session_id}' not found")

    session = _sessions[session_id]
    script = session.get("script")
    if not script:
        raise ValueError("No script generated yet. Ask the AI to generate one first.")

    file_path = session["file_path"]

    # Execute the script in a sandboxed namespace
    namespace = {}
    try:
        exec(script, namespace)
    except Exception as e:
        return {"success": False, "error": f"Script compilation error: {str(e)}", "traceback": traceback.format_exc()}

    transform_fn = namespace.get("transform")
    if not transform_fn:
        return {"success": False, "error": "Script does not define a 'transform' function"}

    try:
        df = transform_fn(file_path)
    except Exception as e:
        return {"success": False, "error": f"Script execution error: {str(e)}", "traceback": traceback.format_exc()}

    if not isinstance(df, pd.DataFrame):
        return {"success": False, "error": f"Script returned {type(df).__name__}, expected DataFrame"}

    # Save output — preserve the original workbook in full and append the
    # transformed result as a new sheet so the user can audit the migration.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    from app.engine.pipeline import run_export

    source_path = Path(file_path)
    # Derive topic from the output filename, stripping the source filename prefix.
    topic = output_path.stem
    src_stem = source_path.stem
    if topic.startswith(src_stem):
        topic = topic[len(src_stem):].lstrip("_- ") or src_stem
    if not topic or topic == src_stem:
        topic = session.get("goal") or "migrated"

    run_export(
        df,
        output_path,
        source_file_path=source_path,
        working_sheet_topic=topic,
    )

    return {
        "success": True,
        "rows": len(df),
        "columns": list(df.columns),
        "preview": {
            "headers": [str(c) for c in df.columns],
            "rows": df.head(10).fillna("").values.tolist(),
        },
    }


def get_session(session_id: str) -> dict | None:
    session = _sessions.get(session_id)
    if not session:
        return None
    return {
        "session_id": session_id,
        "message_count": len(session["messages"]),
        "has_script": session.get("script") is not None,
        "goal": session["goal"],
    }


def _extract_script(text: str) -> str | None:
    """Extract Python code block from AI response."""
    pattern = r"```python\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None
