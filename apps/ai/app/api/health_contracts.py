"""Public contracts specific to the Phase 1 health endpoint."""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.ai.schemas import AIHealthReport
from app.api.contracts import ApiContractModel
from app.ports.backend2 import SubsystemReadiness
from app.workflow.contracts import UtcTimestamp


class HealthStatus(StrEnum):
    """The aggregate readiness state of the local service."""

    READY = "ready"
    DEGRADED = "degraded"


class HealthResponse(ApiContractModel):
    """Sanitized readiness facts for the local service and its dependencies."""

    status: HealthStatus
    service: str = Field(default="workbench-ai", min_length=1, max_length=100)
    api_version: str = Field(default="v1", min_length=1, max_length=32)
    local_only: Literal[True] = True
    external_api_count: Literal[0] = 0
    ai: AIHealthReport
    storage: SubsystemReadiness
    sandbox: SubsystemReadiness
    audit: SubsystemReadiness
    outbound_network_blocked: bool
    checked_at: UtcTimestamp
