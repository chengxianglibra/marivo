"""Analysis-scoped runtime bindings for parameterized physical sources."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from math import isfinite
from typing import NoReturn, TypeAlias

from marivo.analysis.errors import AnalysisRepair, SourceBindingError
from marivo.datasource.ir import (
    JsonSourceIR,
    QueryParamScalar,
    QueryParamScalarList,
    json_source_param_names,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EntityKind, Ref, SemanticKind

SourceBindingMap: TypeAlias = Mapping[
    Ref[EntityKind], Mapping[str, QueryParamScalar | QueryParamScalarList]
]
NormalizedSourceBindings: TypeAlias = dict[str, dict[str, QueryParamScalar | QueryParamScalarList]]
SourceBindingScopes: TypeAlias = dict[object, NormalizedSourceBindings]

_ACTIVE_SOURCE_BINDINGS: ContextVar[SourceBindingScopes | None] = ContextVar(
    "marivo_analysis_source_bindings",
    default=None,
)


def current_source_bindings(owner: object) -> NormalizedSourceBindings:
    """Return an isolated copy of one session runtime's current bindings."""
    bindings = (_ACTIVE_SOURCE_BINDINGS.get() or {}).get(owner, {})
    return {entity_id: dict(params) for entity_id, params in bindings.items()}


def _binding_error(
    message: str,
    *,
    expected: str,
    received: str,
    location: str = "session.source_bindings",
) -> NoReturn:
    raise SourceBindingError(
        message=message,
        expected=expected,
        received=received,
        location=location,
        repair=AnalysisRepair(
            kind="retry",
            action="Bind every declared non-secret source parameter to its exact Entity ref.",
            help_target=LiveHelpTarget(
                surface="analysis",
                canonical_id="Session.source_bindings",
            ),
        ),
    )


def _check_binding_scalar(
    value: object,
    *,
    ref: Ref[EntityKind],
    name: str,
) -> None:
    """Validate one runtime binding scalar (not a list)."""
    if isinstance(value, str | int | bool):
        return
    if isinstance(value, float):
        if not isfinite(value):
            _binding_error(
                f"source binding value {ref.path!r}.{name} must be a finite float",
                expected="a finite float",
                received=repr(value),
                location=f"session.source_bindings[{ref.path!r}][{name!r}]",
            )
        return
    _binding_error(
        f"source binding value {ref.path!r}.{name} has unsupported type",
        expected="str | int | float | bool | list of these",
        received=type(value).__name__,
        location=f"session.source_bindings[{ref.path!r}][{name!r}]",
    )


def _check_binding_value(
    value: object,
    *,
    ref: Ref[EntityKind],
    name: str,
) -> None:
    """Validate one runtime binding value: a scalar or a flat, non-empty scalar list."""
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        if not value:
            _binding_error(
                f"source binding value {ref.path!r}.{name} must not be an empty list",
                expected="a non-empty flat list of str | int | float | bool",
                received="an empty list",
                location=f"session.source_bindings[{ref.path!r}][{name!r}]",
            )
        for element in value:
            if isinstance(element, Sequence) and not isinstance(element, str | bytes | bytearray):
                _binding_error(
                    f"source binding value {ref.path!r}.{name} list values must be flat",
                    expected="a flat list of str | int | float | bool",
                    received="a nested list",
                    location=f"session.source_bindings[{ref.path!r}][{name!r}]",
                )
            _check_binding_scalar(element, ref=ref, name=name)
        return
    _check_binding_scalar(value, ref=ref, name=name)


def _normalize_source_bindings(
    catalog: object, bindings: SourceBindingMap
) -> NormalizedSourceBindings:
    if not isinstance(bindings, Mapping):
        _binding_error(
            "session.source_bindings(...) requires at least one entity binding",
            expected="a non-empty Mapping[Ref[entity], Mapping[str, scalar]]",
            received=type(bindings).__name__,
        )
    if not bindings:
        _binding_error(
            "session.source_bindings(...) requires at least one entity binding",
            expected="a non-empty Mapping[Ref[entity], Mapping[str, scalar]]",
            received="empty mapping",
        )
    project = getattr(catalog, "_project", None)
    registry = getattr(project, "_registry", None)
    entities = getattr(registry, "entities", None)
    if not isinstance(entities, Mapping):
        _binding_error(
            "session source bindings require a loaded semantic catalog",
            expected="a loaded current semantic catalog",
            received="catalog without an entity registry",
        )

    normalized: NormalizedSourceBindings = {}
    for ref, values in bindings.items():
        if type(ref) is not Ref or ref.kind is not SemanticKind.ENTITY:
            _binding_error(
                "session.source_bindings(...) keys must be exact Ref[entity] values",
                expected="an exact ms.ref.entity(...) key",
                received=repr(ref),
            )
        entity = entities.get(ref.path)
        if entity is None:
            _binding_error(
                f"source binding entity {ref.path!r} is not in the current catalog",
                expected="an Entity ref from the current catalog",
                received=ref.path,
                location=f"session.source_bindings[{ref.path!r}]",
            )
        source = getattr(entity, "source", None)
        if not isinstance(source, JsonSourceIR):
            _binding_error(
                f"source binding entity {ref.path!r} does not use md.json(...)",
                expected="an Entity backed by a parameterized md.json(...) source",
                received=type(source).__name__,
                location=f"session.source_bindings[{ref.path!r}]",
            )
        required = set(json_source_param_names(source))
        if not required:
            _binding_error(
                f"source binding entity {ref.path!r} declares no runtime parameters",
                expected="an Entity whose md.json(...) uses md.source_param(...) values",
                received="no declared runtime source parameters",
                location=f"session.source_bindings[{ref.path!r}]",
            )
        if not isinstance(values, Mapping):
            _binding_error(
                f"source binding values for {ref.path!r} must be a mapping",
                expected="Mapping[str, str | int | float | bool]",
                received=type(values).__name__,
                location=f"session.source_bindings[{ref.path!r}]",
            )
        entity_values: dict[str, QueryParamScalar | QueryParamScalarList] = {}
        for name, value in values.items():
            if not isinstance(name, str):
                _binding_error(
                    "source binding parameter names must be strings",
                    expected="string parameter names",
                    received=repr(name),
                    location=f"session.source_bindings[{ref.path!r}]",
                )
            _check_binding_value(value, ref=ref, name=name)
            entity_values[name] = value
        supplied = set(entity_values)
        missing = tuple(sorted(required - supplied))
        extra = tuple(sorted(supplied - required))
        if missing or extra:
            _binding_error(
                f"source binding mismatch for {ref.path!r}: missing={missing!r}, extra={extra!r}",
                expected=f"exact parameter names {tuple(sorted(required))!r}",
                received=f"parameter names {tuple(sorted(supplied))!r}",
                location=f"session.source_bindings[{ref.path!r}]",
            )
        normalized[ref.path] = entity_values
    return normalized


@contextmanager
def source_binding_scope(
    owner: object,
    catalog: object,
    bindings: SourceBindingMap,
) -> Iterator[None]:
    """Install validated bindings for one nested analysis execution scope."""
    normalized = _normalize_source_bindings(catalog, bindings)
    active = dict(_ACTIVE_SOURCE_BINDINGS.get() or {})
    active[owner] = normalized
    token = _ACTIVE_SOURCE_BINDINGS.set(active)
    try:
        yield None
    finally:
        _ACTIVE_SOURCE_BINDINGS.reset(token)
