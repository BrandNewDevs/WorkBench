"""End-to-end golden workflow orchestration and typed result reporting."""

from time import perf_counter

from app.ai.errors import AIError, NoRelevantEvidence
from app.ai.evaluation.contracts import (
    GoldenCorpus,
    GoldenGateResult,
    GoldenRunMetrics,
    GoldenRunResult,
    GoldenSuiteResult,
)
from app.ai.evaluation.gates import (
    draft_gates,
    match_findings,
    model_health_gate,
    reproducibility_signature,
    severity_gate,
)
from app.ai.evaluation.observability import ObservationSnapshot, ObservedModelAdapter
from app.ai.generation import StructuredTextGenerator
from app.ai.knowledge.chroma_ingestion import (
    ChromaKnowledgeIngestor,
    create_persistent_chroma_client,
)
from app.ai.models.ports import ModelAdapter
from app.ai.schemas import (
    ApprovedKnowledgeRoot,
    ApprovedPath,
    ApprovedVisualInput,
    Capability,
    DraftRequest,
    EvidenceChunk,
    InputModality,
    KnowledgeQuery,
    ModelProfile,
    SourceDocument,
    TaskDescriptor,
    TaskKind,
    VisualAnalysisRequest,
    VisualMimeType,
)
from app.ai.vision import LocalVisualNormalizer, VisionAnalyzer

_SUBJECT = "Pump P-17 inspection follow-up"
_OBJECTIVE = "Draft an evidence-grounded approval note for inspection follow-up."


class GoldenEvaluator:
    """Run identical golden gates against a fake or real local Ollama adapter."""

    def __init__(
        self,
        *,
        corpus: GoldenCorpus,
        model_adapter: ModelAdapter,
        model_profile: ModelProfile,
        knowledge_root: ApprovedKnowledgeRoot,
    ) -> None:
        self._corpus = corpus
        self._profile = model_profile
        self._observed_model = ObservedModelAdapter(model_adapter)
        client = create_persistent_chroma_client(knowledge_root)
        self._knowledge = ChromaKnowledgeIngestor(
            client,
            self._observed_model,
            model_profile,
        )
        self._vision = VisionAnalyzer(
            self._observed_model,
            model_profile,
            LocalVisualNormalizer(),
        )
        self._generator = StructuredTextGenerator(self._observed_model, model_profile)

    async def run_three_times(self) -> GoldenSuiteResult:
        """Run exactly three consecutive workflows and compare stable quality outcomes."""

        runs: list[GoldenRunResult] = []
        for run_number in range(1, 4):
            runs.append(await self._run_once(run_number))
        signatures = {reproducibility_signature(run) for run in runs}
        reproducible = len(signatures) == 1
        diagnostics: tuple[str, ...] = ()
        if not reproducible:
            diagnostics = (
                "Golden outcomes changed between consecutive runs; compare matched findings, "
                "retrieval ranks, and gate results.",
            )
        return GoldenSuiteResult(
            corpus_id=self._corpus.expected.corpus_id,
            passed=all(run.passed for run in runs) and reproducible,
            reproducible=reproducible,
            runs=tuple(runs),
            diagnostics=diagnostics,
        )

    async def _run_once(self, run_number: int) -> GoldenRunResult:
        started = perf_counter()
        before = self._observed_model.snapshot()
        durations: dict[str, float] = {}
        gates: list[GoldenGateResult] = []
        diagnostics: list[str] = []
        matched_keys: tuple[str, ...] = ()
        retrieval_ranks: dict[str, int | None] = {
            query.query_id: None for query in self._corpus.expected.retrieval_queries
        }
        extraction_successes = 0
        final_schema_valid = False

        try:
            stage = perf_counter()
            health = await self._observed_model.health(self._profile)
            durations["health"] = _elapsed_ms(stage)
            health_result = model_health_gate(health, self._profile)
            gates.append(health_result)
            if not health_result.passed:
                diagnostics.append(health_result.diagnostic)
                return self._result(
                    run_number=run_number,
                    started=started,
                    before=before,
                    durations=durations,
                    gates=gates,
                    diagnostics=diagnostics,
                    matched_keys=matched_keys,
                    extraction_successes=extraction_successes,
                    retrieval_ranks=retrieval_ranks,
                    final_schema_valid=final_schema_valid,
                )

            stage = perf_counter()
            await self._ingest_corpus()
            durations["knowledgeIngestion"] = _elapsed_ms(stage)

            stage = perf_counter()
            analysis = await self._vision.analyze_visual(self._visual_request())
            durations["visionExtraction"] = _elapsed_ms(stage)
            matched_keys, finding_diagnostics = match_findings(
                analysis.findings,
                self._corpus.expected,
            )
            extraction_successes = len(matched_keys)
            findings_passed = extraction_successes == len(
                self._corpus.expected.required_findings
            )
            gates.append(
                GoldenGateResult(
                    name="vision-recall",
                    passed=findings_passed,
                    diagnostic=(
                        "All three expected findings were grounded at their required locations."
                        if findings_passed
                        else "; ".join(finding_diagnostics)
                    ),
                )
            )
            diagnostics.extend(finding_diagnostics)
            severity_result = severity_gate(analysis.findings, self._corpus.expected)
            gates.append(severity_result)
            if not severity_result.passed:
                diagnostics.append(severity_result.diagnostic)

            stage = perf_counter()
            evidence, retrieval_diagnostics = await self._retrieve_evidence(retrieval_ranks)
            durations["knowledgeRetrieval"] = _elapsed_ms(stage)
            retrieval_passed = all(rank is not None for rank in retrieval_ranks.values())
            gates.append(
                GoldenGateResult(
                    name="retrieval-recall-at-3",
                    passed=retrieval_passed,
                    diagnostic=(
                        "Every golden query returned the expected SOP evidence in its top three."
                        if retrieval_passed
                        else "; ".join(retrieval_diagnostics)
                    ),
                )
            )
            diagnostics.extend(retrieval_diagnostics)

            stage = perf_counter()
            draft_request = DraftRequest(
                subject=_SUBJECT,
                objective=(
                    f"{_OBJECTIVE} Include this exact uncertainty statement: "
                    f"{self._corpus.expected.required_uncertainty_statement}"
                ),
                findings=analysis.findings,
                evidence=evidence,
                template_instructions=self._corpus.approval_note_template.read_text(
                    encoding="utf-8"
                ),
            )
            draft = await self._generator.create_grounded_draft(draft_request)
            durations["groundedDrafting"] = _elapsed_ms(stage)
            final_schema_valid = True
            generated_draft_gates = draft_gates(
                draft_request,
                draft,
                self._corpus.expected,
            )
            gates.extend(generated_draft_gates)
            diagnostics.extend(
                gate.diagnostic for gate in generated_draft_gates if not gate.passed
            )
        except (AIError, OSError, UnicodeError, ValueError) as error:
            diagnostic = f"{type(error).__name__}: {error}"
            diagnostics.append(diagnostic)
            gates.append(
                GoldenGateResult(
                    name="workflow-completion",
                    passed=False,
                    diagnostic=diagnostic,
                )
            )

        return self._result(
            run_number=run_number,
            started=started,
            before=before,
            durations=durations,
            gates=gates,
            diagnostics=diagnostics,
            matched_keys=matched_keys,
            extraction_successes=extraction_successes,
            retrieval_ranks=retrieval_ranks,
            final_schema_valid=final_schema_valid,
        )

    async def _retrieve_evidence(
        self,
        retrieval_ranks: dict[str, int | None],
    ) -> tuple[tuple[EvidenceChunk, ...], tuple[str, ...]]:
        evidence: dict[str, EvidenceChunk] = {}
        diagnostics: list[str] = []
        for query in self._corpus.expected.retrieval_queries:
            try:
                results = await self._knowledge.search(KnowledgeQuery(text=query.text))
            except NoRelevantEvidence as error:
                diagnostics.append(f"{query.query_id}: {error}")
                continue
            rank = next(
                (
                    index
                    for index, item in enumerate(results[:3], start=1)
                    if (
                        item.source_id,
                        item.page_number,
                        item.section,
                    )
                    == (
                        query.expected.source_id,
                        query.expected.page_number,
                        query.expected.section,
                    )
                ),
                None,
            )
            retrieval_ranks[query.query_id] = rank
            if rank is None:
                diagnostics.append(
                    f"{query.query_id}: expected SOP page/section was absent from top three"
                )
            for item in results[:3]:
                evidence[item.chunk_id] = item
        return tuple(evidence.values()), tuple(diagnostics)

    async def _ingest_corpus(self) -> None:
        documents = (
            (self._corpus.sop, "golden-sop", "pump-maintenance-sop"),
            (
                self._corpus.previous_approval_note,
                "golden-previous-note",
                "previous-approval-note",
            ),
            (
                self._corpus.approval_note_template,
                "golden-approval-template",
                "approval-note-template",
            ),
        )
        for path, document_id, source_id in documents:
            await self._knowledge.ingest(
                SourceDocument(
                    document_id=document_id,
                    document_name=path.name,
                    mime_type="text/markdown",
                    source_id=source_id,
                    content=path.read_bytes(),
                )
            )

    def _visual_request(self) -> VisualAnalysisRequest:
        session_id = f"golden-{self._corpus.expected.corpus_id}"
        return VisualAnalysisRequest(
            inputs=(
                ApprovedVisualInput(
                    approved_path=ApprovedPath(
                        path=self._corpus.scanned_report,
                        source_id="inspection-report",
                        session_id=session_id,
                    ),
                    mime_type=VisualMimeType.PDF,
                    document_name=self._corpus.scanned_report.name,
                ),
                ApprovedVisualInput(
                    approved_path=ApprovedPath(
                        path=self._corpus.site_photograph,
                        source_id="site-photograph",
                        session_id=session_id,
                    ),
                    mime_type=VisualMimeType.PNG,
                    document_name=self._corpus.site_photograph.name,
                ),
            ),
            task=TaskDescriptor(
                task_id=session_id,
                kind=TaskKind.VISUAL_ANALYSIS,
                summary="Extract observed pump defects without inferring engineering certainty.",
                modalities=(InputModality.SCANNED_PDF, InputModality.IMAGE),
                file_types=(VisualMimeType.PDF.value, VisualMimeType.PNG.value),
            ),
        )

    def _result(
        self,
        *,
        run_number: int,
        started: float,
        before: ObservationSnapshot,
        durations: dict[str, float],
        gates: list[GoldenGateResult],
        diagnostics: list[str],
        matched_keys: tuple[str, ...],
        extraction_successes: int,
        retrieval_ranks: dict[str, int | None],
        final_schema_valid: bool,
    ) -> GoldenRunResult:
        after = self._observed_model.snapshot()
        expected_count = len(self._corpus.expected.required_findings)
        if not gates:
            gates.append(
                GoldenGateResult(
                    name="workflow-completion",
                    passed=False,
                    diagnostic="The golden workflow produced no quality gates.",
                )
            )
        metrics = GoldenRunMetrics(
            run_number=run_number,
            extraction_successes=extraction_successes,
            expected_findings=expected_count,
            vision_recall=extraction_successes / expected_count,
            retrieval_ranks=retrieval_ranks,
            schema_failures=after.schema_failures - before.schema_failures,
            fallback_uses=after.fallback_uses - before.fallback_uses,
            final_schema_valid=final_schema_valid,
            model_invocations=after.model_invocations - before.model_invocations,
            prompt_tokens=after.prompt_tokens - before.prompt_tokens,
            generated_tokens=after.generated_tokens - before.generated_tokens,
            model_total_duration_ms=_nanoseconds_to_milliseconds(
                after.model_total_duration_ns - before.model_total_duration_ns
            ),
            model_load_duration_ms=_nanoseconds_to_milliseconds(
                after.model_load_duration_ns - before.model_load_duration_ns
            ),
            generation_tokens_per_second=_tokens_per_second(before, after),
            selected_models=_selected_models(before, after),
            operation_durations_ms=durations,
            total_duration_ms=_elapsed_ms(started),
        )
        return GoldenRunResult(
            corpus_id=self._corpus.expected.corpus_id,
            run_number=run_number,
            passed=all(gate.passed for gate in gates) and final_schema_valid,
            matched_finding_keys=matched_keys,
            gates=tuple(gates),
            diagnostics=tuple(diagnostics),
            metrics=metrics,
        )


def _elapsed_ms(started: float) -> float:
    return max(0.0, (perf_counter() - started) * 1_000)


def _nanoseconds_to_milliseconds(value: int) -> float:
    return max(0.0, value / 1_000_000)


def _tokens_per_second(
    before: ObservationSnapshot,
    after: ObservationSnapshot,
) -> float | None:
    generated_tokens = after.generated_tokens - before.generated_tokens
    duration_ns = after.generation_duration_ns - before.generation_duration_ns
    if generated_tokens <= 0 or duration_ns <= 0:
        return None
    return generated_tokens / (duration_ns / 1_000_000_000)


def _selected_models(
    before: ObservationSnapshot,
    after: ObservationSnapshot,
) -> dict[str, tuple[str, ...]]:
    selections = after.model_selections[len(before.model_selections) :]
    return {
        capability.value: tuple(
            dict.fromkeys(
                model
                for selected_capability, model in selections
                if selected_capability is capability
            )
        )
        for capability in Capability
    }
