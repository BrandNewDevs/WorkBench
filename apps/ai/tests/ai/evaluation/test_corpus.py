"""Golden corpus loading through its public application seam."""

from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from app.ai.errors import GoldenCorpusError
from app.ai.evaluation.corpus import load_golden_corpus

GOLDEN_ROOT = Path(__file__).parents[2] / "fixtures" / "golden"


def test_golden_corpus_contains_all_versioned_sanitized_inputs() -> None:
    corpus = load_golden_corpus(GOLDEN_ROOT)

    assert corpus.expected.schema_version == "golden-corpus-v1"
    assert corpus.expected.corpus_id == "pump-inspection-v1"
    assert len(corpus.expected.required_findings) == 3
    assert len(corpus.expected.retrieval_queries) == 3
    assert corpus.scanned_report.name == "scanned-inspection-report.pdf"
    assert corpus.site_photograph.name == "site-photograph.png"
    assert corpus.sop.name == "pump-maintenance-sop.md"
    assert corpus.previous_approval_note.name == "previous-approval-note.md"
    assert corpus.approval_note_template.name == "approval-note-template.md"
    assert all(path.is_file() and path.stat().st_size > 0 for path in corpus.files)


def test_golden_visuals_are_an_image_only_report_and_native_site_image() -> None:
    corpus = load_golden_corpus(GOLDEN_ROOT)

    with pymupdf.open(corpus.scanned_report) as report:  # type: ignore[no-untyped-call]
        assert report.page_count == 2
        assert all(not page.get_text().strip() for page in report)
    with Image.open(corpus.site_photograph) as photograph:
        assert photograph.format == "PNG"
        assert photograph.width >= 1_000
        assert photograph.height >= 700


def test_incomplete_corpus_fails_with_the_missing_artifact_name(tmp_path: Path) -> None:
    with pytest.raises(GoldenCorpusError, match=r"scanned-inspection-report\.pdf"):
        load_golden_corpus(tmp_path)
