"""Code-owned finite metadata contracts; never a generic property writer."""

EDIT_VALUES = {
    "amount_policy": frozenset({"fixed_contract", "source_defined"}),
    "frequency": frozenset(
        {"once", "monthly", "quarterly", "semiannual", "annual", "irregular", "statement_driven"}
    ),
    # First assessment-stream initialization only. Taxonomy text is an exact
    # reviewed convention, not a claim that the domain has a closed taxonomy.
    "namespace": frozenset({"assessment_account"}),
    "responsibility": frozenset({"owner"}),
    "charge_key": frozenset({"assessment_tax"}),
    "obligation_type": frozenset({"property_tax"}),
    "currency": frozenset({"MYR"}),
    "evidence_mode": frozenset({"issued_bill"}),
    "due_mode": frozenset({"from_document"}),
}
TEXT_FIELDS = frozenset({"namespace", "charge_key", "obligation_type"})


def edit_type(field: str) -> str:
    return "rich_text" if field in TEXT_FIELDS else "select"
