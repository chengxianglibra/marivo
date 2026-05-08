from __future__ import annotations

from app.runtime.ports import RuntimePorts
from app.semantic_runtime.errors import SemanticRuntimeNotReadyError


def __getattr__(name: str) -> object:
    if name == "MarivoRuntime":
        from app.runtime.runtime import MarivoRuntime

        return MarivoRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MarivoRuntime",
    "RuntimePorts",
    "SemanticRuntimeNotReadyError",
]
