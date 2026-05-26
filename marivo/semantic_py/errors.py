"""Error hierarchy for marivo.semantic_py v1.1.

All errors flow through a single raise helper and share a common
string template.  ErrorKind enum centralises every known error kind
with its associated hint factory.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, NoReturn

from marivo.semantic_py.ir import SourceLocation

__all__ = [
    "HINTS",
    "ErrorKind",
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
    MISSING_MODEL = "missing_model"
    MISSING_DATASETS = "missing_datasets"
    INVALID_REF = "invalid_ref"
    INVALID_DECOMPOSITION = "invalid_decomposition"
    INVALID_COMPONENT_BODY = "invalid_component_body"
    INVALID_COMPONENT_NAME = "invalid_component_name"
    OUTSIDE_LOADER_CONTEXT = "outside_loader_context"
    OUTSIDE_DERIVED_METRIC_BODY = "outside_derived_metric_body"
    METRIC_BODY_NOT_SINGLE_RETURN = "metric_body_not_single_return"
    INVALID_AI_CONTEXT = "invalid_ai_context"
    SQL_ESCAPE_HATCH = "sql_escape_hatch"

    # assembly-time
    MODEL_FILE_MISSING = "model_file_missing"
    MODEL_FILE_MISMATCH = "model_file_mismatch"
    MISSING_DATASET_REF = "missing_dataset_ref"
    MISSING_FIELD_REF = "missing_field_ref"
    MISSING_METRIC_REF = "missing_metric_ref"
    CROSS_MODEL_CYCLE = "cross_model_cycle"
    HOUR_TIME_FIELD_PREFIX_MISSING = "hour_time_field_prefix_missing"
    INVALID_RELATIONSHIP_ENDPOINT = "invalid_relationship_endpoint"
    ORGANIZATION_ERROR = "organization_error"
    INVALID_PROJECT = "invalid_project"

    # runtime
    METRIC_NOT_FOUND = "metric_not_found"
    MATERIALIZE_FAILED = "materialize_failed"
    BACKEND_MISMATCH = "backend_mismatch"
    COMPILE_ERROR = "compile_error"
    CROSS_DATASOURCE_NOT_SUPPORTED = "cross_datasource_not_supported"

    # parity
    SOURCE_SQL_MISSING = "source_sql_missing"
    UNVERIFIED_PROVENANCE = "unverified_provenance"
    PARITY_VALUE_MISMATCH = "parity_value_mismatch"
    PARITY_NOT_SCALAR = "parity_not_scalar"


# ---------------------------------------------------------------------------
# Hint factories
# ---------------------------------------------------------------------------


def _hint_duplicate_name(**_kwargs: Any) -> str:
    return "Each name must be unique within its model scope."


def _hint_missing_model(**_kwargs: Any) -> str:
    return "Call ms.model(name=...) before declaring semantic objects."


def _hint_missing_datasets(**_kwargs: Any) -> str:
    return "Base metrics must declare datasets=[...]. Derived metrics must have components in decomposition."


def _hint_invalid_ref(**_kwargs: Any) -> str:
    return "Use ref objects returned by ms.datasource/dataset/field/time_field/metric decorators."


def _hint_invalid_decomposition(**_kwargs: Any) -> str:
    return "Use ms.sum(), ms.ratio(numerator=..., denominator=...), or ms.weighted_average(numerator=..., weight=...)."


def _hint_invalid_component_body(**_kwargs: Any) -> str:
    return "Derived metric bodies may only use ms.component('<name>') with arithmetic operators."


def _hint_invalid_component_name(**_kwargs: Any) -> str:
    return "ms.component() name must be one of the keys declared in the decomposition (e.g. 'numerator', 'denominator', 'weight')."


def _hint_outside_loader_context(**_kwargs: Any) -> str:
    return "Decorators can only be used inside files loaded by the semantic project loader."


def _hint_outside_derived_metric_body(**_kwargs: Any) -> str:
    return "ms.component() can only be called inside a derived metric function body."


def _hint_metric_body_not_single_return(**_kwargs: Any) -> str:
    return "Metric function body must contain exactly one return expression."


def _hint_invalid_ai_context(**_kwargs: Any) -> str:
    return "ai_context must be a dict with keys from: business_definition, guardrails, synonyms, examples, instructions, owner_notes."


def _hint_sql_escape_hatch(**_kwargs: Any) -> str:
    return "Raw SQL expressions are not allowed in metric bodies. Use source_sql on the decorator instead."


def _hint_model_file_missing(**_kwargs: Any) -> str:
    return "Each model directory must contain a _model.py file."


def _hint_model_file_mismatch(**_kwargs: Any) -> str:
    return "The model name in _model.py must match the directory name."


def _hint_missing_dataset_ref(**_kwargs: Any) -> str:
    return "Reference a registered dataset by passing the DatasetRef or its semantic_id."


def _hint_missing_field_ref(**_kwargs: Any) -> str:
    return "Reference a registered field by passing the FieldRef or its semantic_id."


def _hint_missing_metric_ref(**_kwargs: Any) -> str:
    return "Reference a registered metric by passing the MetricRef or its semantic_id."


def _hint_cross_model_cycle(**_kwargs: Any) -> str:
    return "Remove circular references between models."


def _hint_hour_time_field_prefix_missing(**_kwargs: Any) -> str:
    return "Hour time fields require a required_prefix pointing to a day-level time field."


def _hint_invalid_relationship_endpoint(**_kwargs: Any) -> str:
    return "Relationship from_/to_ must reference a registered dataset."


def _hint_organization_error(**_kwargs: Any) -> str:
    return "Check the project directory structure and file organization."


def _hint_invalid_project(**_kwargs: Any) -> str:
    return "Ensure the project root contains .marivo/semantic/."


def _hint_metric_not_found(**_kwargs: Any) -> str:
    return "Check the metric name and ensure the project is loaded."


def _hint_materialize_failed(**_kwargs: Any) -> str:
    return "Check the metric function, referenced datasets, and backend factory."


def _hint_backend_mismatch(**_kwargs: Any) -> str:
    return "Ensure the backend dialect matches the datasource backend_type."


def _hint_compile_error(**_kwargs: Any) -> str:
    return "Check the metric expression for unsupported operations."


def _hint_cross_datasource_not_supported(**_kwargs: Any) -> str:
    return "All datasets in a metric must share the same datasource."


def _hint_source_sql_missing(**_kwargs: Any) -> str:
    return "Add source_sql to the metric decorator before running parity checks."


def _hint_unverified_provenance(**_kwargs: Any) -> str:
    return "Run parity_check() to verify metric results against source SQL."


def _hint_parity_value_mismatch(**_kwargs: Any) -> str:
    return "Metric value differs from source SQL. Check the metric expression for semantic drift."


def _hint_parity_not_scalar(**_kwargs: Any) -> str:
    return "Parity checks require exactly one scalar result value."


HINTS: dict[ErrorKind, Callable[..., str]] = {
    ErrorKind.DUPLICATE_NAME: _hint_duplicate_name,
    ErrorKind.MISSING_MODEL: _hint_missing_model,
    ErrorKind.MISSING_DATASETS: _hint_missing_datasets,
    ErrorKind.INVALID_REF: _hint_invalid_ref,
    ErrorKind.INVALID_DECOMPOSITION: _hint_invalid_decomposition,
    ErrorKind.INVALID_COMPONENT_BODY: _hint_invalid_component_body,
    ErrorKind.INVALID_COMPONENT_NAME: _hint_invalid_component_name,
    ErrorKind.OUTSIDE_LOADER_CONTEXT: _hint_outside_loader_context,
    ErrorKind.OUTSIDE_DERIVED_METRIC_BODY: _hint_outside_derived_metric_body,
    ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN: _hint_metric_body_not_single_return,
    ErrorKind.INVALID_AI_CONTEXT: _hint_invalid_ai_context,
    ErrorKind.SQL_ESCAPE_HATCH: _hint_sql_escape_hatch,
    ErrorKind.MODEL_FILE_MISSING: _hint_model_file_missing,
    ErrorKind.MODEL_FILE_MISMATCH: _hint_model_file_mismatch,
    ErrorKind.MISSING_DATASET_REF: _hint_missing_dataset_ref,
    ErrorKind.MISSING_FIELD_REF: _hint_missing_field_ref,
    ErrorKind.MISSING_METRIC_REF: _hint_missing_metric_ref,
    ErrorKind.CROSS_MODEL_CYCLE: _hint_cross_model_cycle,
    ErrorKind.HOUR_TIME_FIELD_PREFIX_MISSING: _hint_hour_time_field_prefix_missing,
    ErrorKind.INVALID_RELATIONSHIP_ENDPOINT: _hint_invalid_relationship_endpoint,
    ErrorKind.ORGANIZATION_ERROR: _hint_organization_error,
    ErrorKind.INVALID_PROJECT: _hint_invalid_project,
    ErrorKind.METRIC_NOT_FOUND: _hint_metric_not_found,
    ErrorKind.MATERIALIZE_FAILED: _hint_materialize_failed,
    ErrorKind.BACKEND_MISMATCH: _hint_backend_mismatch,
    ErrorKind.COMPILE_ERROR: _hint_compile_error,
    ErrorKind.CROSS_DATASOURCE_NOT_SUPPORTED: _hint_cross_datasource_not_supported,
    ErrorKind.SOURCE_SQL_MISSING: _hint_source_sql_missing,
    ErrorKind.UNVERIFIED_PROVENANCE: _hint_unverified_provenance,
    ErrorKind.PARITY_VALUE_MISMATCH: _hint_parity_value_mismatch,
    ErrorKind.PARITY_NOT_SCALAR: _hint_parity_not_scalar,
}


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class SemanticError(Exception):
    """Base class for all semantic_py errors.

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

    def __init__(
        self,
        *,
        kind: str,
        message: str,
        refs: tuple[str, ...] = (),
        location: SourceLocation | None = None,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.kind = kind
        self.message = message
        self.semantic_refs = refs
        self.location = location
        self.hint = hint
        self.details = details or {}
        super().__init__(str(self))

    def __str__(self) -> str:
        lines: list[str] = [f"[{self.kind}] {self.message}"]
        if self.semantic_refs:
            lines.append(f"  refs: {', '.join(self.semantic_refs)}")
        if self.location is not None:
            lines.append(f"  at: {self.location.file}:{self.location.line}")
        if self.hint is not None:
            lines.append(f"  hint: {self.hint}")
        return "\n".join(lines)


class SemanticDecoratorError(SemanticError):
    """Error raised during decorator-time validation."""


class SemanticLoadError(SemanticError):
    """Error raised during assembly-time (loader Pass 2) validation."""


class SemanticRuntimeError(SemanticError):
    """Error raised during runtime operations (materialize, compile)."""


class SemanticParityError(SemanticError):
    """Error raised during parity checking."""


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


@dataclass(frozen=True)
class StructuredWarning:
    """Non-fatal warning produced during assembly validation.

    Frozen dataclass matching the spec's structure.
    """

    kind: Literal["string_ref", "unverified_provenance", "potentially_fragile_reference"]
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
) -> NoReturn:
    """Raise a structured SemanticError with hint from the HINTS registry."""
    if hint is None:
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
    )
