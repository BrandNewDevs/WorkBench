"""Approved local model profiles selected through application configuration."""

from types import MappingProxyType

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.schemas import GenerationLimits, ModelProfile

SAFE_8GB_PROFILE_ID = "safe-8gb"
JETSON_CANDIDATE_PROFILE_ID = "jetson-candidate"


def _profile(
    *,
    profile_id: str,
    text_candidates: tuple[str, ...],
    vision_candidates: tuple[str, ...],
) -> ModelProfile:
    """Build an immutable profile with conservative workstation limits."""

    return ModelProfile(
        profile_id=profile_id,
        text_candidates=text_candidates,
        vision_candidates=vision_candidates,
        embedding_candidates=("qwen3-embedding:0.6b",),
        text_limits=GenerationLimits(
            context_window=8_192,
            max_output_tokens=2_048,
            timeout_seconds=180,
        ),
        vision_limits=GenerationLimits(
            context_window=8_192,
            max_output_tokens=2_048,
            timeout_seconds=300,
        ),
        embedding_batch_size=8,
    )


_APPROVED_PROFILES = MappingProxyType(
    {
        SAFE_8GB_PROFILE_ID: _profile(
            profile_id=SAFE_8GB_PROFILE_ID,
            text_candidates=("qwen3:4b", "qwen3:1.7b"),
            vision_candidates=("qwen3-vl:4b", "qwen3-vl:2b"),
        ),
        JETSON_CANDIDATE_PROFILE_ID: _profile(
            profile_id=JETSON_CANDIDATE_PROFILE_ID,
            text_candidates=("qwen3:8b", "qwen3:4b", "qwen3:1.7b"),
            vision_candidates=("qwen3-vl:8b", "qwen3-vl:4b", "qwen3-vl:2b"),
        ),
    }
)


class ModelSettings(BaseSettings):
    """Environment-backed selection of an approved local model profile."""

    model_config = SettingsConfigDict(
        env_prefix="WORKBENCH_AI_",
        extra="ignore",
        frozen=True,
    )

    model_profile: str = Field(default=SAFE_8GB_PROFILE_ID, min_length=1)


def load_model_profile(settings: ModelSettings | None = None) -> ModelProfile:
    """Load the selected approved profile or reject an unknown configuration."""

    selected = settings or ModelSettings()
    try:
        return _APPROVED_PROFILES[selected.model_profile]
    except KeyError as error:
        approved = ", ".join(sorted(_APPROVED_PROFILES))
        raise ValueError(
            f"unknown model profile '{selected.model_profile}'; approved profiles: {approved}"
        ) from error
