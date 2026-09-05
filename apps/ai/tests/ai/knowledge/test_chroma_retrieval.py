"""Integration tests for grounded retrieval from the local Chroma corpus."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from chromadb.api import ClientAPI

from app.ai.errors import KnowledgeIndexUnavailable, NoRelevantEvidence
from app.ai.evaluation.samples import sample_inference_metrics, sample_model_profile
from app.ai.fakes import FakeModelAdapter
from app.ai.knowledge.chroma_ingestion import (
    KNOWLEDGE_SCHEMA_VERSION,
    ChromaKnowledgeIngestor,
    create_persistent_chroma_client,
)
from app.ai.knowledge.config import KnowledgeProcessingSettings
from app.ai.schemas import (
    ApprovedKnowledgeRoot,
    EmbeddingRequest,
    EmbeddingResult,
    KnowledgeQuery,
    SourceDocument,
)

if TYPE_CHECKING:
    from app.ai.knowledge.ports import RetrievalMetricsSink


class RetrievalMetricsView(Protocol):
    """Fields required by the hardware-comparison acceptance check."""

    collection_name: str
    embedding_model_id: str
    elapsed_ms: float
    embedding_elapsed_ms: float
    candidate_count: int
    returned_count: int
    candidate_scores: tuple[float, ...]
    returned_scores: tuple[float, ...]


class RecordingMetricsSink:
    """Capture non-confidential retrieval measurements at the output boundary."""

    def __init__(self) -> None:
        self.records: list[object] = []

    def record(self, metrics: object) -> None:
        """Keep the emitted observation for assertions."""

        self.records.append(metrics)


class GoldenEmbeddingAdapter(FakeModelAdapter):
    """Map sanitized corpus topics to stable vectors at the Ollama boundary."""

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Return known vectors without a model runtime or network call."""

        vectors = tuple(self._vector_for(text) for text in request.inputs)
        return EmbeddingResult(
            model=request.model,
            vectors=vectors,
            metrics=sample_inference_metrics(),
        )

    @staticmethod
    def _vector_for(text: str) -> tuple[float, ...]:
        normalized = text.casefold()
        if "moderate-match" in normalized:
            return (0.6, 0.0, 0.0, 0.8)
        if "isolation" in normalized or "inlet valve" in normalized:
            return (1.0, 0.0, 0.0, 0.0)
        if "corrosion" in normalized or "thickness" in normalized:
            return (0.0, 1.0, 0.0, 0.0)
        if "approval" in normalized or "required sections" in normalized:
            return (0.0, 0.0, 1.0, 0.0)
        return (0.0, 0.0, 0.0, 1.0)


def source_document(
    document_id: str,
    document_name: str,
    source_id: str,
    content: str,
) -> SourceDocument:
    """Build one sanitized local golden-corpus document."""

    return SourceDocument(
        document_id=document_id,
        document_name=document_name,
        mime_type="text/markdown",
        source_id=source_id,
        content=content.encode("utf-8"),
    )


async def populated_knowledge_store(
    tmp_path: Path,
    *,
    metrics_sink: RetrievalMetricsSink | None = None,
) -> tuple[ChromaKnowledgeIngestor, ClientAPI]:
    """Index the sanitized SOP, prior note, and approval template locally."""

    storage_root = tmp_path / "chroma"
    storage_root.mkdir()
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=storage_root))
    if metrics_sink is None:
        store = ChromaKnowledgeIngestor(
            client,
            GoldenEmbeddingAdapter(),
            sample_model_profile(),
        )
    else:
        store = ChromaKnowledgeIngestor(
            client,
            GoldenEmbeddingAdapter(),
            sample_model_profile(),
            metrics_sink=metrics_sink,
        )
    documents = (
        source_document(
            "isolation-sop",
            "Isolation SOP.md",
            "source-isolation-sop",
            "# ISOLATION PROCEDURE\n\nClose the inlet valve before inspection.",
        ),
        source_document(
            "corrosion-note",
            "Prior corrosion note.md",
            "source-corrosion-note",
            "# PRIOR DECISION\n\nRequest thickness measurement after corrosion is observed.",
        ),
        source_document(
            "approval-template",
            "Approval note template.md",
            "source-approval-template",
            "# REQUIRED SECTIONS\n\nApproval notes need findings and a recommendation.",
        ),
    )
    for document in documents:
        await store.ingest(document)
    return store, client


@pytest.mark.parametrize(
    ("query", "expected_source"),
    (
        ("Which isolation valve step is required before inspection?", "source-isolation-sop"),
        ("What follows an observed corrosion finding?", "source-corrosion-note"),
        ("What belongs in an approval note?", "source-approval-template"),
    ),
)
async def test_golden_queries_return_expected_source_in_top_three(
    tmp_path: Path,
    query: str,
    expected_source: str,
) -> None:
    """Ground each MVP question in its expected local source and page."""

    store, _ = await populated_knowledge_store(tmp_path)

    evidence = await store.search(KnowledgeQuery(text=query))

    top_three_locations = {
        (chunk.source_id, chunk.page_number) for chunk in evidence[:3]
    }
    assert (expected_source, 1) in top_three_locations


async def test_unrelated_query_returns_no_evidence(tmp_path: Path) -> None:
    """Never pass unrelated local chunks into grounded drafting context."""

    store, _ = await populated_knowledge_store(tmp_path)

    with pytest.raises(NoRelevantEvidence):
        await store.search(KnowledgeQuery(text="How should the office garden be watered?"))


async def test_existing_non_cosine_collection_is_rejected(tmp_path: Path) -> None:
    """Never interpret L2 distances with the cosine relevance formula."""

    storage_root = tmp_path / "chroma"
    storage_root.mkdir()
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=storage_root))
    profile = sample_model_profile()
    store = ChromaKnowledgeIngestor(client, GoldenEmbeddingAdapter(), profile)
    client.create_collection(
        name=store.collection_name,
        metadata={
            "embeddingModelId": profile.embedding_candidates[0],
            "schemaVersion": KNOWLEDGE_SCHEMA_VERSION,
        },
        configuration={"hnsw": {"space": "l2"}},
        embedding_function=None,
    )

    with pytest.raises(KnowledgeIndexUnavailable, match="cosine"):
        await store.search(KnowledgeQuery(text="Find the applicable SOP."))


async def test_query_can_raise_the_relevance_floor(tmp_path: Path) -> None:
    """Apply the caller's bounded threshold to normalized cosine scores."""

    storage_root = tmp_path / "chroma"
    storage_root.mkdir()
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=storage_root))
    store = ChromaKnowledgeIngestor(
        client,
        GoldenEmbeddingAdapter(),
        sample_model_profile(),
    )
    await store.ingest(
        source_document(
            "isolation-sop",
            "Isolation SOP.md",
            "source-isolation-sop",
            "# PROCEDURE\n\nFollow the isolation steps before inspection.",
        )
    )

    accepted = await store.search(
        KnowledgeQuery(text="moderate-match", minimum_score=0.59)
    )

    assert accepted[0].score == pytest.approx(0.6)
    with pytest.raises(NoRelevantEvidence):
        await store.search(KnowledgeQuery(text="moderate-match", minimum_score=0.61))


async def test_retrieval_is_deterministic_and_returns_real_chunk_ids(
    tmp_path: Path,
) -> None:
    """Keep result order stable and return only IDs present in the active collection."""

    store, client = await populated_knowledge_store(tmp_path)
    query = KnowledgeQuery(text="What follows an observed corrosion finding?")

    first = await store.search(query)
    second = await store.search(query)

    collection = client.get_collection(store.collection_name, embedding_function=None)
    indexed_ids = set(collection.get()["ids"])
    assert first == second
    assert {chunk.chunk_id for chunk in first} <= indexed_ids
    assert all(chunk.embedding_model == "qwen3-embedding:0.6b" for chunk in first)


async def test_tampered_retrieved_content_is_rejected(tmp_path: Path) -> None:
    """Never attach trusted citation metadata to a modified document body."""

    store, client = await populated_knowledge_store(tmp_path)
    collection = client.get_collection(store.collection_name, embedding_function=None)
    stored = collection.get(
        where={"documentId": "approval-template"},
        include=["documents", "embeddings"],
    )
    (chunk_id,) = stored["ids"]
    embeddings = stored["embeddings"]
    assert embeddings is not None
    embedding_values: list[Sequence[float] | Sequence[int]] = [
        cast(Sequence[float], embeddings[0])
    ]
    collection.update(
        ids=[chunk_id],
        embeddings=embedding_values,
        documents=["Modified text that was never indexed or approved."],
    )

    with pytest.raises(KnowledgeIndexUnavailable, match="content hash"):
        await store.search(KnowledgeQuery(text="What belongs in an approval note?"))


async def test_overlapping_chunks_from_one_page_and_section_are_deduplicated(
    tmp_path: Path,
) -> None:
    """Do not repeat substantially identical evidence in the drafting context."""

    storage_root = tmp_path / "chroma"
    storage_root.mkdir()
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=storage_root))
    store = ChromaKnowledgeIngestor(
        client,
        GoldenEmbeddingAdapter(),
        sample_model_profile(),
        KnowledgeProcessingSettings(max_chunk_chars=512),
    )
    repeated_rule = " ".join("isolation" for _ in range(45))
    await store.ingest(
        source_document(
            "duplicate-sop",
            "Duplicate SOP.md",
            "source-duplicate-sop",
            f"# PROCEDURE\n\n{repeated_rule}\n\n{repeated_rule}",
        )
    )

    evidence = await store.search(KnowledgeQuery(text="isolation procedure"))

    assert len(evidence) == 1


async def test_deduplication_uses_bounded_overfetch_to_fill_with_other_sources(
    tmp_path: Path,
) -> None:
    """Consider lower-ranked unique evidence when the nearest chunks overlap."""

    storage_root = tmp_path / "chroma"
    storage_root.mkdir()
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=storage_root))
    store = ChromaKnowledgeIngestor(
        client,
        GoldenEmbeddingAdapter(),
        sample_model_profile(),
        KnowledgeProcessingSettings(max_chunk_chars=512),
    )
    repeated_rule = " ".join("isolation" for _ in range(45))
    await store.ingest(
        source_document(
            "duplicate-sop",
            "Duplicate SOP.md",
            "source-duplicate-sop",
            "# PROCEDURE\n\n" + "\n\n".join(repeated_rule for _ in range(5)),
        )
    )
    await store.ingest(
        source_document(
            "alternative-note",
            "Alternative note.md",
            "source-alternative-note",
            "# GUIDANCE\n\nmoderate-match alternative local guidance.",
        )
    )

    evidence = await store.search(KnowledgeQuery(text="isolation procedure"))

    assert {chunk.source_id for chunk in evidence} == {
        "source-duplicate-sop",
        "source-alternative-note",
    }


async def test_drafting_context_keeps_at_most_three_chunks_per_source_section(
    tmp_path: Path,
) -> None:
    """Prevent one long section from crowding every other source out of context."""

    storage_root = tmp_path / "chroma"
    storage_root.mkdir()
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=storage_root))
    store = ChromaKnowledgeIngestor(
        client,
        GoldenEmbeddingAdapter(),
        sample_model_profile(),
        KnowledgeProcessingSettings(max_chunk_chars=512),
    )
    unique_words = ("alpha", "bravo", "charlie", "delta", "echo")
    paragraphs = [
        f"Isolation requirement {word}. " + " ".join(word for _ in range(45))
        for word in unique_words
    ]
    await store.ingest(
        source_document(
            "long-sop",
            "Long SOP.md",
            "source-long-sop",
            "# PROCEDURE\n\n" + "\n\n".join(paragraphs),
        )
    )

    evidence = await store.search(KnowledgeQuery(text="isolation procedure"))

    assert len(evidence) == 3
    assert {
        (chunk.source_id, chunk.page_number, chunk.section) for chunk in evidence
    } == {("source-long-sop", 1, "PROCEDURE")}


async def test_section_limit_applies_across_page_boundaries(tmp_path: Path) -> None:
    """Cap one source section globally even when that section spans several pages."""

    storage_root = tmp_path / "chroma"
    storage_root.mkdir()
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=storage_root))
    store = ChromaKnowledgeIngestor(
        client,
        GoldenEmbeddingAdapter(),
        sample_model_profile(),
    )
    pages = (
        "# PROCEDURE\n\nIsolation requirement alpha.",
        "Isolation requirement bravo.",
        "Isolation requirement charlie.",
        "Isolation requirement delta.",
    )
    await store.ingest(
        source_document(
            "paged-sop",
            "Paged SOP.md",
            "source-paged-sop",
            "\f".join(pages),
        )
    )

    evidence = await store.search(KnowledgeQuery(text="isolation procedure"))

    assert len(evidence) == 3
    assert {chunk.section for chunk in evidence} == {"PROCEDURE"}


async def test_retrieval_records_safe_latency_and_score_metrics(tmp_path: Path) -> None:
    """Emit enough non-confidential data for workstation and Jetson comparison."""

    sink = RecordingMetricsSink()
    store, _ = await populated_knowledge_store(tmp_path, metrics_sink=sink)

    evidence = await store.search(KnowledgeQuery(text="What belongs in an approval note?"))

    assert len(sink.records) == 1
    metrics = cast(RetrievalMetricsView, sink.records[0])
    assert metrics.collection_name == store.collection_name
    assert metrics.embedding_model_id == "qwen3-embedding:0.6b"
    assert metrics.elapsed_ms >= 0
    assert metrics.embedding_elapsed_ms == 12.5
    assert metrics.candidate_count == 3
    assert metrics.returned_count == len(evidence)
    assert len(metrics.candidate_scores) == 3
    assert metrics.returned_scores == tuple(chunk.score for chunk in evidence)


async def test_rejected_retrieval_records_candidate_scores_without_content(
    tmp_path: Path,
) -> None:
    """Preserve threshold evidence for tuning even when no chunk is returned."""

    sink = RecordingMetricsSink()
    store, _ = await populated_knowledge_store(tmp_path, metrics_sink=sink)

    with pytest.raises(NoRelevantEvidence):
        await store.search(KnowledgeQuery(text="How should the office garden be watered?"))

    metrics = cast(RetrievalMetricsView, sink.records[0])
    assert metrics.candidate_count == 3
    assert metrics.returned_count == 0
    assert metrics.candidate_scores == (0.0, 0.0, 0.0)
    assert metrics.returned_scores == ()


async def test_retrieval_returns_no_more_than_five_candidates(tmp_path: Path) -> None:
    """Keep the public retrieval result bounded even if a caller asks for more."""

    storage_root = tmp_path / "chroma"
    storage_root.mkdir()
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=storage_root))
    store = ChromaKnowledgeIngestor(
        client,
        GoldenEmbeddingAdapter(),
        sample_model_profile(),
    )
    for index in range(6):
        await store.ingest(
            source_document(
                f"isolation-sop-{index}",
                f"Isolation SOP {index}.md",
                f"source-isolation-sop-{index}",
                f"# RULE {index}\n\nIsolation requirement for equipment {index}.",
            )
        )

    evidence = await store.search(
        KnowledgeQuery(text="isolation requirements", top_k=20)
    )

    assert len(evidence) == 5
