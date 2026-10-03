"""Load YAML configuration and environment secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

from wally.exceptions import ConfigurationError
from wally.models.actions import ActionClass
from wally.models.knowledge import KnowledgeClass
from wally.models.workflow import WorkflowDefinition, WorkflowParameter


@dataclass(frozen=True)
class NotionSchemaOverride:
    title_property: str | None = None
    content_property: str | None = None
    type_property: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class NotionDefaults:
    type_property: str = "Type of Knowledge Asset"


@dataclass(frozen=True)
class NotionPlatformConfig:
    """Platform-wide Notion adapter settings (not per-database classifications)."""

    defaults: NotionDefaults
    exclude_ids: tuple[str, ...]
    overrides: dict[str, NotionSchemaOverride]
    legacy_databases: tuple[NotionDatabaseConfig, ...]


@dataclass(frozen=True)
class ReasoningProfileDefinition:
    name: str
    provider: str
    model: str


@dataclass(frozen=True)
class ReasoningRoutingRules:
    fast_tools: frozenset[str]
    deep_tools: frozenset[str]
    fast_tasks: frozenset[str]


_DEFAULT_FAST_TOOLS = frozenset(
    {
        "conversation_search",
        "conversation_recent",
        "knowledge_retrieve",
        "knowledge_get",
        "workflow_list",
        "workflow_trigger",
        "communications_email_search",
        "communications_email_get",
        "communications_calendar_list",
        "communications_calendar_availability",
        "web_search",
        "web_fetch",
        "finance_bills_search",
        "finance_payment_workflows",
    }
)

_DEFAULT_FAST_TASKS = frozenset({"consolidation"})


@dataclass(frozen=True)
class NotionDatabaseConfig:
    name: str
    id: str
    role: str
    readable: bool
    writable: bool
    knowledge_class: KnowledgeClass = KnowledgeClass.OPERATIONAL
    title_property: str = "Name"
    content_property: str | None = None
    type_property: str | None = None


@dataclass(frozen=True)
class Settings:
    project_root: Path
    environment: str
    audit_directory: Path
    session_database: Path
    knowledge_registry_database: Path
    dry_run: bool
    require_approval: tuple[str, ...]
    llm_adapter: str
    llm_model: str
    reasoning_profiles: dict[str, ReasoningProfileDefinition]
    default_reasoning_profile: str
    reasoning_profile_override: str | None
    reasoning_routing: ReasoningRoutingRules
    knowledge_enabled: bool
    knowledge_adapter: str
    system_prompt_path: Path
    safety_prompt_path: Path
    knowledge_prompt_path: Path | None
    workflow_enabled: bool
    workflow_adapter: str
    workflow_prompt_path: Path | None
    communications_enabled: bool
    communications_adapter: str
    communications_prompt_path: Path | None
    google_calendar_id: str
    workflows: tuple[WorkflowDefinition, ...]
    n8n_webhook_base_url: str | None
    openai_api_key: str | None
    notion_api_key: str | None
    google_client_id: str | None
    google_client_secret: str | None
    google_refresh_token: str | None
    conversation_enabled: bool
    conversation_max_context_messages: int
    conversation_consolidate_after: int
    conversation_keep_recent: int
    conversation_search_limit: int
    conversation_prompt_path: Path | None
    web_enabled: bool
    web_adapter: str
    web_prompt_path: Path | None
    web_search_model: str
    web_search_context_size: str
    web_fetch_max_bytes: int
    finance_enabled: bool
    finance_adapter: str
    finance_prompt_path: Path | None
    finance_bills_role: str
    secrets_enabled: bool
    secrets_adapter: str
    browser_enabled: bool
    browser_adapter: str
    browser_headless: bool
    browser_session_timeout_seconds: float
    ops_enabled: bool
    ops_database: Path
    ops_calendar_horizon_days: int
    ops_preparation_hours: int
    ops_email_lookback_days: int
    ops_timezone: str | None
    notion: NotionPlatformConfig
    log_level: str
    telegram_bot_token_ref: str
    telegram_owner_user_id: str


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from start (or cwd) to find the project root (contains pyproject.toml)."""
    current = (start or Path.cwd()).resolve()
    for path in [current, *current.parents]:
        if (path / "pyproject.toml").is_file():
            return path
    raise ConfigurationError("Could not find project root (no pyproject.toml found)")


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_prompt(root: Path, path: Path) -> str:
    if not path.is_file():
        raise ConfigurationError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def _parse_knowledge_class(value: str | None) -> KnowledgeClass:
    if not value:
        return KnowledgeClass.OPERATIONAL
    try:
        return KnowledgeClass(value.lower())
    except ValueError:
        return KnowledgeClass.OPERATIONAL


def _normalize_id(value: str) -> str:
    return value.replace("-", "")


def load_notion_config(root: Path) -> NotionPlatformConfig:
    notion_path = root / "config" / "notion.yaml"
    if not notion_path.is_file():
        return NotionPlatformConfig(
            defaults=NotionDefaults(),
            exclude_ids=(),
            overrides={},
            legacy_databases=(),
        )

    with notion_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    defaults_raw = raw.get("defaults") or {}
    defaults = NotionDefaults(
        type_property=defaults_raw.get("type_property", "Type of Knowledge Asset"),
    )

    exclude_raw = raw.get("exclude") or []
    exclude_ids = tuple(_normalize_id(str(item)) for item in exclude_raw)

    overrides: dict[str, NotionSchemaOverride] = {}
    for db_id, config in (raw.get("overrides") or {}).items():
        if not config:
            continue
        overrides[_normalize_id(str(db_id))] = NotionSchemaOverride(
            title_property=config.get("title_property"),
            content_property=config.get("content_property"),
            type_property=config.get("type_property"),
            role=config.get("role"),
        )

    legacy_databases: list[NotionDatabaseConfig] = []
    for name, config in (raw.get("databases") or {}).items():
        if not config or not config.get("id"):
            continue
        legacy_databases.append(
            NotionDatabaseConfig(
                name=name,
                id=config["id"],
                role=config.get("role", "general"),
                readable=bool(config.get("readable", True)),
                writable=bool(config.get("writable", True)),
                knowledge_class=_parse_knowledge_class(config.get("knowledge_class")),
                title_property=config.get("title_property", "Name"),
                content_property=config.get("content_property"),
                type_property=config.get("type_property"),
            )
        )

    return NotionPlatformConfig(
        defaults=defaults,
        exclude_ids=exclude_ids,
        overrides=overrides,
        legacy_databases=tuple(legacy_databases),
    )


def _parse_action_class(value: str | None) -> ActionClass:
    if not value:
        return ActionClass.IRREVERSIBLE
    try:
        return ActionClass(value.lower())
    except ValueError:
        return ActionClass.IRREVERSIBLE


def load_workflows_config(root: Path) -> tuple[WorkflowDefinition, ...]:
    workflows_path = root / "config" / "workflows.yaml"
    if not workflows_path.is_file():
        return ()

    with workflows_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    definitions: list[WorkflowDefinition] = []
    for name, config in (raw.get("workflows") or {}).items():
        if not config:
            continue
        execution = str(config.get("execution", "n8n")).strip().lower()
        webhook_path = config.get("webhook_path")
        if execution != "browser" and not webhook_path:
            continue
        params: list[WorkflowParameter] = []
        for param_name, meta in (config.get("parameters") or {}).items():
            if not meta:
                continue
            params.append(
                WorkflowParameter(
                    name=param_name,
                    param_type=str(meta.get("type", "string")),
                    description=str(meta.get("description", "")),
                    required=bool(meta.get("required", False)),
                )
            )
        definitions.append(
            WorkflowDefinition(
                name=name,
                description=str(config.get("description", "")),
                webhook_path=str(webhook_path or name),
                action_class=_parse_action_class(config.get("action_class")),
                parameters=tuple(params),
                capability_domain=_capability_field(config, "domain"),
                capability=_capability_field(config, "method"),
                aliases=tuple(str(alias) for alias in (config.get("aliases") or [])),
                execution_backend=str(config.get("execution", "n8n")).strip().lower(),
            )
        )
    return tuple(definitions)


def _capability_field(config: dict, key: str) -> str | None:
    capability = config.get("capability") or {}
    value = capability.get(key)
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _provider_section(providers: dict, key: str) -> dict:
    """Read provider config; accept legacy 'memory' key as alias for 'knowledge'."""
    if key in providers:
        return providers[key] or {}
    if key == "knowledge" and "memory" in providers:
        return providers["memory"] or {}
    return {}


def _load_dotenv(project_root: Path) -> None:
    """Load project .env into os.environ (existing shell vars take precedence)."""
    env_path = project_root / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)


def _parse_routing_rules(llm: dict) -> ReasoningRoutingRules:
    routing = llm.get("routing") or {}
    fast_tools = frozenset(routing.get("fast_tools") or _DEFAULT_FAST_TOOLS)
    deep_tools = frozenset(routing.get("deep_tools") or ())
    fast_tasks = frozenset(routing.get("fast_tasks") or _DEFAULT_FAST_TASKS)
    return ReasoningRoutingRules(
        fast_tools=fast_tools,
        deep_tools=deep_tools,
        fast_tasks=fast_tasks,
    )


def _parse_llm_config(
    llm: dict,
    *,
    profile_override: str | None = None,
) -> tuple[
    str,
    dict[str, ReasoningProfileDefinition],
    str,
    str | None,
    ReasoningRoutingRules,
    str,
]:
    """Load adapter, profiles, default profile, override, routing, and default model."""
    default_adapter = str(llm.get("adapter", "openai"))
    profiles_raw = llm.get("profiles") or {}
    routing = _parse_routing_rules(llm)
    override = profile_override or os.environ.get("WALLY_REASONING_PROFILE") or None

    if profiles_raw:
        default_profile = str(
            llm.get("default_profile") or llm.get("profile", "balanced")
        )
        profiles: dict[str, ReasoningProfileDefinition] = {}
        for name, profile_config in profiles_raw.items():
            if not isinstance(profile_config, dict):
                continue
            model = profile_config.get("model")
            if not model:
                raise ConfigurationError(
                    f"Reasoning profile '{name}' is missing model."
                )
            profiles[name] = ReasoningProfileDefinition(
                name=name,
                provider=str(profile_config.get("provider", default_adapter)),
                model=str(model),
            )

        if default_profile not in profiles:
            known = ", ".join(sorted(profiles))
            raise ConfigurationError(
                f"Unknown default reasoning profile '{default_profile}'. "
                f"Available profiles: {known}"
            )
        if override and override not in profiles:
            known = ", ".join(sorted(profiles))
            raise ConfigurationError(
                f"Unknown reasoning profile override '{override}'. "
                f"Available profiles: {known}"
            )
        default_model = profiles[default_profile].model
        return default_adapter, profiles, default_profile, override, routing, default_model

    if override:
        raise ConfigurationError(
            "WALLY_REASONING_PROFILE / --profile requires providers.llm.profiles in config."
        )

    model = str(llm.get("model", "gpt-4.1"))
    profiles = {
        "custom": ReasoningProfileDefinition(
            name="custom", provider=default_adapter, model=model
        )
    }
    return default_adapter, profiles, "custom", None, routing, model


def load_settings(
    *,
    project_root: Path | None = None,
    config_name: str | None = None,
    reasoning_profile: str | None = None,
) -> Settings:
    root = project_root or find_project_root()
    _load_dotenv(root)
    profile = config_name or os.environ.get("WALLY_CONFIG", "macbook")
    config_path = root / "config" / f"{profile}.yaml"
    if not config_path.is_file():
        raise ConfigurationError(f"Config profile not found: {config_path}")

    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    audit_dir = _resolve_path(root, raw.get("audit", {}).get("directory", "data/audit"))
    session_db = _resolve_path(
        root, raw.get("session", {}).get("database", "data/sessions.db")
    )
    safety = raw.get("safety", {})
    session_cfg = raw.get("session", {})
    conversation_cfg = session_cfg.get("conversation") or {}
    providers = raw.get("providers", {})
    llm = providers.get("llm", {})
    knowledge = _provider_section(providers, "knowledge")
    workflow = _provider_section(providers, "workflow")
    communications = _provider_section(providers, "communications")
    web = _provider_section(providers, "web")
    finance = _provider_section(providers, "finance")
    secrets = _provider_section(providers, "secrets")
    browser = _provider_section(providers, "browser")
    ops_cfg = raw.get("ops") or {}
    telegram_cfg = raw.get("telegram") or {}
    if not isinstance(telegram_cfg, dict):
        telegram_cfg = {}
    prompts = raw.get("prompts", {})

    dry_run_env = os.environ.get("WALLY_DRY_RUN", "").lower()
    if dry_run_env in ("1", "true", "yes"):
        dry_run = True
    elif dry_run_env in ("0", "false", "no"):
        dry_run = False
    else:
        dry_run = bool(safety.get("dry_run", False))

    knowledge_prompt = prompts.get("knowledge") or prompts.get("memory")
    knowledge_prompt_path = _resolve_path(root, knowledge_prompt) if knowledge_prompt else None
    workflow_prompt = prompts.get("workflow")
    workflow_prompt_path = _resolve_path(root, workflow_prompt) if workflow_prompt else None
    communications_prompt = prompts.get("communications")
    communications_prompt_path = (
        _resolve_path(root, communications_prompt) if communications_prompt else None
    )
    conversation_prompt = prompts.get("conversation")
    conversation_prompt_path = (
        _resolve_path(root, conversation_prompt) if conversation_prompt else None
    )
    web_prompt = prompts.get("web")
    web_prompt_path = _resolve_path(root, web_prompt) if web_prompt else None
    finance_prompt = prompts.get("finance")
    finance_prompt_path = (
        _resolve_path(root, finance_prompt) if finance_prompt else None
    )
    registry_db = _resolve_path(
        root, knowledge.get("registry", "data/knowledge_registry.db")
    )

    (
        llm_adapter,
        reasoning_profiles,
        default_reasoning_profile,
        reasoning_profile_override,
        reasoning_routing,
        llm_model,
    ) = _parse_llm_config(llm, profile_override=reasoning_profile)

    return Settings(
        project_root=root,
        environment=raw.get("environment", "development"),
        audit_directory=audit_dir,
        session_database=session_db,
        knowledge_registry_database=registry_db,
        dry_run=dry_run,
        require_approval=tuple(safety.get("require_approval", [])),
        llm_adapter=llm_adapter,
        llm_model=llm_model,
        reasoning_profiles=reasoning_profiles,
        default_reasoning_profile=default_reasoning_profile,
        reasoning_profile_override=reasoning_profile_override,
        reasoning_routing=reasoning_routing,
        knowledge_enabled=bool(knowledge.get("enabled", False)),
        knowledge_adapter=knowledge.get("adapter", "notion"),
        system_prompt_path=_resolve_path(root, prompts.get("system", "prompts/system/v1.md")),
        safety_prompt_path=_resolve_path(
            root, prompts.get("safety", "prompts/safety/v1.md")
        ),
        knowledge_prompt_path=knowledge_prompt_path,
        workflow_enabled=bool(workflow.get("enabled", False)),
        workflow_adapter=workflow.get("adapter", "n8n"),
        workflow_prompt_path=workflow_prompt_path,
        communications_enabled=bool(communications.get("enabled", False)),
        communications_adapter=communications.get("adapter", "google"),
        communications_prompt_path=communications_prompt_path,
        google_calendar_id=str(communications.get("calendar_id", "primary")),
        workflows=load_workflows_config(root),
        n8n_webhook_base_url=os.environ.get("N8N_WEBHOOK_BASE_URL"),
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        notion_api_key=os.environ.get("NOTION_API_KEY"),
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        google_refresh_token=os.environ.get("GOOGLE_REFRESH_TOKEN"),
        conversation_enabled=bool(conversation_cfg.get("enabled", True)),
        conversation_max_context_messages=int(
            conversation_cfg.get("max_context_messages", 50)
        ),
        conversation_consolidate_after=int(conversation_cfg.get("consolidate_after", 40)),
        conversation_keep_recent=int(conversation_cfg.get("keep_recent", 20)),
        conversation_search_limit=int(conversation_cfg.get("search_limit", 10)),
        conversation_prompt_path=conversation_prompt_path,
        web_enabled=bool(web.get("enabled", False)),
        web_adapter=web.get("adapter", "openai"),
        web_prompt_path=web_prompt_path,
        web_search_model=str(web.get("search_model", llm_model)),
        web_search_context_size=str(web.get("search_context_size", "low")),
        web_fetch_max_bytes=int(web.get("fetch_max_bytes", 524_288)),
        finance_enabled=bool(finance.get("enabled", False)),
        finance_adapter=finance.get("adapter", "local"),
        finance_prompt_path=finance_prompt_path,
        finance_bills_role=str(finance.get("bills_role", "finance")),
        secrets_enabled=bool(secrets.get("enabled", False)),
        secrets_adapter=str(secrets.get("adapter", "op_cli")),
        browser_enabled=bool(browser.get("enabled", False)),
        browser_adapter=str(browser.get("adapter", "playwright")),
        browser_headless=bool(browser.get("headless", False)),
        browser_session_timeout_seconds=float(
            browser.get("session_timeout_seconds", 3600)
        ),
        ops_enabled=bool(ops_cfg.get("enabled", True)),
        ops_database=_resolve_path(root, ops_cfg.get("database", "data/operations.db")),
        ops_calendar_horizon_days=int(ops_cfg.get("calendar_horizon_days", 14)),
        ops_preparation_hours=int(ops_cfg.get("preparation_hours", 48)),
        ops_email_lookback_days=int(ops_cfg.get("email_lookback_days", 14)),
        ops_timezone=(
            None
            if not ops_cfg.get("timezone")
            else (str(ops_cfg["timezone"]).strip() or None)
        ),
        notion=load_notion_config(root),
        log_level=os.environ.get("WALLY_LOG_LEVEL", "INFO").upper(),
        telegram_bot_token_ref=str(
            telegram_cfg.get("bot_token_ref")
            or os.environ.get("WALLY_TELEGRAM_BOT_TOKEN_REF")
            or ""
        ).strip(),
        telegram_owner_user_id=str(
            telegram_cfg.get("owner_user_id")
            or os.environ.get("WALLY_TELEGRAM_OWNER_USER_ID")
            or ""
        ).strip(),
    )


def load_instructions(settings: Settings) -> str:
    """Combine system, safety, and capability prompts into LLM instructions."""
    parts = [
        _load_prompt(settings.project_root, settings.system_prompt_path),
        _load_prompt(settings.project_root, settings.safety_prompt_path),
    ]
    if settings.knowledge_enabled and settings.knowledge_prompt_path:
        parts.append(_load_prompt(settings.project_root, settings.knowledge_prompt_path))
    if settings.workflow_enabled and settings.workflow_prompt_path:
        parts.append(_load_prompt(settings.project_root, settings.workflow_prompt_path))
    if settings.communications_enabled and settings.communications_prompt_path:
        parts.append(
            _load_prompt(settings.project_root, settings.communications_prompt_path)
        )
    if settings.conversation_enabled and settings.conversation_prompt_path:
        parts.append(
            _load_prompt(settings.project_root, settings.conversation_prompt_path)
        )
    if settings.web_enabled and settings.web_prompt_path:
        parts.append(_load_prompt(settings.project_root, settings.web_prompt_path))
    if settings.finance_enabled and settings.finance_prompt_path:
        parts.append(_load_prompt(settings.project_root, settings.finance_prompt_path))
    return "\n\n---\n\n".join(part.strip() for part in parts)
