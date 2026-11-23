"""Capture named and expression Entity declarations."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, overload

import ibis.expr.types as ibis_ir

from marivo._kernel.refs import SemanticKind
from marivo._kernel.refs import ref as ref_factory
from marivo._kernel.symbolic import (
    DatasourceSymbol,
    DomainSymbol,
    EntitySymbol,
    TimeDimensionSymbol,
    native_symbol_ref,
    symbol_factory,
)
from marivo.semantic._expression_binding import (
    compile_expression_body,
)
from marivo.semantic.authoring.context import (
    _caller_location,
    _check_duplicate,
    _push_ir,
    _register_authoring_file,
    _require_ctx,
    _resolve_datasource_ref,
    _resolve_domain,
)
from marivo.semantic.authoring.values import _build_ai_context
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticDecoratorError,
    _raise,
)
from marivo.semantic.expressions.inputs import (
    _OMITTED_TIME,
    _OmittedTime,
)
from marivo.semantic.expressions.ir import ExpressionBody
from marivo.semantic.ir import (
    CsvSourceIR,
    EntityIR,
    EntitySourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    SnapshotVersioningIR,
    TableSourceIR,
    ValidityVersioningIR,
)
from marivo.semantic.typing import AiContextValue
from marivo.telemetry import traced

if TYPE_CHECKING:
    from marivo.semantic.ir import SourceLocation
    from marivo.semantic.loading_context import LoaderContext

_register_authoring_file(__file__)


def _reject_named_entity_decorator(ctx: LoaderContext, location: SourceLocation) -> None:
    """Reject direct-constructor decorator syntax before Deferred can absorb it."""
    source = ctx._sources.get(location.file)
    if source is not None and location.line in source.named_decorator_lines:
        _raise(
            ErrorKind.ENTITY_CONSTRUCTOR_AS_DECORATOR,
            "ms.entity(name=...) returns a native Entity symbol and cannot decorate a definition.",
            cls=SemanticDecoratorError,
            location=location,
            expected="name = ms.entity(...) without a decorated function body, or @ms.entity(...) without name=",
            received="@ms.entity(name=...) applied to a definition",
            constraint_id=ConstraintId.ENTITY_CONSTRUCTOR_ASSIGNMENT,
        )


def _register_entity_ir(
    *,
    semantic_id: str,
    domain: str,
    name: str,
    python_symbol: str,
    datasource: DatasourceSymbol,
    source: EntitySourceIR,
    primary_key: tuple[str, ...],
    versioning: SnapshotVersioningIR | ValidityVersioningIR | None,
    default_time: str | None,
    ai_context: AiContextValue | None,
    location: SourceLocation,
    expression_body: ExpressionBody | None = None,
) -> EntitySymbol:
    """Validate one Entity declaration and push its IR into the loader context."""
    ctx = _require_ctx()
    _check_duplicate(ctx, semantic_id, EntityIR)
    if not isinstance(source, (TableSourceIR, ParquetSourceIR, CsvSourceIR, JsonSourceIR)):
        _raise(
            ErrorKind.INVALID_REF,
            "ms.entity(source=...) accepts md.table(...), md.parquet(...), md.csv(...), or md.json(...).",
            cls=SemanticDecoratorError,
            refs=(semantic_id,),
            constraint_id=ConstraintId.REF_SHAPE,
        )

    ds_ref = _resolve_datasource_ref(datasource)
    ai_ctx = _build_ai_context(ai_context)

    ir = EntityIR(
        semantic_id=semantic_id,
        domain=domain,
        name=name,
        datasource=ds_ref,
        source=source,
        primary_key=primary_key,
        ai_context=ai_ctx,
        python_symbol=python_symbol,
        location=location,
        versioning=versioning,
        default_time=default_time,
    )
    ref = ref_factory.entity(semantic_id)
    _push_ir(ctx, ref, ir, expression_body)

    return symbol_factory.entity(ref.path)


def _validate_entity_key(primary_key: list[str] | None) -> None:
    if primary_key is not None and (
        type(primary_key) is not list
        or any(type(column) is not str or not column.strip() for column in primary_key)
    ):
        _raise(
            ErrorKind.INVALID_REF,
            "entity primary_key must be a list of non-empty output column names.",
            cls=SemanticDecoratorError,
            expected="list[str] of non-empty output columns, or None for an unkeyed source",
            received=repr(primary_key),
            hint="List the complete stable Entity identity in primary_key; declare version columns separately.",
        )


def _entity_default_time(default_time: TimeDimensionSymbol | _OmittedTime) -> str | None:
    time_path: str | None = None
    if not isinstance(default_time, _OmittedTime):
        time_ref = native_symbol_ref(default_time)
        if time_ref is None or time_ref.kind is not SemanticKind.TIME_DIMENSION:
            _raise(
                ErrorKind.INVALID_REF,
                "Entity default_time must identify one native TimeDimension.",
                cls=SemanticDecoratorError,
                expected="a native TimeDimension symbol on this Entity",
                received=repr(default_time),
                hint="Pass ms.ref.time_dimension(...) for a field on this Entity, or omit default_time.",
            )
        time_path = time_ref.path
    return time_path


@overload
def entity(
    *,
    name: str,
    datasource: DatasourceSymbol,
    source: EntitySourceIR,
    primary_key: list[str] | None = ...,
    versioning: SnapshotVersioningIR | ValidityVersioningIR | None = ...,
    default_time: TimeDimensionSymbol | _OmittedTime = ...,
    domain: DomainSymbol | None = ...,
    ai_context: AiContextValue | None = ...,
) -> EntitySymbol: ...


@overload
def entity(
    *,
    name: None = None,
    datasource: DatasourceSymbol,
    source: EntitySourceIR,
    primary_key: list[str] | None = ...,
    versioning: SnapshotVersioningIR | ValidityVersioningIR | None = ...,
    default_time: TimeDimensionSymbol | _OmittedTime = ...,
    domain: DomainSymbol | None = ...,
    ai_context: AiContextValue | None = ...,
) -> Callable[[Callable[[ibis_ir.Table], ibis_ir.Table]], EntitySymbol]: ...


@traced(surface="semantic", operation="entity.define")
def entity(
    *,
    name: str | None = None,
    datasource: DatasourceSymbol,
    source: EntitySourceIR,
    primary_key: list[str] | None = None,
    versioning: SnapshotVersioningIR | ValidityVersioningIR | None = None,
    default_time: TimeDimensionSymbol | _OmittedTime = _OMITTED_TIME,
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> EntitySymbol | Callable[[Callable[[ibis_ir.Table], ibis_ir.Table]], EntitySymbol]:
    """Declare an entity directly or over one Table expression of its source.

    With an explicit ``name`` this declares the entity immediately and returns
    a typed native Entity lookup symbol. The loader binds the complete model
    before Catalog .get() returns an immutable Entity. With ``name`` omitted the call returns a decorator:
    the decorated function must take exactly one Table parameter and return
    exactly one Ibis Table expression derived from that parameter. The Entity
    name comes from the function name. The Entity output schema is the
    returned Table schema for the expression form and the source schema for
    the direct form.

    Args:
        name: Entity name for the direct form. Omit to declare through a
            decorator whose function name becomes the Entity name.
        datasource: Datasource ref returned by ``ms.ref.datasource(...)``.
        source: Structured physical source, usually ``md.table(...)``,
            ``md.parquet(...)``, ``md.csv(...)``, or ``md.json(...)``.
            Table ``columns`` maps output names to physical column names;
            types come from source metadata when execution needs them.
            Use ``md.inspect(...)`` to inspect current names and types.
        primary_key: Optional stable Entity output identity columns; version coordinates
            belong only in versioning. Naive timestamps retain wall-clock values and
            precision without a timezone. TimeDimension parsers do not rewrite identity.
        versioning: Explicit snapshot or validity representation; never inferred from source partitions.
        default_time: A native TimeDimension symbol on this Entity. A forward reference
            from ``ms.ref.time_dimension(...)`` is allowed. Source reductions inherit this
            field when time is omitted; this setting is independent of versioning.
        domain: Override the active domain namespace with a ``DomainSymbol`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A native Entity lookup symbol for the direct form, or a decorator
        returning the same symbol role for an expression body. Loader binding
        closes its complete facts before Catalog .get() returns an Entity.

    Raises:
        SemanticDecoratorError: ``datasource`` is not a datasource ref, the
            resolved name collides with another object, or ``source`` is not
            an entity source. SemanticLoadError: the decorated body violates
            the single-expression Table contract.

    Example:
        >>> orders = ms.entity(
        ...     name="orders",
        ...     datasource=ms.ref.datasource("warehouse"),
        ...     source=md.table("orders", columns={"order_id": "id", "amount": "total"}),
        ...     primary_key=["order_id"],
        ... )

    Constraints:
        Declare this constructor inside a semantic project loaded by ``ms.load``.
        Entity bodies use the injected Table, inline literals, deterministic public
        ibis root symbols and typed scalar builtin UDFs from ``ibis.udf.scalar.builtin``
        with at least one argument and concrete argument/return types.
        UDF Python bodies are not executed. Arbitrary helpers, Python/Pandas/Arrow UDFs,
        clock/random symbols and Ibis submodules are not Entity body symbols.
        Use ``raw['column'].cast('decimal(20,0)')`` for the full UInt64 range, or
        ``.cast('int64')`` only for values within signed int64. ``ibis.dtype`` is supported.
        Identity and direct field columns name projected output aliases.
        >>> @ms.entity(
        ...     datasource=ms.ref.datasource("warehouse"),
        ...     source=md.table("order_changes"),
        ...     primary_key=["dt", "order_id"],
        ... )
        ... def daily_orders(raw):
        ...     '''One latest non-deleted order per day and order ID.'''
        ...     return raw.filter(~raw["is_deleted"])

    Capture constraints: The result is an immutable native lookup symbol. It
    supports forward composition; the loader validates and binds the complete
    model before the Catalog returns closed values for analysis.
    """
    ctx = _require_ctx()
    _validate_entity_key(primary_key)
    resolved_domain = _resolve_domain(domain, ctx)
    time_path = _entity_default_time(default_time)

    def register(
        entity_name: str, location: SourceLocation, body: ExpressionBody | None = None
    ) -> EntitySymbol:
        return _register_entity_ir(
            semantic_id=f"{resolved_domain}.{entity_name}",
            domain=resolved_domain,
            name=entity_name,
            python_symbol=entity_name,
            datasource=datasource,
            source=source,
            primary_key=tuple(primary_key) if primary_key else (),
            versioning=versioning,
            default_time=time_path,
            ai_context=ai_context,
            location=location,
            expression_body=body,
        )

    if name is not None:
        location = _caller_location()
        _reject_named_entity_decorator(ctx, location)
        return register(name, location)

    def decorator(fn: Callable[[ibis_ir.Table], ibis_ir.Table]) -> EntitySymbol:
        location = _caller_location()
        target = ref_factory.entity(f"{resolved_domain}.{fn.__name__}")
        body = compile_expression_body(fn, owning_ref=target, body_kind="entity_table")
        return register(fn.__name__, location, body)

    return decorator
