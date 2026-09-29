"""Pytest fixtures."""

from pathlib import Path

import pytest

from wally.config.loader import find_project_root


@pytest.fixture
def project_root() -> Path:
    return find_project_root(Path(__file__).resolve().parents[1])
