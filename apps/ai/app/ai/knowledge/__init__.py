"""Local knowledge ingestion and retrieval interfaces."""

from app.ai.knowledge.ports import KnowledgeAdapter, KnowledgeIngestor, RetrievalMetricsSink

__all__ = ["KnowledgeAdapter", "KnowledgeIngestor", "RetrievalMetricsSink"]
