"""Unit tests for the controlled local LibreOffice conversion boundary."""

import subprocess
from pathlib import Path

import pytest

from app.artifacts import LibreOfficePdfConverter, PdfConversionError


@pytest.mark.asyncio
async def test_converter_uses_argument_list_and_accepts_valid_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "draft.docx"
    source.write_bytes(b"docx")

    def run(arguments: list[str], **options: object) -> subprocess.CompletedProcess[bytes]:
        assert arguments[:4] == ["soffice", "--headless", "--convert-to", "pdf"]
        assert options["shell"] is False
        (tmp_path / "draft.pdf").write_bytes(b"%PDF-1.7\nlocal")
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    result = await LibreOfficePdfConverter().convert(source, tmp_path)
    assert result.read_bytes().startswith(b"%PDF-")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("soffice", 1)])
async def test_converter_maps_missing_executable_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    source = tmp_path / "draft.docx"
    source.write_bytes(b"docx")

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        raise failure

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(PdfConversionError, match="unavailable"):
        await LibreOfficePdfConverter(timeout_seconds=1).convert(source, tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [None, b"", b"not a pdf"])
async def test_converter_rejects_missing_empty_or_invalid_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: bytes | None
) -> None:
    source = tmp_path / "draft.docx"
    source.write_bytes(b"docx")

    def run(arguments: list[str], **options: object) -> subprocess.CompletedProcess[bytes]:
        del options
        if output is not None:
            (tmp_path / "draft.pdf").write_bytes(output)
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(PdfConversionError):
        await LibreOfficePdfConverter().convert(source, tmp_path)
    assert not (tmp_path / "draft.pdf").exists()


@pytest.mark.asyncio
async def test_converter_rejects_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "draft.docx"
    source.write_bytes(b"docx")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda arguments, **options: subprocess.CompletedProcess(arguments, 1, b"", b"error"),
    )
    with pytest.raises(PdfConversionError, match="failed"):
        await LibreOfficePdfConverter().convert(source, tmp_path)
