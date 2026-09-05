"""Dependency-injection boundary for the local knowledge index."""

from typing import Protocol

from app.ai.schemas import (
    EvidenceChunk,
    IngestionResult,
    KnowledgeQuery,
    RetrievalMetrics,
    SourceDocument,
)


class RetrievalMetricsSink(Protocol):
    """Local output boundary for safe retrieval performance observations."""

    def record(self, metrics: RetrievalMetrics) -> None:
        """Record metrics that contain no query or document content."""
        ...


class KnowledgeIngestor(Protocol):
    """Local ingestion operations without exposing Chroma to callers."""

    async def health(self) -> bool:
        """Return whether the local index is ready."""
        ...

    async def ingest(self, document: SourceDocument) -> IngestionResult:
        """Ingest one Backend 2-approved document reference."""
        ...


class KnowledgeAdapter(KnowledgeIngestor, Protocol):
    """Combined ingestion/retrieval seam completed by the retrieval phase."""

    async def search(self, query: KnowledgeQuery) -> list[EvidenceChunk]:
        """Search locally and return application-controlled source metadata."""
        ...
