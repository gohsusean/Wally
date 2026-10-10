"""Human-facing conventions. Never use these strings for authorization or storage."""

from datetime import datetime
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
}


def human_label(value: str) -> str:
    return _LABELS.get(value, value)


def human_time(value: datetime | str, *, now: datetime | None = None) -> str:
    """Convert an aware instant to MYT; optional relative dates use the local day."""
    instant = datetime.fromisoformat(value) if isinstance(value, str) else value
    if instant.tzinfo is None or (now is not None and now.tzinfo is None):
        raise ValueError("User-facing time requires an aware instant.")
    local = instant.astimezone(USER_TIMEZONE)
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'} MYT"
    if now is not None:
        days = (local.date() - now.astimezone(USER_TIMEZONE).date()).days
        if days in (0, 1):
            return f"{'today' if days == 0 else 'tomorrow'} at {clock}"
    return f"{local.day} {local:%b %Y}, {clock}"


def change_lines(changes: list[dict]) -> list[str]:
    lines = []
    for change in changes:
        lines += [
            human_label(change["field"]),
            f"{human_label(change['before'])} → {human_label(change['after'])}",
        ]
    return lines
