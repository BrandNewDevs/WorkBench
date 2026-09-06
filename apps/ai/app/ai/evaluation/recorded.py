"""Recorded local-model boundary fake used only by deterministic golden tests."""

import json

from app.ai.evaluation.samples import sample_inference_metrics, sample_runtime_health
from app.ai.schemas import (
    EmbeddingRequest,
    EmbeddingResult,
    Finding,
    FindingSeverity,
    GroundedClaim,
    GroundedDraft,
    InstalledModel,
    ModelProfile,
    ModelRuntimeHealth,
    SourceReference,
    TextGenerationRequest,
    TextGenerationResult,
    VisionAnalysis,
    VisionGenerationRequest,
    VisionPageResult,
)

_REPORT_NAME = "scanned-inspection-report.pdf"
_PHOTO_NAME = "site-photograph.png"
_UNCERTAINTY = (
    "Remaining wall thickness and leak rate require instrumented verification before "
    "repair approval."
)


def _report_findings() -> tuple[Finding, Finding]:
    location = SourceReference(
        source_id="inspection-report",
        document_name=_REPORT_NAME,
        page_number=2,
    )
    return (
        Finding(
            finding_id="lower-flange-corrosion",
            title="Lower flange surface corrosion",
            description="Localized orange surface corrosion was observed at the lower flange.",
            severity=FindingSeverity.MEDIUM,
            evidence=(location,),
            uncertainty="Remaining wall thickness was not measured.",
        ),
        Finding(
            finding_id="shaft-seal-seepage",
            title="Oil seepage below shaft seal",
            description="A dark oil seepage stain was observed directly below the shaft seal.",
            severity=FindingSeverity.MEDIUM,
            evidence=(location,),
            uncertainty="Leak rate was not measured.",
        ),
    )


def _site_finding() -> Finding:
    return Finding(
        finding_id="missing-guard-bolt",
        title="Missing coupling guard bolt",
        description="One bolt is visibly missing from the yellow coupling safety guard.",
        severity=FindingSeverity.UNKNOWN,
        evidence=(
            SourceReference(
                source_id="site-photograph",
                document_name=_PHOTO_NAME,
                image_id="site-photograph",
            ),
        ),
        uncertainty="A photograph alone cannot establish fitness for service.",
    )


class GoldenRecordedModelAdapter:
    """Return corpus-specific recorded responses without contacting Ollama."""

    def __init__(
        self,
        *,
        omit_site_finding: bool = False,
        invalid_draft_attempts: int = 0,
    ) -> None:
        self._omit_site_finding = omit_site_finding
        self._invalid_draft_attempts = invalid_draft_attempts

    async def list_models(self) -> tuple[InstalledModel, ...]:
        return tuple(
            InstalledModel(name=item.selected_model, size_bytes=0)
            for item in sample_runtime_health().models
            if item.selected_model is not None
        )

    async def health(self, profile: ModelProfile) -> ModelRuntimeHealth:
        del profile
        return sample_runtime_health()

    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        if self._invalid_draft_attempts > 0:
            self._invalid_draft_attempts -= 1
            return TextGenerationResult(
                model=request.model,
                text='{"subject":"incomplete"}',
                structured_output={"subject": "incomplete"},
                metrics=sample_inference_metrics(),
            )

        findings: tuple[Finding, ...] = _report_findings()
        if not self._omit_site_finding:
            findings = (*findings, _site_finding())
        claims = [
            GroundedClaim(
                text="The inspection recorded corrosion at the lower flange.",
                evidence_source_ids=("inspection-report",),
            ),
            GroundedClaim(
                text="The inspection recorded oil seepage below the shaft seal.",
                evidence_source_ids=("inspection-report",),
            ),
        ]
        source_ids = ["inspection-report"]
        if not self._omit_site_finding:
            claims.append(
                GroundedClaim(
                    text="The site photograph shows a missing coupling guard bolt.",
                    evidence_source_ids=("site-photograph",),
                )
            )
            source_ids.append("site-photograph")
        claims.append(
            GroundedClaim(
                text="The SOP requires measurements and guard restoration before a decision.",
                evidence_source_ids=("pump-maintenance-sop",),
            )
        )
        source_ids.append("pump-maintenance-sop")
        draft = GroundedDraft(
            subject="Pump P-17 inspection follow-up",
            summary="Synthetic inspection observations require verified follow-up.",
            findings=findings,
            recommendation="Obtain the measurements and restore the guard before review.",
            critical_claims=tuple(claims),
            evidence_source_ids=tuple(source_ids),
            uncertainties=(_UNCERTAINTY,),
        )
        return _text_result(request.model, draft)

    async def generate_vision(self, request: VisionGenerationRequest) -> TextGenerationResult:
        metadata = _source_metadata(request.user_prompt)
        source_id = str(metadata["sourceId"])
        page_number = metadata["pageNumber"]
        image_id = metadata["imageId"]
        findings: tuple[Finding, ...] = ()
        extracted_text = "Synthetic inspection cover page."
        warnings: tuple[str, ...] = ()
        if source_id == "inspection-report" and page_number == 2:
            findings = _report_findings()
            extracted_text = (
                "Localized corrosion at lower flange. Oil seepage below shaft seal. "
                "One coupling guard bolt missing."
            )
            warnings = ("Wall thickness and leak rate were not available.",)
        elif source_id == "site-photograph":
            findings = () if self._omit_site_finding else (_site_finding(),)
            extracted_text = "A generic pump skid with a yellow coupling guard."
            warnings = ("Photograph alone cannot establish fitness for service.",)

        page = VisionPageResult(
            source_id=source_id,
            page_number=page_number if isinstance(page_number, int) else None,
            image_id=image_id if isinstance(image_id, str) else None,
            extracted_text=extracted_text,
            findings=findings,
            warnings=warnings,
        )
        analysis = VisionAnalysis(
            model=request.model,
            extracted_text=extracted_text,
            pages=(page,),
            findings=findings,
            warnings=warnings,
        )
        return _text_result(request.model, analysis)

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        return EmbeddingResult(
            model=request.model,
            vectors=tuple(_embedding(text) for text in request.inputs),
            metrics=sample_inference_metrics(),
        )

    async def unload(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _text_result(model: str, value: GroundedDraft | VisionAnalysis) -> TextGenerationResult:
    return TextGenerationResult(
        model=model,
        text=value.model_dump_json(by_alias=True),
        structured_output=value.model_dump(mode="json", by_alias=True),
        metrics=sample_inference_metrics(),
    )


def _source_metadata(prompt: str) -> dict[str, object]:
    prefix = "Required source metadata: "
    line = next(line for line in prompt.splitlines() if line.startswith(prefix))
    value = json.loads(line.removeprefix(prefix))
    if not isinstance(value, dict):
        raise ValueError("recorded vision request did not contain source metadata")
    return value


def _embedding(text: str) -> tuple[float, ...]:
    normalized = text.casefold()
    return (
        float("corrosion" in normalized or "flange" in normalized),
        float("oil" in normalized or "seepage" in normalized or "shaft seal" in normalized),
        float(
            "coupling" in normalized
            or "guard" in normalized
            or "bolt" in normalized
            or "fastener" in normalized
        ),
        float("ventilation" in normalized or "belt" in normalized),
        float("template" in normalized or "list only source ids" in normalized),
        0.01,
    )
