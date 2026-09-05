"""Typed AI failures for Backend 1 to translate into workflow or HTTP errors."""


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
