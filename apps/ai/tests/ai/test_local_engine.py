"""Backend-facing behavior tests for the concrete local AI engine."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import JsonValue

from app.ai.engine import AIEngine, AIEngineDependencies
from app.ai.evaluation.samples import (
    sample_evidence_chunk,
    sample_grounded_draft,
    sample_inference_metrics,
    sample_model_profile,
    sample_runtime_health,
    sample_task,
    sample_task_plan,
)
from app.ai.fakes import FakeCapabilityRouter, FakeKnowledgeAdapter
from app.ai.local_engine import LocalAIEngine, create_local_ai_engine
from app.ai.schemas import (
    AgentContext,
    AgentProposal,
    ApprovedKnowledgeRoot,
    CodeRepairContent,
    CodeRepairRequest,
    ConversationGenerationRequest,
    ConversationGenerationResult,
    ConversationMessage,
    ConversationReply,
    ConversationRequest,
    DraftRequest,
    EmbeddingRequest,
    EmbeddingResult,
    Finding,
    FindingSeverity,
    IngestionResult,
    InstalledModel,
    KnowledgeQuery,
    ModelProfile,
    ModelRuntimeHealth,
    ProposedToolCall,
    SourceDocument,
    SourceReference,
    TaskPlan,
    TextGenerationRequest,
    TextGenerationResult,
    ToolDefinition,
    VisionAnalysis,
    VisionGenerationRequest,
    VisionPageResult,
    VisualAnalysisRequest,
    VisualBytesInput,
    VisualMimeType,
)
from app.ai.vision.ports import NormalizedVisualPage


def _vision_analysis() -> VisionAnalysis:
    reference = SourceReference(
        source_id="site-photo",
        document_name="site-photo.png",
        image_id="site-photo",
    )
    finding = Finding(
        finding_id="surface-corrosion",
        title="Surface corrosion observed",
        description="Surface corrosion is visible near the flange.",
        severity=FindingSeverity.MEDIUM,
        evidence=(reference,),
        uncertainty="A photograph cannot establish remaining wall thickness.",
    )
    page = VisionPageResult(
        source_id="site-photo",
        image_id="site-photo",
        extracted_text="Surface corrosion observed.",
        findings=(finding,),
        warnings=("Thickness is not visible.",),
    )
    return VisionAnalysis(
        model="qwen3-vl:4b",
        extracted_text=page.extracted_text,
        pages=(page,),
        findings=page.findings,
        warnings=page.warnings,
    )


@dataclass(slots=True)
class RecordingModelAdapter:
    """Return schema-specific local results while recording interface calls."""

    structured_outputs: dict[str, JsonValue]
    vision_result: VisionAnalysis = field(default_factory=_vision_analysis)
    calls: list[str] = field(default_factory=list)

    async def list_models(self) -> tuple[InstalledModel, ...]:
        self.calls.append("list_models")
        return ()

    async def health(self, profile: ModelProfile) -> ModelRuntimeHealth:
        self.calls.append(f"health:{profile.profile_id}")
        return sample_runtime_health()

    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        title = request.output_schema.get("title")
        if not isinstance(title, str) or title not in self.structured_outputs:
            raise AssertionError(f"No recorded structured output for {title!r}")
        self.calls.append(f"generate_text:{title}")
        output = self.structured_outputs[title]
        return TextGenerationResult(
            model=request.model,
            text=json.dumps(output),
            structured_output=output,
            metrics=sample_inference_metrics(),
        )

    async def generate_conversation(
        self, request: ConversationGenerationRequest
    ) -> ConversationGenerationResult:
        self.calls.append(f"generate_conversation:{request.model}")
        return ConversationGenerationResult(
            model=request.model,
            text="The previous message was: " + request.messages[-1].content,
            metrics=sample_inference_metrics(),
        )

    async def generate_vision(self, request: VisionGenerationRequest) -> TextGenerationResult:
        self.calls.append("generate_vision")
        output = self.vision_result.model_dump(mode="json", by_alias=True)
        return TextGenerationResult(
            model=request.model,
            text=json.dumps(output),
            structured_output=output,
            metrics=sample_inference_metrics(),
        )

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        self.calls.append("create_embeddings")
        return EmbeddingResult(
            model=request.model,
            vectors=tuple((0.1, 0.2) for _ in request.inputs),
            metrics=sample_inference_metrics(),
        )

    async def unload(self) -> None:
        self.calls.append("unload")

    async def close(self) -> None:
        self.calls.append("close")


@dataclass(frozen=True, slots=True)
class FixedVisualNormalizer:
    """Yield one application-identified image without reading the filesystem."""

    def iter_pages(self, visual_input: object) -> Iterator[NormalizedVisualPage]:
        del visual_input
        yield NormalizedVisualPage(
            source_id="site-photo",
            document_name="site-photo.png",
            image_id="site-photo",
            image_bytes=b"normalized-local-image",
            width=640,
            height=480,
        )


def _structured_outputs(proposed_path: Path) -> dict[str, JsonValue]:
    return {
        "TaskPlan": sample_task_plan().model_dump(mode="json", by_alias=True),
        "GroundedDraft": sample_grounded_draft().model_dump(mode="json", by_alias=True),
        "ProposedToolCall": ProposedToolCall(
            tool_name="request_document_export",
            arguments={"destination": str(proposed_path)},
            explanation="Ask Backend 1 to request an approved export.",
        ).model_dump(mode="json", by_alias=True),
        "CodeRepairContent": CodeRepairContent(
            language="python",
            corrected_code="print(4)",
            change_summary="Corrected the expected value.",
        ).model_dump(mode="json", by_alias=True),
    }


def accepts_ai_engine(engine: AIEngine) -> AIEngine:
    """Let mypy prove the concrete adapter satisfies the shared interface."""

    return engine


async def test_local_engine_exposes_every_ai_operation_through_one_interface(
    tmp_path: Path,
) -> None:
    """Exercise the complete backend-facing interface without Ollama or Chroma."""

    proposed_path = tmp_path / "must-not-be-created.docx"
    model = RecordingModelAdapter(_structured_outputs(proposed_path))
    knowledge = FakeKnowledgeAdapter()
    profile = sample_model_profile()
    engine = LocalAIEngine(
        AIEngineDependencies(
            model_adapter=model,
            knowledge_adapter=knowledge,
            router=FakeCapabilityRouter(),
            model_profile=profile,
        ),
        visual_normalizer=FixedVisualNormalizer(),
    )
    interface = accepts_ai_engine(engine)

    health = await interface.health()
    decision = await interface.choose_capability(sample_task())
    conversation: ConversationReply = await interface.reply_to_conversation(
        ConversationRequest(
            session_id="session-local-engine",
            user_message="Can you confirm this is a local conversation?",
        )
    )
    context = AgentContext(
        task=sample_task(),
        conversation=(ConversationMessage(role="user", content="Prepare the note."),),
        allowed_tools=(),
    )
    plan: TaskPlan = await interface.plan_task(context)
    analysis = await interface.analyze_visual(
        VisualAnalysisRequest(
            inputs=(
                VisualBytesInput(
                    content=b"identified-local-image",
                    source_id="site-photo",
                    session_id="session-local-engine",
                    mime_type=VisualMimeType.PNG,
                    document_name="site-photo.png",
                ),
            ),
            task=sample_task(),
        )
    )
    ingestion: IngestionResult = await interface.ingest_knowledge(
        SourceDocument(
            document_id="local-sop",
            document_name="local-sop.md",
            mime_type="text/markdown",
            source_id="local-sop",
            content=b"Approved local procedure.",
        )
    )
    evidence = await interface.search_knowledge(KnowledgeQuery(text="surface corrosion"))
    draft = await interface.create_grounded_draft(
        DraftRequest(
            subject="Approval for follow-up thickness measurement",
            objective="Prepare a grounded approval-note draft.",
            findings=sample_grounded_draft().findings,
            evidence=(sample_evidence_chunk(),),
        )
    )
    proposal_context = AgentContext(
        task=sample_task(),
        conversation=(ConversationMessage(role="user", content="Export the draft."),),
        allowed_tools=(
            ToolDefinition(
                name="request_document_export",
                description="Propose an export for Backend 1 approval.",
                input_schema={
                    "type": "object",
                    "properties": {"destination": {"type": "string"}},
                    "required": ["destination"],
                    "additionalProperties": False,
                },
            ),
        ),
    )
    proposal: AgentProposal = await interface.propose_action(proposal_context)
    repair = await interface.repair_code(
        CodeRepairRequest(
            task="Correct the expected value.",
            language="python",
            code="print(3)",
            test_output="1 failed",
            error_output="expected 4, got 3",
        )
    )

    assert health.runtime_ready is True
    assert health.knowledge_ready is True
    assert decision.selected_model == "qwen3-vl:4b"
    assert conversation.assistant_text.endswith("local conversation?")
    assert conversation.model == "qwen3-vl:4b"
    assert plan == sample_task_plan()
    assert analysis == _vision_analysis()
    assert ingestion.document_id == "local-sop"
    assert evidence == [sample_evidence_chunk()]
    assert draft == sample_grounded_draft()
    assert proposal.tool_call is not None
    assert proposal.tool_call.tool_name == "request_document_export"
    assert not proposed_path.exists()
    assert repair.corrected_code == "print(4)"

    await engine.close()
    await engine.close()
    assert model.calls.count("close") == 1


async def test_local_engine_reports_knowledge_health_without_exposing_failures() -> None:
    """Keep runtime readiness useful when the local index is unavailable."""

    model = RecordingModelAdapter({})
    engine = LocalAIEngine(
        AIEngineDependencies(
            model_adapter=model,
            knowledge_adapter=FakeKnowledgeAdapter(ready=False),
            router=FakeCapabilityRouter(),
            model_profile=sample_model_profile(),
        ),
        visual_normalizer=FixedVisualNormalizer(),
    )

    health = await engine.health()

    assert health.runtime_ready is True
    assert health.knowledge_ready is False
    assert health.knowledge_error == "KnowledgeIndexUnavailable"


async def test_local_engine_factory_does_not_require_running_ollama(tmp_path: Path) -> None:
    """Construct and close the production module without contacting local Ollama."""

    engine = create_local_ai_engine(
        knowledge_root=ApprovedKnowledgeRoot(path=tmp_path),
        model_profile=sample_model_profile(),
    )

    assert accepts_ai_engine(engine) is engine
    await engine.close()
