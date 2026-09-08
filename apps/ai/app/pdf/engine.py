"""Deep local module for approved PDF inspection and controlled output."""

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from math import ceil, floor
from pathlib import Path
from typing import Protocol, cast

import pymupdf

from app.pdf.contracts import (
    PdfAddAnnotation,
    PdfAddPages,
    PdfDocumentDraft,
    PdfEditPlan,
    PdfExtractionMethod,
    PdfOverlay,
    PdfPage,
    PdfPageSequence,
    PdfRedactBlock,
    PdfRedactRegion,
    PdfReplaceText,
    PdfSource,
    PdfTable,
    PdfTextBlock,
)
from app.pdf.renderer import LocalPdfRenderer, PdfRenderError


class PdfDocumentError(ValueError):
    """A user-safe PDF workflow failure."""


PdfWritePolicy = Callable[[PdfDocumentDraft | PdfEditPlan, Path], bool]
_HAS_DIRECTORY_FD = os.open in os.supports_dir_fd


@dataclass(frozen=True, slots=True)
class PdfWriteResult:
    """Metadata calculated from the exact validated bytes published by the engine."""

    page_count: int
    size_bytes: int
    sha256: str


class PdfDocumentEngine(Protocol):
    """Small interface for all approved-input PDF operations."""

    def inspect(self, source: PdfSource, path: Path) -> tuple[PdfPage, ...]: ...

    def render_draft(self, draft: PdfDocumentDraft, destination: Path) -> PdfWriteResult: ...

    def apply_edit(
        self,
        source: PdfSource,
        path: Path,
        pages: tuple[PdfPage, ...],
        plan: PdfEditPlan,
        destination: Path,
    ) -> PdfWriteResult: ...


class LocalPdfDocumentEngine:
    """Keep PDF parsing, safe layout IDs, edits, rendering, and validation local."""

    def __init__(
        self,
        *,
        max_pages: int = 200,
        max_bytes: int = 50 * 1024 * 1024,
        write_policy: PdfWritePolicy | None = None,
    ) -> None:
        self._max_pages = max_pages
        self._max_bytes = max_bytes
        self._renderer = LocalPdfRenderer()
        self._write_policy = write_policy

    def _require_write_approval(
        self, plan: PdfDocumentDraft | PdfEditPlan, destination: Path
    ) -> None:
        if self._write_policy is None or not self._write_policy(plan, destination):
            raise PdfDocumentError("PDF creation requires an exact approved execution claim")

    def inspect(self, source: PdfSource, path: Path) -> tuple[PdfPage, ...]:
        self._require_safe_source(source, path)
        try:
            with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
                if document.needs_pass:
                    raise PdfDocumentError("encrypted PDFs are not supported")
                if document.page_count != source.page_count:
                    raise PdfDocumentError("PDF page count changed after upload")
                if document.page_count > self._max_pages:
                    raise PdfDocumentError("PDF exceeds the configured page limit")
                pages: list[PdfPage] = []
                for index in range(document.page_count):
                    page = document.load_page(index)
                    if (
                        page.rect.get_area() > 4_000_000
                        or max(page.rect.width, page.rect.height) > 4096
                    ):
                        raise PdfDocumentError(
                            "PDF page dimensions exceed the safe rendering limit"
                        )
                    blocks: list[PdfTextBlock] = []
                    raw = page.get_text(
                        "dict",
                        sort=True,
                        flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES,
                    )
                    for block_index, block in enumerate(raw.get("blocks", ())):
                        if block.get("type") != 0:
                            continue
                        text = "\n".join(
                            "".join(span.get("text", "") for span in line.get("spans", ()))
                            for line in block.get("lines", ())
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
                    image_area = 0.0
                    for info in page.get_image_info():
                        image_area += float(pymupdf.Rect(info["bbox"]).get_area())  # type: ignore[no-untyped-call]
                    needs_vision = not blocks or (
                        image_area > page.rect.get_area() * 0.6
                        and sum(len(b.text) for b in blocks) < 200
                    )
                    if blocks and not needs_vision:
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
                                extraction_method=PdfExtractionMethod.MIXED
                                if blocks
                                else PdfExtractionMethod.VISUAL,
                                text_blocks=tuple(blocks),
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

    def render_draft(self, draft: PdfDocumentDraft, destination: Path) -> PdfWriteResult:
        self._require_write_approval(draft, destination)
        try:
            output = self._renderer.render_draft_bytes(draft)
            page_count = self._validate_output(output)
            self._publish_new_file(destination, output)
            return PdfWriteResult(
                page_count=page_count,
                size_bytes=len(output),
                sha256=sha256(output).hexdigest(),
            )
        except PdfRenderError as error:
            raise PdfDocumentError(str(error)) from error

    def apply_edit(
        self,
        source: PdfSource,
        path: Path,
        pages: tuple[PdfPage, ...],
        plan: PdfEditPlan,
        destination: Path,
    ) -> PdfWriteResult:
        self._require_write_approval(plan, destination)
        if path.resolve() == destination.resolve():
            raise PdfDocumentError("the uploaded source cannot be overwritten")
        self._require_safe_source(source, path)
        if plan.source_id != source.source_id:
            raise PdfDocumentError("edit plan does not match the uploaded PDF")
        # Resolve geometry again from the hash-verified original, never from caller metadata.
        block_map = {
            block.block_id: block
            for page in self.inspect(source, path)
            for block in page.text_blocks
        }
        try:
            with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
                if document.needs_pass:
                    raise PdfDocumentError("encrypted PDFs are not supported")
                if document.get_sigflags() > 0:
                    raise PdfDocumentError("digitally signed PDFs cannot be edited")
                for operation in plan.operations:
                    if isinstance(operation, PdfPageSequence):
                        if any(page < 1 or page > document.page_count for page in operation.pages):
                            raise PdfDocumentError("Selected page does not exist")
                        document.select([page - 1 for page in operation.pages])
                    elif isinstance(operation, PdfAddPages):
                        added_pages = self._renderer.render_draft_bytes(operation.draft)
                        with pymupdf.open(  # type: ignore[no-untyped-call]
                            stream=added_pages, filetype="pdf"
                        ) as addition:
                            document.insert_pdf(
                                addition, start_at=0 if operation.position == "before" else -1
                            )
                    elif isinstance(operation, (PdfOverlay, PdfRedactRegion)):
                        if operation.page_number > document.page_count:
                            raise PdfDocumentError("Edit page does not exist")
                        page = document[operation.page_number - 1]
                        rect = pymupdf.Rect(operation.x0, operation.y0, operation.x1, operation.y1)  # type: ignore[no-untyped-call]
                        if rect.is_empty or not page.rect.contains(rect):
                            raise PdfDocumentError("PDF edit region is outside the page")
                        if isinstance(operation, PdfOverlay):
                            if (
                                page.insert_textbox(
                                    rect, operation.text, fontsize=operation.font_size
                                )
                                < 0
                            ):
                                raise PdfDocumentError(
                                    "PDF overlay does not fit its approved region"
                                )
                        else:
                            page.add_redact_annot(rect, fill=(1, 1, 1))
                            page.apply_redactions()
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
                            fonts = {
                                "Helvetica": "helv",
                                "Helvetica-Bold": "hebo",
                                "Helvetica-Oblique": "heit",
                                "Courier": "cour",
                                "Times-Roman": "tiro",
                            }
                            font_name = fonts.get(block.font_name or "")
                            if font_name is None:
                                raise PdfDocumentError(
                                    "Original font is unavailable for safe replacement"
                                )
                            font = pymupdf.Font(font_name)  # type: ignore[no-untyped-call]
                            lines = operation.replacement.splitlines()
                            required_height = (font.ascender - font.descender) * font_size
                            required_height += max(0, len(lines) - 1) * font_size * 1.2
                            if required_height > rect.height + 0.1 or any(
                                font.text_length(line, fontsize=font_size) > rect.width  # type: ignore[no-untyped-call]
                                for line in lines
                            ):
                                raise PdfDocumentError(
                                    "replacement text does not fit the original text area"
                                )
                            page.insert_text(
                                (rect.x0, rect.y0 + font.ascender * font_size),
                                operation.replacement,
                                fontname=font_name,
                                fontsize=font_size,
                                lineheight=1.2,
                                overlay=True,
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
                output = cast(bytes, document.tobytes(garbage=4, deflate=True))
            self._validate_preserved_layout(path, output, plan, block_map)
            page_count = self._validate_output(output)
            self._publish_new_file(destination, output)
            return PdfWriteResult(
                page_count=page_count,
                size_bytes=len(output),
                sha256=sha256(output).hexdigest(),
            )
        except PdfDocumentError:
            raise
        except (
            pymupdf.FileDataError,
            pymupdf.mupdf.FzErrorBase,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
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
    def _validate_preserved_layout(
        source: Path, output: bytes, plan: PdfEditPlan, blocks: dict[str, PdfTextBlock]
    ) -> None:
        with (
            pymupdf.open(source) as original,  # type: ignore[no-untyped-call]
            pymupdf.open(  # type: ignore[no-untyped-call]
                stream=output, filetype="pdf"
            ) as edited,
        ):
            mapping = list(range(original.page_count))
            first = plan.operations[0]
            if isinstance(first, PdfPageSequence):
                mapping = [page - 1 for page in first.pages]
            offset = (
                edited.page_count - original.page_count
                if isinstance(first, PdfAddPages) and first.position == "before"
                else 0
            )
            for output_index, index in enumerate(mapping):
                before, after = original[index], edited[output_index + offset]
                areas: list[tuple[float, float, float, float]] = []
                for op in plan.operations:
                    if isinstance(op, (PdfReplaceText, PdfRedactBlock)):
                        block = blocks[op.block_id]
                        if block.page_number != index + 1:
                            continue
                        areas.append((block.x0, block.y0, block.x1, block.y1))
                        region = pymupdf.Rect(block.x0, block.y0, block.x1, block.y1)  # type: ignore[no-untyped-call]
                        extracted = " ".join(after.get_textbox(region).split())
                        if (
                            isinstance(op, PdfReplaceText)
                            and " ".join(op.replacement.split()) not in extracted
                        ):
                            raise PdfDocumentError("PDF replacement failed text verification")
                        if isinstance(op, PdfRedactBlock) and extracted.strip():
                            raise PdfDocumentError("PDF redaction left native text in its region")
                    elif isinstance(op, PdfAddAnnotation) and op.page_number == index + 1:
                        areas.append((op.x, op.y, op.x + 22, op.y + 22))
                    elif (
                        isinstance(op, (PdfOverlay, PdfRedactRegion))
                        and op.page_number == index + 1
                    ):
                        areas.append((op.x0, op.y0, op.x1, op.y1))
                left, right = before.get_pixmap(alpha=False), after.get_pixmap(alpha=False)
                if (left.width, left.height) != (right.width, right.height):
                    raise PdfDocumentError("PDF edit changed page dimensions")
                original_bytes, edited_bytes = left.samples, right.samples
                for y in range(left.height):
                    intervals = sorted(
                        (max(0, floor(x0) - 1), min(left.width, ceil(x1) + 1))
                        for x0, y0, x1, y1 in areas
                        if floor(y0) - 1 <= y <= ceil(y1) + 1
                    )
                    start = 0
                    for x0, x1 in [*intervals, (left.width, left.width)]:
                        a, b = y * left.stride + start * left.n, y * left.stride + x0 * left.n
                        if b > a and original_bytes[a:b] != edited_bytes[a:b]:
                            raise PdfDocumentError(
                                "PDF edit changed pixels outside approved regions"
                            )
                        start = max(start, x1)

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

    def _validate_output(self, output: bytes) -> int:
        try:
            if len(output) <= 4 or len(output) > self._max_bytes:
                raise PdfDocumentError("local PDF renderer produced no output")
            with pymupdf.open(  # type: ignore[no-untyped-call]
                stream=output, filetype="pdf"
            ) as document:
                if document.needs_pass or not 1 <= document.page_count <= self._max_pages:
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
            raise
        except (
            pymupdf.FileDataError,
            pymupdf.mupdf.FzErrorBase,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            raise PdfDocumentError("local PDF output failed validation") from error

    @staticmethod
    def _publish_new_file(destination: Path, output: bytes) -> None:
        """Create a validated artifact once without following a substituted symlink."""
        directory_descriptor = -1
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            expected_directory = os.stat(destination.parent, follow_symlinks=False)
            if not stat.S_ISDIR(expected_directory.st_mode):
                raise PdfDocumentError("PDF artifact directory is not trusted")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_BINARY", 0)
            if _HAS_DIRECTORY_FD:
                directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                directory_flags |= getattr(os, "O_NOFOLLOW", 0)
                directory_descriptor = os.open(destination.parent, directory_flags)
                opened_directory = os.fstat(directory_descriptor)
                if not LocalPdfDocumentEngine._same_identity(expected_directory, opened_directory):
                    raise PdfDocumentError("PDF artifact directory changed before creation")
                descriptor = os.open(
                    destination.name,
                    flags,
                    0o600,
                    dir_fd=directory_descriptor,
                )
            else:
                opened_directory = expected_directory
                descriptor = os.open(destination, flags, 0o600)
                if not LocalPdfDocumentEngine._descriptor_matches_path(descriptor, destination):
                    os.close(descriptor)
                    descriptor = -1
                    raise PdfDocumentError("PDF artifact path changed before creation")
        except FileExistsError as error:
            if directory_descriptor >= 0:
                os.close(directory_descriptor)
            raise PdfDocumentError("PDF output must be a new file") from error
        except OSError as error:
            if directory_descriptor >= 0:
                os.close(directory_descriptor)
            raise PdfDocumentError("PDF output could not be created safely") from error
        except PdfDocumentError:
            if directory_descriptor >= 0:
                os.close(directory_descriptor)
            raise

        opened = os.fstat(descriptor)
        succeeded = False
        try:
            remaining = memoryview(output)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("artifact write made no progress")
                remaining = remaining[written:]
            os.fsync(descriptor)
            current_directory = os.stat(destination.parent, follow_symlinks=False)
            if not LocalPdfDocumentEngine._same_identity(opened_directory, current_directory):
                raise PdfDocumentError("PDF artifact directory changed during creation")
            succeeded = True
        except OSError as error:
            raise PdfDocumentError("PDF output could not be written safely") from error
        finally:
            os.close(descriptor)
            if not succeeded:
                try:
                    if directory_descriptor >= 0:
                        os.unlink(destination.name, dir_fd=directory_descriptor)
                    else:
                        current = os.lstat(destination)
                        if stat.S_ISREG(current.st_mode) and LocalPdfDocumentEngine._same_identity(
                            current, opened
                        ):
                            destination.unlink()
                except OSError:
                    pass
            if directory_descriptor >= 0:
                os.close(directory_descriptor)

    @staticmethod
    def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
        return left.st_dev == right.st_dev and left.st_ino == right.st_ino

    @staticmethod
    def _descriptor_matches_path(descriptor: int, expected: Path) -> bool:
        """Verify the final Windows handle target when relative open is unavailable."""
        if os.name != "nt":
            # Supported Unix platforms take the directory-descriptor branch.
            return False
        try:
            import ctypes
            import msvcrt
            from ctypes import wintypes

            win_dll = vars(ctypes)["WinDLL"]
            kernel32 = win_dll("kernel32", use_last_error=True)
            final_path = kernel32.GetFinalPathNameByHandleW
            final_path.argtypes = [
                wintypes.HANDLE,
                wintypes.LPWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            final_path.restype = wintypes.DWORD
            get_osfhandle = vars(msvcrt)["get_osfhandle"]
            handle = get_osfhandle(descriptor)
            buffer = ctypes.create_unicode_buffer(32_768)
            length = final_path(handle, buffer, len(buffer), 0)
            if length == 0 or length >= len(buffer):
                return False
            actual = buffer.value
            if actual.startswith("\\\\?\\UNC\\"):
                actual = "\\\\" + actual[8:]
            elif actual.startswith("\\\\?\\"):
                actual = actual[4:]
            return os.path.normcase(os.path.abspath(actual)) == os.path.normcase(
                os.path.abspath(expected)
            )
        except AttributeError, OSError, ValueError:
            return False

    @staticmethod
    def read_verified_artifact(path: Path, *, expected_sha256: str, expected_size: int) -> bytes:
        """Read one registered artifact without following a substituted final path."""
        directory_descriptor = -1
        descriptor = -1
        try:
            expected_directory = os.stat(path.parent, follow_symlinks=False)
            if not stat.S_ISDIR(expected_directory.st_mode):
                raise PdfDocumentError("PDF artifact directory is not trusted")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_BINARY", 0)
            if _HAS_DIRECTORY_FD:
                directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                directory_flags |= getattr(os, "O_NOFOLLOW", 0)
                directory_descriptor = os.open(path.parent, directory_flags)
                opened_directory = os.fstat(directory_descriptor)
                if not LocalPdfDocumentEngine._same_identity(expected_directory, opened_directory):
                    raise PdfDocumentError("PDF artifact directory changed before reading")
                descriptor = os.open(path.name, flags, dir_fd=directory_descriptor)
            else:
                opened_directory = expected_directory
                descriptor = os.open(path, flags)
                if not LocalPdfDocumentEngine._descriptor_matches_path(descriptor, path):
                    raise PdfDocumentError("PDF artifact path changed before reading")
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != expected_size:
                raise PdfDocumentError("Artifact failed integrity verification")
            chunks: list[bytes] = []
            remaining = expected_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    raise PdfDocumentError("Artifact failed integrity verification")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise PdfDocumentError("Artifact failed integrity verification")
            current_directory = os.stat(path.parent, follow_symlinks=False)
            if not LocalPdfDocumentEngine._same_identity(opened_directory, current_directory):
                raise PdfDocumentError("PDF artifact directory changed while reading")
            content = b"".join(chunks)
            if sha256(content).hexdigest() != expected_sha256:
                raise PdfDocumentError("Artifact failed integrity verification")
            return content
        except PdfDocumentError:
            raise
        except OSError as error:
            raise PdfDocumentError("Artifact failed integrity verification") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if directory_descriptor >= 0:
                os.close(directory_descriptor)
