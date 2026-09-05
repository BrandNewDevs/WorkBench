"""Public behavior tests for the deterministic local capability router."""

from dataclasses import dataclass

import pytest

from app.ai.errors import (
    KnowledgeIndexUnavailable,
    ModelNotInstalled,
    ModelRuntimeUnavailable,
    NoEligibleCapability,
    OllamaPolicyViolation,
)
from app.ai.evaluation.samples import sample_health_report, sample_model_profile
from app.ai.routing import DeterministicCapabilityRouter
from app.ai.schemas import (
    AIHealthReport,
    Capability,
    InputModality,
    ModelHealth,
    ModelStatus,
    TaskDescriptor,
    TaskKind,
)


@dataclass(frozen=True, slots=True)
class RoutingCase:
    """One supported task/input combination and its expected route."""

    name: str
    kind: TaskKind
    modalities: tuple[InputModality, ...]
    file_types: tuple[str, ...]
    expected_capability: Capability
    expected_model: str
    reason_fragment: str


SUPPORTED_CASES = (
    RoutingCase(
        name="image analysis",
        kind=TaskKind.VISUAL_ANALYSIS,
        modalities=(InputModality.IMAGE,),
        file_types=("image/jpeg",),
        expected_capability=Capability.VISION,
        expected_model="qwen3-vl:4b",
        reason_fragment="image",
    ),
    RoutingCase(
        name="scanned PDF drafting",
        kind=TaskKind.DRAFTING,
        modalities=(InputModality.SCANNED_PDF,),
        file_types=("application/pdf",),
        expected_capability=Capability.VISION,
        expected_model="qwen3-vl:4b",
        reason_fragment="scanned PDF",
    ),
    RoutingCase(
        name="text chat",
        kind=TaskKind.CHAT,
        modalities=(InputModality.TEXT,),
        file_types=(),
        expected_capability=Capability.TEXT,
        expected_model="qwen3:4b",
        reason_fragment="chat",
    ),
    RoutingCase(
        name="text planning",
        kind=TaskKind.PLANNING,
        modalities=(InputModality.TEXT,),
        file_types=(),
        expected_capability=Capability.TEXT,
        expected_model="qwen3:4b",
        reason_fragment="planning",
    ),
    RoutingCase(
        name="text drafting",
        kind=TaskKind.DRAFTING,
        modalities=(InputModality.TEXT,),
        file_types=("text/plain",),
        expected_capability=Capability.TEXT,
        expected_model="qwen3:4b",
        reason_fragment="drafting",
    ),
    RoutingCase(
        name="code repair",
        kind=TaskKind.CODE_REPAIR,
        modalities=(InputModality.TEXT,),
        file_types=("text/plain",),
        expected_capability=Capability.TEXT,
        expected_model="qwen3:4b",
        reason_fragment="code repair",
    ),
    RoutingCase(
        name="native PDF drafting",
        kind=TaskKind.DRAFTING,
        modalities=(InputModality.NATIVE_DOCUMENT,),
        file_types=("application/pdf",),
        expected_capability=Capability.TEXT,
        expected_model="qwen3:4b",
        reason_fragment="local parser",
    ),
    RoutingCase(
        name="native DOCX drafting",
        kind=TaskKind.DRAFTING,
        modalities=(InputModality.TEXT,),
        file_types=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        expected_capability=Capability.TEXT,
        expected_model="qwen3:4b",
        reason_fragment="local parser",
    ),
    RoutingCase(
        name="MIME identified PNG",
        kind=TaskKind.DRAFTING,
        modalities=(InputModality.TEXT,),
        file_types=("image/png",),
        expected_capability=Capability.VISION,
        expected_model="qwen3-vl:4b",
        reason_fragment="image",
    ),
    RoutingCase(
        name="corpus ingestion",
        kind=TaskKind.KNOWLEDGE_INGESTION,
        modalities=(InputModality.NATIVE_DOCUMENT,),
        file_types=("text/markdown",),
        expected_capability=Capability.EMBEDDING,
        expected_model="qwen3-embedding:0.6b",
        reason_fragment="knowledge ingestion",
    ),
    RoutingCase(
        name="corpus search",
        kind=TaskKind.KNOWLEDGE_SEARCH,
        modalities=(InputModality.TEXT,),
        file_types=(),
        expected_capability=Capability.EMBEDDING,
        expected_model="qwen3-embedding:0.6b",
        reason_fragment="knowledge search",
    ),
)


@pytest.mark.parametrize("case", SUPPORTED_CASES, ids=lambda case: case.name)
def test_supported_tasks_select_the_expected_local_capability(case: RoutingCase) -> None:
    """Map every supported MVP task/input combination without inference."""

    router = DeterministicCapabilityRouter(sample_model_profile())
    task = TaskDescriptor(
        task_id=f"task-{case.name.replace(' ', '-')}",
        kind=case.kind,
        summary=f"Handle the supported {case.name} task.",
        modalities=case.modalities,
        file_types=case.file_types,
    )

    decision = router.choose(task, sample_health_report())

    assert decision.capability is case.expected_capability
    assert decision.selected_model == case.expected_model
    assert case.reason_fragment in decision.reason
    assert decision.used_fallback is False
    assert decision.fallback_reason is None


def test_same_facts_always_produce_the_same_decision() -> None:
    """Keep routing deterministic and free of inference or mutable state."""

    router = DeterministicCapabilityRouter(sample_model_profile())
    task = TaskDescriptor(
        task_id="task-repeatable-route",
        kind=TaskKind.DRAFTING,
        summary="Draft from an uploaded native document.",
        modalities=(InputModality.NATIVE_DOCUMENT,),
        file_types=("application/pdf",),
    )
    health = sample_health_report()

    first = router.choose(task, health)
    second = router.choose(task, health)

    assert first == second


def _health_with(replacement: ModelHealth) -> AIHealthReport:
    base = sample_health_report()
    models = tuple(
        replacement if item.capability is replacement.capability else item
        for item in base.models
    )
    return base.model_copy(update={"models": models})


def test_missing_preferred_model_selects_the_profile_fallback() -> None:
    """Expose the safe local fallback chosen by installed-model health."""

    fallback_health = ModelHealth(
        capability=Capability.TEXT,
        status=ModelStatus.READY,
        installed=True,
        loadable=True,
        selected_model="qwen3:1.7b",
        fallback_reason=(
            "Preferred model 'qwen3:4b' is not installed; selected 'qwen3:1.7b'."
        ),
    )
    task = TaskDescriptor(
        task_id="task-fallback",
        kind=TaskKind.CHAT,
        summary="Answer a local text question.",
        modalities=(InputModality.TEXT,),
    )

    decision = DeterministicCapabilityRouter(sample_model_profile()).choose(
        task,
        _health_with(fallback_health),
    )

    assert decision.selected_model == "qwen3:1.7b"
    assert decision.used_fallback is True
    assert decision.fallback_reason == fallback_health.fallback_reason


def test_image_mime_on_knowledge_search_requires_visual_extraction() -> None:
    """Do not silently discard an image whose modality was mislabeled as text."""

    task = TaskDescriptor(
        task_id="image-search",
        kind=TaskKind.KNOWLEDGE_SEARCH,
        summary="Search the corpus using information in an attached image.",
        modalities=(InputModality.TEXT,),
        file_types=("image/png",),
    )

    with pytest.raises(NoEligibleCapability, match="visual extraction"):
        DeterministicCapabilityRouter(sample_model_profile()).choose(
            task,
            sample_health_report(),
        )


@pytest.mark.parametrize(
    ("task", "reason_fragment"),
    (
        (
            TaskDescriptor(
                task_id="unsupported-native-file",
                kind=TaskKind.DRAFTING,
                summary="Read an unsupported binary document.",
                modalities=(InputModality.NATIVE_DOCUMENT,),
                file_types=("application/octet-stream",),
            ),
            "application/octet-stream",
        ),
        (
            TaskDescriptor(
                task_id="unsupported-image-file",
                kind=TaskKind.VISUAL_ANALYSIS,
                summary="Inspect an unsupported SVG image.",
                modalities=(InputModality.IMAGE,),
                file_types=("image/svg+xml",),
            ),
            "image/svg+xml",
        ),
        (
            TaskDescriptor(
                task_id="missing-visual-input",
                kind=TaskKind.VISUAL_ANALYSIS,
                summary="Analyze an input that is not visual.",
                modalities=(InputModality.TEXT,),
            ),
            "visual input",
        ),
        (
            TaskDescriptor(
                task_id="unsafe-capability-override",
                kind=TaskKind.DRAFTING,
                summary="Try to send a scan to a text-only model.",
                modalities=(InputModality.SCANNED_PDF,),
                file_types=("application/pdf",),
                requested_capability=Capability.TEXT,
            ),
            "requested text",
        ),
        (
            TaskDescriptor(
                task_id="unsafe-knowledge-override",
                kind=TaskKind.KNOWLEDGE_SEARCH,
                summary="Try to bypass the embedding route.",
                modalities=(InputModality.TEXT,),
                requested_capability=Capability.TEXT,
            ),
            "requested text",
        ),
        (
            TaskDescriptor(
                task_id="scan-before-ingestion",
                kind=TaskKind.KNOWLEDGE_INGESTION,
                summary="Try to embed a scan before visual extraction.",
                modalities=(InputModality.SCANNED_PDF,),
                file_types=("application/pdf",),
            ),
            "visual extraction",
        ),
        (
            TaskDescriptor(
                task_id="unsupported-corpus-format",
                kind=TaskKind.KNOWLEDGE_INGESTION,
                summary="Try to index a format the local parser does not support.",
                modalities=(InputModality.TEXT,),
                file_types=("text/x-python",),
            ),
            "knowledge ingestion supports",
        ),
    ),
)
def test_unsupported_or_conflicting_inputs_are_explicitly_rejected(
    task: TaskDescriptor,
    reason_fragment: str,
) -> None:
    """Never guess a model for inputs outside the supported local pipeline."""

    router = DeterministicCapabilityRouter(sample_model_profile())

    with pytest.raises(NoEligibleCapability) as error:
        router.choose(task, sample_health_report())

    assert reason_fragment in str(error.value)


def test_missing_capability_health_is_rejected() -> None:
    """Do not route when no approved local candidate is reported ready."""

    missing = ModelHealth(
        capability=Capability.VISION,
        status=ModelStatus.MISSING,
        installed=False,
        last_error="No approved candidate is installed.",
    )
    task = TaskDescriptor(
        task_id="missing-vision",
        kind=TaskKind.VISUAL_ANALYSIS,
        summary="Inspect a local image.",
        modalities=(InputModality.IMAGE,),
        file_types=("image/webp",),
    )

    with pytest.raises(ModelNotInstalled, match="No approved candidate is installed"):
        DeterministicCapabilityRouter(sample_model_profile()).choose(
            task,
            _health_with(missing),
        )


def test_unavailable_runtime_is_rejected_before_model_selection() -> None:
    """Do not construct a decision when the local Ollama runtime is unavailable."""

    health = sample_health_report().model_copy(
        update={"runtime_ready": False, "runtime_error": "Ollama unavailable"}
    )
    task = TaskDescriptor(
        task_id="runtime-down",
        kind=TaskKind.CHAT,
        summary="Ask a local question.",
        modalities=(InputModality.TEXT,),
    )

    with pytest.raises(ModelRuntimeUnavailable, match="Ollama unavailable"):
        DeterministicCapabilityRouter(sample_model_profile()).choose(task, health)


def test_unavailable_knowledge_index_rejects_embedding_tasks() -> None:
    """Require both local embeddings and the local index for corpus work."""

    health = sample_health_report().model_copy(
        update={"knowledge_ready": False, "knowledge_error": "Chroma unavailable"}
    )
    task = TaskDescriptor(
        task_id="knowledge-down",
        kind=TaskKind.KNOWLEDGE_SEARCH,
        summary="Search the curated local corpus.",
        modalities=(InputModality.TEXT,),
    )

    with pytest.raises(KnowledgeIndexUnavailable, match="Chroma unavailable"):
        DeterministicCapabilityRouter(sample_model_profile()).choose(task, health)


def test_health_cannot_select_a_model_outside_the_active_profile() -> None:
    """Reject arbitrary or remote model substitutions at the routing boundary."""

    unapproved = ModelHealth(
        capability=Capability.TEXT,
        status=ModelStatus.READY,
        installed=True,
        loadable=True,
        selected_model="unapproved-model:latest",
    )
    task = TaskDescriptor(
        task_id="unapproved-model",
        kind=TaskKind.CHAT,
        summary="Answer a local question.",
        modalities=(InputModality.TEXT,),
    )

    with pytest.raises(OllamaPolicyViolation, match="not approved"):
        DeterministicCapabilityRouter(sample_model_profile()).choose(
            task,
            _health_with(unapproved),
        )


def test_active_profile_order_defines_whether_selection_is_a_fallback() -> None:
    """Use the injected profile rather than a hard-coded preferred text model."""

    profile = sample_model_profile().model_copy(
        update={"text_candidates": ("qwen3:1.7b", "qwen3:4b")}
    )
    preferred_health = ModelHealth(
        capability=Capability.TEXT,
        status=ModelStatus.READY,
        installed=True,
        loadable=True,
        selected_model="qwen3:1.7b",
    )
    task = TaskDescriptor(
        task_id="profile-order",
        kind=TaskKind.CHAT,
        summary="Use the active profile order.",
        modalities=(InputModality.TEXT,),
    )

    decision = DeterministicCapabilityRouter(profile).choose(
        task,
        _health_with(preferred_health),
    )

    assert decision.selected_model == "qwen3:1.7b"
    assert decision.used_fallback is False
