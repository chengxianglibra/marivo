"""Authoring surface for project-level datasources."""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from marivo.datasource.errors import (
    DatasourceFieldInvalidError,
    DatasourceSecretInPlaintextError,
)
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.typing import AiContext
from marivo.datasource.typing import _build_ai_context as _shared_build_ai_context


def _datasource_ai_context_error(message: str, details: dict[str, Any]) -> None:
    raise DatasourceFieldInvalidError(
        message=message,
        details={"datasource": "<unknown>", **details},
    )


def _build_ai_context(ai_context: AiContext | dict[str, Any] | None) -> AiContextIR:
    return _shared_build_ai_context(ai_context, on_error=_datasource_ai_context_error)


SENSITIVE_FIELD_STEMS = frozenset(
    {
        "user",
        "password",
        "token",
        "secret",
        "secret_key",
        "access_key",
        "auth",
        "private_key",
        "passphrase",
        "api_key",
    }
)
_DATASOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class DatasourceLoaderContext:
    pending_objects: list[DatasourceIR] = field(default_factory=list)


@dataclass(frozen=True, init=False)
class DatasourceSpec:
    """Validated project-level datasource configuration."""

    name: str
    backend_type: str
    fields: dict[str, Any]
    env_refs: dict[str, str]
    description: str | None
    ai_context: AiContextIR

    def __init__(
        self,
        *,
        name: str,
        backend_type: str,
        description: str | None = None,
        ai_context: AiContext | dict[str, Any] | None = None,
        **fields: Any,
    ) -> None:
        validate_datasource_name(name)
        if not isinstance(backend_type, str) or not backend_type:
            raise DatasourceFieldInvalidError(
                message=f"datasource {name!r} missing required backend_type",
                details={
                    "datasource": name,
                    "field": "backend_type",
                    "reason": "backend_type is required and must be a non-empty string",
                },
            )
        literal_fields, env_refs = _split_fields(name, fields)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "backend_type", backend_type)
        object.__setattr__(self, "fields", literal_fields)
        object.__setattr__(self, "env_refs", env_refs)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "ai_context", _build_ai_context(ai_context))


class DatasourceRef:
    """Global datasource reference used by semantic declarations."""

    __slots__ = ("name", "semantic_id")

    def __init__(self, name: str) -> None:
        validate_datasource_name(name)
        self.name = name
        self.semantic_id = name

    def __repr__(self) -> str:
        return f"DatasourceRef({self.semantic_id!r})"


def ref(name: str) -> DatasourceRef:
    """Reference a global project datasource by short name."""
    return DatasourceRef(name)


_DATASOURCE_CTX: ContextVar[DatasourceLoaderContext | None] = ContextVar(
    "_DATASOURCE_CTX",
    default=None,
)


def _caller_location() -> DatasourceSourceLocation:
    try:
        frame = inspect.currentframe()
        if frame is not None:
            caller_frame = frame.f_back
            if caller_frame is not None:
                caller_frame = caller_frame.f_back
            if caller_frame is not None:
                return DatasourceSourceLocation(
                    file=caller_frame.f_code.co_filename,
                    line=caller_frame.f_lineno,
                )
    except AttributeError:
        pass
    return DatasourceSourceLocation(file="<unknown>", line=0)


def _require_ctx() -> DatasourceLoaderContext:
    ctx = _DATASOURCE_CTX.get()
    if ctx is None:
        raise DatasourceFieldInvalidError(
            message="md.datasource can only be called while loading marivo/datasources/ files",
            details={"datasource": "<unknown>", "field": "<context>", "reason": "outside loader"},
        )
    return ctx


def validate_datasource_name(name: Any) -> None:
    if not isinstance(name, str) or not name:
        raise DatasourceFieldInvalidError(
            message="datasource name must be a non-empty string",
            details={"datasource": name, "field": "<name>", "reason": "empty datasource name"},
        )
    if "." in name:
        raise DatasourceFieldInvalidError(
            message=f"datasource {name!r} must use a global datasource name",
            details={
                "datasource": name,
                "field": "<name>",
                "reason": "datasource name must not be model-qualified",
            },
        )
    if not _DATASOURCE_NAME_RE.fullmatch(name):
        raise DatasourceFieldInvalidError(
            message=f"datasource {name!r} is not a valid datasource name",
            details={
                "datasource": name,
                "field": "<name>",
                "reason": (
                    "datasource name must contain only letters, digits, underscores, and hyphens"
                ),
            },
        )


def _is_jsonable_value(value: Any) -> bool:
    if isinstance(value, str | int | float | bool) or value is None:
        return True
    if isinstance(value, list):
        return all(_is_jsonable_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_jsonable_value(item) for key, item in value.items())
    return False


def _split_fields(
    name: str, raw_fields: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    fields: dict[str, Any] = {}
    env_refs: dict[str, str] = {}
    for key, value in raw_fields.items():
        if not isinstance(key, str) or not key:
            raise DatasourceFieldInvalidError(
                message=f"datasource {name!r} has an empty field name",
                details={"datasource": name, "field": key, "reason": "empty field name"},
            )
        if key.endswith("_env") and len(key) > len("_env"):
            stem = key[: -len("_env")]
            if not isinstance(value, str) or not value:
                raise DatasourceFieldInvalidError(
                    message=f"datasource {name!r} field {key!r} must be a non-empty env var name",
                    details={
                        "datasource": name,
                        "field": key,
                        "reason": "env_ref must reference an env var name as a string",
                    },
                )
            env_refs[stem] = value
            continue
        if key in SENSITIVE_FIELD_STEMS:
            raise DatasourceSecretInPlaintextError(
                message=(
                    f"datasource {name!r} field {key!r} is sensitive and must not "
                    "be stored as a literal"
                ),
                details={"datasource": name, "field": key},
            )
        if not _is_jsonable_value(value):
            raise DatasourceFieldInvalidError(
                message=(
                    f"datasource {name!r} field {key!r} has unsupported value type "
                    f"{type(value).__name__}"
                ),
                details={
                    "datasource": name,
                    "field": key,
                    "reason": (
                        "datasource fields must be JSON values (str, int, float, bool, None, "
                        "lists, or objects with string keys)"
                    ),
                },
            )
        fields[key] = value
    return fields, env_refs


def _ir_from_spec(spec: DatasourceSpec, *, location: DatasourceSourceLocation) -> DatasourceIR:
    return DatasourceIR(
        semantic_id=spec.name,
        name=spec.name,
        backend_type=spec.backend_type,
        fields=dict(spec.fields),
        env_refs=dict(spec.env_refs),
        description=spec.description,
        ai_context=spec.ai_context,
        python_symbol=spec.name,
        location=location,
    )


def datasource(
    spec: DatasourceSpec | None = None,
    *,
    name: str | None = None,
    backend_type: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
    **fields: Any,
) -> None:
    """Declare one project-level datasource."""
    ctx = _require_ctx()
    if spec is not None:
        if name is not None or backend_type is not None or description is not None or fields:
            raise DatasourceFieldInvalidError(
                message="md.datasource accepts either a DatasourceSpec or keyword fields, not both",
                details={
                    "datasource": spec.name,
                    "field": "<arguments>",
                    "reason": "mixed DatasourceSpec and keyword datasource declaration",
                },
            )
        ctx.pending_objects.append(_ir_from_spec(spec, location=_caller_location()))
        return
    if name is None or backend_type is None:
        raise DatasourceFieldInvalidError(
            message="md.datasource requires a DatasourceSpec or name and backend_type",
            details={
                "datasource": name,
                "field": "<arguments>",
                "reason": "missing DatasourceSpec or name/backend_type declaration",
            },
        )
    ctx.pending_objects.append(
        _ir_from_spec(
            DatasourceSpec(
                name=name,
                backend_type=backend_type,
                description=description,
                ai_context=ai_context,
                **fields,
            ),
            location=_caller_location(),
        )
    )
