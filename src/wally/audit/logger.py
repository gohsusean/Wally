"""Append-only JSON Lines audit log."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass
class AuditEvent:
    event_type: str
    session_id: str
    outcome: str
    action_type: str | None = None
    provider: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    approval_status: str | None = None
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
    prompt_version: str | None = None
    dry_run: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class AuditLogger:
    """Write structured events to daily JSONL files."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)

    def _path_for_today(self) -> Path:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        return self._directory / f"{day}.jsonl"

    def log(self, event: AuditEvent) -> None:
        path = self._path_for_today()
        line = json.dumps(asdict(event), ensure_ascii=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def log_simple(
        self,
        *,
        event_type: str,
        session_id: str,
        outcome: str,
        **kwargs: Any,
    ) -> None:
        self.log(
            AuditEvent(
                event_type=event_type,
                session_id=session_id,
                outcome=outcome,
                **kwargs,
            )
        )
