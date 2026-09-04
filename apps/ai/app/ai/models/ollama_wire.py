"""Private Pydantic models for the local Ollama HTTP wire format."""

from pydantic import BaseModel, ConfigDict, Field


class OllamaWireModel(BaseModel):
    """Allow additive Ollama response fields while validating fields we consume."""

    model_config = ConfigDict(extra="ignore")


class OllamaModelDetails(OllamaWireModel):
    """Model metadata returned by the tags endpoint."""

    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None


class OllamaModelRecord(OllamaWireModel):
    """One installed model returned by the tags endpoint."""

    name: str = Field(min_length=1)
    model: str | None = None
    size: int = Field(default=0, ge=0)
    digest: str = ""
    details: OllamaModelDetails = Field(default_factory=OllamaModelDetails)


class OllamaTagsResponse(OllamaWireModel):
    """Validated response from ``GET /api/tags``."""

    models: tuple[OllamaModelRecord, ...]


class OllamaAssistantMessage(OllamaWireModel):
    """Assistant content returned by a non-streaming chat request."""

    role: str
    content: str


class OllamaChatResponse(OllamaWireModel):
    """Validated response from ``POST /api/chat``."""

    model: str = Field(min_length=1)
    message: OllamaAssistantMessage
    done: bool
    done_reason: str | None = None
    total_duration: int | None = Field(default=None, ge=0)
    load_duration: int | None = Field(default=None, ge=0)
    prompt_eval_count: int | None = Field(default=None, ge=0)
    prompt_eval_duration: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)
    eval_duration: int | None = Field(default=None, ge=0)


class OllamaEmbedResponse(OllamaWireModel):
    """Validated response from ``POST /api/embed``."""

    model: str = Field(min_length=1)
    embeddings: tuple[tuple[float, ...], ...] = Field(min_length=1)
    total_duration: int | None = Field(default=None, ge=0)
    load_duration: int | None = Field(default=None, ge=0)
    prompt_eval_count: int | None = Field(default=None, ge=0)
