"""Sanitized deterministic examples for unit tests and backend integration."""

from pathlib import Path

from pydantic import JsonValue

from app.ai.schemas import (
    AgentContext,
    AgentProposal,
    AIHealthReport,
    ApprovedKnowledgeRoot,
    ApprovedPath,
    ApprovedVisualInput,
    Capability,
    CapabilityDecision,
    CodeRepairRequest,
    CodeRepairResult,
    ContractModel,
    ConversationMessage,
    DraftRequest,
    EmbeddingRequest,
    EmbeddingResult,
    EvidenceChunk,
    Finding,
    FindingSeverity,
    GenerationLimits,
    GroundedDraft,
    InferenceMetrics,
    IngestionResult,
    InputModality,
    InstalledModel,
    KnowledgeQuery,
    ModelHealth,
    ModelProfile,
    ModelRuntimeHealth,
    ModelStatus,
    ProposedToolCall,
    SourceDocument,
    SourceReference,
    TaskDescriptor,
    TaskKind,
    TextGenerationRequest,
    TextGenerationResult,
    ToolDefinition,
    VisionAnalysis,
    VisionGenerationRequest,
    VisionPageResult,
    VisualAnalysisRequest,
    VisualMimeType,
)


def sample_generation_limits() -> GenerationLimits:
    """Return small, valid generation limits for deterministic tests."""

    return GenerationLimits(context_window=8_192, max_output_tokens=1_024, timeout_seconds=60)


def sample_inference_metrics() -> InferenceMetrics:
    """Return non-sensitive timing and token metadata for fake inference."""

    return InferenceMetrics(
        client_elapsed_ms=12.5,
        total_duration_ns=12_000_000,
        load_duration_ns=2_000_000,
        prompt_eval_count=12,
        prompt_eval_duration_ns=3_000_000,
        eval_count=8,
        eval_duration_ns=7_000_000,
    )


def sample_model_profile() -> ModelProfile:
    """Return the safe local model profile agreed for the MVP."""

    limits = sample_generation_limits()
    return ModelProfile(
        profile_id="safe-8gb",
        text_candidates=("qwen3:4b", "qwen3:1.7b"),
        vision_candidates=("qwen3-vl:4b", "qwen3-vl:2b"),
        embedding_candidates=("qwen3-embedding:0.6b",),
        text_limits=limits,
        vision_limits=limits,
        embedding_batch_size=8,
    )


def sample_model_health() -> tuple[ModelHealth, ...]:
    """Return ready local health for all three capabilities."""

    return (
        ModelHealth(
            capability=Capability.TEXT,
            status=ModelStatus.READY,
            installed=True,
            loadable=True,
            selected_model="qwen3:4b",
        ),
        ModelHealth(
            capability=Capability.VISION,
            status=ModelStatus.READY,
            installed=True,
            loadable=True,
            selected_model="qwen3-vl:4b",
        ),
        ModelHealth(
            capability=Capability.EMBEDDING,
            status=ModelStatus.READY,
            installed=True,
            loadable=True,
            selected_model="qwen3-embedding:0.6b",
        ),
    )


def sample_runtime_health() -> ModelRuntimeHealth:
    """Return a healthy fake Ollama result without contacting Ollama."""

    return ModelRuntimeHealth(runtime_ready=True, models=sample_model_health())


def sample_health_report() -> AIHealthReport:
    """Return a fully ready local AI health report."""

    runtime = sample_runtime_health()
    return AIHealthReport(
        runtime_ready=runtime.runtime_ready,
        runtime_error=runtime.runtime_error,
        models=runtime.models,
        knowledge_ready=True,
    )


def sample_task() -> TaskDescriptor:
    """Return a visual inspection task descriptor."""

    return TaskDescriptor(
        task_id="task-inspection-001",
        kind=TaskKind.VISUAL_ANALYSIS,
        summary="Extract observed defects from the uploaded inspection report.",
        modalities=(InputModality.SCANNED_PDF,),
        file_types=("application/pdf",),
    )


def sample_approved_path() -> ApprovedPath:
    """Return an inert path reference; no test reads this path."""

    return ApprovedPath(
        path=Path("/approved/session-001/inspection-report.pdf"),
        source_id="inspection-report-page-2",
        session_id="session-001",
    )


def sample_source_reference() -> SourceReference:
    """Return traceable, application-controlled source metadata."""

    return SourceReference(
        source_id="inspection-report-page-2",
        document_name="inspection-report.pdf",
        page_number=2,
        section="Visual inspection",
    )


def sample_finding() -> Finding:
    """Return a sanitized inspection finding."""

    return Finding(
        finding_id="finding-corrosion-001",
        title="Surface corrosion observed",
        description="Localized surface corrosion is visible near the lower flange.",
        severity=FindingSeverity.MEDIUM,
        evidence=(sample_source_reference(),),
        uncertainty="Remaining wall thickness cannot be determined from the image alone.",
    )


def sample_evidence_chunk() -> EvidenceChunk:
    """Return one sanitized SOP passage with complete citation metadata."""

    return EvidenceChunk(
        source_id="sop-corrosion-page-7",
        chunk_id="sop-corrosion-page-7-section-4-2",
        document_id="sop-corrosion-control",
        document_name="Corrosion Control SOP.pdf",
        mime_type="application/pdf",
        page_number=7,
        section="4.2 Surface corrosion",
        content="Record the location and request thickness measurement before repair approval.",
        score=0.92,
        content_hash="sha256:sample-evidence-content",
        embedding_model="qwen3-embedding:0.6b",
    )


def sample_vision_analysis() -> VisionAnalysis:
    """Return a fake vision result containing the sample finding."""

    finding = sample_finding()
    page = VisionPageResult(
        source_id="inspection-report-page-2",
        page_number=2,
        extracted_text="Localized corrosion observed near lower flange.",
        findings=(finding,),
    )
    return VisionAnalysis(
        model="qwen3-vl:4b",
        extracted_text=page.extracted_text,
        pages=(page,),
        findings=(finding,),
        warnings=("Thickness value was not legible.",),
    )


def sample_grounded_draft() -> GroundedDraft:
    """Return a draft grounded in the sample finding and SOP evidence."""

    return GroundedDraft(
        subject="Approval for follow-up thickness measurement",
        summary="Surface corrosion was observed during the inspection.",
        findings=(sample_finding(),),
        recommendation="Approve a thickness measurement before deciding on repair work.",
        evidence_source_ids=(
            "inspection-report-page-2",
            "sop-corrosion-page-7",
        ),
        uncertainties=("The image does not establish remaining wall thickness.",),
    )


def representative_contracts() -> tuple[ContractModel, ...]:
    """Instantiate a representative example of every Phase 0 Pydantic model."""

    task = sample_task()
    approved_path = sample_approved_path()
    finding = sample_finding()
    evidence = sample_evidence_chunk()
    limits = sample_generation_limits()
    tool_schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"format": {"type": "string"}},
        "required": ["format"],
    }
    tool = ToolDefinition(
        name="request_document_export",
        description="Ask Backend 1 to consider exporting a draft.",
        input_schema=tool_schema,
    )
    tool_call = ProposedToolCall(
        tool_name=tool.name,
        arguments={"format": "docx"},
        explanation="The user requested a Word deliverable.",
    )
    runtime_health = sample_runtime_health()
    return (
        limits,
        sample_inference_metrics(),
        InstalledModel(
            name="qwen3:4b",
            size_bytes=2_500_000_000,
            digest="sha256:sample-model",
            family="qwen3",
            parameter_size="4B",
            quantization_level="Q4_K_M",
        ),
        sample_model_profile(),
        *sample_model_health(),
        runtime_health,
        sample_health_report(),
        task,
        CapabilityDecision(
            capability=Capability.VISION,
            selected_model="qwen3-vl:4b",
            reason="The input contains a scanned PDF.",
        ),
        approved_path,
        ApprovedKnowledgeRoot(path=Path("/approved/local-knowledge")),
        sample_source_reference(),
        finding,
        evidence,
        sample_vision_analysis().pages[0],
        sample_vision_analysis(),
        sample_grounded_draft(),
        tool,
        tool_call,
        AgentProposal(tool_call=tool_call),
        VisualAnalysisRequest(
            inputs=(
                ApprovedVisualInput(
                    approved_path=approved_path,
                    mime_type=VisualMimeType.PDF,
                    document_name="inspection-report.pdf",
                ),
            ),
            task=task,
        ),
        SourceDocument(
            document_id="inspection-report",
            document_name="inspection-report.pdf",
            mime_type="application/pdf",
            approved_path=approved_path,
        ),
        IngestionResult(
            document_id="inspection-report",
            collection_name="qwen3-embedding-0.6b-v1",
            indexed_chunks=3,
            unchanged_chunks=0,
            replaced_chunks=0,
        ),
        KnowledgeQuery(text="What follow-up is required for surface corrosion?"),
        DraftRequest(
            subject="Corrosion follow-up",
            objective="Prepare an approval-note draft.",
            findings=(finding,),
            evidence=(evidence,),
        ),
        ConversationMessage(role="user", content="Prepare the approval note."),
        AgentContext(
            task=task,
            conversation=(
                ConversationMessage(role="user", content="Prepare the approval note."),
            ),
            allowed_tools=(tool,),
            evidence=(evidence,),
        ),
        CodeRepairRequest(
            task="Correct the total calculation.",
            language="python",
            code="print(sum([1, 2]))",
            test_output="1 failed",
            error_output="expected 4, got 3",
        ),
        CodeRepairResult(
            language="python",
            corrected_code="print(sum([1, 3]))",
            change_summary="Corrected the second input value.",
            model="qwen3:4b",
        ),
        TextGenerationRequest(
            model="qwen3:4b",
            system_prompt="Return valid structured output.",
            user_prompt="Summarize the supplied finding.",
            output_schema=tool_schema,
            limits=limits,
        ),
        TextGenerationResult(
            model="qwen3:4b",
            text='{"status":"ok"}',
            structured_output={"status": "ok"},
            metrics=sample_inference_metrics(),
        ),
        VisionGenerationRequest(
            model="qwen3-vl:4b",
            system_prompt="Return valid structured output.",
            user_prompt="Extract visible findings.",
            images_base64=("c2FuaXRpemVkLWltYWdl",),
            output_schema=tool_schema,
            limits=limits,
        ),
        EmbeddingRequest(model="qwen3-embedding:0.6b", inputs=(evidence.content,)),
        EmbeddingResult(
            model="qwen3-embedding:0.6b",
            vectors=((0.1, 0.2, 0.3),),
            metrics=sample_inference_metrics(),
        ),
    )
