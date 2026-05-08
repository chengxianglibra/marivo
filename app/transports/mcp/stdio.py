"""marivo-stdio console-script entry point."""

from __future__ import annotations

import logging
from pathlib import Path

from app.profiles.local import LocalConfig, create_local_runtime

logger = logging.getLogger(__name__)


def main() -> None:
    """Start a stdio MCP server backed by create_local_runtime()."""
    from mcp.server.fastmcp import FastMCP

    from app.transports.mcp.resources import register_resources
    from app.transports.mcp.tools import register_tools

    workspace = Path.cwd()
    config = LocalConfig(workspace_root=workspace)
    runtime = create_local_runtime(config, explicit="local")

    server = FastMCP("marivo")
    register_tools(server, runtime)
    register_resources(server, runtime)
    server.run()  # stdio is FastMCP default
