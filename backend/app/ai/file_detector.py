from app.ai.client import get_client

DETECTION_PROMPT = """You are an ERP financial data migration expert. Given the following column headers and sample data from a file, identify the file type.

Possible file types:
- trial_balance: Trial Balance report (accounts with debit/credit balances)
- gl_history: General Ledger transaction history
- open_ap: Open Accounts Payable (outstanding vendor invoices)
- open_ar: Open Accounts Receivable (outstanding customer invoices)
- chart_of_accounts: Chart of Accounts master data
- vendors: Vendor/Supplier master data
- customers: Customer master data

Column headers: {headers}

Sample rows (first 5):
{sample_rows}

Filename: {filename}

Respond with ONLY a JSON object:
{{"file_type": "<type>", "confidence": <0.0-1.0>, "reasoning": "<brief explanation>"}}"""


def detect_file_type_ai(headers: list[str], sample_rows: list[list], filename: str) -> dict:
    client = get_client()

    prompt = DETECTION_PROMPT.format(
        headers=", ".join(headers),
        sample_rows="\n".join(str(row) for row in sample_rows[:5]),
        filename=filename,
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    import json
    try:
        result = json.loads(message.content[0].text)
        return result
    except (json.JSONDecodeError, IndexError):
        return {"file_type": None, "confidence": 0.0, "reasoning": "Failed to parse AI response"}
