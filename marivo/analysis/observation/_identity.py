"""Canonical identities for immutable observation inputs."""

from __future__ import annotations

import hashlib
import math
from json.encoder import encode_basestring_ascii
from typing import TypeAlias

CanonicalValue: TypeAlias = bool | int | float | str | tuple["CanonicalValue", ...] | None


def _canonical(value: CanonicalValue) -> str:
    if value is None:
        return '["null"]'
    if type(value) is bool:
        return '["bool",true]' if value else '["bool",false]'
    if type(value) is int:
        return f'["int","{value}"]'
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("canonical observation identities require finite numbers")
        return f'["float","{value.hex()}"]'
    if type(value) is str:
        return f'["str",{encode_basestring_ascii(value)}]'
    if isinstance(value, tuple):
        return '["tuple",[' + ",".join(_canonical(item) for item in value) + "]]"
    raise TypeError(f"unsupported canonical observation value: {type(value).__name__}")


def _canonical_digest(value: CanonicalValue) -> str:
    """Hash a tagged, compact ASCII encoding of an immutable value tuple."""
    return hashlib.sha256(_canonical(value).encode("ascii")).hexdigest()
