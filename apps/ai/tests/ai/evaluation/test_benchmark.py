"""Tests for content-free benchmark reports and deterministic promotion decisions."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.ai.evaluation.benchmark import build_benchmark_report, compare_for_promotion
from app.ai.evaluation.benchmark_contracts import (
    BenchmarkReport,
    HardwareKind,
    HardwareSnapshot,
    PromotionOutcome,
    PromotionThresholds,
    ResourceObservation,
)
from app.ai.evaluation.contracts import (
    GoldenGateResult,
    GoldenRunMetrics,
    GoldenRunResult,
    GoldenSuiteResult,
)
from app.ai.models.profiles import ModelSettings, load_model_profile
from app.ai.schemas import (
    Capability,
    InstalledModel,
    ModelHealth,
    ModelRuntimeHealth,
    ModelStatus,
)

_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
_ALL_GATES = (
    "model-health",
    "vision-recall",
    "allowed-severity-values",
    "retrieval-recall-at-3",
    "required-draft-sections",
    "citation-integrity",
    "required-sop-citation",
    "required-uncertainty",
    "forbidden-unsupported-conclusions",
    "final-schema",
)


def test_benchmark_binds_exact_models_and_content_free_metrics() -> None:
    report = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)

    assert report.profile_id == "safe-8gb"
    assert report.prompt_versions["vision"] == "vision-extraction-v2"
    assert report.average_workflow_duration_ms == 250
    assert report.average_generation_tokens_per_second == 20
    assert {item.model_digest for item in report.capabilities} == {
        "digest-embedding",
        "digest-text",
        "digest-vision",
    }
    assert report.for_capability(Capability.TEXT).observed_models == ("qwen3:4b",)
    serialized = report.model_dump_json(by_alias=True)
    assert "systemPrompt" not in serialized
    assert "userPrompt" not in serialized
    assert "imagesBase64" not in serialized
    assert "documentContent" not in serialized


def test_passing_jetson_candidates_are_promoted_independently() -> None:
    baseline = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)
    candidate = _report("jetson-candidate", hardware_kind=HardwareKind.JETSON)

    result = compare_for_promotion(baseline, candidate, created_at_utc=_NOW)

    decisions = {item.capability: item for item in result.decisions}
    assert decisions[Capability.TEXT].outcome is PromotionOutcome.PROMOTE
    assert decisions[Capability.TEXT].recommended_model == "qwen3:8b"
    assert decisions[Capability.VISION].outcome is PromotionOutcome.PROMOTE
    assert decisions[Capability.VISION].recommended_model == "qwen3-vl:8b"
    assert decisions[Capability.EMBEDDING].outcome is PromotionOutcome.RETAIN_SAFE
    assert decisions[Capability.EMBEDDING].recommended_model == "qwen3-embedding:0.6b"
    assert result.thresholds.max_memory_pressure_ratio == 0.9


def test_vision_failure_does_not_block_a_passing_text_promotion() -> None:
    baseline = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)
    suite = _golden_suite(
        "jetson-candidate",
        failed_gate="vision-recall",
    )
    candidate = _report(
        "jetson-candidate",
        hardware_kind=HardwareKind.JETSON,
        suite=suite,
    )

    result = compare_for_promotion(baseline, candidate)
    decisions = {item.capability: item for item in result.decisions}

    assert decisions[Capability.TEXT].outcome is PromotionOutcome.PROMOTE
    assert decisions[Capability.VISION].outcome is PromotionOutcome.REJECT
    assert decisions[Capability.VISION].recommended_model == "qwen3-vl:4b"


def test_vision_schema_failure_does_not_reject_text_candidate() -> None:
    baseline = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)
    candidate = _report(
        "jetson-candidate",
        hardware_kind=HardwareKind.JETSON,
        suite=_golden_suite(
            "jetson-candidate",
            schema_failure_capability=Capability.VISION,
        ),
    )

    result = compare_for_promotion(baseline, candidate)
    decisions = {item.capability: item for item in result.decisions}

    assert decisions[Capability.TEXT].outcome is PromotionOutcome.PROMOTE
    assert decisions[Capability.VISION].outcome is PromotionOutcome.REJECT


def test_fallback_candidate_is_rejected_even_when_quality_gates_pass() -> None:
    baseline = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)
    fallback_models: dict[str, tuple[str, ...]] = {
        "text": ("qwen3:8b",),
        "vision": ("qwen3-vl:4b",),
        "embedding": ("qwen3-embedding:0.6b",),
    }
    candidate = _report(
        "jetson-candidate",
        hardware_kind=HardwareKind.JETSON,
        suite=_golden_suite("jetson-candidate", selected_models=fallback_models),
    )

    result = compare_for_promotion(baseline, candidate)
    vision = next(item for item in result.decisions if item.capability is Capability.VISION)

    assert vision.outcome is PromotionOutcome.REJECT
    assert vision.recommended_model == "qwen3-vl:4b"
    assert any("fallback" in reason.casefold() for reason in vision.reasons)


@pytest.mark.parametrize(
    ("resources", "hardware_kind", "expected_reason"),
    (
        (
            ResourceObservation(measurement_source="nvidia-smi", sample_count=0),
            HardwareKind.JETSON,
            "memory pressure",
        ),
        (
            ResourceObservation(
                measurement_source="fixture",
                sample_count=3,
                memory_capacity_bytes=100,
                peak_memory_used_bytes=70,
                max_temperature_celsius=70,
                thermal_throttling_observed=False,
            ),
            HardwareKind.WORKSTATION,
            "identified Jetson",
        ),
    ),
)
def test_missing_promotion_proof_is_inconclusive(
    resources: ResourceObservation,
    hardware_kind: HardwareKind,
    expected_reason: str,
) -> None:
    baseline = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)
    candidate = _report(
        "jetson-candidate",
        hardware_kind=hardware_kind,
        resources=resources,
    )

    result = compare_for_promotion(baseline, candidate)
    text = next(item for item in result.decisions if item.capability is Capability.TEXT)

    assert text.outcome is PromotionOutcome.INCONCLUSIVE
    assert any(expected_reason in reason for reason in text.reasons)


def test_missing_exact_model_identity_is_inconclusive() -> None:
    baseline = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)
    candidate = _report("jetson-candidate", hardware_kind=HardwareKind.JETSON)
    capabilities = tuple(
        item.model_copy(update={"model_digest": None})
        if item.capability is Capability.TEXT
        else item
        for item in candidate.capabilities
    )
    candidate_without_digest = candidate.model_copy(update={"capabilities": capabilities})

    result = compare_for_promotion(baseline, candidate_without_digest)
    text = next(item for item in result.decisions if item.capability is Capability.TEXT)

    assert text.outcome is PromotionOutcome.INCONCLUSIVE
    assert any("digest" in reason for reason in text.reasons)


def test_excessive_memory_or_latency_rejects_candidate() -> None:
    baseline = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)
    candidate = _report(
        "jetson-candidate",
        hardware_kind=HardwareKind.JETSON,
        resources=ResourceObservation(
            measurement_source="linux-procfs-shared-memory",
            sample_count=3,
            memory_capacity_bytes=100,
            peak_memory_used_bytes=96,
            max_temperature_celsius=70,
            thermal_throttling_observed=False,
        ),
        duration_multiplier=3,
    )

    result = compare_for_promotion(
        baseline,
        candidate,
        thresholds=PromotionThresholds(
            max_operation_duration_ratio=2,
            max_memory_pressure_ratio=0.9,
        ),
    )

    for capability in (Capability.TEXT, Capability.VISION):
        decision = next(item for item in result.decisions if item.capability is capability)
        assert decision.outcome is PromotionOutcome.REJECT
        assert any("memory pressure" in reason.casefold() for reason in decision.reasons)
        assert any("latency" in reason.casefold() for reason in decision.reasons)


def test_different_golden_corpora_cannot_be_compared() -> None:
    baseline = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)
    candidate = _report("jetson-candidate", hardware_kind=HardwareKind.JETSON)
    changed_suite = candidate.golden_suite.model_copy(update={"corpus_id": "other-corpus"})
    changed = candidate.model_copy(update={"golden_suite": changed_suite})

    with pytest.raises(ValueError, match="same golden corpus"):
        compare_for_promotion(baseline, changed)


def test_different_prompt_versions_cannot_be_compared() -> None:
    baseline = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)
    candidate = _report("jetson-candidate", hardware_kind=HardwareKind.JETSON)
    changed_versions = dict(candidate.prompt_versions)
    changed_versions["vision"] = "vision-extraction-review-fixture"
    changed = candidate.model_copy(update={"prompt_versions": changed_versions})

    with pytest.raises(ValueError, match="same prompt versions"):
        compare_for_promotion(baseline, changed)


def test_benchmark_contract_rejects_incomplete_prompt_version_evidence() -> None:
    report = _report("safe-8gb", hardware_kind=HardwareKind.WORKSTATION)
    payload = report.model_dump(mode="json", by_alias=True)
    prompt_versions = payload["promptVersions"]
    assert isinstance(prompt_versions, dict)
    prompt_versions.pop("vision")

    with pytest.raises(ValidationError, match="every approved prompt version"):
        BenchmarkReport.model_validate(payload)


def _report(
    profile_id: str,
    *,
    hardware_kind: HardwareKind,
    suite: GoldenSuiteResult | None = None,
    resources: ResourceObservation | None = None,
    duration_multiplier: float = 1,
) -> BenchmarkReport:
    profile = load_model_profile(ModelSettings(model_profile=profile_id))
    selected = {
        Capability.TEXT: profile.text_candidates[0],
        Capability.VISION: profile.vision_candidates[0],
        Capability.EMBEDDING: profile.embedding_candidates[0],
    }
    hardware = HardwareSnapshot(
        captured_at_utc=_NOW,
        hardware_kind=hardware_kind,
        operating_system="Linux",
        operating_system_release="test",
        architecture="aarch64" if hardware_kind is HardwareKind.JETSON else "x86_64",
        total_memory_bytes=100,
        available_storage_bytes=1_000,
        accelerator_name=(
            "Jetson Orin test fixture"
            if hardware_kind is HardwareKind.JETSON
            else "RTX fixture"
        ),
        accelerator_memory_bytes=100,
        accelerator_uses_shared_memory=hardware_kind is HardwareKind.JETSON,
        jetson_model=(
            "NVIDIA Jetson Orin test fixture"
            if hardware_kind is HardwareKind.JETSON
            else None
        ),
        jetpack_version="fixture" if hardware_kind is HardwareKind.JETSON else None,
        cuda_version="fixture",
        ollama_version="fixture",
    )
    installed = tuple(
        InstalledModel(
            name=model,
            size_bytes=1,
            digest=f"digest-{capability.value}",
            parameter_size="fixture",
            quantization_level="fixture",
        )
        for capability, model in selected.items()
    )
    health = ModelRuntimeHealth(
        runtime_ready=True,
        models=tuple(
            ModelHealth(
                capability=capability,
                status=ModelStatus.READY,
                installed=True,
                loadable=True,
                selected_model=model,
            )
            for capability, model in selected.items()
        ),
    )
    selected_suite = suite or _golden_suite(
        profile_id,
        duration_multiplier=duration_multiplier,
    )
    if suite is not None and duration_multiplier != 1:
        selected_suite = _golden_suite(
            profile_id,
            duration_multiplier=duration_multiplier,
        )
    return build_benchmark_report(
        profile=profile,
        hardware=hardware,
        runtime_health=health,
        installed_models=installed,
        golden_suite=selected_suite,
        resources=resources or _resources(),
        created_at_utc=_NOW,
    )


def _resources() -> ResourceObservation:
    return ResourceObservation(
        measurement_source="fixture",
        sample_count=3,
        memory_capacity_bytes=100,
        peak_memory_used_bytes=70,
        max_temperature_celsius=70,
        thermal_throttling_observed=False,
    )


def _golden_suite(
    profile_id: str,
    *,
    failed_gate: str | None = None,
    selected_models: dict[str, tuple[str, ...]] | None = None,
    duration_multiplier: float = 1,
    schema_failure_capability: Capability | None = None,
) -> GoldenSuiteResult:
    profile = load_model_profile(ModelSettings(model_profile=profile_id))
    models = selected_models or {
        "text": (profile.text_candidates[0],),
        "vision": (profile.vision_candidates[0],),
        "embedding": (profile.embedding_candidates[0],),
    }
    runs: list[GoldenRunResult] = []
    for run_number in range(1, 4):
        gates = tuple(
            GoldenGateResult(
                name=name,
                passed=name != failed_gate,
                diagnostic="fixture gate",
            )
            for name in _ALL_GATES
        )
        runs.append(
            GoldenRunResult(
                corpus_id="golden-industrial-inspection-v1",
                run_number=run_number,
                passed=failed_gate is None,
                matched_finding_keys=("corrosion", "leak", "bolt"),
                gates=gates,
                diagnostics=(),
                metrics=GoldenRunMetrics(
                    run_number=run_number,
                    extraction_successes=3,
                    expected_findings=3,
                    vision_recall=1,
                    retrieval_ranks={"query": 1},
                    schema_failures=int(schema_failure_capability is not None),
                    schema_failures_by_capability={
                        capability.value: int(capability is schema_failure_capability)
                        for capability in Capability
                    },
                    fallback_uses=0,
                    final_schema_valid=True,
                    model_invocations=6,
                    prompt_tokens=100,
                    generated_tokens=20,
                    model_total_duration_ms=200 * duration_multiplier,
                    model_load_duration_ms=10,
                    generation_tokens_per_second=20,
                    selected_models=models,
                    operation_durations_ms={
                        "knowledgeIngestion": 25 * duration_multiplier,
                        "visionExtraction": 100 * duration_multiplier,
                        "knowledgeRetrieval": 25 * duration_multiplier,
                        "groundedDrafting": 100 * duration_multiplier,
                    },
                    total_duration_ms=250 * duration_multiplier,
                ),
            )
        )
    return GoldenSuiteResult(
        corpus_id="golden-industrial-inspection-v1",
        passed=failed_gate is None,
        reproducible=True,
        runs=tuple(runs),
        diagnostics=(),
    )
