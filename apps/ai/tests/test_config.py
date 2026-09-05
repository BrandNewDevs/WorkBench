"""Tests for local-only runtime configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ApplicationSettings, default_state_directory


def test_default_database_path_is_outside_the_service_source_tree() -> None:
    settings = ApplicationSettings()
    service_root = Path(__file__).parents[1]

    assert settings.database_path != service_root / "data" / "workbench.db"
    assert service_root not in settings.database_path.parents


def test_blank_platform_data_home_values_use_the_documented_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.platform.system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", "   ")
    assert default_state_directory() == Path.home() / "AppData" / "Local" / "WorkBench"

    monkeypatch.setattr("app.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_DATA_HOME", "")
    assert default_state_directory() == Path.home() / ".local" / "share" / "workbench"


def test_platform_data_home_must_be_an_absolute_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_DATA_HOME", "relative-data")

    with pytest.raises(ValueError, match="absolute directory"):
        default_state_directory()


def test_database_path_rejects_empty_memory_and_directory_values(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="local SQLite file"):
        ApplicationSettings.model_validate({"database_path": ""})
    with pytest.raises(ValidationError, match="local SQLite file"):
        ApplicationSettings.model_validate({"database_path": ":memory:"})
    with pytest.raises(ValidationError, match="must name a file"):
        ApplicationSettings(database_path=tmp_path)
