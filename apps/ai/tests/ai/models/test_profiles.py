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


def test_laptop_chat_profile_uses_1_7b_without_a_text_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat the small laptop model as intentional rather than degraded fallback use."""

    monkeypatch.setenv("WORKBENCH_AI_MODEL_PROFILE", "laptop-chat-1.7b")

    profile = load_model_profile()

    assert profile.profile_id == "laptop-chat-1.7b"
    assert profile.text_candidates == ("qwen3:1.7b",)
    assert profile.vision_candidates == ("qwen3-vl:4b", "qwen3-vl:2b")
    assert profile.embedding_candidates == ("qwen3-embedding:0.6b",)


@pytest.mark.parametrize(
    ("profile_id", "text_model", "vision_model"),
    (
        ("jetson-text-candidate", "qwen3:8b", "qwen3-vl:4b"),
        ("jetson-vision-candidate", "qwen3:4b", "qwen3-vl:8b"),
    ),
)
def test_isolated_jetson_profiles_change_one_capability_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
    profile_id: str,
    text_model: str,
    vision_model: str,
) -> None:
    """Let operators benchmark text and vision independently."""

    monkeypatch.setenv("WORKBENCH_AI_MODEL_PROFILE", profile_id)

    profile = load_model_profile()

    assert profile.text_candidates[0] == text_model
    assert profile.vision_candidates[0] == vision_model
    assert profile.embedding_candidates == ("qwen3-embedding:0.6b",)


def test_unknown_profile_is_rejected() -> None:
    """Prevent unreviewed model names from entering runtime selection."""

    with pytest.raises(ValueError, match="unknown model profile"):
        load_model_profile(ModelSettings(model_profile="unapproved"))
