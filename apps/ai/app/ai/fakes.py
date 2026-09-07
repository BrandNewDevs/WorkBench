"""Deterministic in-memory fakes for AI and backend unit tests."""

from dataclasses import dataclass, field
from typing import Literal

from app.ai.engine import AIEngineDependencies
from app.ai.errors import AIError, InvalidStructuredOutput, InvalidToolProposal
from app.ai.evaluation.samples import (
    sample_evidence_chunk,
    sample_grounded_draft,
    sample_health_report,
    sample_inference_metrics,
    sample_runtime_health,
    sample_task_plan,
    sample_vision_analysis,
)
from app.ai.models.structured_output import validate_output_schema, validate_structured_output
from app.ai.schemas import (
    AgentContext,
    AgentProposal,
    AIHealthReport,
    Capability,
    CapabilityDecision,
    CodeRepairRequest,
    CodeRepairResult,
    DraftRequest,
    EmbeddingRequest,
    EmbeddingResult,
    EvidenceChunk,
    GroundedDraft,
    IngestionResult,
    InstalledModel,
    KnowledgeQuery,
    ModelProfile,
    ModelRuntimeHealth,
    SourceDocument,
    TaskDescriptor,
    TaskPlan,
    TextGenerationRequest,
    TextGenerationResult,
    VisionAnalysis,
    VisionGenerationRequest,
    VisualAnalysisRequest,
)

type FakeEngineOperation = Literal[
    "health",
    "choose_capability",
    "plan_task",
    "analyze_visual",
    "ingest_knowledge",
    "search_knowledge",
    "create_grounded_draft",
    "propose_action",
    "repair_code",
]


@dataclass(slots=True)
class FakeModelAdapter:
    """Return recorded local-model results without starting Ollama."""

    runtime_health: ModelRuntimeHealth = field(default_factory=sample_runtime_health)
    vision_result: VisionAnalysis = field(default_factory=sample_vision_analysis)
    calls: list[str] = field(default_factory=list, init=False)

    async def list_models(self) -> tuple[InstalledModel, ...]:
        """Return the selected fake models as installed."""

        self.calls.append("list_models")
        return tuple(
            InstalledModel(name=health.selected_model, size_bytes=0)
            for health in self.runtime_health.models
            if health.selected_model is not None
        )

    async def health(self, profile: ModelProfile) -> ModelRuntimeHealth:
        """Return configured fake health and record the operation."""

        self.calls.append(f"health:{profile.profile_id}")
        return self.runtime_health

    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Return deterministic structured text."""

        self.calls.append(f"generate_text:{request.model}")
        return TextGenerationResult(
            model=request.model,
            text='{"status":"ok"}',
            structured_output={"status": "ok"},
            metrics=sample_inference_metrics(),
        )

    async def generate_vision(self, request: VisionGenerationRequest) -> TextGenerationResult:
        """Return deterministic structured vision text."""

        self.calls.append(f"generate_vision:{request.model}")
        return TextGenerationResult(
            model=request.model,
            text=self.vision_result.model_dump_json(by_alias=True),
            structured_output=self.vision_result.model_dump(mode="json", by_alias=True),
            metrics=sample_inference_metrics(),
        )

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Return one stable vector per input string."""

        self.calls.append(f"create_embeddings:{request.model}")
        vectors = tuple((0.1, 0.2, 0.3) for _ in request.inputs)
        return EmbeddingResult(
            model=request.model,
            vectors=vectors,
            metrics=sample_inference_metrics(),
        )

    async def unload(self) -> None:
        """Record an unload without contacting a runtime."""

        self.calls.append("unload")

    async def close(self) -> None:
        """Record lifecycle cleanup without owning external resources."""

        self.calls.append("close")


@dataclass(slots=True)
class FakeKnowledgeAdapter:
    """Return local evidence without importing or starting Chroma."""

    evidence: tuple[EvidenceChunk, ...] = field(
        default_factory=lambda: (sample_evidence_chunk(),)
    )
    ready: bool = True
    calls: list[str] = field(default_factory=list, init=False)

    async def health(self) -> bool:
        """Return configured fake index readiness."""

        self.calls.append("health")
        return self.ready

    async def ingest(self, document: SourceDocument) -> IngestionResult:
        """Return a deterministic ingestion summary without reading the file."""

        self.calls.append(f"ingest:{document.document_id}")
        return IngestionResult(
            document_id=document.document_id,
            collection_name="fake-local-knowledge-v1",
            indexed_chunks=len(self.evidence),
            unchanged_chunks=0,
            replaced_chunks=0,
        )

    async def search(self, query: KnowledgeQuery) -> list[EvidenceChunk]:
        """Return configured evidence without running a vector query."""

        self.calls.append(f"search:{query.text}")
        return list(self.evidence[: query.top_k])


@dataclass(slots=True)
class FakeCapabilityRouter:
    """Return a fixed deterministic decision for workflow tests."""

    decision: CapabilityDecision = field(
        default_factory=lambda: CapabilityDecision(
            capability=Capability.VISION,
            selected_model="qwen3-vl:4b",
            reason="Configured fake decision.",
        )
    )

    def choose(self, task: TaskDescriptor, health: AIHealthReport) -> CapabilityDecision:
        """Return the configured decision without implementing Phase 5 routing."""

        del task, health
        return self.decision


@dataclass(slots=True)
class FakeAIEngine:
    """Configurable backend-facing fake for success, fallback, and failure paths."""

    health_report: AIHealthReport = field(default_factory=sample_health_report)
    capability_decision: CapabilityDecision = field(
        default_factory=lambda: CapabilityDecision(
            capability=Capability.VISION,
            selected_model="qwen3-vl:4b",
            reason="The fake task contains a scanned document.",
        )
    )
    plan: TaskPlan = field(default_factory=sample_task_plan)
    vision_result: VisionAnalysis = field(default_factory=sample_vision_analysis)
    evidence: tuple[EvidenceChunk, ...] = field(
        default_factory=lambda: (sample_evidence_chunk(),)
    )
    draft: GroundedDraft = field(default_factory=sample_grounded_draft)
    ingestion_result: IngestionResult | None = None
    action_proposal: AgentProposal | None = None
    code_repair_result: CodeRepairResult | None = None
    failures: dict[FakeEngineOperation, AIError] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list, init=False)

    async def health(self) -> AIHealthReport:
        """Return configured combined health."""

        self.calls.append("health")
        self._raise_configured_failure("health")
        return self.health_report

    async def choose_capability(self, task: TaskDescriptor) -> CapabilityDecision:
        """Return a deterministic fake capability selection."""

        self.calls.append(f"choose_capability:{task.task_id}")
        self._raise_configured_failure("choose_capability")
        return self.capability_decision

    async def plan_task(self, request: AgentContext) -> TaskPlan:
        """Return a bounded fake plan without taking any workflow action."""

        self.calls.append(f"plan_task:{request.task.task_id}")
        self._raise_configured_failure("plan_task")
        return self.plan

    async def analyze_visual(self, request: VisualAnalysisRequest) -> VisionAnalysis:
        """Return a visual result containing the sample finding."""

        self.calls.append(f"analyze_visual:{request.task.task_id}")
        self._raise_configured_failure("analyze_visual")
        return self.vision_result

    async def ingest_knowledge(self, document: SourceDocument) -> IngestionResult:
        """Return a fake ingestion result without reading the approved path."""

        self.calls.append(f"ingest_knowledge:{document.document_id}")
        self._raise_configured_failure("ingest_knowledge")
        if self.ingestion_result is not None:
            return self.ingestion_result
        return IngestionResult(
            document_id=document.document_id,
            collection_name="fake-local-knowledge-v1",
            indexed_chunks=len(self.evidence),
            unchanged_chunks=0,
            replaced_chunks=0,
        )

    async def search_knowledge(self, query: KnowledgeQuery) -> list[EvidenceChunk]:
        """Return the configured sample evidence."""

        self.calls.append(f"search_knowledge:{query.text}")
        self._raise_configured_failure("search_knowledge")
        return list(self.evidence[: query.top_k])

    async def create_grounded_draft(self, request: DraftRequest) -> GroundedDraft:
        """Return the sample grounded draft."""

        self.calls.append(f"create_grounded_draft:{request.subject}")
        self._raise_configured_failure("create_grounded_draft")
        return self.draft

    async def propose_action(self, request: AgentContext) -> AgentProposal:
        """Propose one supplied tool but never execute it."""

        self.calls.append(f"propose_action:{request.task.task_id}")
        self._raise_configured_failure("propose_action")
        if self.action_proposal is not None:
            self._validate_action_proposal(request, self.action_proposal)
            return self.action_proposal
        if not request.allowed_tools:
            return AgentProposal(response_text="No backend-approved tools are available.")
        return AgentProposal(
            response_text="No fake tool proposal was configured for this test."
        )

    async def repair_code(self, request: CodeRepairRequest) -> CodeRepairResult:
        """Return input code unchanged; no sandbox execution occurs."""

        self.calls.append(f"repair_code:{request.language}")
        self._raise_configured_failure("repair_code")
        if self.code_repair_result is not None:
            return self.code_repair_result
        return CodeRepairResult(
            language=request.language,
            corrected_code=request.code,
            change_summary="Deterministic fake result; code was not executed.",
            model="qwen3:4b",
        )

    def _raise_configured_failure(self, operation: FakeEngineOperation) -> None:
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    @staticmethod
    def _validate_action_proposal(request: AgentContext, proposal: AgentProposal) -> None:
        if proposal.response_text is not None:
            return
        tool_call = proposal.tool_call
        if tool_call is None:  # pragma: no cover - AgentProposal validates this invariant.
            raise InvalidToolProposal("configured fake proposal has no outcome")
        allowed_tools = {tool.name: tool for tool in request.allowed_tools}
        tool = allowed_tools.get(tool_call.tool_name)
        if tool is None:
            raise InvalidToolProposal(
                f"configured fake proposal named an unapproved tool: {tool_call.tool_name}"
            )
        validate_output_schema(tool.input_schema)
        try:
            validate_structured_output(tool.input_schema, tool_call.arguments)
        except InvalidStructuredOutput as error:
            raise InvalidToolProposal(
                f"configured fake proposal arguments did not match {tool_call.tool_name}"
            ) from error


def fake_engine_dependencies(profile: ModelProfile) -> AIEngineDependencies:
    """Build injectable fake dependencies for a future concrete AI engine."""

    return AIEngineDependencies(
        model_adapter=FakeModelAdapter(),
        knowledge_adapter=FakeKnowledgeAdapter(),
        router=FakeCapabilityRouter(),
        model_profile=profile,
    )
