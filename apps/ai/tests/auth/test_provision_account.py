"""Command-line behavior for initial account provisioning."""

import argparse
import asyncio
import sys
from pathlib import Path

import pytest

from app.auth.provisioning import provision_initial_employee
from app.provision_account import _arguments, _run
from app.storage.sqlite import LocalSQLiteDatabase


def test_provisioning_help_exits_successfully_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["workbench-provision-account", "--help"])

    with pytest.raises(SystemExit) as exited:
        _arguments()

    assert exited.value.code == 0


def test_provisioning_rejects_non_interactive_input(capsys: pytest.CaptureFixture[str]) -> None:
    result = asyncio.run(_run(argparse.Namespace(database_path=None, list_accounts=False)))

    assert result == 2
    assert "requires an interactive local terminal" in capsys.readouterr().err


def test_list_reports_a_missing_database(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    result = asyncio.run(
        _run(argparse.Namespace(database_path=tmp_path / "workbench.db", list_accounts=True))
    )

    assert result == 1
    assert "No WorkBench database exists" in capsys.readouterr().err


def test_list_reports_an_empty_identity_store(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    database_path = tmp_path / "workbench.db"
    asyncio.run(LocalSQLiteDatabase(database_path).initialize())

    result = asyncio.run(
        _run(argparse.Namespace(database_path=database_path, list_accounts=True))
    )

    assert result == 1
    assert "No accounts are provisioned" in capsys.readouterr().err


def test_list_prints_accounts_without_secrets(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    database_path = tmp_path / "workbench.db"
    asyncio.run(
        provision_initial_employee(
            database_path=database_path,
            username="jdoe",
            display_name="Jane Doe",
            password="correct horse battery staple",
        )
    )

    result = asyncio.run(
        _run(argparse.Namespace(database_path=database_path, list_accounts=True))
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "jdoe" in output
    assert "Jane Doe" in output
    assert "employee" in output
    assert "active" in output
    assert "correct horse battery staple" not in output
