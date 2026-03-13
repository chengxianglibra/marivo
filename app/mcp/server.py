from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from app.mcp.tools import register_tools


mcp = FastMCP("omnidb_mcp", json_response=True)
register_tools(mcp)


def main() -> None:
    mcp.run(transport=os.getenv("OMNIDB_MCP_TRANSPORT", "stdio"))
