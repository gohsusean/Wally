"""Config loader tests."""

from pathlib import Path

import pytest

from wally.config.loader import load_instructions, load_settings
from wally.exceptions import ConfigurationError


def test_load_settings_macbook(project_root: Path) -> None:
    settings = load_settings(project_root=project_root, config_name="macbook")
    assert settings.environment == "development"
    assert settings.default_reasoning_profile == "balanced"
    assert settings.reasoning_profile_override is None
    assert settings.llm_model == "gpt-5"
    assert settings.web_enabled is True
    assert settings.web_adapter == "openai"
    assert settings.finance_enabled is True
    assert settings.finance_bills_role == "finance"
    assert settings.web_search_model == "gpt-5-mini"
    assert settings.system_prompt_path.is_file()
    assert settings.safety_prompt_path.is_file()


def test_reasoning_profile_override(project_root: Path) -> None:
    settings = load_settings(
        project_root=project_root, config_name="macbook", reasoning_profile="fast"
    )
    assert settings.reasoning_profile_override == "fast"
    assert settings.llm_model == "gpt-5"
    assert settings.reasoning_profiles["fast"].model == "gpt-5-mini"


def test_reasoning_profile_deep(project_root: Path) -> None:
    settings = load_settings(
        project_root=project_root, config_name="macbook", reasoning_profile="deep"
    )
    assert settings.reasoning_profile_override == "deep"
    assert settings.reasoning_profiles["deep"].model == "gpt-5-thinking"


def test_load_settings_missing_profile(project_root: Path) -> None:
    with pytest.raises(ConfigurationError, match="Config profile not found"):
        load_settings(project_root=project_root, config_name="nonexistent")


def test_load_instructions(project_root: Path) -> None:
    settings = load_settings(project_root=project_root, config_name="macbook")
    instructions = load_instructions(settings)
    assert "Wally" in instructions
    assert "Safety" in instructions or "approval" in instructions.lower()


def test_dry_run_from_env(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WALLY_DRY_RUN", "true")
    settings = load_settings(project_root=project_root, config_name="macbook")
    assert settings.dry_run is True


def test_load_openai_key_from_dotenv(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_path = project_root / ".env"
    original = env_path.read_text(encoding="utf-8") if env_path.is_file() else None
    env_path.write_text("OPENAI_API_KEY=sk-test-from-dotenv\n", encoding="utf-8")
    try:
        settings = load_settings(project_root=project_root, config_name="macbook")
        assert settings.openai_api_key == "sk-test-from-dotenv"
    finally:
        if original is None:
            env_path.unlink(missing_ok=True)
        else:
            env_path.write_text(original, encoding="utf-8")


def test_shell_env_overrides_dotenv(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-shell")
    env_path = project_root / ".env"
    original = env_path.read_text(encoding="utf-8") if env_path.is_file() else None
    env_path.write_text("OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    try:
        settings = load_settings(project_root=project_root, config_name="macbook")
        assert settings.openai_api_key == "sk-from-shell"
    finally:
        if original is None:
            env_path.unlink(missing_ok=True)
        else:
            env_path.write_text(original, encoding="utf-8")
