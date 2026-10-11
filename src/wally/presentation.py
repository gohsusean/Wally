"""Human-facing conventions. Never use these strings for authorization or storage."""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

USER_TIMEZONE = ZoneInfo("Asia/Kuala_Lumpur")
_LABELS = {
    "once": "Once",
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "annual": "Annual",
    "irregular": "Irregular",
    "amount_policy": "Amount policy",
    "frequency": "Frequency",
    "fixed_contract": "Fixed contract",
    "source_defined": "From source",
    "semiannual": "Every six months",
    "statement_driven": "When a statement arrives",
    "namespace": "Identifier namespace",
    "responsibility": "Responsibility",
    "charge_key": "Charge key",
    "obligation_type": "Obligation type",
    "currency": "Currency",
    "evidence_mode": "Evidence mode",
    "due_mode": "Due mode",
    "assessment_account": "Assessment account",
    "owner": "Owner",
    "assessment_tax": "Assessment tax",
    "property_tax": "Property tax",
    "issued_bill": "Issued bill",
    "from_document": "From document",
}


class TelegramHTML(str):
    """Trusted renderer output. Plain messages never opt into HTML parsing."""


def human_label(value: str | None) -> str:
    if value is None:
        return "Missing"
    return _LABELS.get(value, value)


def human_time(
    value: datetime | str, *, now: datetime | None = None, timezone: ZoneInfo = USER_TIMEZONE
) -> str:
    """Default to Malaysian time without a label; label explicitly requested other zones."""
    instant = datetime.fromisoformat(value) if isinstance(value, str) else value
    if instant.tzinfo is None or (now is not None and now.tzinfo is None):
        raise ValueError("User-facing time requires an aware instant.")
    local = instant.astimezone(timezone)
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"
    if timezone.key not in {"Asia/Kuala_Lumpur", "Asia/Kuching"}:
        clock += f" {local.tzname()}"
    if now is not None:
        days = (local.date() - now.astimezone(timezone).date()).days
        if days in (0, 1):
            return f"{'today' if days == 0 else 'tomorrow'} at {clock}"
    return f"{local.day} {local:%b %Y}, {clock}"


def change_lines(changes: list[dict], *, telegram_html=False, unchanged=False) -> list[str]:
    lines = []
    for change in changes:
        label = human_label(change["field"])
        before, after = human_label(change["before"]), human_label(change["after"])
        if telegram_html:
            label, before, after = escape(label), escape(before), escape(after)
            after = f"<b>{after}</b>"
        if unchanged:
            if telegram_html:
                before = f"<b>{before}</b>"
            lines.append(f"{label} remains {before}")
        else:
            lines += [label, f"{before} → {after}"]
    return lines
