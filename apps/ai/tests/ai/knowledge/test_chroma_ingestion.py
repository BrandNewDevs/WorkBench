"""Integration tests for local, versioned, idempotent Chroma ingestion."""

import socket
from pathlib import Path

import pytest
from chromadb.api import ClientAPI

from app.ai.errors import KnowledgeIndexUnavailable
from app.ai.evaluation.samples import sample_model_profile
from app.ai.fakes import FakeModelAdapter
from app.ai.knowledge.chroma_ingestion import (
    KNOWLEDGE_SCHEMA_VERSION,
    ChromaKnowledgeIngestor,
    collection_name_for,
    create_persistent_chroma_client,
)
from app.ai.schemas import (
    ApprovedKnowledgeRoot,
    EmbeddingRequest,
    EmbeddingResult,
    ModelProfile,
    SourceDocument,
)

REQUIRED_METADATA = {
    "sourceId",
    "chunkId",
    "documentId",
    "documentName",
    "pageNumber",
    "section",
    "mimeType",
    "contentHash",
    "embeddingModelId",
    "schemaVersion",
}


class RecordingEmbeddingAdapter(FakeModelAdapter):
    """Record each local embedding batch while returning deterministic vectors."""

    def __init__(self) -> None:
        super().__init__()
        self.embedding_requests: list[EmbeddingRequest] = []

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Delegate to the no-network fake after recording exact batch inputs."""

        self.embedding_requests.append(request)
        return await super().create_embeddings(request)


class WrongModelEmbeddingAdapter(RecordingEmbeddingAdapter):
    """Simulate a runtime returning vectors from a different model space."""

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        result = await super().create_embeddings(request)
        return result.model_copy(update={"model": "unexpected-embedding-model"})


def source_document(
    document_id: str,
    document_name: str,
    content: str,
    *,
    mime_type: str = "text/markdown",
) -> SourceDocument:
    """Build sanitized corpus content with application-owned identity."""

    return SourceDocument(
        document_id=document_id,
        document_name=document_name,
        mime_type=mime_type,
        source_id=f"source-{document_id}",
        content=content.encode("utf-8"),
    )


def local_ingestor(
    tmp_path: Path,
    *,
    profile: ModelProfile | None = None,
    adapter: RecordingEmbeddingAdapter | None = None,
) -> tuple[ChromaKnowledgeIngestor, ClientAPI, RecordingEmbeddingAdapter]:
    """Compose the production persistent client under a temporary approved root."""

    storage_root = tmp_path / "chroma"
    storage_root.mkdir(exist_ok=True)
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=storage_root))
    recording_adapter = adapter or RecordingEmbeddingAdapter()
    ingestor = ChromaKnowledgeIngestor(
        client,
        recording_adapter,
        profile or sample_model_profile(),
    )
    return ingestor, client, recording_adapter


async def test_sanitized_sop_prior_note_and_template_are_indexed_with_metadata(
    tmp_path: Path,
) -> None:
    """Persist all three MVP corpus roles with complete traceable metadata."""

    ingestor, client, adapter = local_ingestor(tmp_path)
    documents = (
        source_document(
            "isolation-sop",
            "Isolation SOP.md",
            "# ISOLATION PROCEDURE\n\nClose the inlet valve before inspection.",
        ),
        source_document(
            "prior-note",
            "Prior approval note.md",
            "# PREVIOUS DECISION\n\nApprove thickness measurement before repair.",
        ),
        source_document(
            "approval-template",
            "Approval note template.md",
            "# REQUIRED SECTIONS\n\nSubject, findings, recommendation, and uncertainty.",
        ),
    )

    results = [await ingestor.ingest(document) for document in documents]

    collection = client.get_collection(ingestor.collection_name, embedding_function=None)
    stored = collection.get(include=["metadatas", "documents", "embeddings"])
    metadatas = stored["metadatas"]
    embeddings = stored["embeddings"]
    assert all(result.indexed_chunks == 1 for result in results)
    assert collection.count() == 3
    assert metadatas is not None
    assert all(
        metadata is not None and metadata.keys() >= REQUIRED_METADATA
        for metadata in metadatas
    )
    assert all(metadata is not None and metadata["pageNumber"] == 1 for metadata in metadatas)
    assert all(
        metadata is not None
        and metadata["embeddingModelId"] == "qwen3-embedding:0.6b"
        and metadata["schemaVersion"] == KNOWLEDGE_SCHEMA_VERSION
        for metadata in metadatas
    )
    assert embeddings is not None and len(embeddings) == 3
    assert len(adapter.embedding_requests) == 3
    assert collection._embedding_function is None
    assert (tmp_path / "chroma" / "chroma.sqlite3").is_file()


async def test_reingesting_unchanged_document_does_not_embed_or_duplicate(
    tmp_path: Path,
) -> None:
    """Recognize stable chunk IDs before calling Ollama or writing Chroma."""

    ingestor, client, adapter = local_ingestor(tmp_path)
    document = source_document(
        "isolation-sop",
        "Isolation SOP.md",
        "# PROCEDURE\n\nClose the valve.\n\nConfirm zero pressure.",
    )

    first = await ingestor.ingest(document)
    embedding_call_count = len(adapter.embedding_requests)
    second = await ingestor.ingest(document)

    collection = client.get_collection(ingestor.collection_name, embedding_function=None)
    assert first.indexed_chunks == 1
    assert second.indexed_chunks == 0
    assert second.unchanged_chunks == 1
    assert second.replaced_chunks == 0
    assert collection.count() == 1
    assert len(adapter.embedding_requests) == embedding_call_count


async def test_immutable_metadata_mismatch_is_not_silently_accepted(tmp_path: Path) -> None:
    """Treat changed provenance under a stable chunk ID as index corruption."""

    ingestor, client, _ = local_ingestor(tmp_path)
    document = source_document(
        "isolation-sop",
        "Isolation SOP.md",
        "# PROCEDURE\n\nClose the valve.",
    )
    await ingestor.ingest(document)
    collection = client.get_collection(ingestor.collection_name, embedding_function=None)
    (chunk_id,) = collection.get()["ids"]
    collection.update(ids=[chunk_id], metadatas=[{"section": "tampered"}])

    with pytest.raises(KnowledgeIndexUnavailable, match="immutable chunk ID"):
        await ingestor.ingest(document)


async def test_tampered_obsolete_metadata_is_rejected_before_deletion(
    tmp_path: Path,
) -> None:
    """Validate obsolete stored provenance before making any index changes."""

    ingestor, client, _ = local_ingestor(tmp_path)
    original = source_document(
        "isolation-sop",
        "Isolation SOP.md",
        "# PROCEDURE\n\nClose the inlet valve.\n\n# RECORDS\n\nRecord the inspector name.",
    )
    changed = source_document(
        "isolation-sop",
        "Isolation SOP.md",
        "# RECORDS\n\nRecord the inspector name.",
    )
    await ingestor.ingest(original)
    collection = client.get_collection(ingestor.collection_name, embedding_function=None)
    stored = collection.get(include=["metadatas"])
    metadatas = stored["metadatas"]
    assert metadatas is not None
    obsolete_id = next(
        chunk_id
        for chunk_id, metadata in zip(stored["ids"], metadatas, strict=True)
        if metadata is not None and metadata["section"] == "PROCEDURE"
    )
    collection.update(ids=[obsolete_id], metadatas=[{"section": "tampered"}])

    with pytest.raises(KnowledgeIndexUnavailable, match="immutable chunk ID"):
        await ingestor.ingest(changed)

    assert set(collection.get()["ids"]) == set(stored["ids"])


async def test_changed_document_replaces_only_affected_section(tmp_path: Path) -> None:
    """Keep the unchanged section ID and embedding while replacing one rule."""

    ingestor, client, adapter = local_ingestor(tmp_path)
    original = source_document(
        "isolation-sop",
        "Isolation SOP.md",
        "# PROCEDURE\n\nClose the inlet valve.\n\n# RECORDS\n\nRecord the inspector name.",
    )
    changed = source_document(
        "isolation-sop",
        "Isolation SOP.md",
        "# PROCEDURE\n\nClose both inlet valves.\n\n# RECORDS\n\nRecord the inspector name.",
    )

    await ingestor.ingest(original)
    collection = client.get_collection(ingestor.collection_name, embedding_function=None)
    before_ids = set(collection.get(where={"documentId": "isolation-sop"})["ids"])
    requests_before_change = len(adapter.embedding_requests)
    result = await ingestor.ingest(changed)
    after_ids = set(collection.get(where={"documentId": "isolation-sop"})["ids"])

    assert result.indexed_chunks == 1
    assert result.unchanged_chunks == 1
    assert result.replaced_chunks == 1
    assert len(before_ids & after_ids) == 1
    assert collection.count() == 2
    assert len(adapter.embedding_requests) == requests_before_change + 1
    assert len(adapter.embedding_requests[-1].inputs) == 1


async def test_embedding_requests_follow_the_profile_batch_size(tmp_path: Path) -> None:
    """Bound each Ollama embedding request rather than embedding a corpus at once."""

    profile = sample_model_profile().model_copy(update={"embedding_batch_size": 2})
    ingestor, _, adapter = local_ingestor(tmp_path, profile=profile)
    content = "\n\n".join(
        f"# SECTION {index}\n\nProcedure text for section {index}." for index in range(5)
    )

    result = await ingestor.ingest(
        source_document("batch-sop", "Batch SOP.md", content)
    )

    assert result.indexed_chunks == 5
    assert [len(request.inputs) for request in adapter.embedding_requests] == [2, 2, 1]


async def test_changed_embedding_model_uses_a_new_collection(tmp_path: Path) -> None:
    """Never place vectors from different embedding models in one collection."""

    first_ingestor, client, first_adapter = local_ingestor(tmp_path)
    document = source_document("sop", "SOP.md", "# RULE\n\nRecord the finding.")
    await first_ingestor.ingest(document)
    second_profile = sample_model_profile().model_copy(
        update={"embedding_candidates": ("replacement-embedding:1b",)}
    )
    second_adapter = RecordingEmbeddingAdapter()
    second_ingestor = ChromaKnowledgeIngestor(
        client,
        second_adapter,
        second_profile,
    )

    await second_ingestor.ingest(document)

    collections = client.list_collections()
    assert first_ingestor.collection_name != second_ingestor.collection_name
    assert {collection.name for collection in collections} == {
        first_ingestor.collection_name,
        second_ingestor.collection_name,
    }
    assert len(first_adapter.embedding_requests) == 1
    assert len(second_adapter.embedding_requests) == 1


async def test_runtime_cannot_mix_an_unexpected_embedding_model(tmp_path: Path) -> None:
    """Reject mismatched model metadata before any vector is written."""

    wrong_adapter = WrongModelEmbeddingAdapter()
    ingestor, client, _ = local_ingestor(tmp_path, adapter=wrong_adapter)

    with pytest.raises(KnowledgeIndexUnavailable, match="separate versioned collection"):
        await ingestor.ingest(
            source_document("sop", "SOP.md", "# RULE\n\nRecord the finding.")
        )

    collection = client.get_collection(ingestor.collection_name, embedding_function=None)
    assert collection.count() == 0


def test_persistent_client_disables_telemetry_and_remote_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create the index without allowing a Python network connection."""

    def reject_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("local Chroma attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    root = tmp_path / "chroma"
    root.mkdir()

    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=root))
    settings = client.get_settings()

    assert settings.anonymized_telemetry is False
    assert settings.chroma_otel_collection_endpoint == ""
    assert settings.chroma_server_host is None
    assert settings.chroma_api_impl == "chromadb.api.rust.RustBindingsAPI"
    assert settings.is_persistent is True
    assert settings.allow_reset is False


def test_unapproved_or_missing_storage_root_is_rejected(tmp_path: Path) -> None:
    """Require an existing absolute root supplied by Backend 2."""

    for root in (Path("relative/chroma"), tmp_path / "missing"):
        with pytest.raises(KnowledgeIndexUnavailable):
            create_persistent_chroma_client(ApprovedKnowledgeRoot(path=root))


def test_collection_name_contains_model_identity_and_schema_version() -> None:
    """Keep vector spaces visibly separate under deterministic valid names."""

    name = collection_name_for("qwen3-embedding:0.6b")

    assert name.startswith("workbench-knowledge-qwen3-embedding-0-6b-")
    assert name.endswith(f"-{KNOWLEDGE_SCHEMA_VERSION}")
