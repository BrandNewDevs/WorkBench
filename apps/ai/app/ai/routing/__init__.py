"""Deterministic capability routing and its injectable contract."""

from app.ai.routing.deterministic import DeterministicCapabilityRouter
from app.ai.routing.ports import CapabilityRouter

__all__ = ["CapabilityRouter", "DeterministicCapabilityRouter"]
