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
    cors_allowed_origins: tuple[str, ...] = ("http://localhost:5173",)
    health_check_timeout_seconds: float = Field(default=5, gt=0, le=30)

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
