"""Identity data safe to pass from auth dependencies to workflow code."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.api.contracts import ApiContractModel


class UserRole(StrEnum):
    """The two locally seeded roles planned for the MVP."""

    EMPLOYEE = "employee"
    OPERATOR = "operator"


class AuthenticatedUser(ApiContractModel):
    """Authenticated request identity; it never contains a password or token."""

    user_id: UUID
    username: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    role: UserRole
    auth_session_id: UUID
