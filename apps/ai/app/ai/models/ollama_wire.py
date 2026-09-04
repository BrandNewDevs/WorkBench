"""Private Pydantic models for the local Ollama HTTP wire format."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class OllamaRequestModel(BaseModel):
    """Strict base for request data controlled by WorkBench."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid", frozen=True)


class OllamaChatMessage(OllamaRequestModel):
    """One text or normalized-image message sent to local Ollama."""

    role: Literal["system", "user"]
    content: str
    images: tuple[str, ...] | None = None


class OllamaGenerationOptions(OllamaRequestModel):
    """Bounded generation options used by structured chat requests."""

    temperature: float = Field(ge=0, le=1)
    num_ctx: int = Field(gt=0)
    num_predict: int = Field(gt=0)


class OllamaChatRequest(OllamaRequestModel):
    """Validated non-streaming request for structured local generation."""

    model: str = Field(min_length=1)
    messages: tuple[OllamaChatMessage, ...] = Field(min_length=1)
    stream: Literal[False] = False
    think: Literal[False] = False
    format: dict[str, JsonValue]
    keep_alive: str | int
    options: OllamaGenerationOptions


class OllamaEmbedRequest(OllamaRequestModel):
    """Validated local embedding request."""

    model: str = Field(min_length=1)
    input: tuple[str, ...] = Field(min_length=1)
    truncate: Literal[False] = False


class OllamaUnloadRequest(OllamaRequestModel):
    """Validated request to unload one tracked generative model."""

    model: str = Field(min_length=1)
    messages: tuple[OllamaChatMessage, ...] = ()
    stream: Literal[False] = False
    keep_alive: Literal[0] = 0

    @field_validator("messages")
    @classmethod
    def require_empty_messages(
        cls, messages: tuple[OllamaChatMessage, ...]
    ) -> tuple[OllamaChatMessage, ...]:
        """Keep unload requests distinct from inference requests."""

        if messages:
            raise ValueError("Ollama unload requests must not contain messages")
        return messages


class OllamaWireModel(BaseModel):
    """Allow additive Ollama response fields while validating fields we consume."""

    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")


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
