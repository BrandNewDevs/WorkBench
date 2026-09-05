"""Public AI engine contract and its injectable dependencies."""

from dataclasses import dataclass
from typing import Protocol

from app.ai.knowledge.ports import KnowledgeAdapter
from app.ai.models.ports import ModelAdapter
from app.ai.routing.ports import CapabilityRouter
from app.ai.schemas import (
    AgentContext,
    AgentProposal,
    AIHealthReport,
    CapabilityDecision,
    CodeRepairRequest,
    CodeRepairResult,
    DraftRequest,
    EvidenceChunk,
    GroundedDraft,
    IngestionResult,
    KnowledgeQuery,
    ModelProfile,
    SourceDocument,
    TaskDescriptor,
    TaskPlan,
    VisionAnalysis,
    VisualAnalysisRequest,
)


@dataclass(frozen=True, slots=True)
class AIEngineDependencies:
    """Dependencies supplied by the composition root, never built in business code."""

    model_adapter: ModelAdapter
    knowledge_adapter: KnowledgeAdapter
    router: CapabilityRouter
    model_profile: ModelProfile


class AIEngine(Protocol):
    """Stable async API exposed by the AI layer to Backend 1."""

    async def health(self) -> AIHealthReport:
        """Report local model-runtime and knowledge-index readiness."""
        ...

    async def choose_capability(self, task: TaskDescriptor) -> CapabilityDecision:
        """Select the local model capability for a task."""
        ...

    async def plan_task(self, request: AgentContext) -> TaskPlan:
        """Propose a typed sequence without executing or approving its steps."""
        ...

    async def analyze_visual(self, request: VisualAnalysisRequest) -> VisionAnalysis:
        """Extract text and findings from approved visual inputs."""
        ...

    async def ingest_knowledge(self, document: SourceDocument) -> IngestionResult:
        """Ingest a document whose path was approved by Backend 2."""
        ...

    async def search_knowledge(self, query: KnowledgeQuery) -> list[EvidenceChunk]:
        """Return local evidence with application-controlled metadata."""
        ...

    async def create_grounded_draft(self, request: DraftRequest) -> GroundedDraft:
        """Create structured content for Backend 2 to render."""
        ...

    async def propose_action(self, request: AgentContext) -> AgentProposal:
        """Return text or one proposed tool call; never execute it."""
        ...

    async def repair_code(self, request: CodeRepairRequest) -> CodeRepairResult:
        """Suggest corrected code from sandbox feedback without running it."""
        ...
