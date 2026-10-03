"""Wally v0.1 scaffold smoke tests."""

import wally


def test_version() -> None:
    assert wally.__version__ == "0.17.0"


def test_docs_exist() -> None:
    """Core documentation must exist before implementation begins."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    required = [
        "docs/architecture.md",
        "docs/principles.md",
        "docs/roadmap.md",
        "docs/decisions.md",
        "docs/layout.md",
        "docs/chief-of-staff.md",
    ]
    for path in required:
        assert (root / path).is_file(), f"Missing required doc: {path}"
