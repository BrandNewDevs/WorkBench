"""Small dependency seam between visual decoding and model orchestration."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from app.ai.schemas import VisualInput


@dataclass(frozen=True, slots=True)
class NormalizedVisualPage:
    """One bounded PNG plus application-controlled source metadata."""

    source_id: str
    document_name: str
    image_bytes: bytes = field(repr=False)
    width: int
    height: int
    page_number: int | None = None
    image_id: str | None = None


class VisualNormalizer(Protocol):
    """Convert one approved path or byte upload into bounded page images."""

    def iter_pages(self, visual_input: VisualInput) -> Iterator[NormalizedVisualPage]:
        """Yield normalized pages lazily so callers can process them sequentially."""
        ...
