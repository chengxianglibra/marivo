from __future__ import annotations

from collections.abc import Callable

from marivo_mcp.config import MarivoMcpConfig
from marivo_mcp.http_client import MarivoHttpClient
from marivo_mcp.sdk import FastMcpServer

_ParamScalar = str | int | float | bool | None
_ParamList = list[_ParamScalar]
_ParamValue = _ParamScalar | _ParamList

_SEMANTIC_RESOURCE_PATHS = {
    "models": "/semantic-models",
    "datasets": "/semantic-models/{model}/datasets",
    "relationships": "/semantic-models/{model}/relationships",
    "metrics": "/semantic-models/{model}/metrics",
}


def _resource_metadata(
    *, http_method: str | None = None, http_paths: tuple[str, ...] = ()
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    def decorator(func: Callable[..., object]) -> Callable[..., object]:
        func._marivo_http_method = http_method  # type: ignore[attr-defined]
        func._marivo_http_paths = http_paths  # type: ignore[attr-defined]
        return func

    return decorator


def register_resources(
    server: FastMcpServer,
    config: MarivoMcpConfig,
    *,
    client_factory: Callable[[MarivoMcpConfig], MarivoHttpClient] | None = None,
) -> None:
    """Register read-only MCP resources that mirror canonical Marivo HTTP surfaces."""
    resolved_client_factory = client_factory or MarivoHttpClient
    client = resolved_client_factory(config)

    @server.resource("marivo://server/config")
    @_resource_metadata()
    def server_config() -> str:
        """Expose minimal non-secret runtime configuration for local debugging."""
        return (
            "marivo-mcp scaffold\n"
            f"base_url={config.base_url}\n"
            f"timeout_ms={config.timeout_ms}\n"
            f"openapi_cache_ttl_sec={config.openapi_cache_ttl_sec}\n"
            f"default_datasource_id={config.default_datasource_id or ''}\n"
        )

    @server.resource("marivo://sessions/{session_id}/state")
    @_resource_metadata(http_method="GET", http_paths=("/sessions/{session_id}/state",))
    def session_state(session_id: str) -> object:
        """Mirror GET /sessions/{session_id}/state as a read-only MCP resource."""
        return _read_resource(client, f"/sessions/{session_id}/state")

    @server.resource("marivo://sessions/{session_id}/propositions/{proposition_id}/context")
    @_resource_metadata(
        http_method="GET",
        http_paths=("/sessions/{session_id}/propositions/{proposition_id}/context",),
    )
    def proposition_context(session_id: str, proposition_id: str) -> object:
        """Mirror GET /sessions/{session_id}/propositions/{proposition_id}/context as a read-only MCP resource."""
        return _read_resource(
            client, f"/sessions/{session_id}/propositions/{proposition_id}/context"
        )

    @server.resource("marivo://semantic/{family}")
    @_resource_metadata(
        http_method="GET",
        http_paths=(
            "/semantic-models",
            "/semantic-models/{model}/datasets",
            "/semantic-models/{model}/relationships",
            "/semantic-models/{model}/metrics",
        ),
    )
    def semantic_family(family: str) -> object:
        """Mirror semantic family list endpoints without adding MCP-only filtering semantics."""
        path = _SEMANTIC_RESOURCE_PATHS.get(family)
        if path is None:
            supported = ", ".join(sorted(_SEMANTIC_RESOURCE_PATHS))
            raise ValueError(
                f"Unsupported semantic family {family!r}. Supported families: {supported}."
            )
        return _read_resource(client, path)


def _read_resource(
    client: MarivoHttpClient,
    path: str,
    *,
    params: dict[str, _ParamValue] | None = None,
) -> object:
    return client.request_canonical("GET", path, params=params)
