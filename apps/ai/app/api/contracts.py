"""Strict, endpoint-neutral JSON contracts for the local API."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiContractModel(BaseModel):
    """Base model for Backend 1 public and persistence-boundary data."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class ErrorResponse(ApiContractModel):
    """Sanitized error shape shared by future local API endpoints."""

    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
