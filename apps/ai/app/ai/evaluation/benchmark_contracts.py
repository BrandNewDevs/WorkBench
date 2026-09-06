"""Content-free contracts for hardware benchmarks and model promotion."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.ai.evaluation.contracts import GoldenSuiteResult
from app.ai.schemas import Capability, ContractModel, InstalledModel

_REQUIRED_PROMPT_VERSION_KEYS = frozenset(
    {
        "planning",
        "vision",
        "groundedDrafting",
        "toolProposal",
        "codeRepair",
        "uncertainty",
    }
)


class HardwareKind(StrEnum):
    """Supported local hardware classes without identifying a user or host."""

    WORKSTATION = "workstation"
    JETSON = "jetson"
    UNKNOWN = "unknown"


class PromotionOutcome(StrEnum):
    """Deterministic disposition for one candidate capability."""

    PROMOTE = "promote"
    RETAIN_SAFE = "retainSafe"
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"


class HardwareSnapshot(ContractModel):
    """Sanitized local-machine facts required to interpret a benchmark."""

    schema_version: Literal["hardware-snapshot-v1"] = "hardware-snapshot-v1"
    captured_at_utc: AwareDatetime
    hardware_kind: HardwareKind
    operating_system: str = Field(min_length=1)
    operating_system_release: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    total_memory_bytes: int | None = Field(default=None, gt=0)
    available_storage_bytes: int = Field(ge=0)
    accelerator_name: str | None = Field(default=None, min_length=1)
    accelerator_memory_bytes: int | None = Field(default=None, gt=0)
    accelerator_uses_shared_memory: bool = False
    jetson_model: str | None = Field(default=None, min_length=1)
    jetpack_version: str | None = Field(default=None, min_length=1)
    nvidia_platform_release: str | None = Field(default=None, min_length=1)
    cuda_version: str | None = Field(default=None, min_length=1)
    ollama_version: str | None = Field(default=None, min_length=1)
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def jetson_facts_are_consistent(self) -> "HardwareSnapshot":
        if self.hardware_kind is HardwareKind.JETSON and self.jetson_model is None:
            raise ValueError("Jetson hardware requires its exact device model")
        if self.accelerator_uses_shared_memory and self.total_memory_bytes is None:
            raise ValueError("shared accelerator memory requires total system memory")
        return self


class ResourceObservation(ContractModel):
    """Peak resource facts sampled while the unchanged golden suite runs."""

    measurement_source: str = Field(min_length=1)
    sample_count: int = Field(ge=0)
    memory_capacity_bytes: int | None = Field(default=None, gt=0)
    peak_memory_used_bytes: int | None = Field(default=None, ge=0)
    max_temperature_celsius: float | None = Field(default=None, ge=0)
    thermal_throttling_observed: bool | None = None
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def measurements_are_complete_and_bounded(self) -> "ResourceObservation":
        if self.sample_count == 0 and any(
            value is not None
            for value in (
                self.memory_capacity_bytes,
                self.peak_memory_used_bytes,
                self.max_temperature_celsius,
                self.thermal_throttling_observed,
            )
        ):
            raise ValueError("resource values require at least one sample")
        if (
            self.memory_capacity_bytes is not None
            and self.peak_memory_used_bytes is not None
            and self.peak_memory_used_bytes > self.memory_capacity_bytes
        ):
            raise ValueError("peak memory use cannot exceed measured capacity")
        return self

    @property
    def memory_pressure_ratio(self) -> float | None:
        if self.memory_capacity_bytes is None or self.peak_memory_used_bytes is None:
            return None
        return self.peak_memory_used_bytes / self.memory_capacity_bytes


class CapabilityBenchmark(ContractModel):
    """Comparable evidence for one model capability in a profile run."""

    capability: Capability
    configured_preferred_model: str = Field(min_length=1)
    health_selected_model: str | None = Field(default=None, min_length=1)
    observed_models: tuple[str, ...]
    evaluated_runs: int = Field(ge=1)
    preferred_model_runs: int = Field(ge=0)
    model_digest: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None
    used_fallback: bool
    quality_gates_passed: bool
    average_operation_duration_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def preferred_runs_do_not_exceed_total(self) -> "CapabilityBenchmark":
        if self.preferred_model_runs > self.evaluated_runs:
            raise ValueError("preferred-model runs cannot exceed evaluated runs")
        return self


class BenchmarkReport(ContractModel):
    """One reproducible golden-suite result tied to sanitized machine facts."""

    schema_version: Literal["model-benchmark-v1"] = "model-benchmark-v1"
    created_at_utc: AwareDatetime
    profile_id: str = Field(min_length=1)
    prompt_versions: dict[str, str]
    hardware: HardwareSnapshot
    installed_profile_models: tuple[InstalledModel, ...]
    capabilities: tuple[CapabilityBenchmark, ...] = Field(min_length=3, max_length=3)
    resources: ResourceObservation
    golden_suite: GoldenSuiteResult
    average_workflow_duration_ms: float = Field(ge=0)
    average_generation_tokens_per_second: float | None = Field(default=None, ge=0)

    @field_validator("prompt_versions")
    @classmethod
    def require_complete_prompt_version_evidence(
        cls,
        versions: dict[str, str],
    ) -> dict[str, str]:
        if set(versions) != _REQUIRED_PROMPT_VERSION_KEYS:
            raise ValueError("benchmark report requires every approved prompt version")
        if any(not version.strip() for version in versions.values()):
            raise ValueError("benchmark prompt versions must not be blank")
        return versions

    @model_validator(mode="after")
    def has_each_capability_once(self) -> "BenchmarkReport":
        capabilities = tuple(item.capability for item in self.capabilities)
        if set(capabilities) != set(Capability) or len(capabilities) != len(set(capabilities)):
            raise ValueError("benchmark report requires text, vision, and embedding once each")
        return self

    def for_capability(self, capability: Capability) -> CapabilityBenchmark:
        """Return one required capability record."""

        return next(item for item in self.capabilities if item.capability is capability)


class PromotionThresholds(ContractModel):
    """Reviewable limits applied identically to every candidate comparison."""

    max_operation_duration_ratio: float = Field(default=2.0, ge=1)
    max_memory_pressure_ratio: float = Field(default=0.9, gt=0, le=1)
    max_temperature_celsius: float = Field(default=85.0, gt=0)
    maximum_schema_failures: int = Field(default=0, ge=0)


class CapabilityPromotionDecision(ContractModel):
    """Human-readable recommendation that never changes active configuration."""

    capability: Capability
    outcome: PromotionOutcome
    baseline_model: str = Field(min_length=1)
    candidate_model: str = Field(min_length=1)
    recommended_model: str = Field(min_length=1)
    reasons: tuple[str, ...] = Field(min_length=1)
    baseline_average_duration_ms: float = Field(ge=0)
    candidate_average_duration_ms: float = Field(ge=0)


class PromotionReport(ContractModel):
    """Independent text/vision decisions with the embedding space pinned."""

    schema_version: Literal["model-promotion-v1"] = "model-promotion-v1"
    created_at_utc: AwareDatetime
    baseline_profile_id: str = Field(min_length=1)
    candidate_profile_id: str = Field(min_length=1)
    thresholds: PromotionThresholds
    decisions: tuple[CapabilityPromotionDecision, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def has_each_capability_once(self) -> "PromotionReport":
        capabilities = tuple(item.capability for item in self.decisions)
        if set(capabilities) != set(Capability) or len(capabilities) != len(set(capabilities)):
            raise ValueError("promotion report requires text, vision, and embedding once each")
        return self

    @property
    def has_promotions(self) -> bool:
        return any(item.outcome is PromotionOutcome.PROMOTE for item in self.decisions)


def utc_now() -> datetime:
    """Return one timezone-aware timestamp for report creation."""

    return datetime.now(UTC)
