"""Opt-in complete golden evaluation against preloaded local Ollama models."""

import os
from pathlib import Path

import pytest

from app.ai.evaluation.corpus import load_golden_corpus
from app.ai.evaluation.evaluator import GoldenEvaluator
from app.ai.models import create_ollama_adapter, load_model_profile
from app.ai.schemas import ApprovedKnowledgeRoot, Capability, ModelStatus

pytestmark = [
    pytest.mark.live_ollama,
    pytest.mark.skipif(
        os.getenv("WORKBENCH_RUN_LIVE_OLLAMA") != "1",
        reason="set WORKBENCH_RUN_LIVE_OLLAMA=1 with all approved models preloaded",
    ),
]

GOLDEN_ROOT = Path(__file__).parents[2] / "fixtures" / "golden"


async def test_complete_golden_workflow_passes_three_times(tmp_path: Path) -> None:
    """Run every golden gate without downloading or remotely calling a model."""

    storage = tmp_path / "golden-chroma"
    storage.mkdir()
    profile = load_model_profile()
    adapter = create_ollama_adapter(profile=profile)
    try:
        health = await adapter.health(profile)
        ready = {
            item.capability
            for item in health.models
            if item.status is ModelStatus.READY
        }
        required = {Capability.TEXT, Capability.VISION, Capability.EMBEDDING}
        if not health.runtime_ready or not required.issubset(ready):
            pytest.skip(
                "local Ollama or a preloaded text, vision, or embedding model is unavailable"
            )
        result = await GoldenEvaluator(
            corpus=load_golden_corpus(GOLDEN_ROOT),
            model_adapter=adapter,
            model_profile=profile,
            knowledge_root=ApprovedKnowledgeRoot(path=storage),
        ).run_three_times()
    finally:
        await adapter.close()

    assert result.passed, result.model_dump_json(by_alias=True, indent=2)
