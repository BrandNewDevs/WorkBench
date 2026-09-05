"""Tests for structure-first, stable local knowledge chunks."""

from app.ai.knowledge.chunking import KnowledgeChunker
from app.ai.knowledge.config import KnowledgeProcessingSettings
from app.ai.knowledge.contracts import DocumentBlock, ParsedDocument

EMBEDDING_MODEL = "qwen3-embedding:0.6b"


def parsed_document(*, changed_rule: bool = False) -> ParsedDocument:
    """Create two independent sections so one can change without touching the other."""

    safety_rule = (
        "Close the inlet valve before inspection. Confirm zero pressure. Apply the lock."
    )
    if changed_rule:
        safety_rule = (
            "Close both inlet valves before inspection. Confirm zero pressure. Apply the lock."
        )
    return ParsedDocument(
        source_id="sop-source",
        document_id="isolation-sop",
        document_name="Isolation SOP.pdf",
        mime_type="application/pdf",
        blocks=(
            DocumentBlock(page_number=1, text="ISOLATION PROCEDURE", is_heading=True),
            DocumentBlock(page_number=1, text=safety_rule),
            DocumentBlock(page_number=1, text="RECORDS", is_heading=True),
            DocumentBlock(
                page_number=1,
                text="Record the valve identity, pressure reading, and inspector name.",
            ),
        ),
    )


def test_complete_procedure_stays_together_with_heading_context() -> None:
    """Do not split a complete rule that already fits the embedding target."""

    chunks = KnowledgeChunker().chunk(parsed_document(), EMBEDDING_MODEL)

    isolation_chunk = next(chunk for chunk in chunks if chunk.section == "ISOLATION PROCEDURE")
    assert "Close the inlet valve" in isolation_chunk.content
    assert "Confirm zero pressure" in isolation_chunk.content
    assert "Apply the lock" in isolation_chunk.content
    assert isolation_chunk.content.startswith("ISOLATION PROCEDURE\n\n")


def test_length_fallback_keeps_every_chunk_below_the_bound() -> None:
    """Split an oversized paragraph by sentences and words only after structure."""

    settings = KnowledgeProcessingSettings(max_chunk_chars=512)
    long_rule = " ".join(
        f"Sentence {index} requires the inspector to record the observed condition."
        for index in range(30)
    )
    document = ParsedDocument(
        source_id="long-source",
        document_id="long-sop",
        document_name="Long SOP",
        mime_type="text/plain",
        blocks=(
            DocumentBlock(page_number=1, text="PROCEDURE", is_heading=True),
            DocumentBlock(page_number=1, text=long_rule),
        ),
    )

    chunks = KnowledgeChunker(settings).chunk(document, EMBEDDING_MODEL)

    assert len(chunks) > 1
    assert all(len(chunk.content) <= settings.max_chunk_chars for chunk in chunks)


def test_chunk_metadata_is_complete_and_deterministic() -> None:
    """Generate stable IDs and all fields required for application-rendered citations."""

    chunker = KnowledgeChunker()
    first = chunker.chunk(parsed_document(), EMBEDDING_MODEL)
    second = chunker.chunk(parsed_document(), EMBEDDING_MODEL)

    assert first == second
    assert all(chunk.source_id == "sop-source" for chunk in first)
    assert all(chunk.page_number == 1 for chunk in first)
    assert all(chunk.section for chunk in first)
    assert all(chunk.content_hash for chunk in first)
    assert all(chunk.embedding_model_id == EMBEDDING_MODEL for chunk in first)
    assert all(chunk.chroma_metadata("v1")["chunkId"] == chunk.chunk_id for chunk in first)


def test_changing_one_section_preserves_unaffected_chunk_ids() -> None:
    """Let ingestion replace one changed rule without re-embedding another section."""

    chunker = KnowledgeChunker()
    original = chunker.chunk(parsed_document(), EMBEDDING_MODEL)
    changed = chunker.chunk(parsed_document(changed_rule=True), EMBEDDING_MODEL)

    original_by_section = {chunk.section: chunk.chunk_id for chunk in original}
    changed_by_section = {chunk.section: chunk.chunk_id for chunk in changed}
    assert original_by_section["RECORDS"] == changed_by_section["RECORDS"]
    assert original_by_section["ISOLATION PROCEDURE"] != changed_by_section[
        "ISOLATION PROCEDURE"
    ]
