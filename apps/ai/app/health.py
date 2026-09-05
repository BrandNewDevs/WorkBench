"""Dependency health aggregation for the local service."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.ai.engine import AIEngine
from app.ai.schemas import AIHealthReport, Capability, ModelStatus
from app.api.health_contracts import HealthResponse, HealthStatus
from app.ports.backend2 import (
    AuditStore,
    AuthSessionStore,
    IdentityStore,
    SubsystemReadiness,
    SystemHealthProvider,
    SystemHealthReport,
)

ShutdownCallback = Callable[[], Awaitable[None]]
StartupCallback = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    """Injected service dependencies; absent providers are explicitly unavailable."""

    ai_engine: AIEngine | None = None
    system_health_provider: SystemHealthProvider | None = None
    identity_store: IdentityStore | None = None
    auth_session_store: AuthSessionStore | None = None
    audit_store: AuditStore | None = None
    shutdown: ShutdownCallback | None = None
    startup: StartupCallback | None = None


def _unavailable_subsystem() -> SubsystemReadiness:
    """Return a safe dependency failure without exposing exception details."""

    return SubsystemReadiness(ready=False, detail="Health check unavailable")


def _unavailable_ai() -> AIHealthReport:
    """Return safe AI readiness facts when no provider can report state."""

    return AIHealthReport(
        runtime_ready=False,
        runtime_error="Health check unavailable",
        models=(),
        knowledge_ready=False,
        knowledge_error="Health check unavailable",
    )


def _unavailable_system() -> SystemHealthReport:
    """Return safe Backend 2 readiness facts when its provider is unavailable."""

    unavailable = _unavailable_subsystem()
    return SystemHealthReport(
        storage=unavailable,
        sandbox=unavailable,
        audit=unavailable,
        outbound_network_blocked=False,
    )


async def _check_ai(engine: AIEngine | None, timeout_seconds: float) -> AIHealthReport:
    """Read AI readiness with a per-dependency deadline and sanitized failures."""

    if engine is None:
        return _unavailable_ai()
    try:
        async with asyncio.timeout(timeout_seconds):
            return await engine.health()
    except Exception:
        return _unavailable_ai()


async def _check_system(
    provider: SystemHealthProvider | None, timeout_seconds: float
) -> SystemHealthReport:
    """Read Backend 2 readiness with a per-dependency deadline and sanitized failures."""

    if provider is None:
        return _unavailable_system()
    try:
        async with asyncio.timeout(timeout_seconds):
            return await provider.health()
    except Exception:
        return _unavailable_system()


def _ai_is_ready(report: AIHealthReport) -> bool:
    """Require the locally configured text, vision, and embedding capabilities."""

    required_capabilities = {Capability.TEXT, Capability.VISION, Capability.EMBEDDING}
    ready_capabilities = {
        model.capability for model in report.models if model.status is ModelStatus.READY
    }
    return (
        report.runtime_ready
        and report.knowledge_ready
        and ready_capabilities == required_capabilities
        and len(report.models) == len(required_capabilities)
    )


def _system_is_ready(report: SystemHealthReport) -> bool:
    """Require every local Backend 2 dependency and its outbound-network control."""

    return (
        report.storage.ready
        and report.sandbox.ready
        and report.audit.ready
        and report.outbound_network_blocked
    )


def _auth_dependencies_are_ready(dependencies: ApplicationDependencies) -> bool:
    """Require the stores that make the Phase 2 employee API usable at runtime."""

    return (
        dependencies.identity_store is not None
        and dependencies.auth_session_store is not None
        and dependencies.audit_store is not None
    )


async def build_health_response(
    dependencies: ApplicationDependencies, *, timeout_seconds: float
) -> HealthResponse:
    """Aggregate independent local dependency checks concurrently."""

    ai, system = await asyncio.gather(
        _check_ai(dependencies.ai_engine, timeout_seconds),
        _check_system(dependencies.system_health_provider, timeout_seconds),
    )
    is_ready = (
        _ai_is_ready(ai)
        and _system_is_ready(system)
        and _auth_dependencies_are_ready(dependencies)
    )
    return HealthResponse(
        status=HealthStatus.READY if is_ready else HealthStatus.DEGRADED,
        ai=ai,
        storage=system.storage,
        sandbox=system.sandbox,
        audit=system.audit,
        outbound_network_blocked=system.outbound_network_blocked,
        checked_at=datetime.now(UTC),
    )
