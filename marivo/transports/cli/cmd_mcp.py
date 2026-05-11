from __future__ import annotations

import argparse
from pathlib import Path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def handle(args: argparse.Namespace) -> None:
    import os

    from mcp.server.fastmcp import FastMCP

    from marivo.identity import current_user, resolve_user
    from marivo.profiles.local import LocalConfig, create_local_runtime
    from marivo.transports.mcp.resources import register_resources
    from marivo.transports.mcp.tools import register_tools

    workspace_root = Path(os.environ.get("MARIVO_WORKSPACE_ROOT", Path.cwd()))
    config = LocalConfig(workspace_root=workspace_root)

    import getpass

    user = resolve_user() or getpass.getuser()
    current_user.set(user)
    runtime = create_local_runtime(config, explicit="local")
    server = FastMCP("marivo")
    register_tools(server, runtime, transport="stdio")
    register_resources(server, runtime)
    server.run()
