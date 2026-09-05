"""Persistent local Chroma ingestion using embeddings supplied by Ollama."""

import asyncio
import re
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path

import chromadb
from chromadb import Collection
from chromadb.api import ClientAPI
from chromadb.config import Settings
from pydantic import ValidationError

from app.ai.errors import (
    CorruptKnowledgeInput,
    KnowledgeIndexUnavailable,
)
from app.ai.knowledge.chunking import KnowledgeChunker
from app.ai.knowledge.config import KnowledgeProcessingSettings
from app.ai.knowledge.contracts import (
    IndexedChunkMetadata,
    KnowledgeChunk,
    KnowledgeChunkIdentity,
    knowledge_chunk_id,
)
from app.ai.knowledge.document_parser import LocalDocumentParser
from app.ai.models.ports import ModelAdapter
from app.ai.schemas import (
    ApprovedKnowledgeRoot,
    EmbeddingRequest,
    IngestionResult,
    ModelProfile,
    SourceDocument,
)

KNOWLEDGE_SCHEMA_VERSION = "v1"
_COLLECTION_PREFIX = "workbench-knowledge"


def collection_name_for(embedding_model_id: str) -> str:
    """Create a valid collection name tied to one model and schema version."""

    slug = re.sub(r"[^a-z0-9]+", "-", embedding_model_id.lower()).strip("-")
    slug = slug[:80].rstrip("-") or "model"
    model_digest = sha256(embedding_model_id.encode("utf-8")).hexdigest()[:10]
    return f"{_COLLECTION_PREFIX}-{slug}-{model_digest}-{KNOWLEDGE_SCHEMA_VERSION}"


def create_persistent_chroma_client(root: ApprovedKnowledgeRoot) -> ClientAPI:
    """Open only Backend 2's approved root with every telemetry path disabled."""

    path = _validated_storage_root(root.path)
    settings = Settings(
        allow_reset=False,
        anonymized_telemetry=False,
        chroma_api_impl="chromadb.api.rust.RustBindingsAPI",
        is_persistent=True,
        persist_directory=str(path),
        chroma_server_host=None,
        chroma_server_ssl_enabled=False,
        chroma_otel_collection_endpoint="",
    )
    try:
        return chromadb.PersistentClient(path=path, settings=settings)
    except Exception as error:
        raise KnowledgeIndexUnavailable("local Chroma index could not be opened") from error


def _validated_storage_root(path: Path) -> Path:
    if not path.is_absolute():
        raise KnowledgeIndexUnavailable("knowledge storage root must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise KnowledgeIndexUnavailable("knowledge storage root does not exist") from error
    if not resolved.is_dir():
        raise KnowledgeIndexUnavailable("knowledge storage root must be a directory")
    return resolved


class ChromaKnowledgeIngestor:
    """Parse, chunk, embed, and idempotently persist one approved document."""

    def __init__(
        self,
        client: ClientAPI,
        model_adapter: ModelAdapter,
        model_profile: ModelProfile,
        settings: KnowledgeProcessingSettings | None = None,
    ) -> None:
        self._client = client
        self._model_adapter = model_adapter
        self._model_profile = model_profile
        self._settings = settings or KnowledgeProcessingSettings()
        self._parser = LocalDocumentParser(self._settings)
        self._chunker = KnowledgeChunker(self._settings)
        self._ingestion_lock = asyncio.Lock()

    @property
    def collection_name(self) -> str:
        """Expose the deterministic model/schema collection identity."""

        return collection_name_for(self._embedding_model_id)

    @property
    def _embedding_model_id(self) -> str:
        return self._model_profile.embedding_candidates[0]

    async def health(self) -> bool:
        """Check the injected local persistent client without a network probe."""

        try:
            await asyncio.to_thread(self._client.heartbeat)
        except Exception:
            return False
        return True

    async def ingest(self, document: SourceDocument) -> IngestionResult:
        """Index only new chunks, then remove obsolete chunks for this document."""

        async with self._ingestion_lock:
            parsed = await asyncio.to_thread(self._parser.parse, document)
            chunks = self._chunker.chunk(parsed, self._embedding_model_id)
            if not chunks:
                raise CorruptKnowledgeInput(
                    "knowledge document contains headings but no indexable content"
                )

            collection = await asyncio.to_thread(self._get_or_create_collection)
            existing = await self._existing_document_metadata(
                collection,
                document_id=document.document_id,
                source_id=parsed.source_id,
            )
            existing_ids = set(existing)
            chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
            new_ids = set(chunks_by_id)
            unchanged_ids = existing_ids & new_ids
            for chunk_id in unchanged_ids:
                expected_metadata = chunks_by_id[chunk_id].chroma_metadata(
                    KNOWLEDGE_SCHEMA_VERSION
                )
                if existing[chunk_id] != expected_metadata:
                    raise KnowledgeIndexUnavailable(
                        "stored chunk metadata does not match its immutable chunk ID"
                    )
            added_chunks = tuple(
                chunk for chunk in chunks if chunk.chunk_id not in existing_ids
            )
            obsolete_ids = sorted(existing_ids - new_ids)

            embeddings = await self._embed_chunks(added_chunks)
            if added_chunks:
                await self._upsert_chunks(collection, added_chunks, embeddings)
            if obsolete_ids:
                await self._delete_chunks(collection, obsolete_ids)

            return IngestionResult(
                document_id=document.document_id,
                collection_name=collection.name,
                indexed_chunks=len(added_chunks),
                unchanged_chunks=len(unchanged_ids),
                replaced_chunks=len(obsolete_ids),
            )

    def _get_or_create_collection(self) -> Collection:
        metadata = {
            "embeddingModelId": self._embedding_model_id,
            "schemaVersion": KNOWLEDGE_SCHEMA_VERSION,
        }
        try:
            collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata=metadata,
                configuration={"hnsw": {"space": "cosine"}},
                embedding_function=None,
            )
        except Exception as error:
            raise KnowledgeIndexUnavailable("local Chroma collection is unavailable") from error

        actual_metadata = collection.metadata or {}
        if any(actual_metadata.get(key) != value for key, value in metadata.items()):
            raise KnowledgeIndexUnavailable(
                "local Chroma collection metadata does not match its model/schema identity"
            )
        return collection

    async def _existing_document_metadata(
        self,
        collection: Collection,
        document_id: str,
        source_id: str,
    ) -> dict[str, Mapping[str, object]]:
        try:
            result = await asyncio.to_thread(
                collection.get,
                where={"documentId": document_id},
                include=["metadatas"],
            )
        except Exception as error:
            raise KnowledgeIndexUnavailable("local Chroma document state is unavailable") from error
        metadatas = result["metadatas"]
        if metadatas is None or len(metadatas) != len(result["ids"]):
            raise KnowledgeIndexUnavailable("local Chroma chunk metadata is incomplete")

        records: dict[str, Mapping[str, object]] = {}
        identities: dict[KnowledgeChunkIdentity, set[str]] = {}
        for stored_id, metadata in zip(result["ids"], metadatas, strict=True):
            if metadata is None:
                raise KnowledgeIndexUnavailable("local Chroma chunk metadata is incomplete")
            try:
                indexed = IndexedChunkMetadata.model_validate(metadata, strict=True)
            except ValidationError as error:
                raise KnowledgeIndexUnavailable(
                    "stored chunk metadata does not match its immutable chunk ID"
                ) from error
            if (
                indexed.chunk_id != stored_id
                or indexed.document_id != document_id
                or indexed.source_id != source_id
                or indexed.embedding_model_id != self._embedding_model_id
                or indexed.schema_version != KNOWLEDGE_SCHEMA_VERSION
            ):
                raise KnowledgeIndexUnavailable(
                    "stored chunk metadata does not match its immutable chunk ID"
                )
            records[stored_id] = metadata
            identities.setdefault(indexed.identity, set()).add(stored_id)

        for identity, stored_ids in identities.items():
            expected_ids = {
                knowledge_chunk_id(identity, occurrence)
                for occurrence in range(len(stored_ids))
            }
            if stored_ids != expected_ids:
                raise KnowledgeIndexUnavailable(
                    "stored chunk metadata does not match its immutable chunk ID"
                )
        return records

    async def _embed_chunks(
        self,
        chunks: tuple[KnowledgeChunk, ...],
    ) -> tuple[tuple[float, ...], ...]:
        vectors: list[tuple[float, ...]] = []
        expected_dimensions: int | None = None
        batch_size = self._model_profile.embedding_batch_size
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            result = await self._model_adapter.create_embeddings(
                EmbeddingRequest(
                    model=self._embedding_model_id,
                    inputs=tuple(chunk.content for chunk in batch),
                )
            )
            if result.model != self._embedding_model_id:
                raise KnowledgeIndexUnavailable(
                    "embedding model changed; use its separate versioned collection"
                )
            if len(result.vectors) != len(batch):
                raise KnowledgeIndexUnavailable("embedding result count does not match its batch")
            for vector in result.vectors:
                if expected_dimensions is None:
                    expected_dimensions = len(vector)
                elif len(vector) != expected_dimensions:
                    raise KnowledgeIndexUnavailable(
                        "embedding dimensions changed during document ingestion"
                    )
                vectors.append(vector)
        return tuple(vectors)

    async def _upsert_chunks(
        self,
        collection: Collection,
        chunks: tuple[KnowledgeChunk, ...],
        embeddings: tuple[tuple[float, ...], ...],
    ) -> None:
        embedding_values: list[Sequence[float] | Sequence[int]] = [
            list(vector) for vector in embeddings
        ]
        try:
            await asyncio.to_thread(
                collection.upsert,
                ids=[chunk.chunk_id for chunk in chunks],
                embeddings=embedding_values,
                documents=[chunk.content for chunk in chunks],
                metadatas=[
                    chunk.chroma_metadata(KNOWLEDGE_SCHEMA_VERSION) for chunk in chunks
                ],
            )
        except Exception as error:
            raise KnowledgeIndexUnavailable("local Chroma chunks could not be stored") from error

    @staticmethod
    async def _delete_chunks(collection: Collection, chunk_ids: list[str]) -> None:
        try:
            await asyncio.to_thread(collection.delete, ids=chunk_ids)
        except Exception as error:
            raise KnowledgeIndexUnavailable(
                "obsolete local Chroma chunks could not be removed"
            ) from error
