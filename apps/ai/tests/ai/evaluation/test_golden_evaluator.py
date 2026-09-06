"""End-to-end golden evaluation through the public evaluator seam."""

from pathlib import Path

import pytest

from app.ai.evaluation.corpus import load_golden_corpus
from app.ai.evaluation.evaluator import GoldenEvaluator
from app.ai.evaluation.recorded import GoldenRecordedModelAdapter
from app.ai.models.profiles import load_model_profile
from app.ai.schemas import ApprovedKnowledgeRoot

GOLDEN_ROOT = Path(__file__).parents[2] / "fixtures" / "golden"


async def test_recorded_golden_workflow_passes_three_consecutive_runs(
    tmp_path: Path,
) -> None:
    index_root = tmp_path / "chroma"
    index_root.mkdir()
    corpus = load_golden_corpus(GOLDEN_ROOT)
    evaluator = GoldenEvaluator(
        corpus=corpus,
        model_adapter=GoldenRecordedModelAdapter(),
        model_profile=load_model_profile(),
        knowledge_root=ApprovedKnowledgeRoot(path=index_root),
    )

    result = await evaluator.run_three_times()

    assert result.passed is True
    assert result.reproducible is True
    assert len(result.runs) == 3
    assert all(run.passed for run in result.runs)
    assert all(run.metrics.extraction_successes == 3 for run in result.runs)
    assert all(run.metrics.vision_recall == 1 for run in result.runs)
    assert all(
        set(run.metrics.retrieval_ranks.values()) == {1} for run in result.runs
    )
    assert all(run.metrics.final_schema_valid for run in result.runs)
    assert all(run.metrics.fallback_uses == 0 for run in result.runs)
    assert all(run.metrics.model_invocations > 0 for run in result.runs)
    assert all(run.metrics.prompt_tokens > 0 for run in result.runs)
    assert all(run.metrics.generated_tokens > 0 for run in result.runs)
    assert all(run.metrics.generation_tokens_per_second is not None for run in result.runs)
    assert all(
        run.metrics.selected_models
        == {
            "text": ("qwen3:4b",),
            "vision": ("qwen3-vl:4b",),
            "embedding": ("qwen3-embedding:0.6b",),
        }
        for run in result.runs
    )
    assert all(run.metrics.total_duration_ms >= 0 for run in result.runs)


async def test_golden_failure_names_the_missing_expected_finding(tmp_path: Path) -> None:
    index_root = tmp_path / "chroma"
    index_root.mkdir()
    evaluator = GoldenEvaluator(
        corpus=load_golden_corpus(GOLDEN_ROOT),
        model_adapter=GoldenRecordedModelAdapter(omit_site_finding=True),
        model_profile=load_model_profile(),
        knowledge_root=ApprovedKnowledgeRoot(path=index_root),
    )

    result = await evaluator.run_three_times()

    assert result.passed is False
    assert all(run.metrics.extraction_successes == 2 for run in result.runs)
    assert any(
        "missing-guard-bolt" in diagnostic
        for run in result.runs
        for diagnostic in run.diagnostics
    )


async def test_recovered_schema_failure_is_counted_without_failing_final_schema(
    tmp_path: Path,
) -> None:
    index_root = tmp_path / "chroma"
    index_root.mkdir()
    evaluator = GoldenEvaluator(
        corpus=load_golden_corpus(GOLDEN_ROOT),
        model_adapter=GoldenRecordedModelAdapter(invalid_draft_attempts=1),
        model_profile=load_model_profile(),
        knowledge_root=ApprovedKnowledgeRoot(path=index_root),
    )

    result = await evaluator.run_three_times()

    assert result.passed is True
    assert result.runs[0].metrics.schema_failures == 1
    assert result.runs[0].metrics.schema_failures_by_capability == {
        "text": 1,
        "vision": 0,
        "embedding": 0,
    }
    assert result.runs[0].metrics.final_schema_valid is True
    assert sum(run.metrics.schema_failures for run in result.runs) == 1


@pytest.mark.parametrize(
    "output_field",
    ("summary", "recommendation", "findingDescription", "criticalClaim"),
)
async def test_forbidden_conclusion_in_any_assertion_field_fails_with_named_diagnostic(
    tmp_path: Path,
    output_field: str,
) -> None:
    index_root = tmp_path / "chroma"
    index_root.mkdir()
    evaluator = GoldenEvaluator(
        corpus=load_golden_corpus(GOLDEN_ROOT),
        model_adapter=GoldenRecordedModelAdapter(
            forbidden_conclusion_field=output_field,
        ),
        model_profile=load_model_profile(),
        knowledge_root=ApprovedKnowledgeRoot(path=index_root),
    )

    result = await evaluator.run_three_times()

    assert result.passed is False
    assert all(
        any(
            gate.name == "forbidden-unsupported-conclusions"
            and gate.passed is False
            and "equipment is safe to operate" in gate.diagnostic
            for gate in run.gates
        )
        for run in result.runs
    )


async def test_valid_citations_still_fail_when_required_sop_is_not_cited(
    tmp_path: Path,
) -> None:
    index_root = tmp_path / "chroma"
    index_root.mkdir()
    evaluator = GoldenEvaluator(
        corpus=load_golden_corpus(GOLDEN_ROOT),
        model_adapter=GoldenRecordedModelAdapter(omit_sop_citation=True),
        model_profile=load_model_profile(),
        knowledge_root=ApprovedKnowledgeRoot(path=index_root),
    )

    result = await evaluator.run_three_times()

    assert result.passed is False
    assert all(
        any(
            gate.name == "required-sop-citation"
            and gate.passed is False
            and "pump-maintenance-sop" in gate.diagnostic
            for gate in run.gates
        )
        for run in result.runs
    )
    assert all(
        next(gate for gate in run.gates if gate.name == "citation-integrity").passed
        for run in result.runs
    )
