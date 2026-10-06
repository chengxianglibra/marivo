"""Closed JSON transport for independently spawned regression-test processes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeAlias

from marivo.analysis.methods.physical import FixedShape, NoTime, QualificationKey, SourceShape

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]


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


def key_json(key: QualificationKey) -> dict[str, Json]:
    time: dict[str, Json] = (
        {"kind": "NoTime"}
        if isinstance(key.shape.time, NoTime)
        else {
            "kind": "instant",
            "unit": key.shape.time.unit,
            "timezone": key.shape.time.timezone,
        }
    )
    shape: dict[str, Json] = {"kind": "FixedShape", "time": time}
    if isinstance(key.shape, SourceShape):
        shape = {
            "kind": "SourceShape",
            "backend": key.shape.backend,
            "form": key.shape.form,
            "table_kind": key.shape.table_kind,
            "time": time,
        }
    else:
        assert isinstance(key.shape, FixedShape)
    return {
        "method": f"{key.method.name}@v{key.method.version}",
        "input_types": [value.name for value in key.input_types],
        "input_domains": list(key.input_domains),
        "shape": shape,
        "route": key.route,
    }
