"""Stable data contracts shared by the AI layer and Backend 1.

These models describe data only. They do not grant file access, approve a tool,
execute an action, or start a local service.
"""

from base64 import b64decode
from binascii import Error as Base64DecodeError
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from pydantic.alias_generators import to_camel


class ContractModel(BaseModel):
    """Base model for strict Python/JSON application boundaries."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class Capability(StrEnum):
    """A local-model capability understood by the deterministic router."""

    TEXT = "text"
    VISION = "vision"
    EMBEDDING = "embedding"


class TaskKind(StrEnum):
    """AI task categories. Workflow state remains Backend 1-owned."""

    CHAT = "chat"
    PLANNING = "planning"
    DRAFTING = "drafting"
    CODE_REPAIR = "codeRepair"
    KNOWLEDGE_INGESTION = "knowledgeIngestion"
    KNOWLEDGE_SEARCH = "knowledgeSearch"
    VISUAL_ANALYSIS = "visualAnalysis"


class InputModality(StrEnum):
    """Input forms that affect capability selection."""

    TEXT = "text"
    IMAGE = "image"
    SCANNED_PDF = "scannedPdf"
    NATIVE_DOCUMENT = "nativeDocument"


class VisualMimeType(StrEnum):
    """Visual formats deliberately supported by the MVP pipeline."""

    PDF = "application/pdf"
    JPEG = "image/jpeg"
    PNG = "image/png"
    WEBP = "image/webp"


class ModelStatus(StrEnum):
    """Readiness state for one model capability."""

    READY = "ready"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class FindingSeverity(StrEnum):
    """Conservative severity labels for extracted findings."""

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GenerationLimits(ContractModel):
    """Bounded generation settings supplied by a model profile."""

    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0)


class InferenceMetrics(ContractModel):
    """Non-confidential timing and token counts returned by Ollama."""

    client_elapsed_ms: float = Field(ge=0)
    total_duration_ns: int | None = Field(default=None, ge=0)
    load_duration_ns: int | None = Field(default=None, ge=0)
    prompt_eval_count: int | None = Field(default=None, ge=0)
    prompt_eval_duration_ns: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)
    eval_duration_ns: int | None = Field(default=None, ge=0)


class InstalledModel(ContractModel):
    """Non-sensitive metadata reported by Ollama's local model registry."""

    name: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    digest: str = ""
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None


class ModelProfile(ContractModel):
    """Ordered local candidates and limits for each AI capability."""

    profile_id: str = Field(min_length=1)
    text_candidates: tuple[str, ...] = Field(min_length=1)
    vision_candidates: tuple[str, ...] = Field(min_length=1)
    embedding_candidates: tuple[str, ...] = Field(min_length=1)
    text_limits: GenerationLimits
    vision_limits: GenerationLimits
    embedding_batch_size: int = Field(gt=0)

    @model_validator(mode="after")
    def reject_duplicate_candidates(self) -> "ModelProfile":
        """Keep fallback order deterministic and unambiguous."""

        candidate_groups = (
            self.text_candidates,
            self.vision_candidates,
            self.embedding_candidates,
        )
        if any(len(group) != len(set(group)) for group in candidate_groups):
            raise ValueError("model candidates must be unique within each capability")
        return self


class ModelHealth(ContractModel):
    """Readiness and fallback details for one capability."""

    capability: Capability
    status: ModelStatus
    installed: bool
    loadable: bool | None = None
    selected_model: str | None = None
    fallback_reason: str | None = None
    last_error: str | None = None

    @model_validator(mode="after")
    def ready_model_is_usable(self) -> "ModelHealth":
        """A ready result must identify a model that was loaded successfully."""

        if self.status is ModelStatus.READY and (
            not self.installed or self.loadable is not True or self.selected_model is None
        ):
            raise ValueError("ready model health requires an installed, loadable selected model")
        return self


class ModelRuntimeHealth(ContractModel):
    """Local model-runtime readiness before knowledge health is combined."""

    runtime_ready: bool
    runtime_error: str | None = None
    models: tuple[ModelHealth, ...]


class AIHealthReport(ContractModel):
    """Combined local runtime, model, and knowledge-index readiness."""

    runtime_ready: bool
    runtime_error: str | None = None
    models: tuple[ModelHealth, ...]
    knowledge_ready: bool
    knowledge_error: str | None = None


class TaskDescriptor(ContractModel):
    """Facts the router may use; this contains no permission decision."""

    task_id: str = Field(min_length=1)
    kind: TaskKind
    summary: str = Field(min_length=1)
    modalities: tuple[InputModality, ...] = Field(min_length=1)
    file_types: tuple[str, ...] = ()
    requested_capability: Capability | None = None


class CapabilityDecision(ContractModel):
    """Deterministic routing result for a task."""

    capability: Capability
    selected_model: str
    reason: str = Field(min_length=1)
    used_fallback: bool = False
    fallback_reason: str | None = None

    @model_validator(mode="after")
    def fallback_has_reason(self) -> "CapabilityDecision":
        """Make fallback behavior visible to Backend 1 and audit events."""

        if self.used_fallback and not self.fallback_reason:
            raise ValueError("a fallback capability decision requires a fallback reason")
        return self


class ApprovedPath(ContractModel):
    """A file reference already approved and resolved by Backend 2.

    The AI layer consumes this reference. It must not discover neighboring files
    or derive a broader filesystem root from it.
    """

    path: Path
    source_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class ApprovedKnowledgeRoot(ContractModel):
    """A local persistence root explicitly supplied by Backend 2."""

    path: Path


class ApprovedVisualInput(ContractModel):
    """A visual file whose exact path was already approved by Backend 2."""

    input_kind: Literal["approvedPath"] = "approvedPath"
    approved_path: ApprovedPath
    mime_type: VisualMimeType
    document_name: str = Field(min_length=1)


class VisualBytesInput(ContractModel):
    """An in-memory upload supplied by Backend 1 with immutable source metadata."""

    input_kind: Literal["bytes"] = "bytes"
    content: bytes = Field(min_length=1, repr=False)
    source_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    mime_type: VisualMimeType
    document_name: str = Field(min_length=1)


VisualInput: TypeAlias = Annotated[
    ApprovedVisualInput | VisualBytesInput,
    Field(discriminator="input_kind"),
]


class SourceReference(ContractModel):
    """Application-controlled evidence location for a finding."""

    source_id: str = Field(min_length=1)
    document_name: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    image_id: str | None = None
    section: str | None = None

    @model_validator(mode="after")
    def has_specific_location(self) -> "SourceReference":
        """Require a traceable page, image, or section locator."""

        if self.page_number is None and self.image_id is None and self.section is None:
            raise ValueError("source reference requires a page, image, or section")
        return self


class Finding(ContractModel):
    """One structured observation with traceable evidence."""

    finding_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: FindingSeverity = FindingSeverity.UNKNOWN
    evidence: tuple[SourceReference, ...] = Field(min_length=1)
    uncertainty: str | None = None


class EvidenceChunk(ContractModel):
    """One locally retrieved passage and its immutable source metadata."""

    source_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document_name: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    section: str | None = None
    content: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    content_hash: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)


class VisionPageResult(ContractModel):
    """Structured output for one page or image."""

    source_id: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    image_id: str | None = None
    extracted_text: str
    findings: tuple[Finding, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def has_exactly_one_visual_locator(self) -> "VisionPageResult":
        """Identify a PDF page or a native image, never an ambiguous source."""

        locator_count = int(self.page_number is not None) + int(self.image_id is not None)
        if locator_count != 1:
            raise ValueError("vision page result requires exactly one page or image locator")
        return self


class VisionAnalysis(ContractModel):
    """Combined OCR/vision result; it does not make workflow decisions."""

    model: str = Field(min_length=1)
    extracted_text: str
    pages: tuple[VisionPageResult, ...] = Field(min_length=1)
    findings: tuple[Finding, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def findings_reference_returned_pages(self) -> "VisionAnalysis":
        """Reject findings whose evidence is absent from the returned page set."""

        page_locations = {
            (page.source_id, page.page_number, page.image_id) for page in self.pages
        }
        page_findings = tuple(finding for page in self.pages for finding in page.findings)
        if self.findings != page_findings:
            raise ValueError("analysis findings must match the findings attached to its pages")

        for finding in self.findings:
            for evidence in finding.evidence:
                location = (evidence.source_id, evidence.page_number, evidence.image_id)
                if location not in page_locations:
                    raise ValueError("finding evidence must reference a returned page or image")
        return self


class GroundedDraft(ContractModel):
    """Structured draft content for Backend 2 to render as an artifact."""

    subject: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    findings: tuple[Finding, ...]
    recommendation: str = Field(min_length=1)
    evidence_source_ids: tuple[str, ...] = Field(min_length=1)
    uncertainties: tuple[str, ...] = ()

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> "GroundedDraft":
        """Avoid ambiguous duplicate citation markers in rendered drafts."""

        if len(self.evidence_source_ids) != len(set(self.evidence_source_ids)):
            raise ValueError("draft evidence source IDs must be unique")
        return self


class ToolDefinition(ContractModel):
    """One tool schema supplied by Backend 1 for proposal validation."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue]


class ProposedToolCall(ContractModel):
    """A proposed action only; Backend 1 decides whether it may execute."""

    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]
    explanation: str = Field(min_length=1)


class AgentProposal(ContractModel):
    """Exactly one response or one tool proposal returned by the AI."""

    response_text: str | None = None
    tool_call: ProposedToolCall | None = None

    @model_validator(mode="after")
    def contains_exactly_one_outcome(self) -> "AgentProposal":
        """Prevent a response from smuggling an action alongside normal text."""

        outcome_count = int(self.response_text is not None) + int(self.tool_call is not None)
        if outcome_count != 1:
            raise ValueError("agent proposal requires exactly one response or tool call")
        return self


class VisualAnalysisRequest(ContractModel):
    """Backend-approved inputs for a vision/OCR operation."""

    inputs: tuple[VisualInput, ...] = Field(min_length=1)
    task: TaskDescriptor


class SourceDocument(ContractModel):
    """Backend-supplied path or bytes for local knowledge ingestion."""

    document_id: str = Field(min_length=1)
    document_name: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    source_id: str | None = Field(default=None, min_length=1)
    approved_path: ApprovedPath | None = None
    content: bytes | None = Field(default=None, min_length=1, repr=False)

    @model_validator(mode="after")
    def has_one_backend_supplied_input(self) -> "SourceDocument":
        """Accept exact approved paths or identified bytes, never arbitrary paths."""

        input_count = int(self.approved_path is not None) + int(self.content is not None)
        if input_count != 1:
            raise ValueError("source document requires exactly one approved path or byte payload")
        if self.content is not None and self.source_id is None:
            raise ValueError("source document bytes require a source ID")
        if (
            self.approved_path is not None
            and self.source_id is not None
            and self.source_id != self.approved_path.source_id
        ):
            raise ValueError("source document ID must match its approved path")
        return self

    @property
    def effective_source_id(self) -> str:
        """Return the application-owned source ID for either input form."""

        if self.source_id is not None:
            return self.source_id
        if self.approved_path is None:  # pragma: no cover - protected by validation
            raise ValueError("source document has no source ID")
        return self.approved_path.source_id


class IngestionResult(ContractModel):
    """Summary of a local, idempotent knowledge-ingestion operation."""

    document_id: str = Field(min_length=1)
    collection_name: str = Field(min_length=1)
    indexed_chunks: int = Field(ge=0)
    unchanged_chunks: int = Field(ge=0)
    replaced_chunks: int = Field(ge=0)


class KnowledgeQuery(ContractModel):
    """Bounded local search request."""

    text: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    minimum_score: float = Field(default=0.3, ge=0, le=1)

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, text: str) -> str:
        """Normalize surrounding whitespace and reject an empty semantic query."""

        normalized = text.strip()
        if not normalized:
            raise ValueError("knowledge query text must not be blank")
        return normalized


class DraftRequest(ContractModel):
    """Grounded material from which a structured draft may be generated."""

    subject: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    findings: tuple[Finding, ...]
    evidence: tuple[EvidenceChunk, ...] = Field(min_length=1)
    template_instructions: str | None = None


class ConversationMessage(ContractModel):
    """A concise conversation item passed to planning, without hidden reasoning."""

    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1)


class AgentContext(ContractModel):
    """Backend-owned workflow context made available to the AI planner."""

    task: TaskDescriptor
    conversation: tuple[ConversationMessage, ...]
    allowed_tools: tuple[ToolDefinition, ...]
    evidence: tuple[EvidenceChunk, ...] = ()


class CodeRepairRequest(ContractModel):
    """Sandbox feedback supplied by Backend 2 for a code repair attempt."""

    task: str = Field(min_length=1)
    language: str = Field(min_length=1)
    code: str
    test_output: str
    error_output: str


class CodeRepairResult(ContractModel):
    """Corrected source only; the AI layer never executes it."""

    language: str = Field(min_length=1)
    corrected_code: str
    change_summary: str = Field(min_length=1)
    model: str = Field(min_length=1)


class TextGenerationRequest(ContractModel):
    """Low-level structured request for a local model adapter."""

    model: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    output_schema: dict[str, JsonValue]
    limits: GenerationLimits
    temperature: float = Field(default=0, ge=0, le=1)


class VisionGenerationRequest(ContractModel):
    """Normalized images and prompts for the low-level local model adapter."""

    model: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    images_base64: tuple[str, ...] = Field(min_length=1)
    output_schema: dict[str, JsonValue]
    limits: GenerationLimits
    temperature: float = Field(default=0, ge=0, le=1)

    @field_validator("images_base64")
    @classmethod
    def require_valid_nonempty_base64(cls, images: tuple[str, ...]) -> tuple[str, ...]:
        """Reject malformed or empty normalized image payloads before inference."""

        for image in images:
            if not image:
                raise ValueError("base64 image data must not be empty")
            try:
                decoded = b64decode(image, validate=True)
            except (Base64DecodeError, ValueError) as error:
                raise ValueError("image data must use valid standard base64 encoding") from error
            if not decoded:
                raise ValueError("base64 image data must decode to at least one byte")
        return images


class TextGenerationResult(ContractModel):
    """Raw validated response returned by a local model adapter."""

    model: str = Field(min_length=1)
    text: str
    structured_output: JsonValue
    metrics: InferenceMetrics
    done_reason: str | None = None
    used_fallback: bool = False
    fallback_reason: str | None = None


class EmbeddingRequest(ContractModel):
    """Batch of confidential text to embed using a local model."""

    model: str = Field(min_length=1)
    inputs: tuple[str, ...] = Field(min_length=1)


class EmbeddingResult(ContractModel):
    """Local embedding vectors in the same order as the request inputs."""

    model: str = Field(min_length=1)
    vectors: tuple[tuple[float, ...], ...] = Field(min_length=1)
    metrics: InferenceMetrics
    used_fallback: bool = False
    fallback_reason: str | None = None
