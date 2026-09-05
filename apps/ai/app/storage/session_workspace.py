"""Contained local filesystem workspaces for workflow sessions."""

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class WorkspacePathError(ValueError):
    """Raised when a workspace identifier or path would escape its boundary."""


class WorkspaceArea(StrEnum):
    """The only directories writable inside a session workspace."""

    UPLOADS = "uploads"
    EXTRACTED = "extracted"
    TEMP = "temp"
    ARTIFACTS = "artifacts"


@dataclass(frozen=True, slots=True)
class SessionWorkspace:
    """Resolved local paths belonging to one workflow session."""

    session_id: str
    root: Path
    uploads: Path
    extracted: Path
    temp: Path
    artifacts: Path

    def path_for(self, area: WorkspaceArea) -> Path:
        """Return the path corresponding to an allowed workspace area."""

        return {
            WorkspaceArea.UPLOADS: self.uploads,
            WorkspaceArea.EXTRACTED: self.extracted,
            WorkspaceArea.TEMP: self.temp,
            WorkspaceArea.ARTIFACTS: self.artifacts,
        }[area]


class LocalSessionWorkspaceStore:
    """Create and manage session-owned files beneath one local root directory."""

    def __init__(self, sessions_root: Path = Path("sessions")) -> None:
        self._sessions_root = sessions_root.resolve(strict=False)

    @property
    def sessions_root(self) -> Path:
        """Return the configured, resolved local sessions root."""

        return self._sessions_root

    def create_session_workspace(self, session_id: str) -> SessionWorkspace:
        """Create all directories for a session, or return them if they already exist."""

        workspace = self._workspace_for(session_id)
        self._sessions_root.mkdir(parents=True, exist_ok=True)
        self._reject_symlink(workspace.root)
        workspace.root.mkdir(exist_ok=True)

        for area in WorkspaceArea:
            area_path = workspace.path_for(area)
            self._reject_symlink(area_path)
            area_path.mkdir(exist_ok=True)

        return workspace

    def get_session_workspace(self, session_id: str) -> SessionWorkspace:
        """Return an existing complete workspace without creating missing state."""

        workspace = self._workspace_for(session_id)
        self._reject_symlink(workspace.root)
        if not workspace.root.is_dir():
            raise FileNotFoundError(f"Session workspace does not exist: {session_id}")

        for area in WorkspaceArea:
            area_path = workspace.path_for(area)
            self._reject_symlink(area_path)
            if not area_path.is_dir():
                raise FileNotFoundError(
                    f"Session workspace area does not exist: {session_id}/{area.value}"
                )

        return workspace

    def save_file(
        self,
        session_id: str,
        area: WorkspaceArea | str,
        file_name: str,
        content: bytes,
    ) -> Path:
        """Atomically save bytes under one allowed area of an existing workspace."""

        workspace = self.get_session_workspace(session_id)
        workspace_area = self._validate_area(area)
        safe_file_name = self._validate_file_name(file_name)
        area_path = workspace.path_for(workspace_area)
        unresolved_destination = area_path / safe_file_name
        self._reject_symlink(unresolved_destination)
        destination = self._contained_path(unresolved_destination)

        descriptor, temporary_name = tempfile.mkstemp(
            dir=area_path,
            prefix=f".{safe_file_name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, destination)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

        return destination

    def cleanup_session_workspace(self, session_id: str) -> bool:
        """Remove one validated session workspace and return whether it existed."""

        workspace = self._workspace_for(session_id)
        self._reject_symlink(workspace.root)
        if not workspace.root.exists():
            return False
        if not workspace.root.is_dir():
            raise WorkspacePathError(f"Session workspace is not a directory: {session_id}")

        shutil.rmtree(workspace.root)
        return True

    def _workspace_for(self, session_id: str) -> SessionWorkspace:
        safe_session_id = self._validate_session_id(session_id)
        unresolved_root = self._sessions_root / safe_session_id
        self._reject_symlink(unresolved_root)
        root = self._contained_path(unresolved_root)
        return SessionWorkspace(
            session_id=safe_session_id,
            root=root,
            uploads=root / WorkspaceArea.UPLOADS.value,
            extracted=root / WorkspaceArea.EXTRACTED.value,
            temp=root / WorkspaceArea.TEMP.value,
            artifacts=root / WorkspaceArea.ARTIFACTS.value,
        )

    def _contained_path(self, candidate: Path) -> Path:
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self._sessions_root)
        except ValueError as error:
            raise WorkspacePathError("Path escapes the configured sessions root") from error
        return resolved

    @staticmethod
    def _validate_session_id(session_id: str) -> str:
        if not _SESSION_ID_PATTERN.fullmatch(session_id):
            raise WorkspacePathError(
                "Session ID must contain only letters, numbers, underscores, or hyphens"
            )
        return session_id

    @staticmethod
    def _validate_area(area: WorkspaceArea | str) -> WorkspaceArea:
        try:
            return WorkspaceArea(area)
        except ValueError as error:
            allowed = ", ".join(item.value for item in WorkspaceArea)
            raise WorkspacePathError(f"Workspace area must be one of: {allowed}") from error

    @staticmethod
    def _validate_file_name(file_name: str) -> str:
        windows_stem = file_name.split(".", maxsplit=1)[0].upper()
        if (
            not file_name
            or file_name in {".", ".."}
            or any(character in '<>:"/\\|?*' for character in file_name)
            or any(ord(character) < 32 for character in file_name)
            or file_name.endswith((" ", "."))
            or windows_stem in _WINDOWS_RESERVED_NAMES
            or Path(file_name).is_absolute()
        ):
            raise WorkspacePathError("File name must be a single safe path component")
        return file_name

    @staticmethod
    def _reject_symlink(path: Path) -> None:
        if path.is_symlink():
            raise WorkspacePathError(f"Symbolic links are not allowed in workspaces: {path}")
