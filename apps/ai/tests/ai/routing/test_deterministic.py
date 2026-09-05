"""Public behavior tests for the deterministic local capability router."""

from dataclasses import dataclass

import pytest

from app.ai.evaluation.samples import sample_health_report, sample_model_profile
from app.ai.routing import DeterministicCapabilityRouter
from app.ai.schemas import Capability, InputModality, TaskDescriptor, TaskKind


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
        modalities=(InputModality.NATIVE_DOCUMENT,),
        file_types=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        expected_capability=Capability.TEXT,
        expected_model="qwen3:4b",
        reason_fragment="local parser",
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
