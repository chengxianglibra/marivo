from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from marivo.api.app_factory import create_app as create_app

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    if name != "create_app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from marivo.api.app_factory import create_app

    return create_app
