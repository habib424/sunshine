"""
Declarative contracts for file types.

A contract is the authoritative definition of what a valid file of a given
type must look like. Validators check compliance with a contract and emit
issues keyed by stable issue codes. Those issue codes are the link between
validation, resolution, and the learned rules store.
"""

from app.engine.contracts.journal_entry import JOURNAL_ENTRY_CONTRACT

CONTRACTS = {
    "journal_entry": JOURNAL_ENTRY_CONTRACT,
}


def get_contract(name: str) -> dict:
    if name not in CONTRACTS:
        raise KeyError(
            f"Unknown contract '{name}'. Available: {list(CONTRACTS.keys())}"
        )
    return CONTRACTS[name]
