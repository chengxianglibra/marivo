"""Error hierarchy for marivo.semantic v1.1.

All errors flow through a single raise helper and share a common
string template.  ErrorKind enum centralises every known error kind
with its associated hint factory.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, NoReturn

from marivo.semantic.constraints import (
    ConstraintId,
    default_constraint_for_error_kind,
    default_hint_for_error_kind,
    get_constraint,
)
from marivo.semantic.ir import SourceLocation

__all__ = [
    "HINTS",
    "ErrorKind",
    "LadderOrderError",
    "SemanticDecoratorError",
    "SemanticError",
    "SemanticLoadError",
    "SemanticLoadFailed",
    "SemanticParityError",
    "SemanticRuntimeError",
    "StructuredWarning",
    "WarningKind",
    "_raise",
]


# ---------------------------------------------------------------------------
# ErrorKind enum
# ---------------------------------------------------------------------------


class ErrorKind(StrEnum):
    """Canonical error kind identifiers.

    Grouped by phase (decorator-time, assembly-time, runtime, parity).
    Every ``SemanticError.kind`` must be a member of this enum.
    """

    # decorator-time
    DUPLICATE_NAME = "duplicate_name"
    MISSING_DOMAIN = "missing_domain"
    MISSING_ENTITIES = "missing_entities"
    INVALID_REF = "invalid_ref"
    INVALID_COMPOSITION = "invalid_composition"
    INVALID_COMPONENT_BODY = "invalid_component_body"
    OUTSIDE_LOADER_CONTEXT = "outside_loader_context"
    METRIC_BODY_NOT_SINGLE_RETURN = "metric_body_not_single_return"
    INVALID_AI_CONTEXT = "invalid_ai_context"
    SQL_ESCAPE_HATCH = "sql_escape_hatch"
    IBIS_ATTR_SHADOW = "ibis_attr_shadow"

    # assembly-time
    DOMAIN_FILE_MISSING = "domain_file_missing"
    DOMAIN_FILE_MISMATCH = "domain_file_mismatch"
    MISSING_ENTITY_REF = "missing_entity_ref"
    MISSING_DIMENSION_REF = "missing_dimension_ref"
    MISSING_METRIC_REF = "missing_metric_ref"
    CROSS_MODEL_CYCLE = "cross_model_cycle"

    INVALID_RELATIONSHIP_ENDPOINT = "invalid_relationship_endpoint"
    ORGANIZATION_ERROR = "organization_error"
    INVALID_PROJECT = "invalid_project"
    MISSING_METRIC_ADDITIVITY = "missing_metric_additivity"
    MISSING_METRIC_ROOT_ENTITY = "missing_metric_root_entity"
    INVALID_METRIC_ROOT_ENTITY = "invalid_metric_root_entity"
    UNKNOWN_MEASURE = "unknown_measure"
    MISSING_MEASURE_ADDITIVITY = "missing_measure_additivity"
    INVALID_MEASURE_AGGREGATION = "invalid_measure_aggregation"
    INCOMMENSURABLE_LINEAR_UNITS = "incommensurable_linear_units"
    INVALID_VERIFICATION_MODE = "invalid_verification_mode"
    INVALID_ENTITY_VERSIONING = "invalid_entity_versioning"
    NON_ROOT_METRIC_AGGREGATE = "non_root_metric_aggregate"
    INVALID_METRIC_FANOUT_POLICY = "invalid_metric_fanout_policy"
    DERIVED_METRIC_FANOUT_POLICY = "derived_metric_fanout_policy"

    DUPLICATE_DEFAULT_TIME_DIMENSION = "duplicate_default_time_dimension"
    INVALID_SAMPLE_INTERVAL = "invalid_sample_interval"
    INVALID_TIME_FOLD = "invalid_time_fold"
    TIME_FOLD_REQUIRES_SEMI_ADDITIVE = "time_fold_requires_semi_additive"
    TIME_FOLD_REQUIRES_SAMPLED_TIME_FIELD = "time_fold_requires_sampled_time_field"
    MISSING_TIME_FOLD = "missing_time_fold"
    MISSING_STATUS_TIME_DIMENSION = "missing_status_time_dimension"
    INVALID_STATUS_TIME_DIMENSION = "invalid_status_time_dimension"

    # runtime
    NOT_FOUND = "not_found"
    ENTITY_NOT_FOUND = "entity_not_found"
    DIMENSION_NOT_FOUND = "dimension_not_found"
    METRIC_NOT_FOUND = "metric_not_found"
    MATERIALIZE_FAILED = "materialize_failed"
    BACKEND_MISMATCH = "backend_mismatch"
    COMPILE_ERROR = "compile_error"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"
    CROSS_DATASOURCE_NOT_SUPPORTED = "cross_datasource_not_supported"
    BACKEND_FACTORY_REQUIRED = "backend_factory_required"
    INSPECT_SOURCE_REQUIRED = "inspect_source_required"
    PROJECT_NOT_LOADED = "project_not_loaded"
    LADDER_ORDER = "ladder_order"

    # catalog
    UNSUPPORTED_KIND = "unsupported_kind"
    UNSUPPORTED_LIST_PARENT = "unsupported_list_parent"
    CONFLICTING_PARAMETERS = "conflicting_parameters"

    # parity
    PROVENANCE_DIALECT_MISSING = "provenance_dialect_missing"
    UNVERIFIED_PROVENANCE = "unverified_provenance"
    PARITY_VALUE_MISMATCH = "parity_value_mismatch"
    PARITY_NOT_SCALAR = "parity_not_scalar"


# ---------------------------------------------------------------------------
# Catalog-backed hint factories
# ---------------------------------------------------------------------------


def _hint_from_catalog(kind: ErrorKind, **_kwargs: Any) -> str:
    hint = default_hint_for_error_kind(kind.value)
    if hint is not None:
        return hint
    return "Run ms.help('constraints') to inspect semantic constraints."


HINTS: dict[ErrorKind, Callable[..., str]] = {
    kind: (lambda _kind=kind, **kwargs: _hint_from_catalog(_kind, **kwargs)) for kind in ErrorKind
}


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class SemanticError(Exception):
    """Base class for all semantic errors.

    Shared template for ``__str__``::

        [kind] message
          refs: ref1, ref2
          at: file:line
          hint: ...
    """

    kind: str
    semantic_refs: tuple[str, ...]
    location: SourceLocation | None
    hint: str | None
    details: dict[str, Any]
    constraint_id: str | None

    def __init__(
        self,
        *,
        kind: str,
        message: str,
        refs: tuple[str, ...] = (),
        location: SourceLocation | None = None,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
        constraint_id: ConstraintId | str | None = None,
    ) -> None:
        if constraint_id is None:
            default_constraint = default_constraint_for_error_kind(kind)
            constraint_id = default_constraint.id if default_constraint is not None else None
        constraint = get_constraint(constraint_id) if constraint_id is not None else None
        if hint is None and constraint is not None:
            hint = constraint.hint
        self.kind = kind
        self.message = message
        self.semantic_refs = refs
        self.location = location
        self.hint = hint
        self.details = details or {}
        self.constraint_id = str(constraint_id) if constraint_id is not None else None
        super().__init__(str(self))

    def __str__(self) -> str:
        lines: list[str] = [f"[{self.kind}] {self.message}"]
        if self.semantic_refs:
            lines.append(f"  refs: {', '.join(self.semantic_refs)}")
        if self.location is not None:
            lines.append(f"  at: {self.location.file}:{self.location.line}")
        if self.hint is not None:
            lines.append(f"  hint: {self.hint}")
        dym = self.details.get("did_you_mean")
        if isinstance(dym, list) and dym:
            lines.append(f"  Did you mean: {', '.join(dym)}")
        return "\n".join(lines)


class SemanticDecoratorError(SemanticError):
    """Error raised during decorator-time validation."""


class SemanticLoadError(SemanticError):
    """Error raised during assembly-time (loader Pass 2) validation."""


class SemanticRuntimeError(SemanticError):
    """Error raised during runtime operations (materialize, compile)."""


class SemanticParityError(SemanticError):
    """Error raised during parity checking."""


class LadderOrderError(SemanticRuntimeError):
    """Raised when a prepare_* call requires a prerequisite verify_object that hasn't been completed."""


class SemanticLoadFailed(Exception):  # noqa: N818
    """Raised when reader methods are called on an errored project.

    Wraps one or more SemanticError instances that prevented the
    project from reaching the ready state.
    """

    def __init__(self, errors: Sequence[SemanticError]) -> None:
        self.errors = tuple(errors)
        joined = "; ".join(str(error) for error in self.errors)
        super().__init__(joined)


# ---------------------------------------------------------------------------
# Warning types
# ---------------------------------------------------------------------------


class WarningKind(StrEnum):
    """Canonical warning kind identifiers for non-fatal issues."""

    STRING_REF = "string_ref"
    UNVERIFIED_PROVENANCE = "unverified_provenance"
    POTENTIALLY_FRAGILE_REFERENCE = "potentially_fragile_reference"
    TIME_DIMENSION_PUSHDOWN_ADVISORY = "time_dimension_pushdown_advisory"
    TIME_DIMENSION_DTYPE_ADVISORY = "time_dimension_dtype_advisory"
    FILTERED_DOMAIN_REF = "filtered_domain_ref"


@dataclass(frozen=True)
class StructuredWarning:
    """Non-fatal warning produced during assembly validation.

    Frozen dataclass matching the spec's structure.
    """

    kind: Literal[
        "string_ref",
        "unverified_provenance",
        "potentially_fragile_reference",
        "time_dimension_pushdown_advisory",
        "time_dimension_dtype_advisory",
        "filtered_domain_ref",
    ]
    message: str
    refs: tuple[str, ...]
    location: SourceLocation | None

    def __str__(self) -> str:
        lines: list[str] = [f"[{self.kind}] {self.message}"]
        if self.refs:
            lines.append(f"  refs: {', '.join(self.refs)}")
        if self.location is not None:
            lines.append(f"  at: {self.location.file}:{self.location.line}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"StructuredWarning(kind={self.kind!r}, message={self.message!r}, "
            f"refs={self.refs!r}, location={self.location!r})"
        )


# ---------------------------------------------------------------------------
# Single raise helper
# ---------------------------------------------------------------------------


def _raise(
    kind: ErrorKind,
    message: str,
    *,
    cls: type[SemanticError] = SemanticDecoratorError,
    refs: Sequence[str] = (),
    location: SourceLocation | None = None,
    hint: str | None = None,
    details: dict[str, Any] | None = None,
    constraint_id: ConstraintId | str | None = None,
) -> NoReturn:
    """Raise a structured SemanticError with hint from the HINTS registry."""
    if hint is None:
        constraint = get_constraint(constraint_id) if constraint_id is not None else None
        if constraint is not None:
            hint = constraint.hint
        else:
            hint_fn = HINTS.get(kind)
            if hint_fn is not None:
                hint = hint_fn()
    raise cls(
        kind=kind.value,
        message=message,
        refs=tuple(refs),
        location=location,
        hint=hint,
        details=details,
        constraint_id=constraint_id,
    )
