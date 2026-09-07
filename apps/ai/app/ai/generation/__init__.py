"""Local text-generation workflows."""

from app.ai.generation.conversation import LocalConversationGenerator
from app.ai.generation.structured import StructuredTextGenerator

__all__ = ["LocalConversationGenerator", "StructuredTextGenerator"]
