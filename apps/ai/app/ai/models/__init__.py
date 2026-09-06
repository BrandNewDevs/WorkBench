"""Local model runtime interfaces with lazy concrete adapter exports."""

from typing import TYPE_CHECKING, Any

from app.ai.models.ports import ModelAdapter

if TYPE_CHECKING:
    from app.ai.models.ollama import OllamaModelAdapter
    from app.ai.models.ollama_http import OllamaSettings
    from app.ai.models.profiles import ModelSettings

__all__ = [
    "ModelAdapter",
    "ModelSettings",
    "OllamaModelAdapter",
    "OllamaSettings",
    "create_ollama_adapter",
    "load_model_profile",
]


def __getattr__(name: str) -> Any:
    """Load concrete runtime code only when composition explicitly requests it."""

    if name in {"OllamaModelAdapter", "create_ollama_adapter"}:
        from app.ai.models import ollama

        return getattr(ollama, name)
    if name == "OllamaSettings":
        from app.ai.models.ollama_http import OllamaSettings

        return OllamaSettings
    if name in {"ModelSettings", "load_model_profile"}:
        from app.ai.models import profiles

        return getattr(profiles, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
