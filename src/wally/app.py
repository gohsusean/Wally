"""Application bootstrap and dependency wiring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wally.adapters.browser.factory import create_browser_provider
from wally.adapters.cli.approval import CLIApprovalProvider
from wally.adapters.conversation.local import create_conversation_provider
from wally.adapters.finance.local import create_finance_provider
from wally.adapters.google.adapter import create_communications_provider
from wally.adapters.n8n.adapter import create_workflow_provider
from wally.adapters.notion.bootstrap import build_knowledge_stack
from wally.adapters.openai.adapter import create_llm_provider
from wally.adapters.secrets import create_secrets_provider
from wally.adapters.web.adapter import create_web_provider
from wally.audit.logger import AuditLogger
from wally.config.loader import Settings, load_settings
from wally.exceptions import ProviderUnavailableError
from wally.knowledge.registry import KnowledgeRegistry
from wally.ops.act import ActVerifyService
from wally.ops.service import ObserveBriefService
from wally.ops.store import OperationsStore
from wally.orchestrator.core import Orchestrator
from wally.orchestrator.tools import ToolRegistry
from wally.providers.capability import CapabilityProvider
from wally.providers.communications import CommunicationsProvider
from wally.providers.conversation import ConversationProvider
from wally.providers.finance import FinanceProvider
from wally.providers.knowledge import KnowledgeProvider
from wally.providers.llm import LLMProvider
from wally.providers.secrets import SecretsProvider
from wally.providers.web import WebProvider
from wally.providers.workflow import WorkflowProvider
from wally.runtime.browser_executor import GovernedBrowserExecutor
from wally.runtime.secret_resolver import GovernedSecretsResolver
from wally.safety.gates import ApprovalGate
from wally.session.store import SessionStore


@dataclass
class App:
    settings: Settings
    orchestrator: Orchestrator
    sessions: SessionStore
    audit: AuditLogger
    knowledge: KnowledgeProvider | None
    workflow: WorkflowProvider | None
    communications: CommunicationsProvider | None
    conversation: ConversationProvider | None
    web: WebProvider | None
    finance: FinanceProvider | None
    secrets: SecretsProvider | None
    knowledge_registry: KnowledgeRegistry | None
    ops: ObserveBriefService | None
    act: ActVerifyService | None = None


class _UnavailableLLM:
    """Placeholder when the LLM adapter cannot be initialised."""

    name = "openai"
    _reason: str

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def is_healthy(self) -> bool:
        return False

    def complete(self, request):
        raise ProviderUnavailableError(self.name, self._reason)


def _build_providers(
    settings: Settings,
) -> tuple[
    dict[str, CapabilityProvider],
    KnowledgeProvider | None,
    KnowledgeRegistry | None,
    WorkflowProvider | None,
    CommunicationsProvider | None,
    ConversationProvider | None,
    WebProvider | None,
]:
    providers: dict[str, CapabilityProvider] = {}
    knowledge: KnowledgeProvider | None = None
    registry: KnowledgeRegistry | None = None
    workflow: WorkflowProvider | None = None
    communications: CommunicationsProvider | None = None
    conversation: ConversationProvider | None = None
    web: WebProvider | None = None

    if settings.knowledge_enabled:
        try:
            registry, knowledge = build_knowledge_stack(settings)
            providers["knowledge"] = knowledge
        except ProviderUnavailableError:
            knowledge = None
            registry = None

    if settings.workflow_enabled:
        try:
            workflow = create_workflow_provider(settings)
            if workflow is not None:
                providers["workflow"] = workflow
        except ProviderUnavailableError:
            workflow = None

    if settings.communications_enabled:
        try:
            communications = create_communications_provider(settings)
            if communications is not None:
                providers["communications"] = communications
        except ProviderUnavailableError:
            communications = None

    if settings.web_enabled:
        try:
            web = create_web_provider(settings)
            if web is not None:
                providers["web"] = web
        except ProviderUnavailableError:
            web = None

    return providers, knowledge, registry, workflow, communications, conversation, web


def create_app(
    *,
    project_root: Path | None = None,
    config_name: str | None = None,
    reasoning_profile: str | None = None,
) -> App:
    settings = load_settings(
        project_root=project_root,
        config_name=config_name,
        reasoning_profile=reasoning_profile,
    )
    try:
        llm: LLMProvider = create_llm_provider(settings)
    except ProviderUnavailableError as exc:
        llm = _UnavailableLLM(exc.reason)

    providers, knowledge, registry, workflow, communications, _, web = _build_providers(settings)

    sessions = SessionStore(settings.session_database)
    conversation = create_conversation_provider(settings, sessions)
    if conversation is not None:
        providers["conversation"] = conversation

    finance: FinanceProvider | None = None
    browser_provider = None
    audit = AuditLogger(settings.audit_directory)
    secrets_provider = None
    try:
        secrets_provider = create_secrets_provider(settings)
    except ProviderUnavailableError:
        secrets_provider = None
    secrets_resolver = GovernedSecretsResolver(secrets_provider, audit=audit)

    try:
        browser_provider = create_browser_provider(settings)
    except ProviderUnavailableError:
        browser_provider = None
    browser_executor = GovernedBrowserExecutor(
        browser_provider,
        session_timeout_seconds=settings.browser_session_timeout_seconds,
        secrets=secrets_resolver,
    )

    try:
        finance = create_finance_provider(
            settings,
            knowledge=knowledge,
            workflow=workflow,
            browser_executor=browser_executor,
            secrets=secrets_resolver,
        )
        if finance is not None:
            providers["finance"] = finance
    except ProviderUnavailableError:
        finance = None

    gate = ApprovalGate(require_approval=settings.require_approval, dry_run=settings.dry_run)
    tools = ToolRegistry(
        providers=providers,
        knowledge=knowledge,
        finance=finance,
        finance_bills_role=settings.finance_bills_role,
        browser_executor=browser_executor,
        gate=gate,
        approval=CLIApprovalProvider(),
        audit=audit,
        dry_run=settings.dry_run,
    )
    orchestrator = Orchestrator(
        settings=settings,
        llm=llm,
        sessions=sessions,
        audit=audit,
        tools=tools,
        knowledge=knowledge,
        workflow=workflow,
        communications=communications,
        conversation=conversation,
        web=web,
        finance=finance,
        secrets=secrets_provider,
    )
    ops = None
    act = None
    if settings.ops_enabled:
        ops_store = OperationsStore(settings.ops_database)
        ops = ObserveBriefService(
            ops_store,
            audit=audit,
            communications=communications,
            knowledge=knowledge,
            calendar_horizon_days=settings.ops_calendar_horizon_days,
            preparation_hours=settings.ops_preparation_hours,
            email_lookback_days=settings.ops_email_lookback_days,
            bills_role=settings.finance_bills_role,
            display_timezone=settings.ops_timezone,
        )
        act = ActVerifyService(
            ops_store,
            reconcile=ops.reconcile_proposals,
            knowledge=knowledge,
            browser_executor=browser_executor,
            gate=gate,
            approval=CLIApprovalProvider(),
            audit=audit,
            bills_role=settings.finance_bills_role,
        )
    return App(
        settings=settings,
        orchestrator=orchestrator,
        sessions=sessions,
        audit=audit,
        knowledge=knowledge,
        workflow=workflow,
        communications=communications,
        conversation=conversation,
        web=web,
        finance=finance,
        secrets=secrets_provider,
        knowledge_registry=registry,
        ops=ops,
        act=act,
    )
