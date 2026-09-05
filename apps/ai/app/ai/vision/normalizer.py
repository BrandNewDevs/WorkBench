"""Bounded local decoding for backend-approved images and scanned PDFs."""

import warnings
from collections.abc import Iterator
from io import BytesIO
from math import sqrt

import pymupdf
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.errors import (
    CorruptVisualInput,
    EncryptedVisualInput,
    UnsupportedVisualInput,
    VisualInputTooLarge,
)
from app.ai.schemas import (
    ApprovedVisualInput,
    VisualBytesInput,
    VisualInput,
    VisualMimeType,
)
from app.ai.vision.ports import NormalizedVisualPage

_MEBIBYTE = 1024 * 1024
_IMAGE_FORMATS_BY_MIME = {
    VisualMimeType.JPEG: "JPEG",
    VisualMimeType.PNG: "PNG",
    VisualMimeType.WEBP: "WEBP",
}
_PDF_ERRORS = (
    pymupdf.EmptyFileError,
    pymupdf.FileDataError,
    pymupdf.mupdf.FzErrorBase,
    RuntimeError,
    ValueError,
    OSError,
)


class VisionProcessingSettings(BaseSettings):
    """Conservative resource limits suitable for the 8 GB workstation profile."""

    model_config = SettingsConfigDict(
        env_prefix="WORKBENCH_VISION_",
        extra="ignore",
        frozen=True,
    )

    max_input_bytes: int = Field(default=25 * _MEBIBYTE, gt=0)
    max_pdf_pages: int = Field(default=20, gt=0, le=100)
    pdf_dpi: int = Field(default=144, ge=72, le=300)
    max_long_edge: int = Field(default=1_800, ge=512, le=4_096)
    max_source_pixels: int = Field(default=40_000_000, gt=0)
    max_rendered_pixels: int = Field(default=3_240_000, gt=0)
    max_normalized_bytes: int = Field(default=10 * _MEBIBYTE, gt=0)

    @model_validator(mode="after")
    def rendered_image_fits_source_limit(self) -> "VisionProcessingSettings":
        """Keep the final image bound no larger than the accepted source bound."""

        if self.max_rendered_pixels > self.max_source_pixels:
            raise ValueError("max rendered pixels cannot exceed max source pixels")
        return self


class LocalVisualNormalizer:
    """Read only an explicitly supplied input and yield sanitized page images."""

    def __init__(self, settings: VisionProcessingSettings | None = None) -> None:
        self._settings = settings or VisionProcessingSettings()

    def iter_pages(self, visual_input: VisualInput) -> Iterator[NormalizedVisualPage]:
        """Decode a supported input without discovering any neighboring files."""

        data, source_id, document_name, mime_type = self._read_input(visual_input)
        if mime_type is VisualMimeType.PDF:
            yield from self._iter_pdf_pages(data, source_id, document_name)
            return

        normalized, width, height = self._normalize_image(data, mime_type)
        yield NormalizedVisualPage(
            source_id=source_id,
            document_name=document_name,
            image_id=source_id,
            image_bytes=normalized,
            width=width,
            height=height,
        )

    def _read_input(self, visual_input: VisualInput) -> tuple[bytes, str, str, VisualMimeType]:
        if isinstance(visual_input, ApprovedVisualInput):
            approved = visual_input.approved_path
            try:
                size = approved.path.stat().st_size
                if not approved.path.is_file():
                    raise CorruptVisualInput("approved visual input is not a regular file")
                self._require_bounded_size(size)
                data = approved.path.read_bytes()
            except UnsupportedVisualInput:
                raise
            except OSError as error:
                raise CorruptVisualInput("approved visual input could not be read") from error
            source_id = approved.source_id
        elif isinstance(visual_input, VisualBytesInput):
            data = visual_input.content
            self._require_bounded_size(len(data))
            source_id = visual_input.source_id
        else:
            raise UnsupportedVisualInput("unsupported visual input contract")

        return data, source_id, visual_input.document_name, visual_input.mime_type

    def _require_bounded_size(self, size: int) -> None:
        if size <= 0:
            raise CorruptVisualInput("visual input is empty")
        if size > self._settings.max_input_bytes:
            raise VisualInputTooLarge("visual input exceeds the configured byte limit")

    def _iter_pdf_pages(
        self,
        data: bytes,
        source_id: str,
        document_name: str,
    ) -> Iterator[NormalizedVisualPage]:
        try:
            with pymupdf.open(  # type: ignore[no-untyped-call]
                stream=data, filetype="pdf"
            ) as document:
                if document.needs_pass:
                    raise EncryptedVisualInput("encrypted PDFs are not supported")
                if document.page_count == 0:
                    raise CorruptVisualInput("PDF contains no pages")
                if document.page_count > self._settings.max_pdf_pages:
                    raise VisualInputTooLarge("PDF exceeds the configured page limit")

                for page_index in range(document.page_count):
                    page = document.load_page(page_index)
                    scale = self._pdf_render_scale(page.rect.width, page.rect.height)
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(scale, scale),  # type: ignore[no-untyped-call]
                        colorspace=pymupdf.csRGB,
                        alpha=False,
                    )
                    normalized, width, height = self._normalize_image(
                        pixmap.tobytes("png"), VisualMimeType.PNG
                    )
                    yield NormalizedVisualPage(
                        source_id=source_id,
                        document_name=document_name,
                        page_number=page_index + 1,
                        image_bytes=normalized,
                        width=width,
                        height=height,
                    )
        except UnsupportedVisualInput:
            raise
        except _PDF_ERRORS as error:
            raise CorruptVisualInput("PDF could not be decoded safely") from error

    def _pdf_render_scale(self, width: float, height: float) -> float:
        if width <= 0 or height <= 0:
            raise CorruptVisualInput("PDF page has invalid dimensions")

        configured_scale = self._settings.pdf_dpi / 72
        edge_scale = self._settings.max_long_edge / max(width, height)
        pixel_scale = sqrt(self._settings.max_rendered_pixels / (width * height))
        return min(configured_scale, edge_scale, pixel_scale)

    def _normalize_image(
        self, data: bytes, expected_mime: VisualMimeType
    ) -> tuple[bytes, int, int]:
        expected_format = _IMAGE_FORMATS_BY_MIME.get(expected_mime)
        if expected_format is None:
            raise UnsupportedVisualInput(f"unsupported visual MIME type: {expected_mime.value}")

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as probe:
                    if probe.format != expected_format:
                        raise UnsupportedVisualInput(
                            "declared MIME type does not match the image content"
                        )
                    self._validate_image_shape(probe)
                    probe.verify()

                with Image.open(BytesIO(data)) as opened:
                    self._validate_image_shape(opened)
                    opened.seek(0)
                    transposed = ImageOps.exif_transpose(opened)
                    try:
                        transposed.thumbnail(
                            (self._settings.max_long_edge, self._settings.max_long_edge),
                            Image.Resampling.LANCZOS,
                        )
                        normalized = self._to_rgb(transposed)
                        try:
                            output = BytesIO()
                            normalized.save(output, format="PNG", compress_level=6)
                            encoded = output.getvalue()
                            width, height = normalized.size
                        finally:
                            normalized.close()
                    finally:
                        if transposed is not opened:
                            transposed.close()
        except UnsupportedVisualInput:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise VisualInputTooLarge("image exceeds safe decoding dimensions") from error
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
            raise CorruptVisualInput("image could not be decoded safely") from error

        if len(encoded) > self._settings.max_normalized_bytes:
            raise VisualInputTooLarge("normalized image exceeds the configured byte limit")
        return encoded, width, height

    def _validate_image_shape(self, image: Image.Image) -> None:
        width, height = image.size
        if width <= 0 or height <= 0:
            raise CorruptVisualInput("image has invalid dimensions")
        if width * height > self._settings.max_source_pixels:
            raise VisualInputTooLarge("image exceeds the configured pixel limit")
        if getattr(image, "n_frames", 1) != 1:
            raise UnsupportedVisualInput("animated or multi-frame images are not supported")

    @staticmethod
    def _to_rgb(image: Image.Image) -> Image.Image:
        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            rgba = image.convert("RGBA")
            try:
                background = Image.new("RGBA", rgba.size, "white")
                return Image.alpha_composite(background, rgba).convert("RGB")
            finally:
                rgba.close()
        return image.convert("RGB")
