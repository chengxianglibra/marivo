"""Parsing and serialization of MARIVO custom_extensions.

Handles the bidirectional mapping between:
  - OSI wire format: custom_extensions[].data (JSON string)
  - Python: typed MARIVO extension Pydantic models
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

MARIVO_VENDOR = "MARIVO"


class OsiCustomExtensionLike(Protocol):
    @property
    def vendor_name(self) -> str: ...

    @property
    def data(self) -> str: ...


def extract_marivo_extension(  # noqa: UP047 — PEP 695 not yet supported by mypy
    custom_extensions: Sequence[Any] | None,
    extension_type: type[T],
) -> T | None:
    """Extract and parse the MARIVO vendor extension from a custom_extensions list."""
    if custom_extensions is None:
        return None
    for ext in custom_extensions:
        candidate = getattr(ext, "root", ext)
        if candidate.vendor_name == MARIVO_VENDOR:
            return extension_type.model_validate_json(candidate.data)
    return None
