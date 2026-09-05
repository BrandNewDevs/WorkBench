"""Exact cookie-authenticated employee API contracts consumed by the desktop client."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.api.contracts import ApiContractModel
from app.auth.contracts import UserRole
from app.workflow.contracts import UtcTimestamp


class EmployeeLoginRequest(ApiContractModel):
    """Credentials supplied only to the explicit local login endpoint."""

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        """Trim and case-normalize usernames while preserving password contents."""

        return value.strip().casefold() if isinstance(value, str) else value


class EmployeeIdentityResponse(ApiContractModel):
    """The employee identity shape intentionally exposed to the renderer."""

    employee_id: UUID
    username: str
    display_name: str
    role: Literal[UserRole.EMPLOYEE]


class EmployeeSessionResponse(ApiContractModel):
    """The one desktop session envelope used for login and restoration."""

    session_id: UUID
    user: EmployeeIdentityResponse
    expires_at: UtcTimestamp


class EmployeeSessionEnvelope(ApiContractModel):
    """Successful login or session restoration response."""

    session: EmployeeSessionResponse


class EmployeeLogoutResponse(ApiContractModel):
    """Idempotent logout result; the cookie is always removed separately."""

    revoked: bool
