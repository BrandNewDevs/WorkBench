"""Typed AI failures for Backend 1 to translate into workflow or HTTP errors."""


class AIError(Exception):
    """Base class for expected AI-layer failures."""


class ModelRuntimeUnavailable(AIError):
    """The configured local model runtime cannot be reached."""


class ModelNotInstalled(AIError):
    """No approved local candidate is installed for the requested capability."""


class ModelCapacityError(AIError):
    """The local runtime lacks capacity to load or run a selected model."""


class InvalidStructuredOutput(AIError):
    """A local model response failed schema validation after allowed retries."""


class UnsupportedVisualInput(AIError):
    """A supplied image or document cannot be safely processed."""


class KnowledgeIndexUnavailable(AIError):
    """The local knowledge index is unavailable or incompatible."""


class NoRelevantEvidence(AIError):
    """No local evidence passed the requested relevance threshold."""

