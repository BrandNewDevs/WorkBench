"""Strict cookie JWT authentication for the local employee API."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID, uuid4

import jwt
from fastapi import Depends, Request
from pwdlib import PasswordHash
from pydantic import Field, ValidationError

from app.api.contracts import ApiContractModel
from app.auth.contracts import AuthenticatedUser, UserRole
from app.config import ApplicationSettings
from app.ports.backend2 import (
    AuditAction,
    AuditRecord,
    AuditStore,
    AuthSessionRecord,
    AuthSessionStore,
    IdentityStore,
    StoredIdentity,
)
from app.workflow.contracts import UtcTimestamp

COOKIE_NAME = "workbench_session"
# Fixed valid Argon2id hash of a non-secret dummy value. It deliberately receives every
# unknown or disabled login password so those failures perform the same costly operation.
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$bjaX8MuFp7/cKq7ruihvKQ$Tcl6L3Vw4LoOo7ZQcv2RSnnoQXg8rPFc14KzcBx4OIQ"
)


class AuthError(Exception):
    """A sanitized authentication failure with cookie-clear semantics."""

    def __init__(self, *, status_code: int, code: str, message: str, clear_cookie: bool) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.clear_cookie = clear_cookie


class JwtClaims(ApiContractModel):
    """Required, verified JWT claims before any identity or revoke operation."""

    subject: UUID = Field(alias="sub")
    token_id: UUID = Field(alias="jti")
    auth_session_id: UUID = Field(alias="sid")
    role: UserRole
    issued_at: UtcTimestamp = Field(alias="iat")
    expires_at: UtcTimestamp = Field(alias="exp")
    issuer: str = Field(alias="iss")
    audience: str = Field(alias="aud")


class IssuedAuthSession(ApiContractModel):
    """The server-only token plus public session facts from a successful login."""

    token: str = Field(min_length=1)
    auth_session_id: UUID
    identity: StoredIdentity
    expires_at: UtcTimestamp


class AuthService:
    """Authenticate, restore, and revoke employee cookie sessions through Backend 2 ports."""

    def __init__(
        self,
        *,
        settings: ApplicationSettings,
        identity_store: IdentityStore | None,
        auth_session_store: AuthSessionStore | None,
        audit_store: AuditStore | None,
    ) -> None:
        self._settings = settings
        self._identity_store = identity_store
        self._auth_session_store = auth_session_store
        self._audit_store = audit_store
        self._password_hash = PasswordHash.recommended()

    @property
    def cookie_name(self) -> str:
        """Return the only cookie name accepted for employee authentication."""

        return COOKIE_NAME

    @property
    def cookie_secure(self) -> bool:
        """Expose the configured loopback HTTP/HTTPS cookie policy."""

        return self._settings.auth_cookie_secure

    def cookie_max_age(self) -> int:
        """Use an eight-hour, non-rolling browser lifetime matching the JWT expiry."""

        return self._settings.auth_session_ttl_seconds

    def encode(self, claims: JwtClaims) -> str:
        """Sign a compact HS256 token with all required session claims."""

        payload = claims.model_dump(by_alias=True)
        for claim_name in ("sub", "jti", "sid"):
            payload[claim_name] = str(payload[claim_name])
        payload["role"] = str(payload["role"])
        return jwt.encode(
            payload,
            self._settings.signing_secret,
            algorithm="HS256",
        )

    def decode(self, token: str) -> JwtClaims:
        """Verify a cookie JWT before trusting its session identifiers."""

        try:
            payload = jwt.decode(
                token,
                self._settings.signing_secret,
                algorithms=["HS256"],
                issuer=self._settings.auth_jwt_issuer,
                audience=self._settings.auth_jwt_audience,
                options={"require": ["sub", "jti", "sid", "role", "iat", "exp", "iss", "aud"]},
            )
            return JwtClaims.model_validate(payload)
        except (jwt.PyJWTError, ValidationError, ValueError, TypeError) as error:
            raise AuthError(
                status_code=401,
                code="invalid_session",
                message="The employee session is invalid.",
                clear_cookie=True,
            ) from error

    async def login(self, *, username: str, password: str) -> IssuedAuthSession:
        """Verify an employee password and persist session metadata before issuing a cookie."""

        identity = await self._identity_by_username(username)
        try:
            password_hash = (
                identity.password_hash
                if identity is not None and not identity.disabled
                else _DUMMY_PASSWORD_HASH
            )
            valid_password = self._password_hash.verify(password, password_hash)
        except Exception:
            valid_password = False
        if identity is None or identity.disabled or not valid_password:
            await self._audit(action=AuditAction.AUTHENTICATION, outcome="loginRejected")
            raise self._invalid_credentials()
        if identity.role is not UserRole.EMPLOYEE:
            await self._audit(
                action=AuditAction.AUTHENTICATION,
                actor_user_id=identity.user_id,
                outcome="roleRejected",
            )
            raise AuthError(
                status_code=403,
                code="role_not_allowed",
                message="The identity is not allowed to use the employee application.",
                clear_cookie=False,
            )

        now = datetime.now(UTC).replace(microsecond=0)
        expires_at = now + timedelta(seconds=self._settings.auth_session_ttl_seconds)
        token_id = uuid4()
        auth_session_id = uuid4()
        claims = JwtClaims(
            sub=identity.user_id,
            jti=token_id,
            sid=auth_session_id,
            role=identity.role,
            iat=now,
            exp=expires_at,
            iss=self._settings.auth_jwt_issuer,
            aud=self._settings.auth_jwt_audience,
        )
        record = AuthSessionRecord(
            auth_session_id=auth_session_id,
            user_id=identity.user_id,
            token_id=token_id,
            expires_at=expires_at,
        )
        store = self._require_auth_session_store()
        try:
            await store.create(record)
        except AuthError:
            raise
        except Exception as error:
            raise self._store_error("auth_session_store_unavailable") from error
        await self._audit(
            action=AuditAction.AUTHENTICATION,
            actor_user_id=identity.user_id,
            outcome="loginSucceeded",
        )
        return IssuedAuthSession(
            token=self.encode(claims),
            auth_session_id=auth_session_id,
            identity=identity,
            expires_at=expires_at,
        )

    async def authenticated_user(self, token: str | None) -> AuthenticatedUser:
        """Restore an employee only when JWT, session record, and identity all agree."""

        if token is None:
            raise AuthError(
                status_code=401,
                code="invalid_session",
                message="The employee session is invalid.",
                clear_cookie=True,
            )
        claims = self.decode(token)
        now = datetime.now(UTC)
        store = self._require_auth_session_store()
        try:
            record = await store.get_active(claims.token_id, now)
        except Exception as error:
            raise self._store_error("auth_session_store_unavailable") from error
        if (
            record is None
            or record.auth_session_id != claims.auth_session_id
            or record.user_id != claims.subject
            or record.token_id != claims.token_id
            or record.expires_at != claims.expires_at
        ):
            raise self._invalid_session()
        identity = await self._identity_by_id(claims.subject)
        if identity is None or identity.disabled or identity.role is not UserRole.EMPLOYEE:
            raise self._invalid_session()
        if claims.role is not UserRole.EMPLOYEE:
            raise self._invalid_session()
        return AuthenticatedUser(
            user_id=identity.user_id,
            username=identity.username,
            display_name=identity.display_name,
            role=identity.role,
            auth_session_id=record.auth_session_id,
        )

    async def logout(self, token: str | None) -> bool:
        """Strictly revoke a verified active token while keeping logout idempotent."""

        if token is None:
            return False
        try:
            claims = self.decode(token)
        except AuthError:
            return False
        try:
            store = self._require_auth_session_store()
        except AuthError as error:
            raise AuthError(
                status_code=error.status_code,
                code=error.code,
                message=error.message,
                clear_cookie=True,
            ) from error
        try:
            revoked = await store.revoke(claims.token_id, datetime.now(UTC))
        except Exception as error:
            raise AuthError(
                status_code=503,
                code="auth_session_store_unavailable",
                message="The local authentication service is unavailable.",
                clear_cookie=True,
            ) from error
        await self._audit(
            action=AuditAction.AUTHENTICATION,
            actor_user_id=claims.subject,
            outcome="logoutRevoked" if revoked else "logoutNoActiveSession",
        )
        return revoked

    async def _identity_by_username(self, username: str) -> StoredIdentity | None:
        if self._identity_store is None:
            raise self._store_error("identity_store_unavailable")
        try:
            return await self._identity_store.get_by_username(username)
        except Exception as error:
            raise self._store_error("identity_store_unavailable") from error

    async def _identity_by_id(self, user_id: UUID) -> StoredIdentity | None:
        if self._identity_store is None:
            raise self._store_error("identity_store_unavailable")
        try:
            return await self._identity_store.get_by_id(user_id)
        except Exception as error:
            raise self._store_error("identity_store_unavailable") from error

    def _require_auth_session_store(self) -> AuthSessionStore:
        if self._auth_session_store is None:
            raise self._store_error("auth_session_store_unavailable")
        return self._auth_session_store

    async def _audit(
        self,
        *,
        action: AuditAction,
        outcome: str,
        actor_user_id: UUID | None = None,
    ) -> None:
        """Best-effort audit integration containing no secrets or request content."""

        if self._audit_store is None:
            return
        try:
            await self._audit_store.append(
                AuditRecord(
                    audit_id=uuid4(),
                    action=action,
                    actor_user_id=actor_user_id,
                    outcome=outcome,
                    occurred_at=datetime.now(UTC),
                )
            )
        except Exception:
            # Authentication remains available if only the independent audit writer is down.
            return

    @staticmethod
    def _invalid_credentials() -> AuthError:
        return AuthError(
            status_code=401,
            code="invalid_credentials",
            message="The username or password is invalid.",
            clear_cookie=False,
        )

    @staticmethod
    def _invalid_session() -> AuthError:
        return AuthError(
            status_code=401,
            code="invalid_session",
            message="The employee session is invalid.",
            clear_cookie=True,
        )

    @staticmethod
    def _store_error(code: str) -> AuthError:
        return AuthError(
            status_code=503,
            code=code,
            message="The local authentication service is unavailable.",
            clear_cookie=False,
        )


async def get_authenticated_user(request: Request) -> AuthenticatedUser:
    """Inject a fully server-validated employee into future protected routes."""

    service = cast(AuthService, request.app.state.auth_service)
    return await service.authenticated_user(request.cookies.get(service.cookie_name))


AuthenticatedEmployee = Annotated[AuthenticatedUser, Depends(get_authenticated_user)]
