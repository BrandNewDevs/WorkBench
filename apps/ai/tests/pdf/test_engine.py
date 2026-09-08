from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest

from app.pdf.contracts import (
    PdfDocumentDraft,
    PdfDraftSection,
    PdfEditPlan,
    PdfReplaceText,
    PdfSource,
)
from app.pdf.engine import LocalPdfDocumentEngine, PdfDocumentError


def _source(path: Path, page_count: int = 1) -> PdfSource:
    return PdfSource(
        upload_id=uuid4(),
        source_id=uuid4(),
        file_name="source.pdf",
        sha256=sha256(path.read_bytes()).hexdigest(),
        page_count=page_count,
    )


def _native_pdf(path: Path) -> None:
    with pymupdf.open() as document:  # type: ignore[no-untyped-call]
        page = document.new_page()
        page.insert_text((72, 72), "Original inspection summary", fontsize=12)
        document.save(path)


def test_inspect_native_pdf_creates_stable_layout_blocks(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdf"
    _native_pdf(source_path)

    pages = LocalPdfDocumentEngine().inspect(_source(source_path), source_path)

    assert len(pages) == 1
    assert pages[0].extraction_method == "native"
    assert pages[0].text_blocks[0].text == "Original inspection summary"
    assert pages[0].text_blocks[0].block_id.startswith("pdfb_")


def test_render_draft_creates_valid_local_pdf(tmp_path: Path) -> None:
    destination = tmp_path / "created.pdf"
    draft = PdfDocumentDraft(
        title="Inspection note",
        purpose="Summarise the approved inspection information.",
        sections=(
            PdfDraftSection(
                heading="Key points",
                paragraphs=("Valve inspection is due before commissioning.",),
                bullets=("Confirm the pressure test record.",),
            ),
        ),
        header="WorkBench",
    )

    page_count = LocalPdfDocumentEngine(
        write_policy=lambda candidate, target: candidate == draft and target == destination
    ).render_draft(draft, destination)

    assert page_count == 1
    with pymupdf.open(destination) as document:  # type: ignore[no-untyped-call]
        assert "Inspection note" in document[0].get_text()


def test_edit_replaces_only_known_native_block_without_mutating_source(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdf"
    output_path = tmp_path / "edited.pdf"
    _native_pdf(source_path)
    original_bytes = source_path.read_bytes()
    source = _source(source_path)
    engine = LocalPdfDocumentEngine(
        write_policy=lambda candidate, target: candidate == plan and target == output_path
    )
    pages = engine.inspect(source, source_path)
    plan = PdfEditPlan(
        source_id=source.source_id,
        output_file_name="edited.pdf",
        operations=(
            PdfReplaceText(
                block_id=pages[0].text_blocks[0].block_id,
                replacement="Updated report",
            ),
        ),
    )

    engine.apply_edit(source, source_path, pages, plan, output_path)

    assert source_path.read_bytes() == original_bytes
    with pymupdf.open(output_path) as document:  # type: ignore[no-untyped-call]
        text = document[0].get_text()
    assert "Updated report" in text
    assert "Original inspection summary" not in text


def test_edit_rejects_unknown_block_id(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdf"
    _native_pdf(source_path)
    source = _source(source_path)
    engine = LocalPdfDocumentEngine(
        write_policy=lambda candidate, target: (
            candidate == plan and target == tmp_path / "edited.pdf"
        )
    )
    pages = engine.inspect(source, source_path)
    plan = PdfEditPlan(
        source_id=source.source_id,
        output_file_name="edited.pdf",
        operations=(PdfReplaceText(block_id="pdfb_" + "0" * 64, replacement="Unsafe"),),
    )

    with pytest.raises(PdfDocumentError, match="non-existent"):
        engine.apply_edit(source, source_path, pages, plan, tmp_path / "edited.pdf")


def test_creation_without_backend_approval_writes_nothing(tmp_path: Path) -> None:
    draft = PdfDocumentDraft(
        title="Draft",
        purpose="Test",
        sections=(PdfDraftSection(heading="Facts", paragraphs=("Approved facts",)),),
    )
    destination = tmp_path / "denied.pdf"
    with pytest.raises(PdfDocumentError, match="approved execution"):
        LocalPdfDocumentEngine().render_draft(draft, destination)
    assert not destination.exists()
