"""Audit logger tests."""

import json
from pathlib import Path

from wally.audit.logger import AuditEvent, AuditLogger


def test_audit_log_writes_jsonl(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path)
    logger.log(
        AuditEvent(
            event_type="llm_request",
            session_id="session-1",
            outcome="success",
            provider="openai",
        )
    )

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    line = files[0].read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["event_type"] == "llm_request"
    assert event["session_id"] == "session-1"
