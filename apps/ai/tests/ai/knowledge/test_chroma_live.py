"""Opt-in ingestion check using a preloaded local Ollama embedding model."""

import os
from pathlib import Path

import pytest

from app.ai.knowledge.chroma_ingestion import (
    ChromaKnowledgeIngestor,
    create_persistent_chroma_client,
)
from app.ai.models import create_ollama_adapter, load_model_profile
from app.ai.schemas import ApprovedKnowledgeRoot, KnowledgeQuery, SourceDocument

pytestmark = [
    pytest.mark.live_ollama,
    pytest.mark.skipif(
        os.getenv("WORKBENCH_RUN_LIVE_OLLAMA") != "1",
        reason="set WORKBENCH_RUN_LIVE_OLLAMA=1 with the approved embedding model preloaded",
    ),
]


async def test_local_ollama_embeddings_are_persisted_idempotently(tmp_path: Path) -> None:
    """Exercise the complete parser-to-Ollama-to-persistent-Chroma ingestion path."""

    storage_root = tmp_path / "chroma"
    storage_root.mkdir()
    profile = load_model_profile()
    model_adapter = create_ollama_adapter(profile=profile)
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=storage_root))
    ingestor = ChromaKnowledgeIngestor(client, model_adapter, profile)
    document = SourceDocument(
        document_id="live-sop",
        document_name="Live sanitized SOP.md",
        mime_type="text/markdown",
        source_id="live-sop-source",
        content=b"# INSPECTION RULE\n\nRecord every unreadable measurement as uncertain.",
    )

    try:
        first = await ingestor.ingest(document)
        second = await ingestor.ingest(document)
        evidence = await ingestor.search(
            KnowledgeQuery(text="How should an unreadable measurement be recorded?")
        )
    finally:
        await model_adapter.close()

    collection = client.get_collection(ingestor.collection_name, embedding_function=None)
    assert first.indexed_chunks == 1
    assert second.unchanged_chunks == 1
    assert evidence[0].source_id == "live-sop-source"
    assert evidence[0].page_number == 1
    assert collection.count() == 1
