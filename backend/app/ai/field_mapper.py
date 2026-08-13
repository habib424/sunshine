from app.ai.client import get_client

MAPPING_PROMPT = """You are an ERP financial data migration expert. Map source columns to target columns.

Source columns: {source_columns}

Target schema columns: {target_columns}

Respond with ONLY a JSON object mapping source column names to target column names:
{{"<source_col>": "<target_col>", ...}}

Only include columns you are confident about. Use null for source columns that don't map to any target."""


def suggest_mappings_ai(source_columns: list[str], target_columns: list[str]) -> dict:
    client = get_client()

    prompt = MAPPING_PROMPT.format(
        source_columns=", ".join(source_columns),
        target_columns=", ".join(target_columns),
    )

    message = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=512,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )

    import json
    try:
        return json.loads(message.content[0].text)
    except (json.JSONDecodeError, IndexError):
        return {}
