from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def _resolve_stdio_user() -> str:
    user = os.environ.get("MARIVO_USER", "").strip()
    if user:
        return user
    return getpass.getuser()


def handle(args: argparse.Namespace) -> None:
    from mcp.server.fastmcp import FastMCP

    from marivo.identity import set_current_user
    from marivo.profiles.local import LocalConfig, create_local_runtime
    from marivo.transports.mcp.resources import register_resources
    from marivo.transports.mcp.tools import register_tools

    workspace_root = Path(os.environ.get("MARIVO_WORKSPACE_ROOT", Path.cwd()))
    config = LocalConfig(workspace_root=workspace_root)

    set_current_user(_resolve_stdio_user())
    runtime = create_local_runtime(config, explicit="local")
    server = FastMCP("marivo")
    register_tools(server, runtime, transport="stdio")
    register_resources(server, runtime)
    server.run()
