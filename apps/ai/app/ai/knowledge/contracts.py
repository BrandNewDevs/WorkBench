"""Internal immutable contracts for parsed documents and indexable chunks."""

from dataclasses import dataclass
from hashlib import sha256
from typing import TypeAlias

from pydantic import Field

from app.ai.schemas import ContractModel

ChromaMetadataValue: TypeAlias = str | int | float | bool
ChromaMetadata: TypeAlias = dict[str, ChromaMetadataValue]
KnowledgeChunkIdentity: TypeAlias = tuple[str, str, str, str, int, str, str]


def knowledge_chunk_identity(
    *,
    source_id: str,
    document_id: str,
    document_name: str,
    mime_type: str,
    page_number: int,
    section: str,
    content_hash: str,
) -> KnowledgeChunkIdentity:
    """Return the immutable fields that identify one chunk occurrence."""

    return (
        source_id,
        document_id,
        document_name,
        mime_type,
        page_number,
        section,
        content_hash,
    )


def knowledge_chunk_id(identity: KnowledgeChunkIdentity, occurrence: int) -> str:
    """Derive a stable Chroma ID from application-owned immutable metadata."""

    encoded_identity = "\0".join((*identity[:4], str(identity[4]), *identity[5:]))
    chunk_digest = sha256(f"{encoded_identity}\0{occurrence}".encode()).hexdigest()
    return f"kc_{chunk_digest}"


@dataclass(frozen=True, slots=True)
class DocumentBlock:
    """One ordered heading or paragraph tied to a logical page."""

    page_number: int
    text: str
    is_heading: bool = False


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Locally extracted structure plus application-owned source metadata."""

    source_id: str
    document_id: str
    document_name: str
    mime_type: str
    blocks: tuple[DocumentBlock, ...]


class IndexedChunkMetadata(ContractModel):
    """Strict immutable provenance read back from the local vector index."""

    source_id: str = Field(min_length=1)
    chunk_id: str = Field(pattern=r"^kc_[0-9a-f]{64}$")
    document_id: str = Field(min_length=1)
    document_name: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    section: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_model_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)

    @property
    def identity(self) -> KnowledgeChunkIdentity:
        """Return the identity whose digest must agree with the stored record ID."""

        return knowledge_chunk_identity(
            source_id=self.source_id,
            document_id=self.document_id,
            document_name=self.document_name,
            mime_type=self.mime_type,
            page_number=self.page_number,
            section=self.section,
            content_hash=self.content_hash,
        )


class KnowledgeChunk(ContractModel):
    """One embedding-ready passage with complete immutable provenance."""

    source_id: str = Field(min_length=1)
    chunk_id: str = Field(pattern=r"^kc_[0-9a-f]{64}$")
    document_id: str = Field(min_length=1)
    document_name: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    section: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    content: str = Field(min_length=1, repr=False)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_model_id: str = Field(min_length=1)

    def chroma_metadata(self, schema_version: str) -> ChromaMetadata:
        """Return only scalar metadata supported by local Chroma."""

        return {
            "sourceId": self.source_id,
            "chunkId": self.chunk_id,
            "documentId": self.document_id,
            "documentName": self.document_name,
            "pageNumber": self.page_number,
            "section": self.section,
            "mimeType": self.mime_type,
            "contentHash": self.content_hash,
            "embeddingModelId": self.embedding_model_id,
            "schemaVersion": schema_version,
        }
