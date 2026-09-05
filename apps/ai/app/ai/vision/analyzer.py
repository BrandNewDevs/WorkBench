"""Sequential local vision orchestration with strict evidence validation."""

from base64 import b64encode

from pydantic import ValidationError

from app.ai.errors import InvalidStructuredOutput
from app.ai.models.ports import ModelAdapter
from app.ai.prompts.vision import (
    VISION_EXTRACTION_SYSTEM_PROMPT,
    build_vision_user_prompt,
)
from app.ai.schemas import (
    ModelProfile,
    SourceReference,
    VisionAnalysis,
    VisionGenerationRequest,
    VisionPageResult,
    VisualAnalysisRequest,
)
from app.ai.vision.ports import NormalizedVisualPage, VisualNormalizer


class VisionAnalyzer:
    """Extract and merge findings without owning workflow or path permissions."""

    def __init__(
        self,
        model_adapter: ModelAdapter,
        model_profile: ModelProfile,
        normalizer: VisualNormalizer,
    ) -> None:
        self._model_adapter = model_adapter
        self._model_profile = model_profile
        self._normalizer = normalizer

    async def analyze_visual(self, request: VisualAnalysisRequest) -> VisionAnalysis:
        """Process approved inputs one page at a time for bounded GPU usage."""

        pages: list[VisionPageResult] = []
        models_used: list[str] = []
        for visual_input in request.inputs:
            for normalized_page in self._normalizer.iter_pages(visual_input):
                page, model = await self._analyze_page(normalized_page, request)
                pages.append(page)
                models_used.append(model)

        if not pages:
            raise InvalidStructuredOutput("visual input produced no analyzable pages")

        warnings = [warning for page in pages for warning in page.warnings]
        if len(set(models_used)) > 1:
            warnings.append("More than one approved local vision model was used across the input.")

        return VisionAnalysis(
            model=models_used[0],
            extracted_text="\n\n".join(
                page.extracted_text for page in pages if page.extracted_text
            ),
            pages=tuple(pages),
            findings=tuple(finding for page in pages for finding in page.findings),
            warnings=tuple(warnings),
        )

    async def _analyze_page(
        self,
        page: NormalizedVisualPage,
        request: VisualAnalysisRequest,
    ) -> tuple[VisionPageResult, str]:
        last_error: InvalidStructuredOutput | ValidationError | None = None
        output_schema = VisionAnalysis.model_json_schema(by_alias=True)
        for attempt in range(2):
            generation_request = VisionGenerationRequest(
                model=self._model_profile.vision_candidates[0],
                system_prompt=VISION_EXTRACTION_SYSTEM_PROMPT,
                user_prompt=build_vision_user_prompt(
                    page,
                    request.task,
                    output_schema,
                    retry=attempt == 1,
                ),
                images_base64=(b64encode(page.image_bytes).decode("ascii"),),
                output_schema=output_schema,
                limits=self._model_profile.vision_limits,
                temperature=0,
            )
            try:
                generation = await self._model_adapter.generate_vision(generation_request)
                analysis = VisionAnalysis.model_validate(generation.structured_output)
                self._validate_page_context(analysis, page)
            except (InvalidStructuredOutput, ValidationError) as error:
                last_error = error
                continue

            result_page = analysis.pages[0]
            if generation.used_fallback and generation.fallback_reason:
                fallback_warning = f"Local model fallback: {generation.fallback_reason}"
                result_page = result_page.model_copy(
                    update={"warnings": (*result_page.warnings, fallback_warning)}
                )
            return result_page, generation.model

        raise InvalidStructuredOutput(
            "vision model returned invalid structured output after one retry"
        ) from last_error

    @staticmethod
    def _validate_page_context(
        analysis: VisionAnalysis,
        expected: NormalizedVisualPage,
    ) -> None:
        if len(analysis.pages) != 1:
            raise InvalidStructuredOutput("page response must contain exactly one page")

        page = analysis.pages[0]
        if (
            page.source_id != expected.source_id
            or page.page_number != expected.page_number
            or page.image_id != expected.image_id
        ):
            raise InvalidStructuredOutput("page response changed application source metadata")
        if analysis.extracted_text != page.extracted_text:
            raise InvalidStructuredOutput("page response returned inconsistent extracted text")
        if analysis.warnings != page.warnings:
            raise InvalidStructuredOutput("page response returned inconsistent warnings")

        expected_location = (
            expected.source_id,
            expected.page_number,
            expected.image_id,
            expected.document_name,
            None,
        )
        for finding in analysis.findings:
            for evidence in finding.evidence:
                if VisionAnalyzer._evidence_location(evidence) != expected_location:
                    raise InvalidStructuredOutput(
                        "finding evidence changed application source metadata"
                    )

    @staticmethod
    def _evidence_location(
        evidence: SourceReference,
    ) -> tuple[str, int | None, str | None, str | None, str | None]:
        return (
            evidence.source_id,
            evidence.page_number,
            evidence.image_id,
            evidence.document_name,
            evidence.section,
        )
