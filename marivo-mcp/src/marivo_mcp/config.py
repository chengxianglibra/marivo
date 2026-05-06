import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class MarivoMcpConfigError(RuntimeError):
    """Raised when required MCP adapter configuration is missing or invalid."""


class TargetResolutionError(MarivoMcpConfigError):
    """Structured target-resolution error raised before MCP startup."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        detail: dict[str, Any],
        guidance: str | None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.guidance = guidance

    def __str__(self) -> str:
        if self.guidance:
            return f"{self.message} {self.guidance}"
        return self.message


class HttpTransportConfig(BaseModel):
    """Configuration for the optional Streamable HTTP transport."""

    model_config = ConfigDict(frozen=True)

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, gt=0, le=65535)
    streamable_http_path: str = Field(default="/mcp", min_length=1)
    stateless_http: bool = True
    json_response: bool = True


class MarivoMcpConfig(BaseModel):
    """Runtime configuration for the standalone Marivo MCP adapter."""

    model_config = ConfigDict(frozen=True)

    mode: Literal["auto", "remote", "local"] = "auto"
    base_url: str | None = Field(default=None, min_length=1)
    api_token: str | None = None
    workspace_root: str | None = None
    local_host: str = Field(default="127.0.0.1", min_length=1)
    local_port: int = Field(default=0, ge=0, le=65535)
    start_timeout_ms: int = Field(default=15_000, gt=0)
    healthcheck_timeout_ms: int = Field(default=2_000, gt=0)
    transport: str = Field(default="stdio", pattern="^(stdio|streamable-http)$")
    timeout_ms: int = Field(default=600_000, gt=0)
    openapi_cache_ttl_sec: int = Field(default=300, ge=0)
    default_datasource_id: str | None = None
    user: str | None = None
    http: HttpTransportConfig = Field(default_factory=HttpTransportConfig)


def load_config_from_env() -> MarivoMcpConfig:
    """Load MCP adapter configuration from environment variables."""
    mode = _load_mode()
    raw_timeout_ms = os.environ.get("MARIVO_TIMEOUT_MS", "600000")
    raw_openapi_cache_ttl_sec = os.environ.get("MARIVO_OPENAPI_CACHE_TTL_SEC", "300")

    try:
        return MarivoMcpConfig.model_validate(
            {
                "mode": mode,
                "base_url": _normalize_optional(os.environ.get("MARIVO_BASE_URL")),
                "api_token": _normalize_optional(os.environ.get("MARIVO_API_TOKEN")),
                "workspace_root": _normalize_optional(os.environ.get("MARIVO_WORKSPACE_ROOT")),
                "local_host": os.environ.get("MARIVO_LOCAL_HOST", "127.0.0.1"),
                "local_port": os.environ.get("MARIVO_LOCAL_PORT", "0"),
                "start_timeout_ms": os.environ.get("MARIVO_START_TIMEOUT_MS", "15000"),
                "healthcheck_timeout_ms": os.environ.get("MARIVO_HEALTHCHECK_TIMEOUT_MS", "2000"),
                "transport": os.environ.get("MARIVO_MCP_TRANSPORT", "stdio").strip() or "stdio",
                "timeout_ms": raw_timeout_ms,
                "openapi_cache_ttl_sec": raw_openapi_cache_ttl_sec,
                "default_datasource_id": _normalize_optional(
                    os.environ.get("MARIVO_DEFAULT_DATASOURCE_ID")
                ),
                "user": _normalize_optional(os.environ.get("MARIVO_USER")),
                "http": {
                    "host": os.environ.get("MARIVO_MCP_HOST", "127.0.0.1"),
                    "port": os.environ.get("MARIVO_MCP_PORT", "8000"),
                    "streamable_http_path": os.environ.get(
                        "MARIVO_MCP_STREAMABLE_HTTP_PATH", "/mcp"
                    ),
                    "stateless_http": _parse_bool_env(
                        os.environ.get("MARIVO_MCP_STATELESS_HTTP"), default=True
                    ),
                    "json_response": _parse_bool_env(
                        os.environ.get("MARIVO_MCP_JSON_RESPONSE"), default=True
                    ),
                },
            }
        )
    except ValidationError as error:
        raise MarivoMcpConfigError(
            f"Invalid marivo-mcp configuration from environment variables: {error}"
        ) from error


def _load_mode() -> Literal["auto", "remote", "local"]:
    raw_mode = os.environ.get("MARIVO_MODE", "auto")
    mode = raw_mode.strip() or "auto"
    allowed = ["auto", "remote", "local"]
    if mode not in allowed:
        raise TargetResolutionError(
            code="config_invalid",
            message=f"无效的 MARIVO_MODE 值：{mode}",
            detail={"mode_value": mode, "allowed": allowed},
            guidance="允许值：auto, remote, local",
        )
    if mode == "remote":
        return "remote"
    if mode == "local":
        return "local"
    return "auto"


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_bool_env(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise MarivoMcpConfigError(
        f"Invalid boolean value {value!r}. Use one of true/false, 1/0, yes/no, on/off."
    )
