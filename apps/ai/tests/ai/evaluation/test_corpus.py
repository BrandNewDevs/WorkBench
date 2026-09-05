"""Golden corpus loading through its public application seam."""

from pathlib import Path

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
