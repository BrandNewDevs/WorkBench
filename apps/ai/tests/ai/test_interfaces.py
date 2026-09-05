"""Import and structural-typing tests for Backend 1's AI seam."""

import subprocess
import sys

from app.ai.engine import AIEngine, AIEngineDependencies
from app.ai.evaluation.samples import sample_model_profile
from app.ai.fakes import (
    FakeAIEngine,
    FakeCapabilityRouter,
    FakeKnowledgeAdapter,
    FakeModelAdapter,
    fake_engine_dependencies,
)
from app.ai.knowledge import KnowledgeAdapter, KnowledgeIngestor
from app.ai.models import ModelAdapter
from app.ai.routing import CapabilityRouter


def accepts_ai_engine(engine: AIEngine) -> AIEngine:
    """Let mypy verify that the backend fake satisfies the public protocol."""

    return engine


def accepts_model_adapter(adapter: ModelAdapter) -> ModelAdapter:
    """Let mypy verify the fake model adapter against its protocol."""

    return adapter


def accepts_knowledge_adapter(adapter: KnowledgeAdapter) -> KnowledgeAdapter:
    """Let mypy verify the fake knowledge adapter against its protocol."""

    return adapter


def accepts_knowledge_ingestor(adapter: KnowledgeIngestor) -> KnowledgeIngestor:
    """Let mypy verify the fake also satisfies the ingestion-only seam."""

    return adapter


def accepts_router(router: CapabilityRouter) -> CapabilityRouter:
    """Let mypy verify the fake router against its protocol."""

    return router


def test_fakes_satisfy_the_public_interfaces() -> None:
    """Keep fake and production dependency seams interchangeable."""

    assert accepts_ai_engine(FakeAIEngine()) is not None
    assert accepts_model_adapter(FakeModelAdapter()) is not None
    assert accepts_knowledge_adapter(FakeKnowledgeAdapter()) is not None
    assert accepts_knowledge_ingestor(FakeKnowledgeAdapter()) is not None
    assert accepts_router(FakeCapabilityRouter()) is not None


def test_dependencies_are_injected() -> None:
    """Construct the dependency bundle entirely from caller-supplied objects."""

    dependencies: AIEngineDependencies = fake_engine_dependencies(sample_model_profile())

    assert dependencies.model_profile.profile_id == "safe-8gb"


def test_backend_import_does_not_import_ollama_or_chroma() -> None:
    """Importing the contract must not initialize heavyweight local services."""

    command = (
        "import sys; from app.ai import AIEngine, AIEngineDependencies; "
        "assert 'chromadb' not in sys.modules; assert 'ollama' not in sys.modules"
    )

    completed = subprocess.run(
        [sys.executable, "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
