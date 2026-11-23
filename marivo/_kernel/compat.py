"""Small runtime compatibility surface for the supported Python versions."""

from __future__ import annotations

import sys
from datetime import timezone
from enum import Enum

from typing_extensions import Never, Self

if sys.version_info >= (3, 11):
    import tomllib as tomllib
else:
    import tomli as tomllib


class StrEnum(str, Enum):
    """Python 3.11-compatible subset of :class:`enum.StrEnum`."""

    def __str__(self) -> str:
        return str.__str__(self)

    def __format__(self, format_spec: str) -> str:
        return str.__format__(self, format_spec)


UTC = timezone.utc

__all__ = ["UTC", "Never", "Self", "StrEnum", "tomllib"]
