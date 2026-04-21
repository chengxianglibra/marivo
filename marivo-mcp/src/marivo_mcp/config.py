from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class MarivoMcpConfigError(RuntimeError):
    """Raised when required MCP adapter configuration is missing or invalid."""


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

    base_url: str = Field(min_length=1)
    api_token: str | None = None
    transport: str = Field(default="stdio", pattern="^(stdio|streamable-http)$")
    timeout_ms: int = Field(default=600_000, gt=0)
    openapi_cache_ttl_sec: int = Field(default=300, ge=0)
    default_source_id: str | None = None
    http: HttpTransportConfig = Field(default_factory=HttpTransportConfig)


def load_config_from_env() -> MarivoMcpConfig:
    """Load MCP adapter configuration from environment variables."""
    raw_base_url = os.environ.get("MARIVO_BASE_URL")
    if raw_base_url is None or not raw_base_url.strip():
        raise MarivoMcpConfigError(
            "MARIVO_BASE_URL is required to start marivo-mcp. "
            "Set it to the Marivo HTTP base URL, for example http://127.0.0.1:8000."
        )

    raw_timeout_ms = os.environ.get("MARIVO_TIMEOUT_MS", "600000")
    raw_openapi_cache_ttl_sec = os.environ.get("MARIVO_OPENAPI_CACHE_TTL_SEC", "300")

    try:
        return MarivoMcpConfig.model_validate(
            {
                "base_url": raw_base_url.strip(),
                "api_token": _normalize_optional(os.environ.get("MARIVO_API_TOKEN")),
                "transport": os.environ.get("MARIVO_MCP_TRANSPORT", "stdio").strip() or "stdio",
                "timeout_ms": raw_timeout_ms,
                "openapi_cache_ttl_sec": raw_openapi_cache_ttl_sec,
                "default_source_id": _normalize_optional(
                    os.environ.get("MARIVO_DEFAULT_SOURCE_ID")
                ),
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
