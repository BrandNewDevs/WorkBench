"""Deterministic tests for fake AI dependencies."""

import pytest

from app.ai.errors import InvalidToolProposal, NoRelevantEvidence
from app.ai.evaluation.samples import (
    sample_approved_path,
    sample_evidence_chunk,
    sample_finding,
    sample_model_profile,
    sample_task,
    sample_task_plan,
)
from app.ai.fakes import FakeAIEngine, FakeKnowledgeAdapter, FakeModelAdapter
from app.ai.schemas import (
    AgentContext,
    AgentProposal,
    Capability,
    CapabilityDecision,
    CodeRepairRequest,
    CodeRepairResult,
    ConversationMessage,
    ConversationRequest,
    DraftRequest,
    IngestionResult,
    KnowledgeQuery,
    ProposedToolCall,
    SourceDocument,
    ToolDefinition,
    VisionGenerationRequest,
)


async def test_fake_ai_engine_returns_a_typed_plan() -> None:
    """Give Backend 1 deterministic planning output without model inference."""

    engine = FakeAIEngine()
    context = AgentContext(
        task=sample_task(),
        conversation=(ConversationMessage(role="user", content="Prepare the note."),),
        allowed_tools=(),
    )

    plan = await engine.plan_task(context)

    assert plan.next_step_id == plan.steps[0].step_id
    assert engine.calls == ["plan_task:task-inspection-001"]


async def test_fake_ai_engine_returns_a_local_conversation_reply() -> None:
    """Let Backend 1 test normal chat without a local model runtime."""

    engine = FakeAIEngine()

    reply = await engine.reply_to_conversation(
        ConversationRequest(
            session_id="session-chat-fake",
            user_message="Is this response local?",
        )
    )

    assert reply.session_id == "session-chat-fake"
    assert reply.model == "qwen3:4b"
    assert engine.calls == ["reply_to_conversation:session-chat-fake"]


async def test_fake_model_returns_structured_vision_output() -> None:
    """Exercise low-level vision generation without Ollama or filesystem access."""

    adapter = FakeModelAdapter()
    profile = sample_model_profile()
    request = VisionGenerationRequest(
        model="qwen3-vl:4b",
        system_prompt="Return valid JSON.",
        user_prompt="Extract findings.",
        images_base64=("c2FuaXRpemVkLWltYWdl",),
        output_schema={"type": "object"},
        limits=profile.vision_limits,
    )

    result = await adapter.generate_vision(request)

    assert result.structured_output == adapter.vision_result.model_dump(
        mode="json", by_alias=True
    )
    assert adapter.calls == ["generate_vision:qwen3-vl:4b"]


async def test_fake_knowledge_returns_sample_evidence() -> None:
    """Exercise retrieval without importing or starting Chroma."""

    adapter = FakeKnowledgeAdapter()

    result = await adapter.search(KnowledgeQuery(text="surface corrosion"))

    assert result == [sample_evidence_chunk()]
    assert adapter.calls == ["search:surface corrosion"]


async def test_fake_ai_engine_returns_a_grounded_draft() -> None:
    """Give Backend 1 a complete deterministic draft response."""

    engine = FakeAIEngine()
    finding = sample_finding()
    evidence = sample_evidence_chunk()
    request = DraftRequest(
        subject="Corrosion follow-up",
        objective="Prepare an approval-note draft.",
        findings=(finding,),
        evidence=(evidence,),
    )

    draft = await engine.create_grounded_draft(request)

    assert finding in draft.findings
    assert evidence.source_id in draft.evidence_source_ids
    assert engine.calls == ["create_grounded_draft:Corrosion follow-up"]


async def test_fake_knowledge_ingestion_does_not_require_the_path_to_exist() -> None:
    """Prove Phase 0 fakes consume approved references without file access."""

    adapter = FakeKnowledgeAdapter()
    approved_path = sample_approved_path()
    document = SourceDocument(
        document_id="inspection-report",
        document_name="inspection-report.pdf",
        mime_type="application/pdf",
        approved_path=approved_path,
    )

    result = await adapter.ingest(document)

    assert result.document_id == document.document_id
    assert not approved_path.path.exists()


async def test_fake_model_health_uses_injected_profile() -> None:
    """Keep model profiles injectable instead of constructing runtime clients."""

    adapter = FakeModelAdapter()
    profile = sample_model_profile()

    health = await adapter.health(profile)

    assert health.runtime_ready is True
    assert adapter.calls == ["health:safe-8gb"]


async def test_fake_ai_engine_returns_configured_backend_results() -> None:
    """Let Backend 1 configure valid success and fallback paths deterministically."""

    fallback = CapabilityDecision(
        capability=Capability.VISION,
        selected_model="qwen3-vl:2b",
        reason="The preferred local vision model is unavailable.",
        used_fallback=True,
        fallback_reason="qwen3-vl:4b could not be loaded.",
    )
    ingestion = IngestionResult(
        document_id="configured-document",
        collection_name="configured-collection",
        indexed_chunks=2,
        unchanged_chunks=0,
        replaced_chunks=0,
    )
    repair = CodeRepairResult(
        language="python",
        corrected_code="print(4)",
        change_summary="Corrected the expected value.",
        model="qwen3:4b",
    )
    proposal = AgentProposal(
        tool_call=ProposedToolCall(
            tool_name="request_document_export",
            arguments={"format": "docx"},
            explanation="The employee requested a Word draft.",
        )
    )
    engine = FakeAIEngine(
        capability_decision=fallback,
        plan=sample_task_plan(),
        ingestion_result=ingestion,
        action_proposal=proposal,
        code_repair_result=repair,
    )
    context = AgentContext(
        task=sample_task(),
        conversation=(ConversationMessage(role="user", content="Export the draft."),),
        allowed_tools=(
            ToolDefinition(
                name="request_document_export",
                description="Propose a local document export.",
                input_schema={
                    "type": "object",
                    "properties": {"format": {"type": "string", "enum": ["docx"]}},
                    "required": ["format"],
                    "additionalProperties": False,
                },
            ),
        ),
    )
    source = SourceDocument(
        document_id="input-document",
        document_name="input.md",
        mime_type="text/markdown",
        source_id="input-document",
        content=b"Approved local content.",
    )

    assert await engine.choose_capability(sample_task()) == fallback
    assert await engine.plan_task(context) == sample_task_plan()
    assert await engine.ingest_knowledge(source) == ingestion
    assert await engine.propose_action(context) == proposal
    assert (
        await engine.repair_code(
            request=CodeRepairRequest(
                task="Correct the output.",
                language="python",
                code="print(3)",
                test_output="1 failed",
                error_output="expected 4",
            )
        )
        == repair
    )


async def test_fake_ai_engine_raises_configured_operation_failure() -> None:
    """Let Backend 1 cover typed AI failure handling without a local runtime."""

    engine = FakeAIEngine(
        failures={"search_knowledge": NoRelevantEvidence("No matching local evidence.")}
    )

    with pytest.raises(NoRelevantEvidence, match="No matching local evidence"):
        await engine.search_knowledge(KnowledgeQuery(text="unrelated request"))

    assert engine.calls == ["search_knowledge:unrelated request"]


async def test_fake_ai_engine_rejects_an_unapproved_configured_tool() -> None:
    """Keep the fake's successful proposal behavior aligned with the real engine."""

    engine = FakeAIEngine(
        action_proposal=AgentProposal(
            tool_call=ProposedToolCall(
                tool_name="unknown_tool",
                arguments={},
                explanation="Invalid recorded proposal.",
            )
        )
    )
    context = AgentContext(
        task=sample_task(),
        conversation=(ConversationMessage(role="user", content="Do the work."),),
        allowed_tools=(),
    )

    with pytest.raises(InvalidToolProposal, match="unapproved"):
        await engine.propose_action(context)
