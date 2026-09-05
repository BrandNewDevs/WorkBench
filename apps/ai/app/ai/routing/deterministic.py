"""Pure, explainable selection of approved local model capabilities."""

from dataclasses import dataclass

from app.ai.errors import (
    KnowledgeIndexUnavailable,
    ModelNotInstalled,
    ModelRuntimeUnavailable,
    NoEligibleCapability,
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
_SUPPORTED_IMAGE_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
_SUPPORTED_NATIVE_DOCUMENT_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/markdown",
        "text/plain",
    }
)
_SUPPORTED_NON_DOCUMENT_TEXT_MIME_TYPES = frozenset({"application/json"})


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
        file_types = self._validated_file_types(task)
        modalities = frozenset(task.modalities)

        if task.kind in _KNOWLEDGE_TASKS:
            if (
                modalities.intersection({InputModality.IMAGE, InputModality.SCANNED_PDF})
                or file_types.intersection(_SUPPORTED_IMAGE_MIME_TYPES)
            ):
                raise NoEligibleCapability(
                    "visual input must complete local visual extraction before knowledge ingestion "
                    "or search"
                )
            unsupported_corpus_types = file_types.difference(
                _SUPPORTED_NATIVE_DOCUMENT_MIME_TYPES
            )
            if (
                task.kind is TaskKind.KNOWLEDGE_INGESTION
                and unsupported_corpus_types
            ):
                supplied = ", ".join(sorted(unsupported_corpus_types))
                raise NoEligibleCapability(
                    "knowledge ingestion supports only PDF, DOCX, plain text, or Markdown; "
                    f"received {supplied}"
                )
            label = _TASK_LABELS[task.kind]
            if (
                task.kind is TaskKind.KNOWLEDGE_INGESTION
                and self._has_native_document(task, file_types)
            ):
                return self._honor_requested_capability(
                    task,
                    (
                        Capability.EMBEDDING,
                        f"The {label} task uses the local parser before local embeddings.",
                    ),
                )
            return self._honor_requested_capability(
                task,
                (Capability.EMBEDDING, f"The {label} task requires local embeddings."),
            )

        if InputModality.SCANNED_PDF in modalities:
            if file_types and "application/pdf" not in file_types:
                supplied = ", ".join(sorted(file_types))
                raise NoEligibleCapability(
                    f"scanned PDF input requires application/pdf, but received {supplied}"
                )
            decision = (
                Capability.VISION,
                "The task includes a scanned PDF, so visual extraction must run first.",
            )
            return self._honor_requested_capability(task, decision)

        has_image_mime = bool(file_types.intersection(_SUPPORTED_IMAGE_MIME_TYPES))
        if InputModality.IMAGE in modalities or has_image_mime:
            if file_types and not has_image_mime:
                supplied = ", ".join(sorted(file_types))
                raise NoEligibleCapability(
                    f"image input requires JPEG, PNG, or WebP, but received {supplied}"
                )
            decision = (
                Capability.VISION,
                "The task includes an image, so visual understanding is required.",
            )
            return self._honor_requested_capability(task, decision)

        if task.kind is TaskKind.VISUAL_ANALYSIS:
            raise NoEligibleCapability(
                "visual analysis requires an image or scanned PDF visual input"
            )

        if self._has_native_document(task, file_types):
            decision = (
                Capability.TEXT,
                "The native document will be extracted by the local parser before text-model use.",
            )
            return self._honor_requested_capability(task, decision)

        if task.kind in _TEXT_TASKS:
            label = _TASK_LABELS[task.kind]
            decision = Capability.TEXT, f"The {label} task requires local text reasoning."
            return self._honor_requested_capability(task, decision)

        raise NoEligibleCapability(f"unsupported task kind: {task.kind.value}")

    @staticmethod
    def _validated_file_types(task: TaskDescriptor) -> frozenset[str]:
        file_types = frozenset(
            raw_file_type.partition(";")[0].strip().lower()
            for raw_file_type in task.file_types
        )
        unsupported = sorted(
            file_type
            for file_type in file_types
            if not file_type.startswith("text/")
            and file_type not in _SUPPORTED_IMAGE_MIME_TYPES
            and file_type not in _SUPPORTED_NATIVE_DOCUMENT_MIME_TYPES
            and file_type not in _SUPPORTED_NON_DOCUMENT_TEXT_MIME_TYPES
        )
        if unsupported:
            raise NoEligibleCapability(
                f"unsupported local input MIME type: {', '.join(unsupported)}"
            )
        if (
            InputModality.NATIVE_DOCUMENT in task.modalities
            and file_types
            and not file_types.intersection(_SUPPORTED_NATIVE_DOCUMENT_MIME_TYPES)
        ):
            supplied = ", ".join(sorted(file_types))
            raise NoEligibleCapability(
                f"native document input requires PDF, DOCX, plain text, or Markdown, but "
                f"received {supplied}"
            )
        return file_types

    @staticmethod
    def _has_native_document(
        task: TaskDescriptor,
        file_types: frozenset[str],
    ) -> bool:
        modalities = frozenset(task.modalities)
        native_binary_mime_types = _SUPPORTED_NATIVE_DOCUMENT_MIME_TYPES.difference(
            {"text/markdown", "text/plain"}
        )
        return (
            InputModality.NATIVE_DOCUMENT in modalities
            or bool(file_types.intersection(native_binary_mime_types))
        )

    @staticmethod
    def _honor_requested_capability(
        task: TaskDescriptor,
        decision: tuple[Capability, str],
    ) -> tuple[Capability, str]:
        capability, reason = decision
        requested = task.requested_capability
        if requested is not None and requested is not capability:
            raise NoEligibleCapability(
                f"task facts require {capability.value}, but the task requested {requested.value}"
            )
        return capability, reason

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
        if not matches:
            raise ModelNotInstalled(
                f"expected one health result for local {capability.value} capability"
            )
        if len(matches) > 1:
            raise NoEligibleCapability(
                f"multiple health results exist for local {capability.value} capability"
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
