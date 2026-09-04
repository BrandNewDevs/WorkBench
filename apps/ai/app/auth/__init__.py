"""Authentication contracts, without authentication routes or token handling."""

from app.auth.contracts import AuthenticatedUser, UserRole

__all__ = ["AuthenticatedUser", "UserRole"]
