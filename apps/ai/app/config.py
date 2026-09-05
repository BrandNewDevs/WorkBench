"""Validated local-only configuration for the FastAPI application."""

import os
import platform
from ipaddress import ip_address
from pathlib import Path

from pydantic import AnyHttpUrl, Field, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_http_url_adapter = TypeAdapter(AnyHttpUrl)


def _configured_data_home(variable_name: str, fallback: Path) -> Path:
    """Return a validated application-data root, treating blank values as unset."""

    configured = os.environ.get(variable_name, "").strip()
    if not configured:
        return fallback
    if "\x00" in configured:
        raise ValueError(f"{variable_name} must be a valid absolute directory path")

    try:
        path = Path(configured).expanduser()
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(f"{variable_name} must be a valid absolute directory path") from error
    if not path.is_absolute():
        raise ValueError(f"{variable_name} must be an absolute directory path")
    if path.exists() and not path.is_dir():
        raise ValueError(f"{variable_name} must name a directory")
    return path


def _state_directory(data_home: Path, application_name: str) -> Path:
    """Build the application directory and reject a conflicting existing file."""

    state_directory = data_home / application_name
    if state_directory.exists() and not state_directory.is_dir():
        raise ValueError("application state path must name a directory")
    return state_directory


def default_state_directory() -> Path:
    """Return the per-user application-state location for the current platform."""

    if platform.system() == "Windows":
        data_home = _configured_data_home(
            "LOCALAPPDATA", Path.home() / "AppData" / "Local"
        )
        return _state_directory(data_home, "WorkBench")
    if platform.system() == "Darwin":
        return _state_directory(Path.home() / "Library" / "Application Support", "WorkBench")
    data_home = _configured_data_home("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return _state_directory(data_home, "workbench")


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
    database_path: Path = Field(default_factory=lambda: default_state_directory() / "workbench.db")

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

    @field_validator("database_path", mode="before")
    @classmethod
    def require_database_file_path(cls, path: Path | str) -> Path:
        """Require a valid local SQLite file path, not an in-memory database."""

        if not isinstance(path, (str, Path)):
            raise ValueError("database path must name a local SQLite file")
        rendered = str(path).strip()
        if not rendered or rendered == ":memory:" or "\x00" in rendered:
            raise ValueError("database path must name a local SQLite file")
        try:
            expanded = Path(rendered).expanduser()
        except (OSError, RuntimeError, ValueError) as error:
            raise ValueError("database path must name a local SQLite file") from error
        if expanded.is_symlink():
            raise ValueError("database path must not be a symbolic link")
        if expanded.exists() and not expanded.is_file():
            raise ValueError("database path must name a file")
        try:
            return expanded.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as error:
            raise ValueError("database path must name a local SQLite file") from error

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
