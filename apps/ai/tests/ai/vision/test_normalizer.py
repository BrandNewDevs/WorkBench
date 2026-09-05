"""Tests for safe image normalization and scanned-PDF rendering."""

from io import BytesIO
from pathlib import Path
from typing import cast

import pymupdf
import pytest
from PIL import Image

from app.ai.errors import (
    CorruptVisualInput,
    EncryptedVisualInput,
    UnsupportedVisualInput,
    VisualInputTooLarge,
)
from app.ai.schemas import (
    ApprovedPath,
    ApprovedVisualInput,
    VisualBytesInput,
    VisualMimeType,
)
from app.ai.vision import LocalVisualNormalizer, VisionProcessingSettings


def image_bytes(*, image_format: str = "PNG", size: tuple[int, int] = (320, 240)) -> bytes:
    """Create a sanitized in-memory image fixture."""

    output = BytesIO()
    with Image.new("RGB", size, "white") as image:
        image.save(output, format=image_format)
    return output.getvalue()


def pdf_bytes(*, pages: int = 1, encrypted: bool = False) -> bytes:
    """Create a small scanned-report stand-in entirely in memory."""

    with pymupdf.open() as document:  # type: ignore[no-untyped-call]
        for page_number in range(1, pages + 1):
            page = document.new_page(width=595, height=842)
            page.insert_text((72, 72), f"Inspection page {page_number}")
        if encrypted:
            return cast(
                bytes,
                document.tobytes(
                    encryption=pymupdf.PDF_ENCRYPT_AES_256,  # type: ignore[attr-defined]
                    owner_pw="owner-secret",
                    user_pw="user-secret",
                ),
            )
        return cast(bytes, document.tobytes())


def byte_input(data: bytes, mime_type: VisualMimeType) -> VisualBytesInput:
    """Attach the source metadata required at the AI boundary."""

    return VisualBytesInput(
        content=data,
        source_id="inspection-source",
        session_id="session-1",
        mime_type=mime_type,
        document_name="inspection-input",
    )


def test_native_image_is_normalized_to_a_bounded_png() -> None:
    """Normalize a native image and preserve its application-owned identity."""

    normalizer = LocalVisualNormalizer(
        VisionProcessingSettings(max_long_edge=512, max_rendered_pixels=512 * 512)
    )

    (page,) = tuple(
        normalizer.iter_pages(
            byte_input(image_bytes(image_format="JPEG", size=(1_200, 800)), VisualMimeType.JPEG)
        )
    )

    assert page.source_id == "inspection-source"
    assert page.image_id == "inspection-source"
    assert page.page_number is None
    assert max(page.width, page.height) <= 512
    assert page.image_bytes.startswith(b"\x89PNG")


def test_scanned_pdf_pages_are_rendered_lazily_and_in_order() -> None:
    """Render bounded page images without combining a whole PDF in memory."""

    normalizer = LocalVisualNormalizer(
        VisionProcessingSettings(max_long_edge=512, max_rendered_pixels=512 * 512)
    )

    pages = tuple(normalizer.iter_pages(byte_input(pdf_bytes(pages=2), VisualMimeType.PDF)))

    assert [page.page_number for page in pages] == [1, 2]
    assert all(page.image_id is None for page in pages)
    assert all(max(page.width, page.height) <= 512 for page in pages)
    assert all(page.width * page.height <= 512 * 512 for page in pages)


def test_exact_backend_approved_path_is_read(tmp_path: Path) -> None:
    """Consume the supplied file only through the ApprovedPath contract."""

    path = tmp_path / "approved.png"
    path.write_bytes(image_bytes())
    visual_input = ApprovedVisualInput(
        approved_path=ApprovedPath(
            path=path,
            source_id="approved-photo",
            session_id="session-1",
        ),
        mime_type=VisualMimeType.PNG,
        document_name="approved.png",
    )

    (page,) = tuple(LocalVisualNormalizer().iter_pages(visual_input))

    assert page.source_id == "approved-photo"
    assert page.document_name == "approved.png"


@pytest.mark.parametrize(
    ("data", "mime_type", "expected_error"),
    [
        (b"not-a-pdf", VisualMimeType.PDF, CorruptVisualInput),
        (image_bytes(), VisualMimeType.JPEG, UnsupportedVisualInput),
        (pdf_bytes(encrypted=True), VisualMimeType.PDF, EncryptedVisualInput),
    ],
)
def test_invalid_or_unsafe_content_returns_a_typed_error(
    data: bytes,
    mime_type: VisualMimeType,
    expected_error: type[UnsupportedVisualInput],
) -> None:
    """Distinguish content failures without leaking decoder exceptions."""

    with pytest.raises(expected_error):
        tuple(LocalVisualNormalizer().iter_pages(byte_input(data, mime_type)))


def test_configured_input_and_page_limits_are_enforced() -> None:
    """Reject work that exceeds workstation memory and latency bounds."""

    tiny_byte_limit = LocalVisualNormalizer(VisionProcessingSettings(max_input_bytes=10))
    one_page_limit = LocalVisualNormalizer(VisionProcessingSettings(max_pdf_pages=1))

    with pytest.raises(VisualInputTooLarge):
        tuple(tiny_byte_limit.iter_pages(byte_input(image_bytes(), VisualMimeType.PNG)))
    with pytest.raises(VisualInputTooLarge):
        tuple(one_page_limit.iter_pages(byte_input(pdf_bytes(pages=2), VisualMimeType.PDF)))
