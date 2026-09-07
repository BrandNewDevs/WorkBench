"""Concrete local implementation of the backend-facing AI engine interface."""

import asyncio
from types import TracebackType

from app.ai.engine import AIEngineDependencies
from app.ai.generation import LocalConversationGenerator, StructuredTextGenerator
from app.ai.knowledge.chroma_ingestion import (
    ChromaKnowledgeIngestor,
    create_persistent_chroma_client,
)
from app.ai.knowledge.config import KnowledgeProcessingSettings
from app.ai.knowledge.ports import RetrievalMetricsSink
from app.ai.models.ollama import create_ollama_adapter
from app.ai.models.ollama_http import OllamaSettings
from app.ai.models.profiles import load_model_profile
from app.ai.routing import DeterministicCapabilityRouter
from app.ai.schemas import (
    AgentContext,
    AgentProposal,
    AIHealthReport,
    ApprovedKnowledgeRoot,
    CapabilityDecision,
    CodeRepairRequest,
    CodeRepairResult,
    ConversationReply,
    ConversationRequest,
    DraftRequest,
    EvidenceChunk,
    GroundedDraft,
    IngestionResult,
    InputModality,
    KnowledgeQuery,
    ModelProfile,
    ModelRuntimeHealth,
    SourceDocument,
    TaskDescriptor,
    TaskKind,
    TaskPlan,
    VisionAnalysis,
    VisualAnalysisRequest,
)
from app.ai.vision import (
    LocalVisualNormalizer,
    VisionAnalyzer,
    VisionProcessingSettings,
    VisualNormalizer,
)


class LocalAIEngine:
    """Hide local routing, generation, vision, and retrieval behind one interface.

    The caller owns composition-time configuration and supplies approved paths.
    Individual operations never construct model-runtime or vector-index clients.
    """

    def __init__(
        self,
        dependencies: AIEngineDependencies,
        *,
        visual_normalizer: VisualNormalizer | None = None,
    ) -> None:
        self._dependencies = dependencies
        self._vision = VisionAnalyzer(
            dependencies.model_adapter,
            dependencies.model_profile,
            (
                visual_normalizer
                if visual_normalizer is not None
                else LocalVisualNormalizer()
            ),
        )
        self._text = StructuredTextGenerator(
            dependencies.model_adapter,
            dependencies.model_profile,
        )
        self._conversation = LocalConversationGenerator(
            dependencies.model_adapter,
            dependencies.model_profile,
        )
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def health(self) -> AIHealthReport:
        """Combine local model and knowledge readiness without leaking exception details."""

        runtime, knowledge = await asyncio.gather(
            self._runtime_health(),
            self._knowledge_health(),
        )
        knowledge_ready, knowledge_error = knowledge
        return AIHealthReport(
            runtime_ready=runtime.runtime_ready,
            runtime_error=runtime.runtime_error,
            models=runtime.models,
            knowledge_ready=knowledge_ready,
            knowledge_error=knowledge_error,
        )

    async def choose_capability(self, task: TaskDescriptor) -> CapabilityDecision:
        """Route from task facts and current local health without model inference."""

        return self._dependencies.router.choose(task, await self.health())

    async def reply_to_conversation(self, request: ConversationRequest) -> ConversationReply:
        """Generate one ordinary local text reply without agent or tool behavior."""

        decision = await self.choose_capability(
            TaskDescriptor(
                task_id=request.session_id,
                kind=TaskKind.CHAT,
                summary="Ordinary local text conversation.",
                modalities=(InputModality.TEXT,),
            )
        )
        reply = await self._conversation.reply(request, model=decision.selected_model)
        if not decision.used_fallback:
            return reply
        return reply.model_copy(
            update={
                "used_fallback": True,
                "fallback_reason": reply.fallback_reason or decision.fallback_reason,
            }
        )

    async def plan_task(self, request: AgentContext) -> TaskPlan:
        """Return a typed plan without advancing any backend workflow stage."""

        return await self._text.plan_task(request)

    async def analyze_visual(self, request: VisualAnalysisRequest) -> VisionAnalysis:
        """Analyze only application-supplied visual inputs."""

        return await self._vision.analyze_visual(request)

    async def ingest_knowledge(self, document: SourceDocument) -> IngestionResult:
        """Ingest one application-approved document into the injected local index."""

        return await self._dependencies.knowledge_adapter.ingest(document)

    async def search_knowledge(self, query: KnowledgeQuery) -> list[EvidenceChunk]:
        """Return evidence from the injected local index."""

        return await self._dependencies.knowledge_adapter.search(query)

    async def create_grounded_draft(self, request: DraftRequest) -> GroundedDraft:
        """Return structured grounded content without persisting or rendering it."""

        return await self._text.create_grounded_draft(request)

    async def propose_action(self, request: AgentContext) -> AgentProposal:
        """Return a validated proposal without invoking a backend tool."""

        return await self._text.propose_action(request)

    async def repair_code(self, request: CodeRepairRequest) -> CodeRepairResult:
        """Return corrected source without executing it."""

        return await self._text.repair_code(request)

    async def close(self) -> None:
        """Close the one shared model adapter exactly once."""

        async with self._close_lock:
            if self._closed:
                return
            await self._dependencies.model_adapter.close()
            self._closed = True

    async def __aenter__(self) -> LocalAIEngine:
        """Support ownership by a future backend composition lifespan."""

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release local model resources without suppressing caller failures."""

        del exc_type, exc_value, traceback
        await self.close()

    async def _runtime_health(self) -> ModelRuntimeHealth:
        try:
            return await self._dependencies.model_adapter.health(
                self._dependencies.model_profile
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            error_code = type(error).__name__
            return ModelRuntimeHealth(
                runtime_ready=False,
                runtime_error=error_code,
                models=(),
            )

    async def _knowledge_health(self) -> tuple[bool, str | None]:
        try:
            ready = await self._dependencies.knowledge_adapter.health()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return False, type(error).__name__
        if ready:
            return True, None
        return False, "KnowledgeIndexUnavailable"


def create_local_ai_engine(
    *,
    knowledge_root: ApprovedKnowledgeRoot,
    model_profile: ModelProfile | None = None,
    ollama_settings: OllamaSettings | None = None,
    knowledge_settings: KnowledgeProcessingSettings | None = None,
    vision_settings: VisionProcessingSettings | None = None,
    retrieval_metrics_sink: RetrievalMetricsSink | None = None,
) -> LocalAIEngine:
    """Compose one reusable, local-only engine without making a runtime request.

    Backend 1 may call this once from its composition root after Backend 2 supplies
    the approved Chroma root. The returned engine owns the Ollama adapter lifecycle.
    """

    profile = model_profile if model_profile is not None else load_model_profile()
    normalizer = LocalVisualNormalizer(vision_settings)
    chroma_client = create_persistent_chroma_client(knowledge_root)
    model_adapter = create_ollama_adapter(
        settings=ollama_settings,
        profile=profile,
    )
    knowledge_adapter = ChromaKnowledgeIngestor(
        chroma_client,
        model_adapter,
        profile,
        knowledge_settings,
        metrics_sink=retrieval_metrics_sink,
    )
    return LocalAIEngine(
        AIEngineDependencies(
            model_adapter=model_adapter,
            knowledge_adapter=knowledge_adapter,
            router=DeterministicCapabilityRouter(profile),
            model_profile=profile,
        ),
        visual_normalizer=normalizer,
    )
