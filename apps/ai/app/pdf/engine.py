"""Deep local module for approved PDF inspection and controlled output."""

from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Protocol, cast

import pymupdf

from app.pdf.contracts import (
    PdfAddAnnotation,
    PdfDocumentDraft,
    PdfEditPlan,
    PdfExtractionMethod,
    PdfPage,
    PdfRedactBlock,
    PdfReplaceText,
    PdfSource,
    PdfTable,
    PdfTextBlock,
)
from app.pdf.renderer import LocalPdfRenderer, PdfRenderError


class PdfDocumentError(ValueError):
    """A user-safe PDF workflow failure."""


PdfWritePolicy = Callable[[PdfDocumentDraft | PdfEditPlan, Path], bool]


class PdfDocumentEngine(Protocol):
    """Small interface for all approved-input PDF operations."""

    def inspect(self, source: PdfSource, path: Path) -> tuple[PdfPage, ...]: ...

    def render_draft(self, draft: PdfDocumentDraft, destination: Path) -> int: ...

    def apply_edit(
        self,
        source: PdfSource,
        path: Path,
        pages: tuple[PdfPage, ...],
        plan: PdfEditPlan,
        destination: Path,
    ) -> int: ...


class LocalPdfDocumentEngine:
    """Keep PDF parsing, safe layout IDs, edits, rendering, and validation local."""

    def __init__(
        self, *, max_pages: int = 200, max_bytes: int = 50 * 1024 * 1024,
        write_policy: PdfWritePolicy | None = None,
    ) -> None:
        self._max_pages = max_pages
        self._max_bytes = max_bytes
        self._renderer = LocalPdfRenderer()
        self._write_policy = write_policy

    def _require_write_approval(self, plan: PdfDocumentDraft | PdfEditPlan, destination: Path) -> None:
        if self._write_policy is None or not self._write_policy(plan, destination):
            raise PdfDocumentError("PDF creation requires an exact approved execution claim")
        if destination.exists() or destination.is_symlink():
            raise PdfDocumentError("PDF output must be a new file")

    def inspect(self, source: PdfSource, path: Path) -> tuple[PdfPage, ...]:
        self._require_safe_source(source, path)
        try:
            with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
                if document.needs_pass:
                    raise PdfDocumentError("encrypted PDFs are not supported")
                if document.page_count != source.page_count:
                    raise PdfDocumentError("PDF page count changed after upload")
                pages: list[PdfPage] = []
                for index in range(document.page_count):
                    page = document.load_page(index)
                    blocks: list[PdfTextBlock] = []
                    raw = page.get_text("dict", sort=True)
                    for block_index, block in enumerate(raw.get("blocks", ())):
                        if block.get("type") != 0:
                            continue
                        text = "".join(
                            span.get("text", "")
                            for line in block.get("lines", ())
                            for span in line.get("spans", ())
                        ).strip()
                        if not text:
                            continue
                        bbox = block.get("bbox", ())
                        if len(bbox) != 4:
                            continue
                        first_span = cast(
                            dict[str, object],
                            next(
                                (
                                    span
                                    for line in block.get("lines", ())
                                    for span in line.get("spans", ())
                                ),
                                {},
                            ),
                        )
                        block_digest = sha256(
                            f"{source.sha256}:{index + 1}:{block_index}:{text}".encode()
                        ).hexdigest()
                        blocks.append(
                            PdfTextBlock(
                                block_id=f"pdfb_{block_digest}",
                                page_number=index + 1,
                                text=text,
                                x0=float(bbox[0]),
                                y0=float(bbox[1]),
                                x1=float(bbox[2]),
                                y1=float(bbox[3]),
                                font_name=str(first_span.get("font", "")) or None,
                                font_size=(
                                    float(cast(float, first_span["size"]))
                                    if first_span.get("size")
                                    else None
                                ),
                            )
                        )
                    tables = self._extract_tables(page, index + 1)
                    if blocks:
                        pages.append(
                            PdfPage(
                                page_number=index + 1,
                                extraction_method=PdfExtractionMethod.NATIVE,
                                text_blocks=tuple(blocks),
                                tables=tables,
                            )
                        )
                    else:
                        pages.append(
                            PdfPage(
                                page_number=index + 1,
                                extraction_method=PdfExtractionMethod.VISUAL,
                                uncertain=True,
                                uncertainty_reason=(
                                    "Page has no usable native text; "
                                    "local vision analysis is required."
                                ),
                            )
                        )
                return tuple(pages)
        except PdfDocumentError:
            raise
        except (
            pymupdf.EmptyFileError,
            pymupdf.FileDataError,
            pymupdf.mupdf.FzErrorBase,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            raise PdfDocumentError("PDF could not be decoded safely") from error

    def render_draft(self, draft: PdfDocumentDraft, destination: Path) -> int:
        self._require_write_approval(draft, destination)
        try:
            self._renderer.render_draft(draft, destination)
            return self._validate_output(destination)
        except PdfRenderError as error:
            raise PdfDocumentError(str(error)) from error

    def apply_edit(
        self,
        source: PdfSource,
        path: Path,
        pages: tuple[PdfPage, ...],
        plan: PdfEditPlan,
        destination: Path,
    ) -> int:
        self._require_write_approval(plan, destination)
        if path.resolve() == destination.resolve():
            raise PdfDocumentError("the uploaded source cannot be overwritten")
        self._require_safe_source(source, path)
        if plan.source_id != source.source_id:
            raise PdfDocumentError("edit plan does not match the uploaded PDF")
        # Resolve geometry again from the hash-verified original, never from caller metadata.
        block_map = {
            block.block_id: block
            for page in self.inspect(source, path) for block in page.text_blocks
        }
        try:
            with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
                if document.needs_pass:
                    raise PdfDocumentError("encrypted PDFs are not supported")
                for operation in plan.operations:
                    if isinstance(operation, (PdfReplaceText, PdfRedactBlock)):
                        block = block_map.get(operation.block_id)
                        if block is None:
                            raise PdfDocumentError(
                                "edit plan references a non-existent native text block"
                            )
                        page = document.load_page(block.page_number - 1)
                        rect = pymupdf.Rect(  # type: ignore[no-untyped-call]
                            block.x0, block.y0, block.x1, block.y1
                        )
                        page.add_redact_annot(rect, fill=(1, 1, 1))
                        page.apply_redactions()
                        if isinstance(operation, PdfReplaceText):
                            font_size = block.font_size or 10
                            replacement_rect = pymupdf.Rect(  # type: ignore[no-untyped-call]
                                rect.x0,
                                max(0, rect.y0 - font_size * 0.2),
                                min(page.rect.width, rect.x1 + 2),
                                min(page.rect.height, rect.y1 + font_size * 0.8),
                            )
                            inserted = page.insert_textbox(
                                replacement_rect,
                                operation.replacement,
                                fontsize=font_size,
                                fontname="helv",
                                color=(0, 0, 0),
                                overlay=True,
                            )
                            if inserted < 0:
                                raise PdfDocumentError(
                                    "replacement text does not fit the original text area"
                                )
                    elif isinstance(operation, PdfAddAnnotation):
                        if operation.page_number > document.page_count:
                            raise PdfDocumentError("annotation page does not exist")
                        page = document.load_page(operation.page_number - 1)
                        if operation.x > page.rect.width or operation.y > page.rect.height:
                            raise PdfDocumentError("annotation position is outside the page")
                        page.add_text_annot(
                            pymupdf.Point(  # type: ignore[no-untyped-call]
                                operation.x, operation.y
                            ),
                            operation.text,
                            icon="Note",
                        )
                destination.parent.mkdir(parents=True, exist_ok=True)
                document.save(destination, garbage=4, deflate=True)
            return self._validate_output(destination)
        except PdfDocumentError:
            destination.unlink(missing_ok=True)
            raise
        except (
            pymupdf.FileDataError,
            pymupdf.mupdf.FzErrorBase,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            destination.unlink(missing_ok=True)
            raise PdfDocumentError("PDF edit could not be completed safely") from error

    def _require_safe_source(self, source: PdfSource, path: Path) -> None:
        try:
            if not path.is_file() or path.stat().st_size > self._max_bytes:
                raise PdfDocumentError("PDF input is unavailable or exceeds the configured limit")
            digest = sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise PdfDocumentError("PDF input could not be read") from error
        if digest != source.sha256:
            raise PdfDocumentError("PDF input no longer matches its approved upload")

    @staticmethod
    def _extract_tables(page: pymupdf.Page, page_number: int) -> tuple[PdfTable, ...]:
        try:
            tables = page.find_tables().tables  # type: ignore[no-untyped-call]
        except AttributeError, RuntimeError, ValueError:
            return ()
        extracted: list[PdfTable] = []
        for table in tables[:20]:
            rows = tuple(tuple((cell or "").strip() for cell in row) for row in table.extract())
            if rows and any(any(cell for cell in row) for row in rows):
                extracted.append(PdfTable(page_number=page_number, rows=rows))
        return tuple(extracted)

    def _validate_output(self, destination: Path) -> int:
        try:
            if not destination.is_file() or destination.stat().st_size <= 4:
                raise PdfDocumentError("local PDF renderer produced no output")
            with pymupdf.open(destination) as document:  # type: ignore[no-untyped-call]
                if document.needs_pass or document.page_count == 0:
                    raise PdfDocumentError("local PDF output failed validation")
                for page in document:
                    if page.rect.is_empty:
                        raise PdfDocumentError("local PDF output contains an empty page")
                    page.get_pixmap(
                        matrix=pymupdf.Matrix(0.5, 0.5),  # type: ignore[no-untyped-call]
                        alpha=False,
                    )
                return cast(int, document.page_count)
        except PdfDocumentError:
            destination.unlink(missing_ok=True)
            raise
        except (
            pymupdf.FileDataError,
            pymupdf.mupdf.FzErrorBase,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            destination.unlink(missing_ok=True)
            raise PdfDocumentError("local PDF output failed validation") from error
