"""Offline tests for the fixed Docker sandbox policy."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.config import ApplicationSettings
from app.sandbox.docker import DockerSandboxExecutor, _read_bounded
from app.storage import LocalSQLiteDatabase


def _executor(tmp_path: Path) -> DockerSandboxExecutor:
    settings = ApplicationSettings(
        database_path=tmp_path / "workbench.db",
        sessions_root=tmp_path / "sessions",
        auth_signing_secret="x" * 32,
    )
    return DockerSandboxExecutor(
        LocalSQLiteDatabase(settings.database_path), Mock(), settings
    )


def test_command_has_fixed_network_resource_and_privilege_policy(tmp_path: Path) -> None:
    command = _executor(tmp_path).command("workbench-test", tmp_path / "task")

    assert command[:3] == ["docker", "run", "--rm"]
    for expected in (
        "--pull", "never", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--read-only", "--pids-limit",
        "--memory", "--cpus", "--user",
    ):
        assert expected in command
    rendered = " ".join(command)
    assert "--privileged" not in command
    assert "--publish" not in command and " -p " not in f" {rendered} "
    assert "docker.sock" not in rendered
    assert "--network host" not in rendered
    assert "dst=/workspace,readonly" in rendered
    assert command[-3:] == ["python", "-I", "/workspace/main.py"]


@pytest.mark.asyncio
async def test_stream_capture_discards_output_after_the_configured_limit() -> None:
    stream = asyncio.StreamReader()
    stream.feed_data(b"abcdefgh")
    stream.feed_eof()

    output, truncated = await _read_bounded(stream, 4)

    assert output == "abcd"
    assert truncated is True
