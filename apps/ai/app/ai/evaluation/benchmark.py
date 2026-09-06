"""Build content-free reports and compare stronger local-model candidates."""

from collections.abc import Iterable
from datetime import datetime
from statistics import fmean

from app.ai.evaluation.benchmark_contracts import (
    BenchmarkReport,
    CapabilityBenchmark,
    CapabilityPromotionDecision,
    HardwareKind,
    HardwareSnapshot,
    PromotionOutcome,
    PromotionReport,
    PromotionThresholds,
    ResourceObservation,
    utc_now,
)
from app.ai.evaluation.contracts import GoldenRunResult, GoldenSuiteResult
from app.ai.prompts.code_repair import CODE_REPAIR_PROMPT_VERSION
from app.ai.prompts.grounded_drafting import GROUNDED_DRAFTING_PROMPT_VERSION
from app.ai.prompts.planning import TASK_PLANNING_PROMPT_VERSION
from app.ai.prompts.tool_proposal import TOOL_PROPOSAL_PROMPT_VERSION
from app.ai.prompts.uncertainty import UNCERTAINTY_HANDLING_PROMPT_VERSION
from app.ai.prompts.vision import VISION_EXTRACTION_PROMPT_VERSION
from app.ai.schemas import (
    Capability,
    InstalledModel,
    ModelProfile,
    ModelRuntimeHealth,
    ModelStatus,
)

_QUALITY_GATES = {
    Capability.TEXT: frozenset(
        {
            "required-draft-sections",
            "citation-integrity",
            "required-sop-citation",
            "required-uncertainty",
            "forbidden-unsupported-conclusions",
            "final-schema",
        }
    ),
    Capability.VISION: frozenset({"vision-recall", "allowed-severity-values"}),
    Capability.EMBEDDING: frozenset({"retrieval-recall-at-3"}),
}
_OPERATION_NAMES = {
    Capability.TEXT: ("groundedDrafting",),
    Capability.VISION: ("visionExtraction",),
    Capability.EMBEDDING: ("knowledgeIngestion", "knowledgeRetrieval"),
}
_PROMPT_VERSIONS = {
    "planning": TASK_PLANNING_PROMPT_VERSION,
    "vision": VISION_EXTRACTION_PROMPT_VERSION,
    "groundedDrafting": GROUNDED_DRAFTING_PROMPT_VERSION,
    "toolProposal": TOOL_PROPOSAL_PROMPT_VERSION,
    "codeRepair": CODE_REPAIR_PROMPT_VERSION,
    "uncertainty": UNCERTAINTY_HANDLING_PROMPT_VERSION,
}


def build_benchmark_report(
    *,
    profile: ModelProfile,
    hardware: HardwareSnapshot,
    runtime_health: ModelRuntimeHealth,
    installed_models: tuple[InstalledModel, ...],
    golden_suite: GoldenSuiteResult,
    resources: ResourceObservation,
    created_at_utc: datetime | None = None,
) -> BenchmarkReport:
    """Bind an unchanged golden result to exact local models and hardware facts."""

    health_by_capability = {item.capability: item for item in runtime_health.models}
    installed_by_name = {item.name: item for item in installed_models}
    candidates = {
        Capability.TEXT: profile.text_candidates,
        Capability.VISION: profile.vision_candidates,
        Capability.EMBEDDING: profile.embedding_candidates,
    }
    capability_reports: list[CapabilityBenchmark] = []
    for capability in Capability:
        preferred_model = candidates[capability][0]
        health = health_by_capability.get(capability)
        health_selected = (
            health.selected_model
            if health is not None and health.status is ModelStatus.READY
            else None
        )
        observed_by_run = tuple(
            run.metrics.selected_models.get(capability.value, ())
            for run in golden_suite.runs
        )
        observed_models = tuple(
            dict.fromkeys(model for models in observed_by_run for model in models)
        )
        preferred_runs = sum(models == (preferred_model,) for models in observed_by_run)
        model_metadata = installed_by_name.get(health_selected or preferred_model)
        capability_reports.append(
            CapabilityBenchmark(
                capability=capability,
                configured_preferred_model=preferred_model,
                health_selected_model=health_selected,
                observed_models=observed_models,
                evaluated_runs=len(golden_suite.runs),
                preferred_model_runs=preferred_runs,
                model_digest=model_metadata.digest if model_metadata else None,
                parameter_size=model_metadata.parameter_size if model_metadata else None,
                quantization_level=(
                    model_metadata.quantization_level if model_metadata else None
                ),
                used_fallback=(
                    health_selected not in (None, preferred_model)
                    or any(model != preferred_model for model in observed_models)
                ),
                quality_gates_passed=_quality_gates_passed(
                    golden_suite.runs,
                    capability,
                ),
                average_operation_duration_ms=_average_operation_duration(
                    golden_suite.runs,
                    capability,
                ),
            )
        )

    configured_names = {
        model for configured in candidates.values() for model in configured
    }
    relevant_models = tuple(
        model for model in installed_models if model.name in configured_names
    )
    tokens_per_second = tuple(
        run.metrics.generation_tokens_per_second
        for run in golden_suite.runs
        if run.metrics.generation_tokens_per_second is not None
    )
    return BenchmarkReport(
        created_at_utc=created_at_utc or utc_now(),
        profile_id=profile.profile_id,
        prompt_versions=_PROMPT_VERSIONS,
        hardware=hardware,
        installed_profile_models=relevant_models,
        capabilities=tuple(capability_reports),
        resources=resources,
        golden_suite=golden_suite,
        average_workflow_duration_ms=fmean(
            run.metrics.total_duration_ms for run in golden_suite.runs
        ),
        average_generation_tokens_per_second=(
            fmean(tokens_per_second) if tokens_per_second else None
        ),
    )


def compare_for_promotion(
    baseline: BenchmarkReport,
    candidate: BenchmarkReport,
    *,
    thresholds: PromotionThresholds | None = None,
    created_at_utc: datetime | None = None,
) -> PromotionReport:
    """Recommend models without activating, installing, or unloading anything."""

    if baseline.golden_suite.corpus_id != candidate.golden_suite.corpus_id:
        raise ValueError("baseline and candidate reports must use the same golden corpus")
    selected_thresholds = thresholds or PromotionThresholds()
    decisions = tuple(
        _capability_decision(
            capability,
            baseline,
            candidate,
            selected_thresholds,
        )
        for capability in Capability
    )
    return PromotionReport(
        created_at_utc=created_at_utc or utc_now(),
        baseline_profile_id=baseline.profile_id,
        candidate_profile_id=candidate.profile_id,
        thresholds=selected_thresholds,
        decisions=decisions,
    )


def _capability_decision(
    capability: Capability,
    baseline_report: BenchmarkReport,
    candidate_report: BenchmarkReport,
    thresholds: PromotionThresholds,
) -> CapabilityPromotionDecision:
    baseline = baseline_report.for_capability(capability)
    candidate = candidate_report.for_capability(capability)
    baseline_model = baseline.health_selected_model or baseline.configured_preferred_model
    candidate_model = candidate.configured_preferred_model
    reasons: list[str] = []

    if capability is Capability.EMBEDDING:
        if candidate_model != baseline_model or candidate.observed_models not in (
            (),
            (baseline_model,),
        ):
            reasons.append(
                "Embedding model changes require a deliberate collection migration and re-index."
            )
            outcome = PromotionOutcome.REJECT
        else:
            reasons.append(
                "The embedding model remains fixed so existing Chroma vectors are not mixed."
            )
            outcome = PromotionOutcome.RETAIN_SAFE
        return _decision(
            capability,
            outcome,
            baseline_model,
            candidate_model,
            baseline,
            candidate,
            reasons,
        )

    if candidate_model == baseline_model:
        reasons.append(
            "This capability uses the safe model; there is no stronger candidate to promote."
        )
        return _decision(
            capability,
            PromotionOutcome.RETAIN_SAFE,
            baseline_model,
            candidate_model,
            baseline,
            candidate,
            reasons,
        )

    baseline_failed = not baseline.quality_gates_passed
    wrong_hardware = candidate_report.hardware.hardware_kind is not HardwareKind.JETSON
    missing_platform_facts = any(
        (
            candidate_report.hardware.jetson_model is None,
            candidate_report.hardware.total_memory_bytes is None,
            candidate_report.hardware.ollama_version is None,
            (
                candidate_report.hardware.jetpack_version is None
                and candidate_report.hardware.nvidia_platform_release is None
            ),
        )
    )
    missing_model_identity = any(
        (
            candidate.health_selected_model is None,
            not candidate.model_digest,
            not candidate.parameter_size,
            not candidate.quantization_level,
        )
    )
    candidate_quality_failed = not candidate.quality_gates_passed
    fallback_used = candidate.used_fallback
    candidate_missing_runs = candidate.preferred_model_runs != candidate.evaluated_runs
    if baseline_failed:
        reasons.append("The safe baseline did not pass this capability's golden quality gates.")
    if wrong_hardware:
        reasons.append("The candidate report was not recorded on an identified Jetson device.")
    if missing_platform_facts:
        reasons.append(
            "The candidate report is missing Jetson memory, platform-release, or Ollama facts."
        )
    if missing_model_identity:
        reasons.append(
            "The selected candidate model tag, digest, parameter size, or quantization is missing."
        )
    if candidate_quality_failed:
        reasons.append("The candidate failed one or more capability-specific golden quality gates.")
    if fallback_used:
        reasons.append("The candidate used a fallback model during evaluation.")
    if candidate_missing_runs:
        reasons.append("The preferred candidate was not observed in every golden run.")
    schema_failures = sum(
        run.metrics.schema_failures_by_capability.get(capability.value, 0)
        for run in candidate_report.golden_suite.runs
    )
    excess_schema_failures = schema_failures > thresholds.maximum_schema_failures
    if excess_schema_failures:
        reasons.append(
            f"The candidate recorded {schema_failures} structured-output failures; "
            f"the limit is {thresholds.maximum_schema_failures}."
        )
    not_reproducible = not candidate_report.golden_suite.reproducible
    if not_reproducible:
        reasons.append("The candidate did not produce reproducible golden outcomes.")

    resources = candidate_report.resources
    pressure = resources.memory_pressure_ratio
    missing_resources = pressure is None
    if missing_resources:
        reasons.append("Peak memory pressure was not measured during the candidate run.")
    excessive_memory = (
        pressure is not None and pressure > thresholds.max_memory_pressure_ratio
    )
    if excessive_memory:
        reasons.append(
            f"Peak memory pressure {pressure:.1%} exceeded the "
            f"{thresholds.max_memory_pressure_ratio:.1%} limit."
        )
    excessive_temperature = (
        resources.max_temperature_celsius is not None
        and resources.max_temperature_celsius > thresholds.max_temperature_celsius
    )
    if excessive_temperature:
        reasons.append(
            f"Maximum temperature {resources.max_temperature_celsius:.1f} C exceeded the "
            f"{thresholds.max_temperature_celsius:.1f} C limit."
        )
    throttled = resources.thermal_throttling_observed is True
    if throttled:
        reasons.append("Thermal throttling was observed during the candidate run.")

    baseline_duration = baseline.average_operation_duration_ms
    candidate_duration = candidate.average_operation_duration_ms
    missing_latency = baseline_duration <= 0 or candidate_duration <= 0
    if missing_latency:
        reasons.append("Comparable capability latency was not recorded in both reports.")
    excessive_latency = (
        not missing_latency
        and candidate_duration
        > baseline_duration * thresholds.max_operation_duration_ratio
    )
    if excessive_latency:
        reasons.append(
            f"Candidate latency was {candidate_duration / baseline_duration:.2f}x the safe "
            f"baseline; the limit is {thresholds.max_operation_duration_ratio:.2f}x."
        )

    has_hard_failure = any(
        (
            candidate_quality_failed,
            fallback_used,
            candidate_missing_runs,
            excess_schema_failures,
            not_reproducible,
            excessive_memory,
            excessive_temperature,
            throttled,
            excessive_latency,
        )
    )
    if has_hard_failure:
        outcome = PromotionOutcome.REJECT
    elif any(
        (
            missing_resources,
            missing_latency,
            baseline_failed,
            wrong_hardware,
            missing_platform_facts,
            missing_model_identity,
        )
    ):
        outcome = PromotionOutcome.INCONCLUSIVE
    else:
        outcome = PromotionOutcome.PROMOTE
        reasons.append(
            "The stronger model passed all capability gates in three runs within resource and "
            "latency limits."
        )
    return _decision(
        capability,
        outcome,
        baseline_model,
        candidate_model,
        baseline,
        candidate,
        reasons,
    )


def _decision(
    capability: Capability,
    outcome: PromotionOutcome,
    baseline_model: str,
    candidate_model: str,
    baseline: CapabilityBenchmark,
    candidate: CapabilityBenchmark,
    reasons: Iterable[str],
) -> CapabilityPromotionDecision:
    return CapabilityPromotionDecision(
        capability=capability,
        outcome=outcome,
        baseline_model=baseline_model,
        candidate_model=candidate_model,
        recommended_model=(
            candidate_model if outcome is PromotionOutcome.PROMOTE else baseline_model
        ),
        reasons=tuple(reasons),
        baseline_average_duration_ms=baseline.average_operation_duration_ms,
        candidate_average_duration_ms=candidate.average_operation_duration_ms,
    )


def _quality_gates_passed(
    runs: tuple[GoldenRunResult, ...],
    capability: Capability,
) -> bool:
    expected_names = _QUALITY_GATES[capability]
    for run in runs:
        by_name = {gate.name: gate.passed for gate in run.gates}
        if not all(by_name.get(name, False) for name in expected_names):
            return False
    return True


def _average_operation_duration(
    runs: tuple[GoldenRunResult, ...],
    capability: Capability,
) -> float:
    names = _OPERATION_NAMES[capability]
    totals = tuple(
        sum(run.metrics.operation_durations_ms.get(name, 0.0) for name in names)
        for run in runs
    )
    return fmean(totals)
