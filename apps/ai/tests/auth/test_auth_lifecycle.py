"""Focused API and service-boundary tests for employee authentication."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from pwdlib import PasswordHash
from pytest import LogCaptureFixture

from app.auth.contracts import UserRole
from app.auth.service import _DUMMY_PASSWORD_HASH, AuthService
from app.config import ApplicationSettings
from app.health import ApplicationDependencies
from app.main import create_app
from app.ports.backend2 import AuditRecord, AuthSessionRecord, StoredIdentity

ORIGIN = "http://127.0.0.1:5173"
PASSWORD = "correct horse battery staple"
TEST_SIGNING_SECRET = "test-only-signing-secret-material-at-least-forty-eight-bytes"


class FakeIdentityStore:
    """Deterministic Backend 2 identity fake; it stores only seeded hashes."""

    def __init__(self, identities: list[StoredIdentity]) -> None:
        self._by_username = {identity.username: identity for identity in identities}
        self._by_id = {identity.user_id: identity for identity in identities}
        self.fail_by_id = False

    async def get_by_username(self, username: str) -> StoredIdentity | None:
        return self._by_username.get(username)

    async def get_by_id(self, user_id: UUID) -> StoredIdentity | None:
        if self.fail_by_id:
            raise RuntimeError("C:/secrets/identity.db unavailable")
        return self._by_id.get(user_id)


class FakeAuthSessionStore:
    """In-memory stand-in for Backend 2's required atomic auth-session store."""

    def __init__(self) -> None:
        self.records: dict[UUID, AuthSessionRecord] = {}
        self.fail_create = False
        self.fail_get_active = False
        self.fail_revoke = False

    async def create(self, record: AuthSessionRecord) -> None:
        if self.fail_create:
            raise RuntimeError("C:/secrets/auth.db unavailable")
        self.records[record.token_id] = record

    async def get_active(self, token_id: UUID, now: datetime) -> AuthSessionRecord | None:
        if self.fail_get_active:
            raise RuntimeError("C:/secrets/auth.db unavailable")
        record = self.records.get(token_id)
        if record is None or record.revoked_at is not None or record.expires_at <= now:
            return None
        return record

    async def revoke(self, token_id: UUID, revoked_at: datetime) -> bool:
        if self.fail_revoke:
            raise RuntimeError("C:/secrets/auth.db unavailable")
        record = self.records.get(token_id)
        if record is None or record.revoked_at is not None or record.expires_at <= revoked_at:
            return False
        self.records[token_id] = record.model_copy(update={"revoked_at": revoked_at})
        return True


class FakeAuditStore:
    """Capture only the typed sanitized facts that Backend 1 may audit."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    async def append(self, record: AuditRecord) -> None:
        self.records.append(record)


def _identity(
    *,
    username: str = "engineer.one",
    role: UserRole = UserRole.EMPLOYEE,
    disabled: bool = False,
) -> StoredIdentity:
    return StoredIdentity(
        user_id=uuid4(),
        username=username,
        display_name="Engineer One",
        role=role,
        password_hash=PasswordHash.recommended().hash(PASSWORD),
        disabled=disabled,
    )


def _client(
    identities: list[StoredIdentity] | None = None,
) -> tuple[TestClient, FakeAuthSessionStore, FakeAuditStore]:
    sessions = FakeAuthSessionStore()
    audits = FakeAuditStore()
    dependencies = ApplicationDependencies(
        identity_store=FakeIdentityStore(identities or [_identity()]),
        auth_session_store=sessions,
        audit_store=audits,
    )
    settings = ApplicationSettings(
        auth_signing_secret=TEST_SIGNING_SECRET,
        cors_allowed_origins=(ORIGIN,),
    )
    return TestClient(create_app(settings=settings, dependencies=dependencies)), sessions, audits


def _login(client: TestClient, *, password: str = PASSWORD) -> Response:
    return cast(
        Response,
        client.post(
            "/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": " Engineer.One ", "password": password},
        ),
    )


def _replace_session_cookie(client: TestClient, token: str) -> None:
    """Ensure a test request presents exactly one chosen cookie token."""

    client.cookies.clear()
    client.cookies.set("workbench_session", token)


def _session_token(client: TestClient) -> str:
    token = client.cookies.get("workbench_session")
    assert isinstance(token, str)
    return token


def _auth_service(client: TestClient) -> AuthService:
    """Return the concrete app state service despite TestClient's generic ASGI typing."""

    application = cast(FastAPI, client.app)
    return cast(AuthService, application.state.auth_service)


def _signed_payload(client: TestClient, token: str, **updates: object) -> str:
    """Create a deliberately modified but correctly signed token for verification tests."""

    service = _auth_service(client)
    payload = jwt.decode(token, options={"verify_signature": False})
    payload.update(updates)
    return jwt.encode(payload, service._settings.signing_secret, algorithm="HS256")


def test_login_matches_desktop_envelope_and_sets_only_an_httponly_cookie() -> None:
    client, sessions, audits = _client()
    with client:
        response = _login(client)

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"session"}
    assert set(payload["session"]) == {"sessionId", "user", "expiresAt"}
    assert payload["session"]["user"] == {
        "employeeId": payload["session"]["user"]["employeeId"],
        "username": "engineer.one",
        "displayName": "Engineer One",
        "role": "employee",
    }
    assert "token" not in response.text.lower()
    cookie = response.headers["set-cookie"].lower()
    assert "workbench_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    assert "max-age=28800" in cookie
    assert "domain=" not in cookie
    assert len(sessions.records) == 1
    assert not hasattr(next(iter(sessions.records.values())), "token")
    assert audits.records[-1].outcome == "loginSucceeded"
    assert PASSWORD not in str(audits.records)


def test_full_login_restore_logout_and_rejected_restore_lifecycle() -> None:
    client, sessions, audits = _client()
    with client:
        login = _login(client)
        restored = client.get("/auth/session", headers={"Origin": ORIGIN})
        logout = client.post("/auth/logout", headers={"Origin": ORIGIN})
        rejected_restore = client.get("/auth/session", headers={"Origin": ORIGIN})

    assert login.status_code == 200
    assert restored.status_code == 200
    assert restored.json() == login.json()
    assert "set-cookie" not in restored.headers
    assert logout.status_code == 200
    assert logout.json() == {"revoked": True}
    assert "max-age=0" in logout.headers["set-cookie"].lower()
    assert rejected_restore.status_code == 401
    assert rejected_restore.json()["code"] == "invalid_session"
    assert len(sessions.records) == 1
    assert next(iter(sessions.records.values())).revoked_at is not None
    assert audits.records[-1].outcome == "logoutRevoked"


def test_repeated_logout_with_the_exact_original_token_is_idempotent_and_clears_cookie() -> None:
    client, _, _ = _client()
    with client:
        _login(client)
        original_token = _session_token(client)
        first = client.post("/auth/logout", headers={"Origin": ORIGIN})
        _replace_session_cookie(client, original_token)
        second = client.post("/auth/logout", headers={"Origin": ORIGIN})

    assert first.json() == {"revoked": True}
    assert second.json() == {"revoked": False}
    assert "max-age=0" in second.headers["set-cookie"].lower()


def test_invalid_credentials_disabled_identity_and_operator_are_safely_rejected() -> None:
    bad_client, _, _ = _client()
    disabled_client, _, _ = _client([_identity(disabled=True)])
    operator_client, sessions, _ = _client([_identity(role=UserRole.OPERATOR)])
    with bad_client, disabled_client, operator_client:
        bad = _login(bad_client, password="wrong")
        disabled = _login(disabled_client)
        operator = _login(operator_client)

    assert bad.status_code == disabled.status_code == 401
    assert (
        bad.json()
        == disabled.json()
        == {
            "code": "invalid_credentials",
            "message": "The username or password is invalid.",
            "requestId": None,
        }
    )
    assert operator.status_code == 403
    assert operator.json()["code"] == "role_not_allowed"
    assert not sessions.records


def test_unknown_and_disabled_logins_verify_the_fixed_dummy_argon2_hash() -> None:
    """Credential failures do equivalent verifier work without exposing identity existence."""

    class RecordingPasswordHash:
        def __init__(self) -> None:
            self.hashes: list[str] = []

        def verify(self, password: str, password_hash: str) -> bool:
            del password
            self.hashes.append(password_hash)
            return False

    employee = _identity()
    disabled = _identity(username="disabled.employee", disabled=True)
    client, _, _ = _client([employee, disabled])
    verifier = RecordingPasswordHash()
    with client:
        service = _auth_service(client)
        service._password_hash = cast(PasswordHash, verifier)
        unknown = client.post(
            "/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "unknown.employee", "password": PASSWORD},
        )
        disabled_response = client.post(
            "/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "disabled.employee", "password": PASSWORD},
        )
        wrong_password = _login(client, password="wrong")

    assert unknown.json() == disabled_response.json() == wrong_password.json()
    assert verifier.hashes == [_DUMMY_PASSWORD_HASH, _DUMMY_PASSWORD_HASH, employee.password_hash]
    assert PasswordHash.recommended().verify(PASSWORD, _DUMMY_PASSWORD_HASH) is False


def test_invalid_or_tampered_session_cookies_fail_closed_and_are_deleted() -> None:
    client, _, _ = _client()
    with client:
        missing = client.get("/auth/session", headers={"Origin": ORIGIN})
        client.cookies.set("workbench_session", "not-a-jwt")
        malformed = client.get("/auth/session", headers={"Origin": ORIGIN})

    assert missing.status_code == malformed.status_code == 401
    assert missing.json()["code"] == malformed.json()["code"] == "invalid_session"
    assert "max-age=0" in missing.headers["set-cookie"].lower()
    assert "max-age=0" in malformed.headers["set-cookie"].lower()


def test_signed_and_unsigned_jwt_tampering_and_claim_failures_are_rejected() -> None:
    """Every JWT parsing failure is fail-closed, including validly signed bad claims."""

    client, _, _ = _client()
    with client:
        assert _login(client).status_code == 200
        valid_token = _session_token(client)
        service = _auth_service(client)
        unverified_payload = jwt.decode(valid_token, options={"verify_signature": False})
        missing_claims_payload = dict(unverified_payload)
        del missing_claims_payload["sid"]
        header, payload, signature = valid_token.split(".")
        altered_signature = ".".join(
            (header, payload, ("a" if signature[0] != "a" else "b") + signature[1:])
        )
        wrong_algorithm = jwt.encode(
            unverified_payload, service._settings.signing_secret, algorithm="HS384"
        )
        invalid_tokens = {
            "altered signature": altered_signature,
            "wrong algorithm": wrong_algorithm,
            "wrong issuer": _signed_payload(client, valid_token, iss="other-issuer"),
            "wrong audience": _signed_payload(client, valid_token, aud="other-audience"),
            "missing required claim": jwt.encode(
                missing_claims_payload, service._settings.signing_secret, algorithm="HS256"
            ),
            "malformed UUID": _signed_payload(client, valid_token, jti="not-a-uuid"),
            "expired token": _signed_payload(
                client, valid_token, exp=int(datetime.now(UTC).timestamp()) - 1
            ),
            "future issued-at": _signed_payload(
                client, valid_token, iat=int(datetime.now(UTC).timestamp()) + 60
            ),
        }
        for token in invalid_tokens.values():
            _replace_session_cookie(client, token)
            response = client.get("/auth/session", headers={"Origin": ORIGIN})
            assert response.status_code == 401
            assert response.json()["code"] == "invalid_session"
            assert "max-age=0" in response.headers["set-cookie"].lower()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("auth_session_id", uuid4()),
        ("user_id", uuid4()),
        ("token_id", uuid4()),
        ("expires_at", datetime.now(UTC) + timedelta(hours=9)),
    ],
)
def test_session_record_must_match_every_jwt_binding(field: str, value: object) -> None:
    """A valid signature alone cannot restore a mismatched server-side session record."""

    client, sessions, _ = _client()
    with client:
        assert _login(client).status_code == 200
        token = _session_token(client)
        claims = _auth_service(client).decode(token)
        original = sessions.records[claims.token_id]
        sessions.records[claims.token_id] = original.model_copy(update={field: value})
        response = client.get("/auth/session", headers={"Origin": ORIGIN})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_session"


def test_missing_or_revoked_session_records_fail_restoration() -> None:
    """Server-side deletion and revocation both override an otherwise valid JWT."""

    client, sessions, _ = _client()
    with client:
        assert _login(client).status_code == 200
        token = _session_token(client)
        claims = _auth_service(client).decode(token)
        del sessions.records[claims.token_id]
        missing = client.get("/auth/session", headers={"Origin": ORIGIN})
        assert _login(client).status_code == 200
        token = _session_token(client)
        claims = _auth_service(client).decode(token)
        record = sessions.records[claims.token_id]
        sessions.records[claims.token_id] = record.model_copy(
            update={"revoked_at": datetime.now(UTC)}
        )
        revoked = client.get("/auth/session", headers={"Origin": ORIGIN})

    assert missing.status_code == revoked.status_code == 401
    assert missing.json()["code"] == revoked.json()["code"] == "invalid_session"


@pytest.mark.parametrize("mutation", ["deleted", "disabled", "role_changed"])
def test_current_identity_must_remain_an_enabled_employee_during_restore(mutation: str) -> None:
    """Identity changes take effect immediately instead of trusting an old JWT role claim."""

    client, _, _ = _client()
    with client:
        assert _login(client).status_code == 200
        token = _session_token(client)
        claims = _auth_service(client).decode(token)
        identity_store = _auth_service(client)._identity_store
        assert isinstance(identity_store, FakeIdentityStore)
        identity = identity_store._by_id[claims.subject]
        if mutation == "deleted":
            del identity_store._by_id[claims.subject]
        elif mutation == "disabled":
            identity_store._by_id[claims.subject] = identity.model_copy(update={"disabled": True})
        else:
            identity_store._by_id[claims.subject] = identity.model_copy(
                update={"role": UserRole.OPERATOR}
            )
        response = client.get("/auth/session", headers={"Origin": ORIGIN})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_session"


def test_auth_store_failure_does_not_set_a_cookie_and_logout_outage_still_clears_it() -> None:
    client, sessions, _ = _client()
    sessions.fail_create = True
    with client:
        login = _login(client)
        sessions.fail_create = False
        _login(client)
        sessions.fail_revoke = True
        logout = client.post("/auth/logout", headers={"Origin": ORIGIN})

    assert login.status_code == 503
    assert login.json()["code"] == "auth_session_store_unavailable"
    assert "set-cookie" not in login.headers
    assert logout.status_code == 503
    assert logout.json()["code"] == "auth_session_store_unavailable"
    assert "max-age=0" in logout.headers["set-cookie"].lower()
    assert "secrets" not in logout.text


def test_restore_store_failures_return_503_without_clearing_an_undetermined_session() -> None:
    """An unavailable store is not misrepresented as a revoked or invalid session."""

    client, sessions, _ = _client()
    with client:
        assert _login(client).status_code == 200
        sessions.fail_get_active = True
        auth_store_outage = client.get("/auth/session", headers={"Origin": ORIGIN})
        sessions.fail_get_active = False
        identity_store = _auth_service(client)._identity_store
        assert isinstance(identity_store, FakeIdentityStore)
        identity_store.fail_by_id = True
        identity_store_outage = client.get("/auth/session", headers={"Origin": ORIGIN})

    assert auth_store_outage.status_code == identity_store_outage.status_code == 503
    assert auth_store_outage.json()["code"] == "auth_session_store_unavailable"
    assert identity_store_outage.json()["code"] == "identity_store_unavailable"
    assert "set-cookie" not in auth_store_outage.headers
    assert "set-cookie" not in identity_store_outage.headers


def test_credentialed_cors_and_origin_enforcement_allow_only_the_configured_renderer() -> None:
    client, _, _ = _client()
    with client:
        preflight = client.options(
            "/auth/login",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        rejected_preflight = client.options(
            "/auth/login",
            headers={
                "Origin": "http://127.0.0.1:9999",
                "Access-Control-Request-Method": "POST",
            },
        )
        rejected_request = client.post(
            "/auth/login",
            headers={"Origin": "null"},
            json={"username": "engineer.one", "password": PASSWORD},
        )

    assert preflight.headers["access-control-allow-origin"] == ORIGIN
    assert preflight.headers["access-control-allow-credentials"] == "true"
    assert rejected_preflight.status_code == 400
    assert rejected_request.status_code == 403
    assert rejected_request.json()["code"] == "invalid_origin"


def test_login_validation_rejects_unknown_fields_without_reflecting_password() -> None:
    client, _, _ = _client()
    with client:
        response = client.post(
            "/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "engineer.one", "password": PASSWORD, "unexpected": "value"},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert PASSWORD not in response.text


def test_auth_routes_document_the_sanitized_error_contract() -> None:
    """OpenAPI must not advertise FastAPI's default validation error body for auth routes."""

    client, _, _ = _client()
    with client:
        paths = client.get("/openapi.json").json()["paths"]

    operations = (("/auth/login", "post"), ("/auth/session", "get"), ("/auth/logout", "post"))
    expected_statuses = {
        ("/auth/login", "post"): {"401", "403", "422", "503"},
        ("/auth/session", "get"): {"401", "403", "503"},
        ("/auth/logout", "post"): {"403", "503"},
    }
    for path, method in operations:
        responses = paths[path][method]["responses"]
        assert set(responses).issuperset({"200", *expected_statuses[(path, method)]})
        for status in expected_statuses[(path, method)]:
            schema = responses[status]["content"]["application/json"]["schema"]
            assert schema["$ref"].endswith("/ErrorResponse")


def test_authentication_does_not_emit_passwords_or_jwts_to_captured_logs(
    caplog: LogCaptureFixture,
) -> None:
    """Authentication has no log path that can disclose credentials or a cookie token."""

    client, _, _ = _client()
    with client, caplog.at_level("DEBUG"):
        assert _login(client).status_code == 200
        token = _session_token(client)
        assert client.get("/auth/session", headers={"Origin": ORIGIN}).status_code == 200

    rendered_logs = caplog.text
    assert PASSWORD not in rendered_logs
    assert token not in rendered_logs


def test_startup_rejects_missing_or_short_jwt_signing_secrets() -> None:
    """A committed/default secret cannot silently enable cookie authentication."""

    missing = ApplicationSettings(auth_signing_secret=None)
    with pytest.raises(ValueError, match="AUTH_SIGNING_SECRET"):
        create_app(settings=missing)
    with pytest.raises(ValueError, match="at least 32 bytes"):
        ApplicationSettings(auth_signing_secret="too-short")
