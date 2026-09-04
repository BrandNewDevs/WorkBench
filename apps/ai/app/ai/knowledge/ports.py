"""Dependency-injection boundary for the local knowledge index."""

from typing import Protocol

from app.ai.schemas import EvidenceChunk, IngestionResult, KnowledgeQuery, SourceDocument


class KnowledgeAdapter(Protocol):
    """Local knowledge operations without exposing Chroma to business logic."""

    async def health(self) -> bool:
        """Return whether the local index is ready."""
        ...

    async def ingest(self, document: SourceDocument) -> IngestionResult:
        """Ingest one Backend 2-approved document reference."""
        ...

    async def search(self, query: KnowledgeQuery) -> list[EvidenceChunk]:
        """Search locally and return application-controlled source metadata."""
        ...
