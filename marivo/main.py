from typing import Any

from marivo.transports.http.app_factory import create_app

# Lazy ASGI app: deferred until first access so that importing this module
# (e.g. `from marivo.main import create_app`) does not open DuckDB or seed data.
# uvicorn resolves `app.main:app` via attribute access, which triggers this.
_app = None


def __getattr__(name: str) -> Any:
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
