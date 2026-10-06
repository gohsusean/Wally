"""Main reasoning loop."""

from __future__ import annotations

from wally.audit.logger import AuditLogger
from wally.config.loader import Settings
from wally.conversation.context import (
    consolidate_session,
    needs_consolidation,
    prepare_llm_messages,
)
from wally.exceptions import ProviderUnavailableError
from wally.models.messages import Message, Role, Session, WallyResponse
from wally.models.principal import RequestContext
from wally.models.task import Task, TaskIntent
from wally.orchestrator.context import build_instructions
from wally.orchestrator.response import compose_response
from wally.orchestrator.tools import ToolRegistry
from wally.providers.communications import CommunicationsProvider
from wally.providers.conversation import ConversationProvider
from wally.providers.finance import FinanceProvider
from wally.providers.knowledge import KnowledgeProvider
from wally.providers.llm import LLMProvider, LLMRequest, ToolResult
from wally.providers.secrets import SecretsProvider
from wally.providers.web import WebProvider
from wally.providers.workflow import WorkflowProvider
from wally.runtime.reasoning_router import ReasoningRouter
from wally.session.store import SessionStore

MAX_TOOL_ITERATIONS = 8


class Orchestrator:
    """Coordinates reasoning, tool execution, persistence, and audit logging."""

    def __init__(
        self,
        *,
        settings: Settings,
        llm: LLMProvider,
        sessions: SessionStore,
        audit: AuditLogger,
        tools: ToolRegistry,
        knowledge: KnowledgeProvider | None = None,
        workflow: WorkflowProvider | None = None,
        communications: CommunicationsProvider | None = None,
        conversation: ConversationProvider | None = None,
        web: WebProvider | None = None,
        finance: FinanceProvider | None = None,
        secrets: SecretsProvider | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._sessions = sessions
        self._audit = audit
        self._tools = tools
        self._knowledge = knowledge
        self._workflow = workflow
        self._communications = communications
        self._conversation = conversation
        self._web = web
        self._finance = finance
        self._secrets = secrets
        self._router = ReasoningRouter(settings=settings)
        self._instructions = build_instructions(settings)
        self._prompt_version = settings.system_prompt_path.name

    def check_health(self) -> dict[str, bool]:
        health = {self._llm.name: self._llm.is_healthy()}
        if self._knowledge is not None:
            health[self._knowledge.name] = self._knowledge.is_healthy()
        elif self._settings.knowledge_enabled:
            health["knowledge"] = False
        if self._workflow is not None:
            health[self._workflow.name] = self._workflow.is_healthy()
        elif self._settings.workflow_enabled:
            health["workflow"] = False
        if self._communications is not None:
            health[self._communications.name] = self._communications.is_healthy()
        elif self._settings.communications_enabled:
            health["communications"] = False
        if self._conversation is not None:
            health[self._conversation.name] = self._conversation.is_healthy()
        elif self._settings.conversation_enabled:
            health["conversation"] = False
        if self._web is not None:
            health[self._web.name] = self._web.is_healthy()
        elif self._settings.web_enabled:
            health["web"] = False
        if self._finance is not None:
            health[self._finance.name] = self._finance.is_healthy()
        elif self._settings.finance_enabled:
            health["finance"] = False
        if self._secrets is not None:
            health[self._secrets.name] = self._secrets.is_healthy()
        elif self._settings.secrets_enabled:
            health["secrets"] = False
        return health

    def handle(
        self,
        session: Session,
        user_input: str,
        *,
        context: RequestContext | None = None,
    ) -> WallyResponse:
        self._tools.set_session_id(session.id)
        if self._conversation is not None:
            self._conversation.set_exclude_session(session.id)

        user_message = Message(role=Role.USER, content=user_input)
        self._sessions.add_message(session.id, user_message)
        session.messages.append(user_message)

        if not self._llm.is_healthy():
            self._audit.log_simple(
                event_type="provider_unavailable",
                session_id=session.id,
                outcome="failed",
                provider=self._llm.name,
            )
            raise ProviderUnavailableError(
                self._llm.name,
                "OpenAI is unreachable or misconfigured. "
                "I cannot reason without the language model.",
            )

        if self._settings.knowledge_enabled and self._knowledge is None:
            raise ProviderUnavailableError(
                "knowledge",
                "Knowledge is enabled but not configured. "
                "Set NOTION_API_KEY and share databases with your Notion integration.",
            )

        if self._settings.workflow_enabled and self._workflow is None:
            raise ProviderUnavailableError(
                "workflow",
                "Workflows are enabled but not configured. "
                "Set N8N_WEBHOOK_BASE_URL and configure config/workflows.yaml.",
            )

        if self._knowledge is not None and not self._knowledge.is_healthy():
            self._audit.log_simple(
                event_type="provider_unavailable",
                session_id=session.id,
                outcome="failed",
                provider="knowledge",
            )
            raise ProviderUnavailableError(
                "knowledge",
                "The knowledge provider is unreachable or misconfigured. "
                "I cannot access your knowledge right now.",
            )

        if self._workflow is not None and not self._workflow.is_healthy():
            raise ProviderUnavailableError(
                "workflow",
                "The workflow provider is misconfigured. "
                "Check N8N_WEBHOOK_BASE_URL and config/workflows.yaml.",
            )

        if self._settings.communications_enabled and self._communications is None:
            raise ProviderUnavailableError(
                "communications",
                "Communications is enabled but not configured. "
                "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN.",
            )

        if self._communications is not None and not self._communications.is_healthy():
            raise ProviderUnavailableError(
                "communications",
                "The communications provider is unreachable or misconfigured. "
                "Check Google OAuth credentials.",
            )

        if self._settings.web_enabled and self._web is None:
            raise ProviderUnavailableError(
                "web",
                "Web search is enabled but not configured. "
                "Set OPENAI_API_KEY for the OpenAI web adapter.",
            )

        if self._web is not None and not self._web.is_healthy():
            raise ProviderUnavailableError(
                "web",
                "The web provider is unreachable or misconfigured. "
                "Check OPENAI_API_KEY.",
            )

        if self._settings.finance_enabled and self._finance is None:
            raise ProviderUnavailableError(
                "finance",
                "Finance is enabled but not configured. "
                "Enable knowledge (NOTION_API_KEY) for bill records.",
            )

        if self._finance is not None and not self._finance.is_healthy():
            raise ProviderUnavailableError(
                "finance",
                "The finance provider is unreachable. Check knowledge configuration.",
            )

        if needs_consolidation(session, self._settings):
            consolidate_session(
                session,
                store=self._sessions,
                llm=self._llm,
                router=self._router,
                settings=self._settings,
            )
            self._audit.log_simple(
                event_type="conversation_consolidated",
                session_id=session.id,
                outcome="success",
                provider="conversation",
                parameters={
                    "covers_message_count": session.summary_covers_through,
                },
            )

        llm_messages = prepare_llm_messages(session, self._settings)
        actions_taken: list[str] = []
        request = LLMRequest(
            instructions=self._instructions,
            messages=llm_messages,
            tools=self._tools.definitions or None,
        )

        conversation_task = Task(intent=TaskIntent.CONVERSATION)
        llm_response = self._call_llm(session, request, conversation_task)

        iteration = 0
        while llm_response.tool_calls and iteration < MAX_TOOL_ITERATIONS:
            iteration += 1
            tool_results: list[ToolResult] = []
            tool_names: list[str] = []
            for call in llm_response.tool_calls:
                tool_names.append(call.name)
                result = self._tools.execute(call, context=context)
                tool_results.append(ToolResult(call_id=result.call_id, output=result.output))
                if result.action_taken:
                    actions_taken.append(result.action_taken)
                self._audit.log_simple(
                    event_type="tool_execution",
                    session_id=session.id,
                    outcome="denied" if result.denied else "success",
                    provider=self._tools.provider_for(call.name),
                    action_type=call.name,
                    parameters=call.arguments,
                    dry_run=self._settings.dry_run,
                )

            follow_up = LLMRequest(
                instructions=self._instructions,
                messages=llm_messages,
                tools=self._tools.definitions or None,
                previous_response_id=llm_response.response_id,
                tool_results=tool_results,
            )
            follow_up_task = Task(
                intent=TaskIntent.TOOL_FOLLOWUP,
                tool_names=tuple(tool_names),
            )
            llm_response = self._call_llm(session, follow_up, follow_up_task)

        assistant_message = Message(role=Role.ASSISTANT, content=llm_response.content)
        self._sessions.add_message(session.id, assistant_message)
        session.messages.append(assistant_message)

        self._audit.log_simple(
            event_type="llm_request",
            session_id=session.id,
            outcome="success",
            provider=self._llm.name,
            prompt_version=self._prompt_version,
            dry_run=self._settings.dry_run,
            parameters={
                "model": llm_response.model,
                "response_id": llm_response.response_id,
                "reasoning_profile": llm_response.reasoning_profile,
            },
        )

        return compose_response(session, llm_response, actions_taken=actions_taken)

    def _call_llm(self, session: Session, request: LLMRequest, task: Task):
        decision = self._router.route(task)
        request.reasoning_profile = decision.profile
        try:
            response = self._llm.complete(request)
        except ProviderUnavailableError:
            self._audit.log_simple(
                event_type="llm_request",
                session_id=session.id,
                outcome="failed",
                provider=self._llm.name,
                prompt_version=self._prompt_version,
                parameters={
                    "reasoning_profile": decision.profile,
                    "routing_source": decision.source,
                },
            )
            raise

        self._audit.log_simple(
            event_type="reasoning_routed",
            session_id=session.id,
            outcome="success",
            provider="runtime",
            parameters={
                "reasoning_profile": decision.profile,
                "routing_source": decision.source,
                "task_intent": task.intent.value,
                "tool_names": list(task.tool_names),
            },
        )
        return response
