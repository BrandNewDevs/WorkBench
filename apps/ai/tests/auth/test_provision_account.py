"""Command-line behavior for initial account provisioning."""

import argparse
import asyncio
import sys

import pytest

from app.provision_account import _arguments, _run


def test_provisioning_help_exits_successfully_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["workbench-provision-account", "--help"])

    with pytest.raises(SystemExit) as exited:
        _arguments()

    assert exited.value.code == 0


def test_provisioning_rejects_non_interactive_input(capsys: pytest.CaptureFixture[str]) -> None:
    result = asyncio.run(_run(argparse.Namespace(database_path=None)))

    assert result == 2
    assert "requires an interactive local terminal" in capsys.readouterr().err
