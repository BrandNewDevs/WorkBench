"""Dependency-injection boundary for deterministic capability routing."""

from typing import Protocol

from app.ai.schemas import AIHealthReport, CapabilityDecision, TaskDescriptor


class CapabilityRouter(Protocol):
    """Select a capability from task facts and current local readiness."""

    def choose(self, task: TaskDescriptor, health: AIHealthReport) -> CapabilityDecision:
        """Choose a model capability without making a permission decision."""
        ...
