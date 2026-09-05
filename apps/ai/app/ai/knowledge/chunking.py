"""Structure-first deterministic chunking for local knowledge documents."""

import re
from collections import defaultdict
from hashlib import sha256

from app.ai.knowledge.config import KnowledgeProcessingSettings
from app.ai.knowledge.contracts import KnowledgeChunk, ParsedDocument

_SENTENCE_BREAK = re.compile(r"(?<=[.!?;])\s+")


class KnowledgeChunker:
    """Keep page and section structure before falling back to length splitting."""

    def __init__(self, settings: KnowledgeProcessingSettings | None = None) -> None:
        self._settings = settings or KnowledgeProcessingSettings()

    def chunk(
        self,
        document: ParsedDocument,
        embedding_model_id: str,
    ) -> tuple[KnowledgeChunk, ...]:
        """Create stable chunks whose IDs change only when their own content changes."""

        grouped: list[tuple[int, str, tuple[str, ...]]] = []
        section = document.document_name[: self._settings.max_heading_chars]
        page_number: int | None = None
        paragraphs: list[str] = []

        def flush() -> None:
            if page_number is not None and paragraphs:
                grouped.append((page_number, section, tuple(paragraphs)))
                paragraphs.clear()

        for block in document.blocks:
            if page_number is not None and block.page_number != page_number:
                flush()
            page_number = block.page_number
            if block.is_heading:
                flush()
                section = block.text[: self._settings.max_heading_chars]
            else:
                paragraphs.append(block.text)
        flush()

        chunks: list[KnowledgeChunk] = []
        identity_occurrences: defaultdict[str, int] = defaultdict(int)
        for current_page, current_section, current_paragraphs in grouped:
            for content in self._pack_section(current_section, current_paragraphs):
                content_hash = sha256(content.encode("utf-8")).hexdigest()
                identity = "\0".join(
                    (
                        document.source_id,
                        document.document_id,
                        document.document_name,
                        document.mime_type,
                        str(current_page),
                        current_section,
                        content_hash,
                    )
                )
                occurrence = identity_occurrences[identity]
                identity_occurrences[identity] += 1
                chunk_digest = sha256(f"{identity}\0{occurrence}".encode()).hexdigest()
                chunks.append(
                    KnowledgeChunk(
                        source_id=document.source_id,
                        chunk_id=f"kc_{chunk_digest}",
                        document_id=document.document_id,
                        document_name=document.document_name,
                        page_number=current_page,
                        section=current_section,
                        mime_type=document.mime_type,
                        content=content,
                        content_hash=content_hash,
                        embedding_model_id=embedding_model_id,
                    )
                )
        return tuple(chunks)

    def _pack_section(self, section: str, paragraphs: tuple[str, ...]) -> tuple[str, ...]:
        prefix = f"{section}\n\n"
        body_limit = self._settings.max_chunk_chars - len(prefix)
        if body_limit <= 0:
            raise ValueError("section heading leaves no room for chunk content")

        pieces = [
            piece
            for paragraph in paragraphs
            for piece in self._split_long_text(paragraph, body_limit)
        ]
        packed: list[str] = []
        current: list[str] = []
        current_length = 0
        for piece in pieces:
            separator_length = 2 if current else 0
            if current and current_length + separator_length + len(piece) > body_limit:
                packed.append(prefix + "\n\n".join(current))
                current = []
                current_length = 0
                separator_length = 0
            current.append(piece)
            current_length += separator_length + len(piece)
        if current:
            packed.append(prefix + "\n\n".join(current))
        return tuple(packed)

    @staticmethod
    def _split_long_text(text: str, limit: int) -> tuple[str, ...]:
        if len(text) <= limit:
            return (text,)

        sentences = [sentence.strip() for sentence in _SENTENCE_BREAK.split(text) if sentence]
        if len(sentences) > 1 and all(len(sentence) <= limit for sentence in sentences):
            return KnowledgeChunker._pack_parts(sentences, limit)

        words = text.split()
        expanded_words = [
            segment
            for word in words
            for segment in (
                tuple(word[index : index + limit] for index in range(0, len(word), limit))
                if len(word) > limit
                else (word,)
            )
        ]
        return KnowledgeChunker._pack_parts(expanded_words, limit, separator=" ")

    @staticmethod
    def _pack_parts(
        parts: list[str],
        limit: int,
        *,
        separator: str = " ",
    ) -> tuple[str, ...]:
        packed: list[str] = []
        current = ""
        for part in parts:
            candidate = part if not current else f"{current}{separator}{part}"
            if current and len(candidate) > limit:
                packed.append(current)
                current = part
            else:
                current = candidate
        if current:
            packed.append(current)
        return tuple(packed)
