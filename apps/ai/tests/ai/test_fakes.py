"""Deterministic tests for fake AI dependencies."""

from app.ai.evaluation.samples import (
    sample_approved_path,
    sample_evidence_chunk,
    sample_finding,
    sample_model_profile,
    sample_task,
)
from app.ai.fakes import FakeAIEngine, FakeKnowledgeAdapter, FakeModelAdapter
from app.ai.schemas import (
    DraftRequest,
    KnowledgeQuery,
    SourceDocument,
    VisualAnalysisRequest,
)


async def test_fake_model_returns_sample_finding_without_reading_a_file() -> None:
    """Exercise visual analysis without Ollama or filesystem access."""

    adapter = FakeModelAdapter()
    request = VisualAnalysisRequest(inputs=(sample_approved_path(),), task=sample_task())

    result = await adapter.analyze_visual(request)

    assert result.findings == (sample_finding(),)
    assert adapter.calls == ["analyze_visual:task-inspection-001"]


async def test_fake_knowledge_returns_sample_evidence() -> None:
    """Exercise retrieval without importing or starting Chroma."""

    adapter = FakeKnowledgeAdapter()

    result = await adapter.search(KnowledgeQuery(text="surface corrosion"))

    assert result == [sample_evidence_chunk()]
    assert adapter.calls == ["search:surface corrosion"]


async def test_fake_ai_engine_returns_a_grounded_draft() -> None:
    """Give Backend 1 a complete deterministic draft response."""

    engine = FakeAIEngine()
    finding = sample_finding()
    evidence = sample_evidence_chunk()
    request = DraftRequest(
        subject="Corrosion follow-up",
        objective="Prepare an approval-note draft.",
        findings=(finding,),
        evidence=(evidence,),
    )

    draft = await engine.create_grounded_draft(request)

    assert finding in draft.findings
    assert evidence.source_id in draft.evidence_source_ids
    assert engine.calls == ["create_grounded_draft:Corrosion follow-up"]


async def test_fake_knowledge_ingestion_does_not_require_the_path_to_exist() -> None:
    """Prove Phase 0 fakes consume approved references without file access."""

    adapter = FakeKnowledgeAdapter()
    approved_path = sample_approved_path()
    document = SourceDocument(
        document_id="inspection-report",
        document_name="inspection-report.pdf",
        mime_type="application/pdf",
        approved_path=approved_path,
    )

    result = await adapter.ingest(document)

    assert result.document_id == document.document_id
    assert not approved_path.path.exists()


async def test_fake_model_health_uses_injected_profile() -> None:
    """Keep model profiles injectable instead of constructing runtime clients."""

    adapter = FakeModelAdapter()
    profile = sample_model_profile()

    health = await adapter.health(profile)

    assert health.runtime_ready is True
    assert adapter.calls == ["health:safe-8gb"]
