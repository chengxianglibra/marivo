from __future__ import annotations

from marivo.runtime.errors import SemanticRuntimeNotReadyError
from marivo.runtime.ports import RuntimePorts


def __getattr__(name: str) -> object:
    if name == "MarivoRuntime":
        from marivo.runtime.runtime import MarivoRuntime

        return MarivoRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MarivoRuntime",
    "RuntimePorts",
    "SemanticRuntimeNotReadyError",
]
