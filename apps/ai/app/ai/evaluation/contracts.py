"""Typed contracts for the versioned, sanitized golden evaluation corpus."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.ai.schemas import ContractModel, FindingSeverity


class ExpectedFinding(ContractModel):
    """One observable finding and the corpus-owned location where it must appear."""

    finding_key: str = Field(min_length=1)
    required_terms: tuple[str, ...] = Field(min_length=1)
    source_id: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    image_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def has_one_expected_location(self) -> ExpectedFinding:
        if (self.page_number is None) == (self.image_id is None):
            raise ValueError("expected finding requires exactly one page or image locator")
        return self


class ExpectedSourceReference(ContractModel):
    """Expected application-owned metadata for a retrieved corpus passage."""

    source_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    section: str = Field(min_length=1)


class GoldenRetrievalQuery(ContractModel):
    """One fixed query and the evidence location required in its top three."""

    query_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    expected: ExpectedSourceReference


class GoldenExpectedResults(ContractModel):
    """Independent source of truth for every required golden quality gate."""

    schema_version: Literal["golden-corpus-v1"]
    corpus_id: str = Field(min_length=1)
    required_findings: tuple[ExpectedFinding, ...] = Field(min_length=3, max_length=3)
    expected_report_page_references: tuple[int, ...] = Field(min_length=1)
    expected_sop_reference: ExpectedSourceReference
    retrieval_queries: tuple[GoldenRetrievalQuery, ...] = Field(min_length=1)
    allowed_severity_values: tuple[FindingSeverity, ...] = Field(min_length=1)
    required_draft_sections: tuple[str, ...] = Field(min_length=1)
    required_uncertainty_statement: str = Field(min_length=1)
    forbidden_unsupported_conclusions: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def references_are_consistent(self) -> GoldenExpectedResults:
        finding_keys = tuple(item.finding_key for item in self.required_findings)
        query_ids = tuple(item.query_id for item in self.retrieval_queries)
        if len(finding_keys) != len(set(finding_keys)):
            raise ValueError("golden finding keys must be unique")
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("golden retrieval query IDs must be unique")
        report_pages = {
            item.page_number
            for item in self.required_findings
            if item.page_number is not None
        }
        if report_pages != set(self.expected_report_page_references):
            raise ValueError("expected report pages must match required finding locations")
        if any(item.expected != self.expected_sop_reference for item in self.retrieval_queries):
            raise ValueError("every golden query must target the expected SOP reference")
        return self


class GoldenGateResult(ContractModel):
    """One independently readable quality-gate outcome."""

    name: str = Field(min_length=1)
    passed: bool
    diagnostic: str = Field(min_length=1)


class GoldenRunMetrics(ContractModel):
    """Comparable quality and timing measurements for one complete run."""

    run_number: int = Field(ge=1)
    extraction_successes: int = Field(ge=0)
    expected_findings: int = Field(ge=1)
    vision_recall: float = Field(ge=0, le=1)
    retrieval_ranks: dict[str, int | None]
    schema_failures: int = Field(ge=0)
    schema_failures_by_capability: dict[str, int]
    fallback_uses: int = Field(ge=0)
    final_schema_valid: bool
    model_invocations: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    generated_tokens: int = Field(ge=0)
    model_client_elapsed_ms: float = Field(ge=0)
    model_total_duration_ms: float = Field(ge=0)
    model_load_duration_ms: float = Field(ge=0)
    generation_tokens_per_second: float | None = Field(default=None, ge=0)
    selected_models: dict[str, tuple[str, ...]]
    operation_durations_ms: dict[str, float]
    total_duration_ms: float = Field(ge=0)


class GoldenRunResult(ContractModel):
    """Actionable pass/fail report for one golden workflow execution."""

    corpus_id: str = Field(min_length=1)
    run_number: int = Field(ge=1)
    passed: bool
    matched_finding_keys: tuple[str, ...]
    gates: tuple[GoldenGateResult, ...] = Field(min_length=1)
    diagnostics: tuple[str, ...]
    metrics: GoldenRunMetrics


class GoldenSuiteResult(ContractModel):
    """Three consecutive results plus their reproducibility decision."""

    corpus_id: str = Field(min_length=1)
    passed: bool
    reproducible: bool
    runs: tuple[GoldenRunResult, ...] = Field(min_length=3, max_length=3)
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GoldenCorpus:
    """Resolved paths and validated expectations for one immutable corpus."""

    root: Path
    scanned_report: Path
    site_photograph: Path
    sop: Path
    previous_approval_note: Path
    approval_note_template: Path
    expected_results_file: Path
    expected: GoldenExpectedResults

    @property
    def files(self) -> tuple[Path, ...]:
        """Return all six required corpus artifacts in stable order."""

        return (
            self.scanned_report,
            self.site_photograph,
            self.sop,
            self.previous_approval_note,
            self.approval_note_template,
            self.expected_results_file,
        )
