"""Validated local-only configuration for the FastAPI application."""

from ipaddress import ip_address

from pydantic import AnyHttpUrl, Field, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_http_url_adapter = TypeAdapter(AnyHttpUrl)


class ApplicationSettings(BaseSettings):
    """Environment-backed settings constrained to the local workstation."""

    model_config = SettingsConfigDict(
        env_prefix="WORKBENCH_APP_",
        extra="ignore",
        frozen=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    cors_allowed_origins: tuple[str, ...] = ("http://127.0.0.1:5173",)
    health_check_timeout_seconds: float = Field(default=5, gt=0, le=30)
    auth_signing_secret: str | None = None
    auth_jwt_issuer: str = Field(default="workbench-local", min_length=1, max_length=200)
    auth_jwt_audience: str = Field(default="workbench-employee", min_length=1, max_length=200)
    auth_session_ttl_seconds: int = Field(default=8 * 60 * 60, ge=60, le=8 * 60 * 60)
    auth_cookie_secure: bool = False

    @field_validator("auth_signing_secret")
    @classmethod
    def require_non_default_signing_secret(cls, secret: str | None) -> str | None:
        """Require a locally provisioned secret rather than a committed default."""

        if secret is None:
            return None
        normalized = secret.strip()
        disallowed = {
            "change-me",
            "changeme",
            "development-secret",
            "default-secret",
            "workbench-secret",
        }
        if len(normalized.encode("utf-8")) < 32 or normalized.lower() in disallowed:
            raise ValueError("auth signing secret must be a non-default value of at least 32 bytes")
        return normalized

    @property
    def signing_secret(self) -> str:
        """Return the required secret, rejecting an unconfigured service before use."""

        if self.auth_signing_secret is None:
            raise ValueError("WORKBENCH_APP_AUTH_SIGNING_SECRET must be provisioned before startup")
        return self.auth_signing_secret

    @field_validator("host")
    @classmethod
    def require_loopback_host(cls, host: str) -> str:
        """Reject LAN and public bind addresses before Uvicorn starts."""

        normalized = host.strip()
        if normalized.lower() == "localhost":
            return normalized
        try:
            address = ip_address(normalized.strip("[]"))
        except ValueError as error:
            raise ValueError("application host must be localhost or a loopback IP") from error
        if not address.is_loopback:
            raise ValueError("application host must be localhost or a loopback IP")
        return normalized

    @field_validator("cors_allowed_origins")
    @classmethod
    def require_explicit_local_cors_origins(
        cls, origins: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Allow only explicit loopback browser origins; never a wildcard."""

        if not origins:
            raise ValueError("at least one explicit CORS origin is required")

        normalized_origins: list[str] = []
        for origin in origins:
            rendered = origin[:-1] if origin.endswith("/") else origin
            if "*" in rendered:
                raise ValueError("CORS origins must not contain wildcards")
            parsed = _http_url_adapter.validate_python(rendered)
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("CORS origins must not contain credentials")
            if parsed.query is not None or parsed.fragment is not None:
                raise ValueError("CORS origins must not contain a query or fragment")
            if parsed.path not in (None, "/"):
                raise ValueError("CORS origins must not contain a path")

            host = (parsed.host or "").strip("[]").lower()
            if host == "localhost":
                normalized_origins.append(rendered)
                continue
            try:
                address = ip_address(host)
            except ValueError as error:
                raise ValueError("CORS origins must use localhost or a loopback IP") from error
            if not address.is_loopback:
                raise ValueError("CORS origins must use localhost or a loopback IP")
            normalized_origins.append(rendered)
        return tuple(normalized_origins)

    @property
    def cors_origins(self) -> tuple[str, ...]:
        """Return origin values in the form expected by Starlette's CORS middleware."""

        return self.cors_allowed_origins
