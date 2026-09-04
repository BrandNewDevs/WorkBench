"""Typed, approval-gated tool policy owned by Backend 1.

Keep this package initializer limited to data contracts so Backend 2 port imports
do not instantiate a registry or create a circular import at application startup.
"""

from app.tools.contracts import ToolName

__all__ = ["ToolName"]
