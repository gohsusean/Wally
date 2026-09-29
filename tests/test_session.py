"""Session store tests."""

from pathlib import Path

from wally.models.messages import Message, Role
from wally.session.store import SessionStore


def test_create_and_resume_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    session = store.create_session()
    assert session.id

    store.add_message(session.id, Message(role=Role.USER, content="Hello"))
    loaded = store.get_session(session.id)
    assert loaded is not None
    assert len(loaded.messages) == 1
    assert loaded.messages[0].content == "Hello"


def test_resume_or_create_new(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    session = store.resume_or_create(None)
    assert session.id


def test_resume_or_create_existing(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    created = store.create_session()
    resumed = store.resume_or_create(created.id)
    assert resumed.id == created.id
