"""Closed JSON transport for independently spawned regression-test processes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeAlias

Json: TypeAlias = bool | int | float | str | list["Json"] | dict[str, "Json"] | None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value: Json) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def checked(value: object) -> Json:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [checked(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, Json] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON keys must be strings")
            result[key] = checked(item)
        return result
    raise ValueError("invalid JSON value")


def obj(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def arr(value: Json) -> list[Json]:
    if not isinstance(value, list):
        raise ValueError("expected JSON array")
    return value


def read(path: Path) -> dict[str, Json]:
    return obj(checked(json.loads(path.read_bytes())))
