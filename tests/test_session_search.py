"""Session FTS backfill tests."""

from pathlib import Path

from wally.models.messages import Message, Role
from wally.session.store import SessionStore


def test_fts_backfill_on_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    store = SessionStore(db_path)
    session = store.create_session()
    store.add_message(session.id, Message(role=Role.USER, content="wifi password is secret"))

    store2 = SessionStore(db_path)
    hits = store2.search_messages("wifi")
    assert len(hits) == 1
