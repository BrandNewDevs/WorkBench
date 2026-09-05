"""Opt-in end-to-end check against a preloaded local Qwen vision model."""

import os
from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.ai.evaluation.samples import sample_task
from app.ai.models import create_ollama_adapter, load_model_profile
from app.ai.schemas import (
    VisualAnalysisRequest,
    VisualBytesInput,
    VisualMimeType,
)
from app.ai.vision import LocalVisualNormalizer, VisionAnalyzer

pytestmark = [
    pytest.mark.live_ollama,
    pytest.mark.skipif(
        os.getenv("WORKBENCH_RUN_LIVE_OLLAMA") != "1",
        reason="set WORKBENCH_RUN_LIVE_OLLAMA=1 with a preloaded approved vision model",
    ),
]


async def test_preloaded_vision_model_returns_grounded_analysis() -> None:
    """Exercise normalization, Ollama vision generation, and schema validation locally."""

    output = BytesIO()
    with Image.new("RGB", (640, 240), "white") as image:
        font = ImageFont.load_default(size=48)
        ImageDraw.Draw(image).text(
            (40, 80),
            "VALVE 17 - INSPECTION",
            fill="black",
            font=font,
        )
        image.save(output, format="PNG")

    profile = load_model_profile()
    adapter = create_ollama_adapter(profile=profile)
    analyzer = VisionAnalyzer(adapter, profile, LocalVisualNormalizer())
    request = VisualAnalysisRequest(
        inputs=(
            VisualBytesInput(
                content=output.getvalue(),
                source_id="live-photo-1",
                session_id="live-session",
                mime_type=VisualMimeType.PNG,
                document_name="live-vision-check.png",
            ),
        ),
        task=sample_task(),
    )

    try:
        result = await analyzer.analyze_visual(request)
    finally:
        await adapter.close()

    assert result.pages[0].source_id == "live-photo-1"
    assert result.pages[0].image_id == "live-photo-1"
    transcription = " ".join(result.extracted_text.upper().split())
    assert "VALVE" in transcription
    assert "17" in transcription
