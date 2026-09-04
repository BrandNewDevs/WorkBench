"""Tests for approved, configuration-selected local model profiles."""

import pytest

from app.ai.models.profiles import ModelSettings, load_model_profile


def test_safe_profile_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the workstation-safe profile when no override is configured."""

    monkeypatch.delenv("WORKBENCH_AI_MODEL_PROFILE", raising=False)
    profile = load_model_profile(ModelSettings())

    assert profile.profile_id == "safe-8gb"
    assert profile.text_candidates == ("qwen3:4b", "qwen3:1.7b")
    assert profile.vision_candidates == ("qwen3-vl:4b", "qwen3-vl:2b")
    assert profile.embedding_candidates == ("qwen3-embedding:0.6b",)


def test_profile_selection_comes_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Select the Jetson candidate without changing adapter code."""

    monkeypatch.setenv("WORKBENCH_AI_MODEL_PROFILE", "jetson-candidate")

    profile = load_model_profile()

    assert profile.text_candidates[0] == "qwen3:8b"
    assert profile.vision_candidates[0] == "qwen3-vl:8b"
    assert profile.embedding_candidates == ("qwen3-embedding:0.6b",)


def test_unknown_profile_is_rejected() -> None:
    """Prevent unreviewed model names from entering runtime selection."""

    with pytest.raises(ValueError, match="unknown model profile"):
        load_model_profile(ModelSettings(model_profile="unapproved"))
