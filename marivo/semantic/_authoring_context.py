"""Loader-context integration and ref/location plumbing for semantic authoring.

Internal module: all symbols are private.  The public authoring surface is
re-exported from ``marivo.semantic.authoring``.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from marivo._symbolic import DatasourceSymbol, DomainSymbol, EntitySymbol, native_symbol_ref
from marivo.refs import (
    EntityKind,
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo.semantic._expression_binding import ExpressionBody
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticDecoratorError,
    SemanticExpressionError,
    _raise,
)
from marivo.semantic.ir import (
    DimensionIR,
    DomainIR,
    EntityIR,
    MeasureIR,
    PeriodCalendarIR,
    RelationshipIR,
    SourceLocation,
    TemporalSetIR,
)
from marivo.semantic.loader import _LOADER_CTX, LoaderContext

if TYPE_CHECKING:
    from marivo.semantic.expressions import Entity


def _value_ref(value: object) -> Ref[SemanticKindTag] | None:
    from ibis.common.deferred import Deferred

    from marivo.semantic._deferred_semantics import consume_field
    from marivo.semantic.expressions import Dimension, Entity, Measure, Relationship, TimeField

    if isinstance(value, Deferred):
        native = native_symbol_ref(value)
        if native is not None:
            return native
        field = consume_field(value)
        if field.field_read is not None and (
            field.field_read.path or field.field_read.at is not None
        ):
            raise SemanticExpressionError(
                kind=ErrorKind.INVALID_TIME_PATH,
                message="A bound field cannot be used in physical authoring.",
                action="Use the native field lookup symbol in physical declarations; retain via only for its supported time= or predicate role.",
                expected="an unbound native field",
                received="a path-bound or anchored field",
            )
        return field.reference.ref

    if isinstance(value, (Dimension, TimeField, Measure)):
        value._require_native(operation="a source declaration lookup")
    if isinstance(value, (Dimension, Entity, Measure, Relationship, TimeField)):
        return (
            value._exact_ref.ref
            if isinstance(value, Entity)
            else value._parameter.ref.ref
            if isinstance(value, Relationship)
            else value.ref.ref
        )
    return None


DefinitionIR: TypeAlias = (
    DomainIR
    | EntityIR
    | DimensionIR
    | MeasureIR
    | RelationshipIR
    | PeriodCalendarIR
    | TemporalSetIR
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
            "Physical declaration capture requires an active semantic model loader scope.",
            cls=SemanticDecoratorError,
            expected="an active physical declaration capture scope",
            received="no active loader scope",
        )
    return ctx


def _resolve_domain(explicit: DomainSymbol | None, ctx: LoaderContext) -> str:
    """Resolve the domain name: explicit ref > default_domain > error."""
    key = native_symbol_ref(explicit)
    if key is not None and key.kind is SemanticKind.DOMAIN:
        return key.path
    if explicit is not None:
        _raise(
            ErrorKind.INVALID_REF,
            "domain= requires a native Domain symbol from ms.domain(name=...) "
            "or ms.ref.domain(...).",
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
    if isinstance(ir, EntityIR):
        return "entity"
    if isinstance(ir, RelationshipIR):
        return "relationship"
    if isinstance(ir, PeriodCalendarIR):
        return "period calendar"
    if isinstance(ir, TemporalSetIR):
        return "temporal set"
    return type(ir).__name__


def _check_duplicate(
    ctx: LoaderContext,
    semantic_id: str,
    ir_type: type[
        EntityIR | DimensionIR | MeasureIR | RelationshipIR | PeriodCalendarIR | TemporalSetIR
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
                RelationshipIR,
                PeriodCalendarIR,
                TemporalSetIR,
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
    """Return the first user source location above authoring and telemetry frames."""
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
    try:
        resolved = _value_ref(ref)
    except SemanticExpressionError as error:
        error.location = _caller_location()
        error.semantic_refs = (parameter, *error.semantic_refs)
        raise
    if resolved is not None and resolved.kind in expected:
        return resolved.path
    expected_label = " or ".join(f"native {kind.value} symbol" for kind in expected)
    received = repr(ref)
    _raise(
        ErrorKind.INVALID_REF,
        f"{parameter} must be {expected_label}; got {type(ref).__name__}: {received!r}. "
        "Pass the matching symbol returned by its constructor or ms.ref.<kind>('<path>'), "
        "or use the closed value already supported by this parameter. Composite "
        "expressions and identity-attribute recipes cannot identify a physical declaration.",
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _domain_from_ref_id(ref_id: str) -> str:
    return ref_id.split(".", 1)[0]


def _entity_lookup(value: Entity | EntitySymbol, *, parameter: str) -> Ref[EntityKind]:

    from marivo.refs import ref as ref_factory

    path = _require_ref_id(value, parameter=parameter, expected=(SemanticKind.ENTITY,))
    return ref_factory.entity(path)


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


def _resolve_datasource_ref(ref: DatasourceSymbol) -> str:
    """Extract canonical datasource id from a datasource ref."""
    key = native_symbol_ref(ref)
    if key is not None and key.kind is SemanticKind.DATASOURCE:
        return key.path
    _raise(
        ErrorKind.INVALID_REF,
        "ms.entity(datasource=...) requires a native Datasource symbol from a "
        "datasource spec's .ref or ms.ref.datasource('warehouse').",
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
    ctx.pending_definitions.append(
        PendingDefinition(
            ref=ref,
            definition=definition,
            expression_body=expression_body,
        )
    )


_register_authoring_file(__file__)
