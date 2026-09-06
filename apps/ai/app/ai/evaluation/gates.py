"""Pure quality gates for the sanitized golden workflow."""

from app.ai.evaluation.contracts import (
    ExpectedFinding,
    GoldenExpectedResults,
    GoldenGateResult,
    GoldenRunResult,
)
from app.ai.schemas import (
    Capability,
    DraftRequest,
    Finding,
    GroundedDraft,
    ModelProfile,
    ModelRuntimeHealth,
    ModelStatus,
)


def model_health_gate(
    health: ModelRuntimeHealth,
    profile: ModelProfile,
) -> GoldenGateResult:
    """Require every local capability to select an approved ready model."""

    if not health.runtime_ready:
        return GoldenGateResult(
            name="model-health",
            passed=False,
            diagnostic="Local Ollama runtime is unavailable for the golden workflow.",
        )
    by_capability = {item.capability: item for item in health.models}
    failures: list[str] = []
    candidates = {
        Capability.TEXT: profile.text_candidates,
        Capability.VISION: profile.vision_candidates,
        Capability.EMBEDDING: profile.embedding_candidates,
    }
    for capability, approved in candidates.items():
        item = by_capability.get(capability)
        if item is None or item.status is not ModelStatus.READY or item.selected_model is None:
            failures.append(f"{capability.value} capability is not ready")
            continue
        if item.selected_model not in approved:
            failures.append(f"{capability.value} selected an unapproved model")
        elif item.selected_model != approved[0] and not item.fallback_reason:
            failures.append(f"{capability.value} fallback has no recorded reason")
    return GoldenGateResult(
        name="model-health",
        passed=not failures,
        diagnostic=(
            "Text, vision, and embedding capabilities are ready with approved local models."
            if not failures
            else "; ".join(failures)
        ),
    )


def match_findings(
    findings: tuple[Finding, ...],
    expected: GoldenExpectedResults,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Match independent expected terms and locations against model findings."""

    matched: list[str] = []
    diagnostics: list[str] = []
    for required in expected.required_findings:
        if any(_finding_matches(item, required) for item in findings):
            matched.append(required.finding_key)
        else:
            locator = required.page_number or required.image_id
            diagnostics.append(
                f"Missing expected finding '{required.finding_key}' at "
                f"{required.source_id}:{locator}."
            )
    return tuple(matched), tuple(diagnostics)


def severity_gate(
    findings: tuple[Finding, ...],
    expected: GoldenExpectedResults,
) -> GoldenGateResult:
    """Reject severity labels outside the corpus-owned allowed set."""

    allowed = set(expected.allowed_severity_values)
    invalid = sorted(
        {finding.severity.value for finding in findings if finding.severity not in allowed}
    )
    return GoldenGateResult(
        name="allowed-severity-values",
        passed=not invalid,
        diagnostic=(
            "All extracted severities are allowed by the golden corpus."
            if not invalid
            else f"Unexpected severity values: {', '.join(invalid)}."
        ),
    )


def draft_gates(
    request: DraftRequest,
    draft: GroundedDraft,
    expected: GoldenExpectedResults,
) -> tuple[GoldenGateResult, ...]:
    """Check required structure, citations, uncertainty, and forbidden claims."""

    payload = draft.model_dump(mode="json", by_alias=True)
    missing_sections = [
        section
        for section in expected.required_draft_sections
        if section not in payload or payload[section] in (None, "", [], {})
    ]
    allowed_source_ids = {item.source_id for item in request.evidence} | {
        reference.source_id
        for finding in request.findings
        for reference in finding.evidence
    }
    unknown_source_ids = sorted(set(draft.evidence_source_ids) - allowed_source_ids)
    expected_uncertainty = _normalize(expected.required_uncertainty_statement)
    uncertainty_present = any(
        _normalize(item) == expected_uncertainty for item in draft.uncertainties
    )
    required_sop = expected.expected_sop_reference
    required_sop_evidence_present = any(
        (item.source_id, item.page_number, item.section)
        == (required_sop.source_id, required_sop.page_number, required_sop.section)
        for item in request.evidence
    )
    required_sop_cited = any(
        required_sop.source_id in claim.evidence_source_ids
        for claim in draft.critical_claims
    )
    assertion_fields = _draft_assertion_fields(draft)
    forbidden = sorted(
        (
            conclusion,
            tuple(
                field_name
                for field_name, value in assertion_fields
                if _normalize(conclusion) in _normalize(value)
            ),
        )
        for conclusion in expected.forbidden_unsupported_conclusions
        if any(
            _normalize(conclusion) in _normalize(value)
            for _, value in assertion_fields
        )
    )
    return (
        GoldenGateResult(
            name="required-draft-sections",
            passed=not missing_sections,
            diagnostic=(
                "Every required approval-note section is populated."
                if not missing_sections
                else f"Missing draft sections: {', '.join(missing_sections)}."
            ),
        ),
        GoldenGateResult(
            name="citation-integrity",
            passed=not unknown_source_ids,
            diagnostic=(
                "Every draft source ID exists in the supplied evidence or findings."
                if not unknown_source_ids
                else f"Unknown draft source IDs: {', '.join(unknown_source_ids)}."
            ),
        ),
        GoldenGateResult(
            name="required-sop-citation",
            passed=required_sop_evidence_present and required_sop_cited,
            diagnostic=_required_sop_diagnostic(
                required_sop.source_id,
                required_sop.page_number,
                required_sop.section,
                evidence_present=required_sop_evidence_present,
                cited=required_sop_cited,
            ),
        ),
        GoldenGateResult(
            name="required-uncertainty",
            passed=uncertainty_present,
            diagnostic=(
                "The required missing-measurement statement is present."
                if uncertainty_present
                else "The required uncertainty statement is absent or changed."
            ),
        ),
        GoldenGateResult(
            name="forbidden-unsupported-conclusions",
            passed=not forbidden,
            diagnostic=(
                "No forbidden unsupported conclusion appears in the rendered draft content."
                if not forbidden
                else "Forbidden unsupported conclusions: "
                + "; ".join(
                    f"{conclusion} ({', '.join(fields)})"
                    for conclusion, fields in forbidden
                )
                + "."
            ),
        ),
        GoldenGateResult(
            name="final-schema",
            passed=True,
            diagnostic="The final approval-note response is a valid GroundedDraft.",
        ),
    )


def reproducibility_signature(run: GoldenRunResult) -> tuple[object, ...]:
    """Exclude timing while comparing three stable quality outcomes."""

    return (
        run.passed,
        run.matched_finding_keys,
        tuple(run.metrics.retrieval_ranks.items()),
        run.metrics.final_schema_valid,
        tuple((gate.name, gate.passed) for gate in run.gates),
    )


def _draft_assertion_fields(draft: GroundedDraft) -> tuple[tuple[str, str], ...]:
    fields: list[tuple[str, str]] = [
        ("summary", draft.summary),
        ("recommendation", draft.recommendation),
    ]
    fields.extend(
        (f"criticalClaims[{index}]", claim.text)
        for index, claim in enumerate(draft.critical_claims)
    )
    for index, finding in enumerate(draft.findings):
        fields.extend(
            (
                (f"findings[{index}].title", finding.title),
                (f"findings[{index}].description", finding.description),
            )
        )
        if finding.uncertainty is not None:
            fields.append((f"findings[{index}].uncertainty", finding.uncertainty))
    fields.extend(
        (f"uncertainties[{index}]", uncertainty)
        for index, uncertainty in enumerate(draft.uncertainties)
    )
    return tuple(fields)


def _required_sop_diagnostic(
    source_id: str,
    page_number: int,
    section: str,
    *,
    evidence_present: bool,
    cited: bool,
) -> str:
    if not evidence_present:
        return (
            f"Required SOP evidence '{source_id}' page {page_number}, section '{section}' "
            "was not supplied to drafting."
        )
    if not cited:
        return f"No critical claim cites required SOP source '{source_id}'."
    return f"A critical claim cites the retrieved required SOP source '{source_id}'."


def _finding_matches(finding: Finding, expected: ExpectedFinding) -> bool:
    searchable = _normalize(f"{finding.title} {finding.description}")
    if not all(_normalize(term) in searchable for term in expected.required_terms):
        return False
    return any(
        reference.source_id == expected.source_id
        and reference.page_number == expected.page_number
        and reference.image_id == expected.image_id
        for reference in finding.evidence
    )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
