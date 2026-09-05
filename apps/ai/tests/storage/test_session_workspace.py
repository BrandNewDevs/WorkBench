"""Tests for contained local session workspaces."""

from pathlib import Path

import pytest

from app.storage import LocalSessionWorkspaceStore, WorkspaceArea, WorkspacePathError


def test_create_session_workspace_creates_every_allowed_area(tmp_path: Path) -> None:
    store = LocalSessionWorkspaceStore(tmp_path / "sessions")

    workspace = store.create_session_workspace("abc123")

    assert workspace.root == (tmp_path / "sessions" / "abc123").resolve()
    assert workspace.root.is_dir()
    assert workspace.uploads.is_dir()
    assert workspace.extracted.is_dir()
    assert workspace.temp.is_dir()
    assert workspace.artifacts.is_dir()


def test_create_session_workspace_is_idempotent(tmp_path: Path) -> None:
    store = LocalSessionWorkspaceStore(tmp_path / "sessions")

    first = store.create_session_workspace("abc123")
    second = store.create_session_workspace("abc123")

    assert first == second


def test_get_session_workspace_requires_an_existing_complete_workspace(tmp_path: Path) -> None:
    store = LocalSessionWorkspaceStore(tmp_path / "sessions")

    with pytest.raises(FileNotFoundError, match="does not exist"):
        store.get_session_workspace("missing")

    workspace = store.create_session_workspace("incomplete")
    workspace.temp.rmdir()

    with pytest.raises(FileNotFoundError, match="temp"):
        store.get_session_workspace("incomplete")


@pytest.mark.parametrize("area", list(WorkspaceArea))
def test_save_file_writes_bytes_only_to_the_selected_area(
    tmp_path: Path, area: WorkspaceArea
) -> None:
    store = LocalSessionWorkspaceStore(tmp_path / "sessions")
    workspace = store.create_session_workspace("abc123")

    saved_path = store.save_file("abc123", area, "inspection.bin", b"local data")

    assert saved_path == workspace.path_for(area) / "inspection.bin"
    assert saved_path.read_bytes() == b"local data"
    assert list(workspace.path_for(area).glob("*.tmp")) == []


@pytest.mark.parametrize(
    "session_id",
    ["", "../secret", "../../secret", "abc/123", "abc\\123", ".", "session id"],
)
def test_session_ids_cannot_escape_the_sessions_root(
    tmp_path: Path, session_id: str
) -> None:
    store = LocalSessionWorkspaceStore(tmp_path / "sessions")

    with pytest.raises(WorkspacePathError):
        store.create_session_workspace(session_id)


@pytest.mark.parametrize(
    "file_name",
    [
        "",
        ".",
        "..",
        "../secret.txt",
        "../../secret.txt",
        "nested/file.txt",
        "C:\\secret.txt",
        "report:stream.txt",
        "report?.txt",
        "report.txt.",
        "report.txt ",
        "NUL.txt",
        "control\x00.txt",
    ],
)
def test_file_names_cannot_escape_the_selected_area(
    tmp_path: Path, file_name: str
) -> None:
    store = LocalSessionWorkspaceStore(tmp_path / "sessions")
    store.create_session_workspace("abc123")

    with pytest.raises(WorkspacePathError):
        store.save_file("abc123", WorkspaceArea.UPLOADS, file_name, b"secret")


def test_save_file_rejects_unknown_workspace_area(tmp_path: Path) -> None:
    store = LocalSessionWorkspaceStore(tmp_path / "sessions")
    store.create_session_workspace("abc123")

    with pytest.raises(WorkspacePathError, match="Workspace area"):
        store.save_file("abc123", "private", "secret.txt", b"secret")


def test_cleanup_removes_only_the_requested_session(tmp_path: Path) -> None:
    store = LocalSessionWorkspaceStore(tmp_path / "sessions")
    first = store.create_session_workspace("first")
    second = store.create_session_workspace("second")
    store.save_file("first", WorkspaceArea.TEMP, "task.py", b"print('local')")

    assert store.cleanup_session_workspace("first") is True

    assert not first.root.exists()
    assert second.root.is_dir()
    assert store.cleanup_session_workspace("first") is False
