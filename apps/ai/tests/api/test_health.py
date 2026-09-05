"""API tests for the Phase 1 local readiness endpoint."""

import asyncio
from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.evaluation.samples import sample_health_report
from app.ai.fakes import FakeAIEngine
from app.ai.schemas import AIHealthReport
from app.config import ApplicationSettings
from app.health import ApplicationDependencies
from app.main import create_app
from app.ports.backend2 import (
    AuditRecord,
    AuthSessionRecord,
    StoredIdentity,
    SubsystemReadiness,
    SystemHealthReport,
)


class FakeSystemHealthProvider:
    """Return controlled Backend 2 facts without touching local services."""

    def __init__(self, report: SystemHealthReport) -> None:
        self.report = report
        self.calls = 0

    async def health(self) -> SystemHealthReport:
        self.calls += 1
        return self.report


class FailingSystemHealthProvider:
    """Simulate a sanitized Backend 2 health-check failure."""

    async def health(self) -> SystemHealthReport:
        raise RuntimeError("C:/confidential/workspaces/secret.db is unavailable")


class SlowAIEngine(FakeAIEngine):
    """Delay health beyond the endpoint's bounded per-dependency timeout."""

    async def health(self) -> AIHealthReport:
        await asyncio.sleep(0.1)
        return await super().health()


class ReadyIdentityStore:
    """Minimal available identity port for readiness-only tests."""

    async def get_by_username(self, username: str) -> StoredIdentity | None:
        del username
        return None

    async def get_by_id(self, user_id: UUID) -> StoredIdentity | None:
        del user_id
        return None


class ReadyAuthSessionStore:
    """Minimal available auth-session port for readiness-only tests."""

    async def create(self, record: AuthSessionRecord) -> None:
        del record

    async def get_active(self, token_id: UUID, now: datetime) -> AuthSessionRecord | None:
        del token_id, now
        return None

    async def revoke(self, token_id: UUID, revoked_at: datetime) -> bool:
        del token_id, revoked_at
        return False


class ReadyAuditStore:
    """Minimal available audit port for readiness-only tests."""

    async def append(self, record: AuditRecord) -> None:
        del record


def _ready_system_report() -> SystemHealthReport:
    ready = SubsystemReadiness(ready=True, detail="Ready")
    return SystemHealthReport(
        storage=ready,
        sandbox=ready,
        audit=ready,
        outbound_network_blocked=True,
    )


def _client(dependencies: ApplicationDependencies) -> TestClient:
    return TestClient(create_app(dependencies=dependencies))


def test_app_registers_health_and_only_phase_two_auth_routes() -> None:
    """The composition root exposes the completed auth lifecycle but no workflow routes."""

    app = create_app()
    documented_paths = set(app.openapi()["paths"])

    assert documented_paths == {"/health", "/auth/login", "/auth/session", "/auth/logout"}


def test_health_returns_ready_camel_case_contract() -> None:
    """Ready local providers produce the documented success response."""

    system = FakeSystemHealthProvider(_ready_system_report())
    dependencies = ApplicationDependencies(
        ai_engine=FakeAIEngine(),
        system_health_provider=system,
        identity_store=ReadyIdentityStore(),
        auth_session_store=ReadyAuthSessionStore(),
        audit_store=ReadyAuditStore(),
    )

    with _client(dependencies) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["service"] == "workbench-ai"
    assert payload["apiVersion"] == "v1"
    assert payload["localOnly"] is True
    assert payload["externalApiCount"] == 0
    assert payload["outboundNetworkBlocked"] is True
    assert "checkedAt" in payload
    assert "api_version" not in payload
    assert payload["ai"]["runtimeReady"] is True
    assert system.calls == 1


def test_health_is_degraded_when_required_auth_dependencies_are_not_composed() -> None:
    """A healthy AI/system report cannot mask an unusable employee authentication API."""

    dependencies = ApplicationDependencies(
        ai_engine=FakeAIEngine(),
        system_health_provider=FakeSystemHealthProvider(_ready_system_report()),
    )

    with _client(dependencies) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_health_returns_degraded_when_any_required_dependency_is_unavailable() -> None:
    """A real missing component is never represented as healthy."""

    degraded_ai = sample_health_report().model_copy(update={"knowledge_ready": False})
    dependencies = ApplicationDependencies(
        ai_engine=FakeAIEngine(health_report=degraded_ai),
        system_health_provider=FakeSystemHealthProvider(_ready_system_report()),
    )

    with _client(dependencies) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["ai"]["knowledgeReady"] is False


def test_health_sanitizes_dependency_exceptions_and_timeouts() -> None:
    """Dependency failures degrade health without exposing implementation details."""

    dependencies = ApplicationDependencies(
        ai_engine=SlowAIEngine(),
        system_health_provider=FailingSystemHealthProvider(),
    )
    settings = ApplicationSettings(health_check_timeout_seconds=0.001)

    with TestClient(create_app(settings=settings, dependencies=dependencies)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    payload = response.json()
    assert payload["ai"]["runtimeReady"] is False
    assert payload["storage"]["ready"] is False
    assert "confidential" not in response.text
    assert "secret.db" not in response.text


def test_application_does_not_probe_dependencies_during_creation() -> None:
    """Startup neither calls local services nor attempts a runtime/model download."""

    system = FakeSystemHealthProvider(_ready_system_report())
    engine = FakeAIEngine()

    create_app(
        dependencies=ApplicationDependencies(
            ai_engine=engine, system_health_provider=system
        )
    )

    assert engine.calls == []
    assert system.calls == 0


def test_health_openapi_documents_ready_and_degraded_responses() -> None:
    """The generated contract advertises the readiness response at both statuses."""

    with _client(ApplicationDependencies()) as client:
        operation = client.get("/openapi.json").json()["paths"]["/health"]["get"]

    assert set(operation["responses"]) >= {"200", "503"}
    assert operation["responses"]["503"]["description"] == "Local dependencies unavailable"


def test_loopback_and_cors_settings_reject_nonlocal_or_wildcard_values() -> None:
    """The process cannot bind to a LAN host or use permissive CORS."""

    with pytest.raises(ValidationError):
        ApplicationSettings(host="0.0.0.0")
    with pytest.raises(ValidationError):
        ApplicationSettings(cors_allowed_origins=("http://example.com",))
    with pytest.raises(ValidationError):
        ApplicationSettings(cors_allowed_origins=("http://*.example.com",))


def test_cors_is_an_explicit_local_allowlist() -> None:
    """Only the configured origin receives CORS permission for the health request."""

    settings = ApplicationSettings(cors_allowed_origins=("http://localhost:5173",))

    with TestClient(create_app(settings=settings)) as client:
        allowed = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:9999",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert denied.status_code == 400
    assert denied.headers.get("access-control-allow-origin") is None


def test_lifespan_runs_injected_cleanup() -> None:
    """Composition-owned resources receive a deterministic shutdown hook."""

    closed = False

    async def close() -> None:
        nonlocal closed
        closed = True

    dependencies = ApplicationDependencies(shutdown=close)

    with _client(dependencies):
        pass

    assert closed is True
