"""Deterministic and live evaluation support for the AI layer."""

from app.ai.evaluation.corpus import load_golden_corpus
from app.ai.evaluation.evaluator import GoldenEvaluator
from app.ai.evaluation.samples import (
    sample_evidence_chunk,
    sample_finding,
    sample_grounded_draft,
)

__all__ = [
    "GoldenEvaluator",
    "load_golden_corpus",
    "sample_evidence_chunk",
    "sample_finding",
    "sample_grounded_draft",
]
