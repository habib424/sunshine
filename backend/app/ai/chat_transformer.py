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
from app.engine.bills_ap import (
    analyze_bills_ap_workbook,
    apply_user_message_to_analysis as apply_user_message_to_bills_ap_analysis,
    format_bills_ap_analysis_message,
    transform_open_ap_to_light_bills,
)
from app.engine.deferrals import (
    DEFERRAL_INTENTS,
    analyze_deferral_workbook,
    apply_user_message_to_analysis,
    format_deferral_analysis_message,
    intent_to_direction,
    transform_deferrals_to_light_je,
)
from app.engine.fx_adjustment import (
    FX_ADJUSTMENT_INTENTS,
    analyze_fx_workbook,
    apply_user_message_to_analysis as apply_user_message_to_fx_analysis,
    format_fx_analysis_message,
    transform_fx_adjustments,
)
from app.engine.open_ap import (
    OPEN_AP_INTENTS,
    analyze_open_ap_workbook,
    apply_user_message_to_analysis as apply_user_message_to_open_ap_analysis,
    format_open_ap_analysis_message,
    transform_open_ap_to_light_ap,
)
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


RECONCILE_SYSTEM_PROMPT = """You are Sunshine, an expert ERP financial data reconciliation assistant. You help users reconcile journal entry data against trial balance or general ledger extracts.

You are working with a specific uploaded workbook. Here is its structure:

{file_structure}

The target output is a reconciliation report with these columns:
{target_columns}

Your job:
1. Identify which sheet contains the journal entry data and which contains the trial balance / GL data
2. Propose a reconciliation plan: how to extract account codes from each sheet, aggregate JE amounts per account, and compare against TB balances
3. Listen to the user's feedback and adjust
4. When approved, generate a Python script

DOMAIN KNOWLEDGE FOR TRIAL BALANCE SHEETS:
- Account codes are often embedded in a description column (e.g., "  100592 Loans to related parties")
- Leading whitespace or indentation indicates sub-accounts vs. parent accounts
- The "Balance" column may use positive = debit, negative = credit, or vice versa
- Some TB exports have subtotal/header rows that should be excluded from matching
- Account codes may need to be extracted using regex (e.g., leading digits before a space)

RECONCILIATION LOGIC:
- For the JE sheet: compute net balance per account code as SUM(debit) - SUM(credit)
- For the TB sheet: extract account code and balance for each account row
- Join on account code and compute difference = je_net_balance - tb_balance
- Round all amounts to 2 decimal places before comparing
- Flag each row: "Matched" if |difference| < 0.01, "Difference" if both exist but don't match, "JE Only" or "TB Only" for unmatched accounts

RULES FOR THE PLAN:
- Write the plan as numbered steps in plain English
- Be specific about which sheets, columns, and parsing approach you'll use
- Always end by asking if the user wants to adjust anything

RULES FOR THE SCRIPT (when user says "run it", "go", "execute", "looks good", etc.):
- Generate a complete, self-contained Python function
- The function signature MUST be: def transform(file_path: str) -> "pd.DataFrame"
- Import pandas inside the function
- Read the Excel file using pd.read_excel with engine="openpyxl"
- Return a DataFrame with the reconciliation report columns
- Handle edge cases (NaN values, type conversion, missing data)
- Do NOT print anything, just return the DataFrame

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
    import openpyxl

    # Get true row counts per sheet (lightweight, metadata only).
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    actual_counts = {ws.title: ws.max_row or 0 for ws in wb.worksheets}
    wb.close()

    sheets = pd.read_excel(file_path, sheet_name=None, header=None, nrows=15, engine="openpyxl")
    structure = {}
    for name, df in sheets.items():
        rows = []
        for i, row in df.iterrows():
            rows.append([str(v) if pd.notna(v) else None for v in row.tolist()])
            if i >= 8:
                break
        structure[name] = {
            "rows": rows,
            "num_cols": len(df.columns),
            "total_rows": actual_counts.get(name, len(df)),
        }
    return structure


def create_session(file_path: Path, goal: str = "journal_entry", intent: str = "convert_to_light_je") -> dict:
    """Create a new chat session for a file."""
    session_id = str(uuid.uuid4())
    file_structure = _read_file_structure(file_path)

    if intent in DEFERRAL_INTENTS:
        direction = intent_to_direction(intent)
        analysis = analyze_deferral_workbook(file_path, direction)
        target = TARGET_SCHEMAS.get("light_journal_entry_v2", {})
        system = (
            "Deterministic deferral migration session. The assistant should "
            "use semantic role detection and structured facts, not generated code. "
            f"Target columns: {json.dumps(target.get('columns', []))}"
        )
    elif intent in OPEN_AP_INTENTS:
        open_ap_mode, analysis = _analyze_open_ap_auto(file_path)
        if open_ap_mode == "bills":
            target = TARGET_SCHEMAS.get("light_bills_ap_upload", {})
            system = (
                "Deterministic open AP upload session (Light bills output). The "
                "assistant should use detected aging-report roles, per-vendor "
                "payment netting and structured facts, not generated code. "
                f"Target columns: {json.dumps(target.get('columns', []))}"
            )
        else:
            target = TARGET_SCHEMAS.get("light_open_ap_upload", {})
            system = (
                "Deterministic open AP upload session (opening JE output). The "
                "assistant should use detected AP invoice roles and the Light "
                "Posting reference lines, not generated code. "
                f"Target columns: {json.dumps(target.get('columns', []))}"
            )
    elif intent in FX_ADJUSTMENT_INTENTS:
        analysis = analyze_fx_workbook(file_path)
        target = TARGET_SCHEMAS.get("light_fx_adjustment", {})
        system = (
            "Deterministic FX currency adjustment session. The assistant should "
            "use detected bank balance roles and structured facts, not generated "
            "code. "
            f"Target columns: {json.dumps(target.get('columns', []))}"
        )
    elif intent == "reconcile_je_to_gl":
        target = TARGET_SCHEMAS.get("reconciliation_report", {})
        system = RECONCILE_SYSTEM_PROMPT.format(
            file_structure=json.dumps(file_structure, indent=2),
            target_columns=json.dumps(target.get("columns", []), indent=2),
        )
    else:
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
        "intent": intent,
    }
    if intent in DEFERRAL_INTENTS:
        _sessions[session_id]["deferral_analysis"] = analysis
    if intent in OPEN_AP_INTENTS:
        _sessions[session_id]["open_ap_mode"] = open_ap_mode
        _sessions[session_id]["open_ap_analysis"] = analysis
    if intent in FX_ADJUSTMENT_INTENTS:
        _sessions[session_id]["fx_analysis"] = analysis

    return {"session_id": session_id, "sheet_names": list(file_structure.keys())}


def chat(session_id: str, user_message: str) -> dict:
    """Send a message in the chat session and get AI response."""
    if session_id not in _sessions:
        raise ValueError(f"Session '{session_id}' not found")

    session = _sessions[session_id]

    if session.get("intent") in DEFERRAL_INTENTS:
        return _chat_deferral(session_id, user_message)
    if session.get("intent") in OPEN_AP_INTENTS:
        return _chat_open_ap(session_id, user_message)
    if session.get("intent") in FX_ADJUSTMENT_INTENTS:
        return _chat_fx(session_id, user_message)

    client = get_client()

    # Add user message
    session["messages"].append({"role": "user", "content": user_message})

    # Call Claude
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=8192,
        system=session["system"],
        messages=session["messages"],
    )

    # Sonnet 5 thinks by default, so content may start with thinking blocks;
    # collect the text blocks and echo the full block list back in history.
    assistant_text = "".join(b.text for b in response.content if b.type == "text")
    session["messages"].append({"role": "assistant", "content": response.content})

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

    if session.get("intent") in DEFERRAL_INTENTS:
        return _execute_deferral(session, output_path)
    if session.get("intent") in OPEN_AP_INTENTS:
        return _execute_open_ap(session, output_path)
    if session.get("intent") in FX_ADJUSTMENT_INTENTS:
        return _execute_fx(session, output_path)

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
    has_script = session.get("script") is not None
    if session.get("intent") in DEFERRAL_INTENTS:
        analysis = session.get("deferral_analysis")
        has_script = bool(analysis and analysis.ready)
    if session.get("intent") in OPEN_AP_INTENTS:
        analysis = session.get("open_ap_analysis")
        has_script = bool(analysis and analysis.ready)
    if session.get("intent") in FX_ADJUSTMENT_INTENTS:
        analysis = session.get("fx_analysis")
        has_script = bool(analysis and analysis.ready)
    return {
        "session_id": session_id,
        "message_count": len(session["messages"]),
        "has_script": has_script,
        "goal": session["goal"],
    }


def _extract_script(text: str) -> str | None:
    """Extract Python code block from AI response."""
    pattern = r"```python\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _chat_fx(session_id: str, user_message: str) -> dict:
    session = _sessions[session_id]
    file_path = Path(session["file_path"])

    session["messages"].append({"role": "user", "content": user_message})
    analysis = session.get("fx_analysis")
    if analysis is None:
        analysis = analyze_fx_workbook(file_path)

    # The initial API call supplies a long task instruction. Treat later
    # messages as corrections/facts such as "clearing account is 900300".
    if len(session["messages"]) > 1:
        analysis = apply_user_message_to_fx_analysis(analysis, user_message, file_path)

    session["fx_analysis"] = analysis
    assistant_text = format_fx_analysis_message(analysis)
    session["messages"].append({"role": "assistant", "content": assistant_text})

    return {
        "message": assistant_text,
        "has_script": analysis.ready,
        "session_id": session_id,
    }


def _execute_fx(session: dict, output_path: Path) -> dict:
    analysis = session.get("fx_analysis")
    if analysis is None:
        file_path = Path(session["file_path"])
        analysis = analyze_fx_workbook(file_path)
        session["fx_analysis"] = analysis
    if not analysis.ready:
        missing = analysis.questions or ["The FX adjustment is missing required facts."]
        return {"success": False, "error": " ".join(missing)}

    df = transform_fx_adjustments(Path(session["file_path"]), analysis)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    from app.engine.pipeline import run_export

    source_path = Path(session["file_path"])
    topic = output_path.stem
    src_stem = source_path.stem
    if topic.startswith(src_stem):
        topic = topic[len(src_stem):].lstrip("_- ") or src_stem

    run_export(
        df,
        output_path,
        source_file_path=source_path,
        working_sheet_topic=topic or "FX_Adjustment",
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


def _chat_deferral(session_id: str, user_message: str) -> dict:
    session = _sessions[session_id]
    file_path = Path(session["file_path"])

    session["messages"].append({"role": "user", "content": user_message})
    analysis = session.get("deferral_analysis")
    if analysis is None:
        analysis = analyze_deferral_workbook(file_path, intent_to_direction(session["intent"]))

    # The initial API call sends a long system-ish user message. Avoid parsing
    # that as user corrections; for later messages, update facts from plain
    # English snippets like "currency is EUR" or "posting date 2026-03-01".
    if len(session["messages"]) > 1:
        analysis = apply_user_message_to_analysis(analysis, user_message, file_path)

    session["deferral_analysis"] = analysis
    assistant_text = format_deferral_analysis_message(analysis)
    session["messages"].append({"role": "assistant", "content": assistant_text})

    return {
        "message": assistant_text,
        "has_script": analysis.ready,
        "session_id": session_id,
    }


def _execute_deferral(session: dict, output_path: Path) -> dict:
    analysis = session.get("deferral_analysis")
    if analysis is None:
        file_path = Path(session["file_path"])
        analysis = analyze_deferral_workbook(file_path, intent_to_direction(session["intent"]))
        session["deferral_analysis"] = analysis
    if not analysis.ready:
        missing = analysis.questions or ["The deferral migration is missing required facts."]
        return {"success": False, "error": " ".join(missing)}

    df = transform_deferrals_to_light_je(Path(session["file_path"]), analysis)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    from app.engine.pipeline import run_export

    source_path = Path(session["file_path"])
    topic = output_path.stem
    src_stem = source_path.stem
    if topic.startswith(src_stem):
        topic = topic[len(src_stem):].lstrip("_- ") or src_stem

    run_export(
        df,
        output_path,
        source_file_path=source_path,
        working_sheet_topic=topic or "Light_JE_Upload",
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


def _analyze_open_ap_auto(file_path: Path) -> tuple[str, object]:
    """One open-AP intent, two deterministic outputs.

    A workbook carrying a Light Posting reference sheet wants its posting
    lines reused as opening JEs. Anything else — aging reports, flat AP
    ledgers — becomes Light bill documents, which need no assumed AP or
    clearing accounts.
    """
    je_analysis = analyze_open_ap_workbook(file_path)
    if je_analysis.reference_sheet:
        return "je", je_analysis
    return "bills", analyze_bills_ap_workbook(file_path)


def _chat_open_ap(session_id: str, user_message: str) -> dict:
    session = _sessions[session_id]
    file_path = Path(session["file_path"])
    mode = session.get("open_ap_mode", "bills")

    session["messages"].append({"role": "user", "content": user_message})
    analysis = session.get("open_ap_analysis")
    if analysis is None:
        mode, analysis = _analyze_open_ap_auto(file_path)
        session["open_ap_mode"] = mode

    # The initial API call supplies a long task instruction. Treat later
    # messages as corrections/facts such as "entity is causaLens".
    if len(session["messages"]) > 1:
        if mode == "bills":
            analysis = apply_user_message_to_bills_ap_analysis(analysis, user_message, file_path)
        else:
            analysis = apply_user_message_to_open_ap_analysis(analysis, user_message, file_path)

    session["open_ap_analysis"] = analysis
    assistant_text = (
        format_bills_ap_analysis_message(analysis)
        if mode == "bills"
        else format_open_ap_analysis_message(analysis)
    )
    session["messages"].append({"role": "assistant", "content": assistant_text})

    return {
        "message": assistant_text,
        "has_script": analysis.ready,
        "session_id": session_id,
    }


def _execute_open_ap(session: dict, output_path: Path) -> dict:
    file_path = Path(session["file_path"])
    mode = session.get("open_ap_mode", "bills")
    analysis = session.get("open_ap_analysis")
    if analysis is None:
        mode, analysis = _analyze_open_ap_auto(file_path)
        session["open_ap_mode"] = mode
        session["open_ap_analysis"] = analysis
    if not analysis.ready:
        missing = analysis.questions or ["The open AP upload is missing required facts."]
        return {"success": False, "error": " ".join(missing)}

    if mode == "bills":
        df = transform_open_ap_to_light_bills(file_path, analysis)
    else:
        df = transform_open_ap_to_light_ap(file_path, analysis)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    from app.engine.pipeline import run_export

    source_path = Path(session["file_path"])
    topic = output_path.stem
    src_stem = source_path.stem
    if topic.startswith(src_stem):
        topic = topic[len(src_stem):].lstrip("_- ") or src_stem

    run_export(
        df,
        output_path,
        source_file_path=source_path,
        working_sheet_topic=topic or "Light_AP_Upload",
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
