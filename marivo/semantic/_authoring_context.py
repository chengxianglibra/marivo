"""Loader-context integration and ref/location plumbing for semantic authoring.

Internal module: all symbols are private.  The public authoring surface is
re-exported from ``marivo.semantic.authoring``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from marivo.refs import (
    DatasourceKind,
    DomainKind,
    EntityKind,
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo.semantic._expression_binding import ExpressionBody
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    DimensionIR,
    DomainIR,
    EntityIR,
    MeasureIR,
    MetricIR,
    PeriodCalendarIR,
    RelationshipIR,
    SourceLocation,
    TemporalSetIR,
    WorkScheduleIR,
)
from marivo.semantic.loader import _LOADER_CTX, LoaderContext

if TYPE_CHECKING:
    from marivo.semantic.expressions import Entity


def _value_ref(value: object) -> Ref[SemanticKindTag] | None:
    from marivo.semantic.expressions import Dimension, Entity, Measure, Relationship, TimeField

    if isinstance(value, TimeField):
        value._require_native(operation="a source declaration lookup")
    if isinstance(value, (Dimension, Entity, Measure, Relationship, TimeField)):
        return value.ref.ref
    if isinstance(value, Ref):
        return value
    return None


DefinitionIR: TypeAlias = (
    DomainIR
    | EntityIR
    | DimensionIR
    | MeasureIR
    | MetricIR
    | RelationshipIR
    | PeriodCalendarIR
    | TemporalSetIR
    | WorkScheduleIR
)


@dataclass(frozen=True, slots=True)
class PendingDefinition:
    """One exact authoring identity, private IR definition, and optional body."""

    ref: Ref[SemanticKindTag]
    definition: DefinitionIR
    expression_body: ExpressionBody | None


_AUTHORING_FILES: set[str] = set()


def _register_authoring_file(path: str) -> None:
    """Register a module file as internal for caller-frame skipping."""
    _AUTHORING_FILES.add(path)


def _is_internal_caller_frame(frame: Any) -> bool:
    """Return whether a frame belongs to authoring or transparent instrumentation."""
    return (
        frame.f_code.co_filename in _AUTHORING_FILES
        or frame.f_globals.get("__name__") == "marivo.telemetry"
    )


def _require_ctx() -> LoaderContext:
    """Get the current LoaderContext or raise OutsideLoaderContextError."""
    ctx = _LOADER_CTX.get()
    if ctx is None:
        _raise(
            ErrorKind.OUTSIDE_LOADER_CONTEXT,
            "Physical capture and finalization require an active semantic model loader scope.",
            cls=SemanticDecoratorError,
            expected="an active physical declaration capture scope",
            received="no active loader scope",
        )
    return ctx


def _resolve_domain(explicit: Ref[DomainKind] | None, ctx: LoaderContext) -> str:
    """Resolve the domain name: explicit ref > default_domain > error."""
    if type(explicit) is Ref and explicit.kind is SemanticKind.DOMAIN:
        return explicit.path
    if explicit is not None:
        _raise(
            ErrorKind.INVALID_REF,
            "domain= accepts Ref[domain] from ms.domain(name=...). "
            "Do not pass a bare string such as 'sales'.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    if ctx.default_domain is not None:
        return ctx.default_domain
    _raise(
        ErrorKind.MISSING_DOMAIN,
        "No domain name specified and no default domain is set. "
        "Call ms.domain(name=...) before declaring semantic objects.",
        cls=SemanticDecoratorError,
    )


def _ir_kind(ir: Any) -> str:
    """Return a human-readable kind label for an IR object."""
    if isinstance(ir, DimensionIR):
        return "time dimension" if ir.is_time_dimension else "dimension"
    if isinstance(ir, MeasureIR):
        return "measure"
    if isinstance(ir, MetricIR):
        return "metric"
    if isinstance(ir, EntityIR):
        return "entity"
    if isinstance(ir, RelationshipIR):
        return "relationship"
    if isinstance(ir, PeriodCalendarIR):
        return "period calendar"
    if isinstance(ir, TemporalSetIR):
        return "temporal set"
    if isinstance(ir, WorkScheduleIR):
        return "work schedule"
    return type(ir).__name__


def _check_duplicate(
    ctx: LoaderContext,
    semantic_id: str,
    ir_type: type[
        EntityIR
        | DimensionIR
        | MeasureIR
        | MetricIR
        | RelationshipIR
        | PeriodCalendarIR
        | TemporalSetIR
        | WorkScheduleIR
    ],
) -> None:
    """Raise DUPLICATE_NAME if semantic_id already in pending_objects of the same kind.

    Also checks for cross-kind collisions between DimensionIR and MeasureIR,
    which share the entity-qualified namespace (``<domain>.<entity>.<field>``).
    """
    _cross_kinds: set[type[DimensionIR | MeasureIR]] = {DimensionIR, MeasureIR}
    for pending in ctx.pending_definitions:
        ir = pending.definition
        if not isinstance(
            ir,
            (
                EntityIR,
                DimensionIR,
                MeasureIR,
                MetricIR,
                RelationshipIR,
                PeriodCalendarIR,
                TemporalSetIR,
                WorkScheduleIR,
            ),
        ):
            continue
        if ir.semantic_id != semantic_id:
            continue
        existing_kind = _ir_kind(ir)
        if isinstance(ir, ir_type):
            _raise(
                ErrorKind.DUPLICATE_NAME,
                f"Name conflict: {semantic_id!r} is already declared as a {existing_kind}.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
            )
        if ir_type in _cross_kinds and type(ir) in _cross_kinds:
            _raise(
                ErrorKind.DUPLICATE_NAME,
                f"Name conflict: {semantic_id!r} is already claimed by a {existing_kind}. "
                f"Use a different name for this object.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
            )


def _caller_location() -> SourceLocation:
    """Best-effort source location from the caller's frame.

    Walks up past internal authoring module frames to find the first
    external caller (the user's code).  Reports the file/line of that
    call site.
    """
    frame = inspect.currentframe()
    try:
        if frame is not None:
            caller_frame = frame.f_back
            while caller_frame is not None:
                if not _is_internal_caller_frame(caller_frame):
                    filename = caller_frame.f_code.co_filename
                    lineno = caller_frame.f_lineno
                    return SourceLocation(file=filename, line=lineno)
                caller_frame = caller_frame.f_back
    except AttributeError:
        pass
    return SourceLocation(file="<unknown>", line=0)


def _user_caller_location() -> SourceLocation:
    """Best-effort source location of the user's call site.

    Walks past internal authoring and transparent telemetry wrapper frames and
    reports the file/line where the user called the public function.
    """
    frame = inspect.currentframe()
    try:
        if frame is not None:
            caller_frame = frame.f_back
            while caller_frame is not None:
                if not _is_internal_caller_frame(caller_frame):
                    return SourceLocation(
                        file=caller_frame.f_code.co_filename,
                        line=caller_frame.f_lineno,
                    )
                caller_frame = caller_frame.f_back
    except AttributeError:
        pass
    return SourceLocation(file="<unknown>", line=0)


def _require_ref_id(
    ref: object,
    *,
    parameter: str,
    expected: tuple[SemanticKind, ...],
) -> str:
    resolved = _value_ref(ref)
    if resolved is not None and resolved.kind in expected:
        return resolved.path
    expected_label = " or ".join(f"Ref[{kind.value}]" for kind in expected)
    received = getattr(ref, "key", ref)
    _raise(
        ErrorKind.INVALID_REF,
        f"{parameter} must be {expected_label}; got {type(ref).__name__}: {received!r}. "
        "Pass the Ref object returned by the semantic authoring call, import a declared "
        "Ref from another model, or use ref.<kind>('<path>') for an explicit "
        "forward or cross-file reference.",
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _domain_from_ref_id(ref_id: str) -> str:
    return ref_id.split(".", 1)[0]


def _entity_lookup(value: Entity | Ref[EntityKind], *, parameter: str) -> Ref[EntityKind]:
    from marivo.semantic.expressions import Entity

    _require_ref_id(value, parameter=parameter, expected=(SemanticKind.ENTITY,))
    if isinstance(value, Entity):
        from marivo.refs import ref as ref_factory

        return ref_factory.entity(value.ref.ref.path)
    return value


def _require_non_empty_column(column: str, *, semantic_id: str) -> str:
    if isinstance(column, str) and column:
        return column
    _raise(
        ErrorKind.INVALID_REF,
        f"{semantic_id!r}: column must be a non-empty string; got {column!r}.",
        refs=(semantic_id,),
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _column_accessor(column: str) -> Callable[[Any], Any]:
    def _accessor(table: Any) -> Any:
        return table[column]

    _accessor.__name__ = f"_marivo_column_{column}"
    return _accessor


def _resolve_datasource_ref(ref: Ref[DatasourceKind]) -> str:
    """Extract canonical datasource id from a datasource ref."""
    if type(ref) is Ref and ref.kind is SemanticKind.DATASOURCE:
        return ref.path
    _raise(
        ErrorKind.INVALID_REF,
        "ms.entity(datasource=...) accepts Ref[datasource] from a datasource spec's .ref "
        "or ref_factory.datasource('warehouse'). Do not pass a bare string.",
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _push_ir(
    ctx: LoaderContext,
    ref: Ref[SemanticKindTag],
    definition: DefinitionIR,
    expression_body: ExpressionBody | None,
) -> None:
    """Capture one definition before the physical dependency closure is complete."""
    from marivo.semantic.errors import SemanticExpressionError

    closed = ctx.finalized is not None
    for prior in ctx.prior_contexts:
        if prior.finalized is None:
            continue
        owner = definition.entity if isinstance(definition, (DimensionIR, MeasureIR)) else None
        if any(
            pending.ref == ref
            or (
                isinstance(pending.definition, EntityIR) and pending.definition.semantic_id == owner
            )
            for pending in prior.pending_definitions
        ):
            closed = True
    if closed:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_STAGE,
            message="Physical declaration capture is already finalized.",
            refs=(ref.key,),
            expected="all physical declarations captured before ms.finalize()",
            received=f"late declaration {ref.key}",
            action="Move this declaration and its dependencies before ms.finalize(); compose only resolved values after finalization.",
        )
    ctx.pending_definitions.append(
        PendingDefinition(
            ref=ref,
            definition=definition,
            expression_body=expression_body,
        )
    )


_register_authoring_file(__file__)
