from __future__ import annotations

from collections.abc import Callable

from factum_mcp.config import FactumMcpConfig
from factum_mcp.http_client import FactumHttpClient
from factum_mcp.sdk import FastMcpServer

_ParamScalar = str | int | float | bool | None
_ParamList = list[_ParamScalar]
_ParamValue = _ParamScalar | _ParamList

_SEMANTIC_RESOURCE_PATHS = {
    "entities": "/semantic/entities",
    "metrics": "/semantic/metrics",
    "process-objects": "/semantic/process-objects",
    "dimensions": "/semantic/dimensions",
    "time": "/semantic/time",
    "enum-sets": "/semantic/enum-sets",
    "bindings": "/semantic/bindings",
    "compatibility-profiles": "/compiler/compatibility-profiles",
}


def _resource_metadata(
    *, http_method: str | None = None, http_paths: tuple[str, ...] = ()
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    def decorator(func: Callable[..., object]) -> Callable[..., object]:
        func._factum_http_method = http_method  # type: ignore[attr-defined]
        func._factum_http_paths = http_paths  # type: ignore[attr-defined]
        return func

    return decorator


def register_resources(
    server: FastMcpServer,
    config: FactumMcpConfig,
    *,
    client_factory: Callable[[FactumMcpConfig], FactumHttpClient] | None = None,
) -> None:
    """Register read-only MCP resources that mirror canonical Factum HTTP surfaces."""
    resolved_client_factory = client_factory or FactumHttpClient
    client = resolved_client_factory(config)

    @server.resource("factum://server/config")
    @_resource_metadata()
    def server_config() -> str:
        """Expose minimal non-secret runtime configuration for local debugging."""
        return (
            "factum-mcp scaffold\n"
            f"base_url={config.base_url}\n"
            f"timeout_ms={config.timeout_ms}\n"
            f"openapi_cache_ttl_sec={config.openapi_cache_ttl_sec}\n"
            f"default_source_id={config.default_source_id or ''}\n"
        )

    @server.resource("factum://catalog/summary")
    @_resource_metadata(
        http_method="GET",
        http_paths=(
            "/openapi/index",
            "/sources",
            "/semantic/entities",
            "/semantic/metrics",
            "/semantic/process-objects",
            "/semantic/dimensions",
            "/semantic/time",
            "/semantic/enum-sets",
            "/semantic/bindings",
            "/compiler/compatibility-profiles",
        ),
    )
    def catalog_summary() -> dict[str, object]:
        """Expose a fixed catalog summary snapshot assembled from canonical HTTP read surfaces."""
        return {
            "openapi_index": _read_resource(client, "/openapi/index"),
            "sources": _read_resource(client, "/sources"),
            "semantic": {
                family: _read_resource(client, path)
                for family, path in _SEMANTIC_RESOURCE_PATHS.items()
            },
        }

    @server.resource("factum://sessions/{session_id}/state")
    @_resource_metadata(http_method="GET", http_paths=("/sessions/{session_id}/state",))
    def session_state(session_id: str) -> object:
        """Mirror GET /sessions/{session_id}/state as a read-only MCP resource."""
        return _read_resource(client, f"/sessions/{session_id}/state")

    @server.resource("factum://sessions/{session_id}/propositions/{proposition_id}/context")
    @_resource_metadata(
        http_method="GET",
        http_paths=("/sessions/{session_id}/propositions/{proposition_id}/context",),
    )
    def proposition_context(session_id: str, proposition_id: str) -> object:
        """Mirror GET /sessions/{session_id}/propositions/{proposition_id}/context as a read-only MCP resource."""
        return _read_resource(
            client, f"/sessions/{session_id}/propositions/{proposition_id}/context"
        )

    @server.resource("factum://semantic/{family}")
    @_resource_metadata(
        http_method="GET",
        http_paths=(
            "/semantic/entities",
            "/semantic/metrics",
            "/semantic/process-objects",
            "/semantic/dimensions",
            "/semantic/time",
            "/semantic/enum-sets",
            "/semantic/bindings",
            "/compiler/compatibility-profiles",
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

    @server.resource("factum://sources/{source_id}/objects")
    @_resource_metadata(http_method="GET", http_paths=("/sources/{source_id}/objects",))
    def source_objects(source_id: str) -> object:
        """Mirror synced source metadata reads via GET /sources/{source_id}/objects only."""
        return _read_resource(client, f"/sources/{source_id}/objects")

    @server.resource("factum://sources/{source_id}/objects/{object_id}")
    @_resource_metadata(
        http_method="GET",
        http_paths=("/sources/{source_id}/objects/{object_id}",),
    )
    def source_object(source_id: str, object_id: str) -> object:
        """Mirror synced source metadata detail via GET /sources/{source_id}/objects/{object_id} only."""
        return _read_resource(client, f"/sources/{source_id}/objects/{object_id}")


def _read_resource(
    client: FactumHttpClient,
    path: str,
    *,
    params: dict[str, _ParamValue] | None = None,
) -> object:
    return client.request_canonical("GET", path, params=params)
