"""Recorded-response tests for structured OCR and finding extraction."""

import asyncio
from collections.abc import Sequence
from io import BytesIO
from typing import cast

import pymupdf
import pytest
from PIL import Image

from app.ai.errors import InvalidStructuredOutput
from app.ai.evaluation.samples import sample_inference_metrics, sample_model_profile, sample_task
from app.ai.fakes import FakeModelAdapter
from app.ai.prompts.vision import VISION_EXTRACTION_SYSTEM_PROMPT
from app.ai.schemas import (
    Finding,
    FindingSeverity,
    SourceReference,
    TextGenerationResult,
    VisionAnalysis,
    VisionGenerationRequest,
    VisionPageResult,
    VisualAnalysisRequest,
    VisualBytesInput,
    VisualMimeType,
)
from app.ai.vision import LocalVisualNormalizer, VisionAnalyzer, VisionProcessingSettings


class ScriptedVisionAdapter(FakeModelAdapter):
    """Replay sanitized model responses while recording concurrency and requests."""

    def __init__(self, outputs: Sequence[TextGenerationResult | Exception]) -> None:
        super().__init__()
        self.outputs = list(outputs)
        self.requests: list[VisionGenerationRequest] = []
        self.active_calls = 0
        self.max_active_calls = 0

    async def generate_vision(self, request: VisionGenerationRequest) -> TextGenerationResult:
        """Return the next response without starting a model runtime."""

        self.requests.append(request)
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0)
            output = self.outputs.pop(0)
            if isinstance(output, Exception):
                raise output
            return output
        finally:
            self.active_calls -= 1


def png_bytes() -> bytes:
    """Create a native inspection-photo stand-in."""

    output = BytesIO()
    with Image.new("RGB", (640, 480), "white") as image:
        image.save(output, format="PNG")
    return output.getvalue()


def single_page_pdf_bytes() -> bytes:
    """Create one in-memory scanned-report stand-in."""

    with pymupdf.open() as document:  # type: ignore[no-untyped-call]
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), "Inspection report: coating damage noted")
        return cast(bytes, document.tobytes())


def visual_input(
    *,
    source_id: str,
    document_name: str,
    mime_type: VisualMimeType,
    content: bytes,
) -> VisualBytesInput:
    """Build one backend-supplied byte input with stable identity."""

    return VisualBytesInput(
        content=content,
        source_id=source_id,
        session_id="session-1",
        mime_type=mime_type,
        document_name=document_name,
    )


def finding(
    *,
    finding_id: str,
    title: str,
    source_id: str,
    document_name: str,
    page_number: int | None = None,
    image_id: str | None = None,
    uncertainty: str | None = None,
) -> Finding:
    """Create a recorded finding tied to the current visual source."""

    return Finding(
        finding_id=finding_id,
        title=title,
        description=title,
        severity=FindingSeverity.UNKNOWN,
        evidence=(
            SourceReference(
                source_id=source_id,
                document_name=document_name,
                page_number=page_number,
                image_id=image_id,
            ),
        ),
        uncertainty=uncertainty,
    )


def recorded_response(
    *,
    source_id: str,
    document_name: str,
    findings: tuple[Finding, ...],
    page_number: int | None = None,
    image_id: str | None = None,
    extracted_text: str = "",
    warnings: tuple[str, ...] = (),
) -> TextGenerationResult:
    """Wrap valid recorded structured output in the model-adapter contract."""

    page = VisionPageResult(
        source_id=source_id,
        page_number=page_number,
        image_id=image_id,
        extracted_text=extracted_text,
        findings=findings,
        warnings=warnings,
    )
    analysis = VisionAnalysis(
        model="recorded-model-value",
        extracted_text=extracted_text,
        pages=(page,),
        findings=findings,
        warnings=warnings,
    )
    return TextGenerationResult(
        model="qwen3-vl:4b",
        text=analysis.model_dump_json(by_alias=True),
        structured_output=analysis.model_dump(mode="json", by_alias=True),
        metrics=sample_inference_metrics(),
    )


def invalid_response() -> TextGenerationResult:
    """Return a syntactically JSON value that violates VisionAnalysis."""

    return TextGenerationResult(
        model="qwen3-vl:4b",
        text='{"unexpected":true}',
        structured_output={"unexpected": True},
        metrics=sample_inference_metrics(),
    )


async def test_golden_report_and_photo_produce_three_grounded_findings() -> None:
    """Merge recorded page results while retaining every real evidence locator."""

    report_findings = (
        finding(
            finding_id="coating-damage",
            title="Coating damage is visible",
            source_id="report-1",
            document_name="inspection.pdf",
            page_number=1,
        ),
        finding(
            finding_id="gauge-unreadable",
            title="Gauge value is unreadable",
            source_id="report-1",
            document_name="inspection.pdf",
            page_number=1,
            uncertainty="The numeric reading cannot be determined from this scan.",
        ),
    )
    photo_findings = (
        finding(
            finding_id="surface-mark",
            title="A dark surface mark is visible",
            source_id="photo-1",
            document_name="equipment.png",
            image_id="photo-1",
            uncertainty="A photograph alone cannot establish the cause or severity.",
        ),
    )
    adapter = ScriptedVisionAdapter(
        (
            recorded_response(
                source_id="report-1",
                document_name="inspection.pdf",
                page_number=1,
                findings=report_findings,
                extracted_text="Inspection report: coating damage noted",
                warnings=("Gauge digits are unreadable.",),
            ),
            recorded_response(
                source_id="photo-1",
                document_name="equipment.png",
                image_id="photo-1",
                findings=photo_findings,
                warnings=("No scale is visible in the photograph.",),
            ),
        )
    )
    analyzer = VisionAnalyzer(
        adapter,
        sample_model_profile(),
        LocalVisualNormalizer(
            VisionProcessingSettings(max_long_edge=512, max_rendered_pixels=512 * 512)
        ),
    )
    request = VisualAnalysisRequest(
        inputs=(
            visual_input(
                source_id="report-1",
                document_name="inspection.pdf",
                mime_type=VisualMimeType.PDF,
                content=single_page_pdf_bytes(),
            ),
            visual_input(
                source_id="photo-1",
                document_name="equipment.png",
                mime_type=VisualMimeType.PNG,
                content=png_bytes(),
            ),
        ),
        task=sample_task(),
    )

    result = await analyzer.analyze_visual(request)

    assert len(result.pages) == 2
    assert len(result.findings) == 3
    assert result.findings[1].uncertainty is not None
    assert result.findings[2].uncertainty is not None
    assert {
        (evidence.source_id, evidence.page_number, evidence.image_id)
        for item in result.findings
        for evidence in item.evidence
    } == {("report-1", 1, None), ("photo-1", None, "photo-1")}
    assert adapter.max_active_calls == 1
    assert [request.temperature for request in adapter.requests] == [0, 0]
    assert all(len(request.images_base64) == 1 for request in adapter.requests)
    assert all(
        request.output_schema == VisionAnalysis.model_json_schema(by_alias=True)
        for request in adapter.requests
    )


async def test_invalid_structured_output_is_retried_once() -> None:
    """Give the local model one correction attempt, then accept a valid response."""

    adapter = ScriptedVisionAdapter(
        (
            invalid_response(),
            recorded_response(
                source_id="photo-1",
                document_name="equipment.png",
                image_id="photo-1",
                findings=(),
                warnings=("Text is unreadable.",),
            ),
        )
    )
    analyzer = VisionAnalyzer(adapter, sample_model_profile(), LocalVisualNormalizer())
    request = VisualAnalysisRequest(
        inputs=(
            visual_input(
                source_id="photo-1",
                document_name="equipment.png",
                mime_type=VisualMimeType.PNG,
                content=png_bytes(),
            ),
        ),
        task=sample_task(),
    )

    result = await analyzer.analyze_visual(request)

    assert result.warnings == ("Text is unreadable.",)
    assert len(adapter.requests) == 2
    assert "previous response was invalid" in adapter.requests[1].user_prompt


async def test_second_invalid_structured_output_returns_typed_error() -> None:
    """Stop after the single allowed retry instead of looping indefinitely."""

    adapter = ScriptedVisionAdapter((invalid_response(), invalid_response()))
    analyzer = VisionAnalyzer(adapter, sample_model_profile(), LocalVisualNormalizer())
    request = VisualAnalysisRequest(
        inputs=(
            visual_input(
                source_id="photo-1",
                document_name="equipment.png",
                mime_type=VisualMimeType.PNG,
                content=png_bytes(),
            ),
        ),
        task=sample_task(),
    )

    with pytest.raises(InvalidStructuredOutput, match="after one retry"):
        await analyzer.analyze_visual(request)

    assert len(adapter.requests) == 2


async def test_model_cannot_change_application_controlled_source_metadata() -> None:
    """Retry a plausible response that cites a source not supplied by Backend 1."""

    wrong_source = recorded_response(
        source_id="invented-source",
        document_name="invented.png",
        image_id="invented-source",
        findings=(),
    )
    correct_source = recorded_response(
        source_id="photo-1",
        document_name="equipment.png",
        image_id="photo-1",
        findings=(),
    )
    adapter = ScriptedVisionAdapter((wrong_source, correct_source))
    analyzer = VisionAnalyzer(adapter, sample_model_profile(), LocalVisualNormalizer())
    request = VisualAnalysisRequest(
        inputs=(
            visual_input(
                source_id="photo-1",
                document_name="equipment.png",
                mime_type=VisualMimeType.PNG,
                content=png_bytes(),
            ),
        ),
        task=sample_task(),
    )

    result = await analyzer.analyze_visual(request)

    assert result.pages[0].source_id == "photo-1"
    assert len(adapter.requests) == 2


async def test_model_fallback_is_visible_without_exposing_document_content() -> None:
    """Carry the local runtime's safe fallback reason into the analysis warnings."""

    fallback_response = recorded_response(
        source_id="photo-1",
        document_name="equipment.png",
        image_id="photo-1",
        findings=(),
    ).model_copy(
        update={
            "model": "qwen3-vl:2b",
            "used_fallback": True,
            "fallback_reason": "Preferred model could not fit; used qwen3-vl:2b.",
        }
    )
    adapter = ScriptedVisionAdapter((fallback_response,))
    analyzer = VisionAnalyzer(adapter, sample_model_profile(), LocalVisualNormalizer())
    request = VisualAnalysisRequest(
        inputs=(
            visual_input(
                source_id="photo-1",
                document_name="equipment.png",
                mime_type=VisualMimeType.PNG,
                content=png_bytes(),
            ),
        ),
        task=sample_task(),
    )

    result = await analyzer.analyze_visual(request)

    assert result.model == "qwen3-vl:2b"
    assert result.warnings == (
        "Local model fallback: Preferred model could not fit; used qwen3-vl:2b.",
    )


def test_prompt_requires_conservative_transcription_and_interpretation() -> None:
    """Lock the safety language that prevents invented engineering certainty."""

    prompt = VISION_EXTRACTION_SYSTEM_PROMPT.lower()
    assert "only text that is legible" in prompt
    assert "unreadable values explicitly" in prompt
    assert "photograph alone cannot prove" in prompt
    assert "untrusted document content" in prompt
