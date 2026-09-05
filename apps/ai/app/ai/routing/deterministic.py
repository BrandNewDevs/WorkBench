"""Pure, explainable selection of approved local model capabilities."""

from dataclasses import dataclass

from app.ai.errors import (
    KnowledgeIndexUnavailable,
    ModelNotInstalled,
    ModelRuntimeUnavailable,
    OllamaPolicyViolation,
)
from app.ai.schemas import (
    AIHealthReport,
    Capability,
    CapabilityDecision,
    InputModality,
    ModelHealth,
    ModelProfile,
    ModelStatus,
    TaskDescriptor,
    TaskKind,
)

_TEXT_TASKS = frozenset(
    {
        TaskKind.CHAT,
        TaskKind.PLANNING,
        TaskKind.DRAFTING,
        TaskKind.CODE_REPAIR,
    }
)
_KNOWLEDGE_TASKS = frozenset(
    {
        TaskKind.KNOWLEDGE_INGESTION,
        TaskKind.KNOWLEDGE_SEARCH,
    }
)
_TASK_LABELS = {
    TaskKind.CHAT: "chat",
    TaskKind.PLANNING: "planning",
    TaskKind.DRAFTING: "drafting",
    TaskKind.CODE_REPAIR: "code repair",
    TaskKind.KNOWLEDGE_INGESTION: "knowledge ingestion",
    TaskKind.KNOWLEDGE_SEARCH: "knowledge search",
    TaskKind.VISUAL_ANALYSIS: "visual analysis",
}


@dataclass(frozen=True, slots=True)
class DeterministicCapabilityRouter:
    """Map task facts and local health to one profile-approved model."""

    profile: ModelProfile

    def choose(self, task: TaskDescriptor, health: AIHealthReport) -> CapabilityDecision:
        """Return a local capability decision without inference or side effects."""

        capability, route_reason = self._route_task(task)
        selected_health = self._ready_health(capability, health)
        selected_model = selected_health.selected_model
        if selected_model is None:  # Ready health validates this; keep narrowing explicit for mypy.
            raise ModelNotInstalled(f"no ready local {capability.value} model is available")

        candidates = self._candidates_for(capability)
        if selected_model not in candidates:
            raise OllamaPolicyViolation(
                f"model '{selected_model}' is not approved by profile '{self.profile.profile_id}'"
            )

        preferred_model = candidates[0]
        used_fallback = selected_model != preferred_model
        fallback_reason = None
        if used_fallback:
            fallback_reason = selected_health.fallback_reason or (
                f"Preferred model '{preferred_model}' is unavailable; selected approved local "
                f"fallback '{selected_model}'."
            )

        return CapabilityDecision(
            capability=capability,
            selected_model=selected_model,
            reason=f"{route_reason} Selected local model '{selected_model}'.",
            used_fallback=used_fallback,
            fallback_reason=fallback_reason,
        )

    def _route_task(self, task: TaskDescriptor) -> tuple[Capability, str]:
        if task.kind in _KNOWLEDGE_TASKS:
            label = _TASK_LABELS[task.kind]
            return Capability.EMBEDDING, f"The {label} task requires local embeddings."

        modalities = frozenset(task.modalities)
        if InputModality.SCANNED_PDF in modalities:
            return (
                Capability.VISION,
                "The task includes a scanned PDF, so visual extraction must run first.",
            )
        if InputModality.IMAGE in modalities:
            return (
                Capability.VISION,
                "The task includes an image, so visual understanding is required.",
            )
        if task.kind is TaskKind.VISUAL_ANALYSIS:
            return Capability.VISION, "The visual analysis task requires visual understanding."

        if InputModality.NATIVE_DOCUMENT in modalities:
            return (
                Capability.TEXT,
                "The native document will be extracted by the local parser before text-model use.",
            )
        if task.kind in _TEXT_TASKS:
            label = _TASK_LABELS[task.kind]
            return Capability.TEXT, f"The {label} task requires local text reasoning."

        raise ValueError(f"unsupported task kind: {task.kind.value}")

    def _ready_health(
        self,
        capability: Capability,
        health: AIHealthReport,
    ) -> ModelHealth:
        if not health.runtime_ready:
            raise ModelRuntimeUnavailable(
                health.runtime_error or "the local model runtime is unavailable"
            )
        if capability is Capability.EMBEDDING and not health.knowledge_ready:
            raise KnowledgeIndexUnavailable(
                health.knowledge_error or "the local knowledge index is unavailable"
            )

        matches = tuple(item for item in health.models if item.capability is capability)
        if len(matches) != 1:
            raise ModelNotInstalled(
                f"expected one health result for local {capability.value} capability"
            )
        selected = matches[0]
        if selected.status is not ModelStatus.READY:
            detail = selected.last_error or selected.status.value
            raise ModelNotInstalled(
                f"no ready local {capability.value} model is available: {detail}"
            )
        return selected

    def _candidates_for(self, capability: Capability) -> tuple[str, ...]:
        if capability is Capability.TEXT:
            return self.profile.text_candidates
        if capability is Capability.VISION:
            return self.profile.vision_candidates
        return self.profile.embedding_candidates
