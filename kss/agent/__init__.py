"""KSS Python-native Agent Core."""

from __future__ import annotations

from kss.agent.attachments import AttachmentError, AttachmentRecord, AttachmentStore
from kss.agent.context import ContextAssembler
from kss.agent.events import AbortToken, EventSequencer
from kss.agent.live_market_context import LiveContextScope, LiveMarketContextService
from kss.agent.memory_store import MemoryRecall, MemoryRecord, MemoryStore
from kss.agent.pi_ai_provider import PiAIHelperClient, PiAIHelperError, PiAIProvider
from kss.agent.provider import (
    ModelCapabilities,
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderError,
    ProviderEvent,
    ProviderUsage,
)
from kss.agent.provider_route import (
    ProviderCredential,
    ProviderModel,
    ProviderRoute,
    ProviderRouteSet,
    ProviderRouteStore,
    legacy_routes_from_environment,
)
from kss.agent.runtime import AgentRuntime, RunAdmission, RuntimeBusyError, RuntimeTurn
from kss.agent.session_store import RunAdmissionError, SessionStore
from kss.agent.skills import (
    SkillDiagnostic,
    SkillInfo,
    SkillManager,
    SkillResource,
    SkillResourceError,
)
from kss.agent.types import (
    AgentContentBlock,
    AgentEvent,
    AgentMessage,
    AgentState,
    Context,
    RunResult,
    RuntimeState,
    ToolCall,
    convert_to_llm,
    message_content_blocks_from_payload,
    visible_text_from_blocks,
)

__all__ = [
    "AbortToken",
    "AgentContentBlock",
    "AgentEvent",
    "AgentMessage",
    "AgentRuntime",
    "AgentState",
    "AttachmentError",
    "AttachmentRecord",
    "AttachmentStore",
    "Context",
    "ContextAssembler",
    "DuplicateTurn",
    "EventSequencer",
    "MemoryRecord",
    "MemoryRecall",
    "MemoryStore",
    "KSSAgentService",
    "LiveContextScope",
    "LiveMarketContextService",
    "RuntimeRunOptions",
    "ModelCapabilities",
    "OpenAICompatibleProvider",
    "PiAIHelperClient",
    "PiAIHelperError",
    "PiAIProvider",
    "ProviderCredential",
    "ProviderConfig",
    "ProviderError",
    "ProviderEvent",
    "ProviderModel",
    "ProviderRoute",
    "ProviderRouteStore",
    "ProviderRouteSet",
    "ProviderUsage",
    "RunResult",
    "RunAdmission",
    "RunAdmissionError",
    "RuntimeBusyError",
    "RuntimeState",
    "RuntimeTurn",
    "SessionStore",
    "SkillDiagnostic",
    "SkillInfo",
    "SkillManager",
    "SkillResource",
    "SkillResourceError",
    "ToolCall",
    "convert_to_llm",
    "legacy_routes_from_environment",
    "message_content_blocks_from_payload",
    "visible_text_from_blocks",
]


def __getattr__(name: str):
    """Lazily expose the application service without creating an import cycle.

    ``kss.llm.chat_client`` only needs the provider primitives.  Importing the
    service eagerly here makes that otherwise-low-level module recurse through
    ``service -> chat_client`` while Python is still initialising this package.
    The public ``from kss.agent import KSSAgentService`` contract stays intact
    for the sidecar and existing callers, but the service is loaded only when
    one of its application-level names is actually requested.
    """

    if name in {"DuplicateTurn", "KSSAgentService", "RuntimeRunOptions"}:
        from kss.agent.service import DuplicateTurn, KSSAgentService, RuntimeRunOptions

        return {
            "DuplicateTurn": DuplicateTurn,
            "KSSAgentService": KSSAgentService,
            "RuntimeRunOptions": RuntimeRunOptions,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
