"""Shared versioned rules for explicit uncertainty handling."""

UNCERTAINTY_HANDLING_PROMPT_VERSION = "uncertainty-handling-v1"

UNCERTAINTY_HANDLING_RULES = """Uncertainty rules:
- State missing, unreadable, conflicting, or unsupported information explicitly.
- Do not replace missing facts with assumptions.
- Do not claim engineering, financial, legal, or operational certainty beyond supplied evidence.
- Keep uncertainty concise and useful to the reviewing employee.
"""
