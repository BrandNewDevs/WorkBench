"""Approval-bound Python execution in a network-disabled local container."""

import asyncio
import shutil
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.config import ApplicationSettings
from app.ports.local_backend import SessionFileStore
from app.storage.sqlite import LocalSQLiteDatabase
from app.tools.contracts import (
    SandboxArguments,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    ToolName,
)
from app.workflow.contracts import ExecutionStatus


def _bounded(data: bytes, maximum: int) -> tuple[str, bool]:
    truncated = len(data) > maximum
    return data[:maximum].decode("utf-8", errors="replace"), truncated


class DockerSandboxExecutor:
    """Run only persisted, claimed Python inputs under fixed Docker policy."""

    def __init__(
        self,
        database: LocalSQLiteDatabase,
        files: SessionFileStore,
        settings: ApplicationSettings,
    ) -> None:
        self._database = database
        self._files = files
        self._settings = settings

    async def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        started = time.monotonic()
        if request.arguments.workspace_id != request.session_id:
            return self._failure("sandbox_context_mismatch", started)
        if not await self._authorized(request):
            return self._failure("sandbox_not_authorized", started)
        source = await self._files.resolve_approved_path(
            upload_id=request.arguments.source_file_id,
            session_id=request.session_id,
            owner_user_id=await self._owner(request),
        )
        if source is None:
            return self._failure("sandbox_input_unavailable", started)
        container = f"workbench-{uuid4().hex}"
        temporary = Path(tempfile.mkdtemp(prefix="workbench-sandbox-"))
        try:
            local_source = temporary / "main.py"
            await asyncio.to_thread(shutil.copyfile, source.path, local_source)
            command = self.command(container, temporary)
            try:
                completed = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    shell=False,
                    capture_output=True,
                    timeout=self._settings.sandbox_timeout_seconds,
                    check=False,
                )
            except FileNotFoundError:
                return self._failure("docker_unavailable", started)
            except subprocess.TimeoutExpired as error:
                stdout, stdout_truncated = _bounded(
                    error.stdout or b"", self._settings.sandbox_stdout_max_bytes
                )
                stderr, stderr_truncated = _bounded(
                    error.stderr or b"", self._settings.sandbox_stderr_max_bytes
                )
                return self._failure(
                    "sandbox_timeout",
                    started,
                    stdout,
                    stderr,
                    stdout_truncated,
                    stderr_truncated,
                    timed_out=True,
                )
            except OSError:
                return self._failure("sandbox_infrastructure_error", started)
            stdout, stdout_truncated = _bounded(
                completed.stdout, self._settings.sandbox_stdout_max_bytes
            )
            stderr, stderr_truncated = _bounded(
                completed.stderr, self._settings.sandbox_stderr_max_bytes
            )
            if completed.returncode == 125 and "pull access denied" in stderr.lower():
                code = "sandbox_image_unavailable"
            elif completed.returncode != 0:
                code = "program_exit_nonzero"
            else:
                return SandboxExecutionResult(
                    status=ExecutionStatus.COMPLETED,
                    exit_code=0,
                    passed=True,
                    stdout=stdout,
                    stderr=stderr,
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            return self._failure(
                code,
                started,
                stdout,
                stderr,
                stdout_truncated,
                stderr_truncated,
                exit_code=completed.returncode,
            )
        finally:
            await asyncio.to_thread(self._remove_container, container)
            await asyncio.to_thread(shutil.rmtree, temporary, True)

    def command(self, container: str, directory: Path) -> list[str]:
        """Build the fixed policy command; exposed for deterministic tests."""
        return [
            self._settings.docker_executable,
            "run",
            "--rm",
            "--name",
            container,
            "--pull",
            "never",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--pids-limit",
            str(self._settings.sandbox_pids_limit),
            "--memory",
            self._settings.sandbox_memory,
            "--cpus",
            str(self._settings.sandbox_cpus),
            "--user",
            self._settings.sandbox_user,
            "--mount",
            f"type=bind,src={directory},dst=/workspace",
            "--workdir",
            "/workspace",
            self._settings.sandbox_image,
            "python",
            "-I",
            "/workspace/main.py",
        ]

    async def _authorized(self, request: SandboxExecutionRequest) -> bool:
        async with self._database.open() as connection:
            cursor = await connection.execute(
                """SELECT normalized_arguments FROM approvals
                WHERE approval_id = ? AND session_id = ? AND workflow_run_id = ?
                  AND execution_claim_token = ? AND status = 'approved'
                  AND decision = 'approved' AND execution_status = 'queued'
                  AND tool_name = ?""",
                (
                    str(request.approval_id),
                    str(request.session_id),
                    str(request.workflow_run_id),
                    str(request.execution_claim_token),
                    ToolName.RUN_SANDBOX.value,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        try:
            return (
                SandboxArguments.model_validate_json(row["normalized_arguments"])
                == request.arguments
            )
        except ValidationError:
            return False

    async def _owner(self, request: SandboxExecutionRequest) -> UUID:
        async with self._database.open() as connection:
            row = await (
                await connection.execute(
                    "SELECT owner_user_id FROM approvals WHERE approval_id = ?",
                    (str(request.approval_id),),
                )
            ).fetchone()
        if row is None:
            raise RuntimeError("authorized sandbox approval disappeared")
        return UUID(row["owner_user_id"])

    def _remove_container(self, container: str) -> None:
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                [self._settings.docker_executable, "rm", "-f", container],
                shell=False,
                capture_output=True,
                timeout=10,
                check=False,
            )

    @staticmethod
    def _failure(
        code: str,
        started: float,
        stdout: str = "",
        stderr: str = "",
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
        *,
        timed_out: bool = False,
        exit_code: int | None = None,
    ) -> SandboxExecutionResult:
        return SandboxExecutionResult(
            status=ExecutionStatus.FAILED,
            exit_code=exit_code,
            passed=False,
            failure_code=code,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            timed_out=timed_out,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
