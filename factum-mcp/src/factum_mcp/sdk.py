from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Protocol, TypeVar, cast

ToolFn = TypeVar("ToolFn", bound=Callable[..., object])
ResourceFn = TypeVar("ResourceFn", bound=Callable[..., object])


class FastMcpSettings(Protocol):
    host: str
    port: int
    streamable_http_path: str


class FastMcpServer(Protocol):
    def tool(self) -> Callable[[ToolFn], ToolFn]: ...

    def resource(self, uri: str) -> Callable[[ResourceFn], ResourceFn]: ...

    def run(self, transport: str | None = None) -> None: ...

    settings: FastMcpSettings


class FastMcpFactory(Protocol):
    def __call__(
        self,
        name: str,
        *,
        stateless_http: bool = ...,
        json_response: bool = ...,
        streamable_http_path: str = ...,
    ) -> FastMcpServer: ...


class FactumMcpDependencyError(RuntimeError):
    """Raised when the Python MCP SDK is unavailable at runtime."""


def load_fastmcp() -> FastMcpFactory:
    """Load the FastMCP application class lazily.

    The repository does not vendor the SDK inside the root service environment,
    so this import stays runtime-only and fails with a clear message when the
    subproject dependencies have not been installed.
    """
    try:
        module = import_module("mcp.server.fastmcp")
    except ModuleNotFoundError as error:
        raise FactumMcpDependencyError(
            "The Python MCP SDK is not installed for factum-mcp. "
            "Install the subproject dependencies in factum-mcp/ before starting the server."
        ) from error

    fastmcp_cls = getattr(module, "FastMCP", None)
    if fastmcp_cls is None:
        raise FactumMcpDependencyError(
            "Installed MCP SDK does not expose mcp.server.fastmcp.FastMCP."
        )
    return cast("FastMcpFactory", fastmcp_cls)
