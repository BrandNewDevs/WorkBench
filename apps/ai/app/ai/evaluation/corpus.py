"""Safe loading for the repository-owned sanitized golden corpus."""

from pathlib import Path

from pydantic import ValidationError

from app.ai.errors import GoldenCorpusError
from app.ai.evaluation.contracts import GoldenCorpus, GoldenExpectedResults

_SCANNED_REPORT = "scanned-inspection-report.pdf"
_SITE_PHOTOGRAPH = "site-photograph.png"
_SOP = "pump-maintenance-sop.md"
_PREVIOUS_NOTE = "previous-approval-note.md"
_TEMPLATE = "approval-note-template.md"
_EXPECTED_RESULTS = "expected-results.json"


def load_golden_corpus(root: Path) -> GoldenCorpus:
    """Resolve and validate all fixed corpus inputs without discovering other files."""

    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise GoldenCorpusError(f"golden corpus root does not exist: {root}") from error
    if not resolved_root.is_dir():
        raise GoldenCorpusError(f"golden corpus root is not a directory: {resolved_root}")

    files = {
        "scanned_report": resolved_root / _SCANNED_REPORT,
        "site_photograph": resolved_root / _SITE_PHOTOGRAPH,
        "sop": resolved_root / _SOP,
        "previous_approval_note": resolved_root / _PREVIOUS_NOTE,
        "approval_note_template": resolved_root / _TEMPLATE,
        "expected_results_file": resolved_root / _EXPECTED_RESULTS,
    }
    for label, path in files.items():
        if not path.is_file():
            raise GoldenCorpusError(f"golden corpus {label} is missing: {path.name}")
        try:
            if path.stat().st_size == 0:
                raise GoldenCorpusError(f"golden corpus {label} is empty: {path.name}")
        except OSError as error:
            raise GoldenCorpusError(f"golden corpus {label} cannot be inspected") from error

    try:
        raw_expected = files["expected_results_file"].read_text(encoding="utf-8")
        expected = GoldenExpectedResults.model_validate_json(raw_expected)
    except (OSError, UnicodeError, ValidationError, ValueError) as error:
        raise GoldenCorpusError("golden expected-results.json is invalid") from error

    return GoldenCorpus(root=resolved_root, expected=expected, **files)
