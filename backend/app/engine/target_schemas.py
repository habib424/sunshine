"""
Target output schema definitions.
Each schema describes the expected column structure of an output file type.
"""

TARGET_SCHEMAS = {
    "light_journal_entry": {
        "description": "Light.inc journal entry upload format",
        "columns": [
            "Company",
            "entry id (required)",
            "date (required)",
            "currency (required)",
            "debit (required)",
            "credit (required)",
            "tax amount",
            "account code (required)",
            "tax code",
            "entry description (required)",
            "line description (required)",
            "business partner name (required)",
            "business partner id",
            "local currency rate",
            "group currency rate",
            "accounting release template name",
            "accounting release start date",
            "accounting release end date",
        ],
    },
    "light_journal_entry_v2": {
        "description": "Light journal entry upload format with release accounting fields",
        "columns": [
            "Entity",
            "Document Number",
            "Currency",
            "Posting Date",
            "Ledger",
            "Business Partner",
            "Entry Description",
            "Account",
            "Debit",
            "Credit",
            "Line Description",
            "Tax Code",
            "Release Template",
            "Release Start Date",
            "Release End Date",
            "Departments (line)",
        ],
    },
    "light_open_ap_upload": {
        "description": "Light AP upload format for open accounts payable",
        "columns": [
            "Entity",
            "Document Number",
            "Currency",
            "Posting Date",
            "Ledger",
            "Business Partner",
            "Entry Description",
            "Account",
            "Debit",
            "Credit",
            "Line Description",
            "Tax Code",
            "Release Template",
            "Release Start Date",
            "Release End Date",
            "Departments (line)",
        ],
    },
    "light_bills_ap_upload": {
        "description": (
            "Light Bills (AP) upload format — one bill document per outstanding "
            "vendor invoice, rather than debit/credit journal lines"
        ),
        "columns": [
            "Vendor",
            "Vendor ID",
            "Entity",
            "Invoice Number",
            "Issue Date",
            "Due Date",
            "Currency",
            "Pay From",
            "Payment Date",
            "Description",
            "Lines With Tax",
            "Invoice Currency Amount",
            "Local Currency Amount",
            "Line Description",
            "Line Amount",
            "Tax Code",
            "Account",
            "Line Tax Amount",
            "Amortization Template",
            "Amortization Start Date",
            "Amortization End Date",
        ],
    },
    "light_fx_adjustment": {
        "description": "Light JE upload for FX currency adjustments with FX rate override",
        "columns": [
            "Entity",
            "Document Number",
            "Currency",
            "Posting Date",
            "Ledger",
            "Business Partner",
            "Entry Description",
            "Light account",
            "Debit",
            "Credit",
            "Line Description",
            "Tax Code",
            "Local Currency FX Rate",
            "Group Currency FX Rate",
            "Release Template",
            "Release Start Date",
            "Release End Date",
            "Departments (line)",
        ],
    },
    "reconciliation_report": {
        "description": "Reconciliation report comparing JE to trial balance",
        "columns": [
            "account_code",
            "account_name",
            "je_net_balance",
            "tb_balance",
            "difference",
            "match_status",
        ],
    },
}


def get_target_schema(name: str) -> dict:
    if name not in TARGET_SCHEMAS:
        raise KeyError(f"Unknown target schema: '{name}'. Available: {list(TARGET_SCHEMAS.keys())}")
    return TARGET_SCHEMAS[name]


def list_target_schemas() -> dict:
    return {k: v["description"] for k, v in TARGET_SCHEMAS.items()}
