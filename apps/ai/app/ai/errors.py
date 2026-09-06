"""Typed AI failures for Backend 1 to translate into workflow or HTTP errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.schemas import InferenceMetrics


class AIError(Exception):
    """Base class for expected AI-layer failures."""


class ModelRuntimeUnavailable(AIError):
    """The configured local model runtime cannot be reached."""


class ModelNotInstalled(AIError):
    """No approved local candidate is installed for the requested capability."""


class ModelCapacityError(AIError):
    """The local runtime lacks capacity to load or run a selected model."""


class ModelRequestTimeout(AIError):
    """A bounded local model request exceeded its configured deadline."""


class ModelRequestFailed(AIError):
    """The local runtime rejected a request for a non-capacity reason."""


class OllamaPolicyViolation(AIError):
    """Configuration or an endpoint would cross the approved local-only seam."""


class NoEligibleCapability(AIError):
    """Task facts do not map to a supported, policy-eligible local capability."""


class InvalidStructuredOutput(AIError):
    """A local model response failed schema validation after allowed retries."""

    def __init__(
        self,
        message: str,
        *,
        model: str | None = None,
        metrics: InferenceMetrics | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        """Keep optional, content-free evidence from a completed model attempt."""

        if (model is None) != (metrics is None):
            raise ValueError("model and metrics must be supplied together")
        super().__init__(message)
        self.model = model
        self.metrics = metrics
        self.fallback_reason = fallback_reason

    @property
    def used_fallback(self) -> bool:
        """Report whether the rejected output came from a fallback candidate."""

        return self.fallback_reason is not None

    def attach_inference_evidence(
        self,
        *,
        model: str,
        metrics: InferenceMetrics,
    ) -> None:
        """Attach safe invocation facts without retaining prompt or response content."""

        self.model = model
        self.metrics = metrics

    def attach_fallback_reason(self, reason: str | None) -> None:
        """Preserve adapter fallback context while the original error propagates."""

        if reason is not None:
            self.fallback_reason = reason


class GroundingViolation(InvalidStructuredOutput):
    """A generated critical claim cites evidence not supplied by the application."""


class InvalidToolProposal(InvalidStructuredOutput):
    """A generated proposal violates Backend 1's allowed tool registry."""


class UnsupportedVisualInput(AIError):
    """A supplied image or document cannot be safely processed."""


class VisualInputTooLarge(UnsupportedVisualInput):
    """A supplied visual exceeds a configured local processing bound."""


class EncryptedVisualInput(UnsupportedVisualInput):
    """A supplied PDF is encrypted and cannot be inspected without a secret."""


class CorruptVisualInput(UnsupportedVisualInput):
    """A supplied image or PDF cannot be decoded safely."""


class KnowledgeIndexUnavailable(AIError):
    """The local knowledge index is unavailable or incompatible."""


class UnsupportedKnowledgeInput(AIError):
    """A supplied knowledge document uses an unsupported format."""


class CorruptKnowledgeInput(UnsupportedKnowledgeInput):
    """A supplied knowledge document cannot be decoded safely."""


class KnowledgeInputTooLarge(UnsupportedKnowledgeInput):
    """A supplied knowledge document exceeds configured processing limits."""


class NoRelevantEvidence(AIError):
    """No local evidence passed the requested relevance threshold."""


class GoldenCorpusError(AIError):
    """The sanitized golden corpus is missing, corrupt, or internally inconsistent."""
