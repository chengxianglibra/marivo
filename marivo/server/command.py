"""Foreground CLI hosting with TLS and a single readiness receipt."""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import ssl
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlsplit

from marivo._kernel.environment import MARIVO_VERSION
from marivo._kernel.project import resolve_project_root
from marivo.server import granite_model as granite_assets
from marivo.server.http import ReadApplication
from marivo.server.wire import API_VERSION, Limits


def _origin(value: str) -> bytes:
    try:
        origin = urlsplit(value)
        valid = (
            origin.scheme in ("http", "https")
            and origin.hostname is not None
            and origin.username is None
            and origin.password is None
            and value == f"{origin.scheme}://{origin.netloc}"
            and not any(character.isspace() for character in value)
            and not origin.netloc.endswith(":")
            and (origin.port is None or origin.port > 0)
            and value.isprintable()
            and not any(character in origin.netloc for character in "\\*")
        )
        if valid:
            return value.encode("ascii")
    except (UnicodeError, ValueError):
        pass
    raise ValueError("--allow-origin requires an exact HTTP(S) Origin without a path or wildcard.")


def serve(
    *,
    project_root: Path | None,
    host: str,
    port: int,
    origins: list[str],
    certfile: str | None,
    keyfile: str | None,
    limits: Limits,
    granite_model: Path | None = None,
    granite_cache_dir: Path | None = None,
) -> None:
    import uvicorn  # noqa: PLC0415 - Only the serve command needs the API extra.

    token = os.environ.get("MARIVO_API_TOKEN", "")
    if re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token) is None:
        raise ValueError("Set MARIVO_API_TOKEN to a valid administrator-configured Bearer token.")
    if not 0 <= port <= 65535:
        raise ValueError("--port must be an integer from 0 through 65535.")
    if (certfile is None) != (keyfile is None):
        raise ValueError("Provide --ssl-certfile and --ssl-keyfile together.")
    root = resolve_project_root() if project_root is None else project_root.resolve()
    if not root.is_dir():
        raise ValueError("--project-root must be an existing directory.")
    if granite_model is not None and granite_model.suffix not in (".onnx", ".ort"):
        raise ValueError("--experimental-granite-model requires an ONNX or ORT model file.")
    allowed_origins = frozenset(map(_origin, origins))
    if find_spec("onnxruntime") is None or find_spec("tokenizers") is None:
        raise ValueError("Install the API runtime with pip install 'marivo[api]'.")
    if granite_model is None:
        granite_model = granite_assets.prepare_granite_model()
    app = ReadApplication(
        root,
        token.encode("ascii"),
        allowed_origins,
        limits,
        granite_model=granite_model,
        granite_cache_dir=granite_cache_dir,
    )
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        ssl_certfile=certfile,
        ssl_keyfile=keyfile,
        ssl_keyfile_password="" if certfile is not None else None,
        lifespan="on",
        ws="none",
        http="h11",
        loop="asyncio",
        interface="asgi3",
        access_log=False,
        proxy_headers=False,
        timeout_graceful_shutdown=5,
    )
    try:
        config.load()
    except (OSError, ssl.SSLError, ValueError):
        raise ValueError(
            "Could not load the TLS certificate and key. Check the PEM files and key pairing."
        ) from None

    class _Server(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            if self.started:
                bound = self.servers[0].sockets[0].getsockname()
                print(
                    json.dumps(
                        {
                            "host": bound[0],
                            "port": bound[1],
                            "protocol": "https" if self.config.is_ssl else "http",
                            "pid": os.getpid(),
                            "marivo_version": MARIVO_VERSION,
                            "api_version": API_VERSION,
                        }
                    ),
                    flush=True,
                )

    with contextlib.suppress(KeyboardInterrupt):
        _Server(config).run()
