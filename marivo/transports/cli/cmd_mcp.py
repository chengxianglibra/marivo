from __future__ import annotations

import argparse
from pathlib import Path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def handle(args: argparse.Namespace) -> None:
    from mcp.server.fastmcp import FastMCP

    from marivo.profiles.local import LocalConfig, create_local_runtime
    from marivo.transports.mcp.resources import register_resources
    from marivo.transports.mcp.tools import register_tools

    config = LocalConfig(workspace_root=Path.cwd())
    runtime = create_local_runtime(config, explicit="local")
    server = FastMCP("marivo")
    register_tools(server, runtime)
    register_resources(server, runtime)
    server.run()
