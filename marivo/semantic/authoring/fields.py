"""Entity-scoped field decorators for semantic authoring.

Internal module: public symbols are re-exported from
``marivo.semantic.authoring``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import ibis.expr.types as ibis_ir
from ibis.common.deferred import Deferred

from marivo._definition.deferred import DeclarationRecord, declare, resolver_of
from marivo._definition.types import SourceLocation as DefinitionSourceLocation
from marivo._kernel.input_context import captured_context
from marivo._kernel.refs import SemanticKind, _validate_segment
from marivo._kernel.refs import ref as ref_factory
from marivo._kernel.symbolic import (
    DimensionSymbol,
    DomainSymbol,
    EntitySymbol,
    MeasureSymbol,
    TimeDimensionSymbol,
    symbol_factory,
)
from marivo.semantic._declarative_expression_compiler import construct_measure_expression
from marivo.semantic._expression_binding import compile_expression_body
from marivo.semantic._expression_carrier import _merge
from marivo.semantic._expression_values import NumericRow, RowExpression
from marivo.semantic.authoring.context import (
    _caller_location,
    _check_duplicate,
    _domain_from_ref_id,
    _entity_lookup,
    _push_ir,
    _register_authoring_file,
    _require_ctx,
    _require_non_empty_column,
    _require_ref_id,
    _resolve_domain,
)
from marivo.semantic.authoring.measure_policy import declare_measure
from marivo.semantic.authoring.retired import reject_parameters
from marivo.semantic.authoring.validation import (
    _compute_column_hash,
    _normalize_additivity,
    _normalize_sample_interval_value,
    _validate_time_parse,
    _validate_unit,
)
from marivo.semantic.authoring.values import _build_ai_context
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticDecoratorError,
    SemanticExpressionError,
    _raise,
)
from marivo.semantic.expressions.inputs import (
    _as_deferred,
)
from marivo.semantic.expressions.ir import ExpressionBody
from marivo.semantic.expressions.objects import TimeField
from marivo.semantic.expressions.objects import _Entity as Entity
from marivo.semantic.ir import (
    DimensionIR,
    DimensionKind,
    MeasureIR,
    SemanticParse,
)
from marivo.semantic.policies import (
    AdditiveAllV1,
    AdditiveOverV1,
    AdditivityPolicy,
    NonAdditiveV1,
)
from marivo.semantic.typing import AiContextValue
from marivo.telemetry import traced


@traced(surface="semantic", operation="dimension.from_column")
def dimension_column(
    *,
    name: str,
    entity: Entity | EntitySymbol,
    column: str,
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> DimensionSymbol:
    """Declare a categorical dimension directly from one physical column.

    Args:
        name: Semantic dimension name.
        entity: Entity symbol returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        column: Physical source column name to read with bracket access.
        domain: Override the active domain namespace with a ``DomainSymbol`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with
            business meaning and agent-facing guidance.

    Returns:
        A native Dimension lookup symbol supporting predicates and count reductions.

    Constraints:
        Use ``@ms.dimension(...)`` when the dimension is an expression over one
        or more columns. This helper is only for direct physical columns.

    Example:
        >>> orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
        >>> region = ms.dimension_column(name="region", entity=orders, column="region")

    Capture constraints: The result is an immutable native lookup symbol. It
    supports forward composition; the loader validates and binds the complete
    model before the Catalog returns closed values for analysis.
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    entity_ref = _entity_lookup(entity, parameter="entity")
    entity_id = entity_ref.path
    obj_name = name
    semantic_id = f"{entity_id}.{obj_name}"
    ref = ref_factory.dimension(semantic_id)
    column_name = _require_non_empty_column(column, semantic_id=semantic_id)
    entity_domain = _domain_from_ref_id(entity_id)
    if entity_domain != resolved_domain:
        _raise(
            ErrorKind.INVALID_REF,
            f"Dimension {semantic_id!r} belongs to entity in domain {entity_domain!r}, "
            f"but the active domain is {resolved_domain!r}.",
            cls=SemanticDecoratorError,
            refs=(semantic_id,),
            constraint_id=ConstraintId.REF_SHAPE,
        )
    _check_duplicate(ctx, semantic_id, DimensionIR)
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()
    ir = DimensionIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        entity=entity_id,
        name=obj_name,
        ai_context=ai_ctx,
        is_time_dimension=False,
        kind=DimensionKind.CATEGORICAL,
        python_symbol=obj_name,
        location=location,
        body_ast_hash=_compute_column_hash(column_name),
        source_column=column_name,
    )
    _push_ir(ctx, ref, ir, ExpressionBody.for_column(column_name))
    return symbol_factory.dimension(ref.path)


@traced(surface="semantic", operation="dimension.define")
def dimension(
    *,
    name: str | None = None,
    entity: Entity | EntitySymbol,
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[[ibis_ir.Table], ibis_ir.Value]], DimensionSymbol]:
    """Declare a categorical dimension whose body returns an ibis expression over its entity.

    The decorated function takes the entity table and returns a single
    expression (fresh sequential local bindings and one final return). Use this for both raw columns and derived
    expressions (e.g. ``table.region``).

    For quantitative expressions, use ``@ms.measure(additivity=...)``
    instead.

    Args:
        name: Dimension name. Defaults to the function name.
        entity: Owning entity ref returned by ``ms.entity(...)``.
        domain: Override the active domain namespace with a ``DomainSymbol`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A decorator returning a native Dimension lookup symbol.

    Raises:
        SemanticDecoratorError: ``entity`` is unknown, ``name`` collides, or the
            body violates the AST whitelist.

    Example:
        >>> @ms.dimension(entity=orders)
        ... def region(orders_table):
        ...     return orders_table.region

    Capture constraints: The result is an immutable native lookup symbol. It
    supports forward composition; the loader validates and binds the complete
    model before the Catalog returns closed values for analysis.
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    def decorator(fn: Callable[[ibis_ir.Table], ibis_ir.Value]) -> DimensionSymbol:
        obj_name = name or fn.__name__
        entity_ref = _require_ref_id(
            entity,
            parameter="entity",
            expected=(SemanticKind.ENTITY,),
        )
        semantic_id = f"{entity_ref}.{obj_name}"
        ref = ref_factory.dimension(semantic_id)
        entity_domain = entity_ref.split(".", 1)[0]
        if entity_domain != resolved_domain:
            _raise(
                ErrorKind.INVALID_REF,
                f"Dimension {semantic_id!r} belongs to entity in domain {entity_domain!r}, "
                f"but the active domain is {resolved_domain!r}.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
                constraint_id=ConstraintId.REF_SHAPE,
            )
        _check_duplicate(ctx, semantic_id, DimensionIR)

        expression_body = compile_expression_body(
            fn,
            owning_ref=ref,
            ordered_entity_refs=(_entity_lookup(entity, parameter="entity"),),
        )
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()

        ir = DimensionIR(
            semantic_id=semantic_id,
            domain=resolved_domain,
            entity=entity_ref,
            name=obj_name,
            ai_context=ai_ctx,
            is_time_dimension=False,
            kind=DimensionKind.CATEGORICAL,
            python_symbol=fn.__name__,
            location=location,
            body_ast_hash=expression_body.body_ast_hash,
        )
        _push_ir(ctx, ref, ir, expression_body)
        return symbol_factory.dimension(ref.path)

    return decorator


@reject_parameters("status_time_fold", "fold", "empty", "time_via")
@traced(surface="semantic", operation="measure.from_column")
def measure_column(
    *,
    name: str,
    entity: Entity | EntitySymbol,
    column: str,
    additivity: AdditivityPolicy,
    status_time_dimension: TimeField | Deferred | None = None,
    unit: str | None = None,
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> MeasureSymbol:
    """Declare a quantitative measure directly from one physical column.

    Args:
        name: Semantic measure name.
        entity: Entity symbol returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        column: Physical source column name to read with bracket access.
        additivity: Closed policy from ``ms.additive_over``, ``ms.additive_except``, ``ms.additive_all`` or ``ms.non_additive``.
        status_time_dimension: Optional business status-time dimension.
        unit: UCUM unit token such as ``"CNY"``, ``"USD"``, ``"%"``, or ``"1"``.
        domain: Override the active domain namespace with a ``DomainSymbol`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with
            business meaning and agent-facing guidance.

    Returns:
        A native Measure lookup symbol for row composition.

    Constraints:
        Use ``@ms.measure(...)`` to compose existing semantic quantities. Bind a
        physical or Entity-derived numeric column with this helper. The same
        column may also have a ``dimension_column`` declaration with a different
        semantic name; neither declaration converts the other role.

    Example:
        >>> orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
        >>> amount = ms.measure_column(
        ...     name="amount", entity=orders, column="amount",
        ...     additivity=ms.additive_all(), unit="CNY",
        ... )

    Capture constraints: The result is an immutable native lookup symbol. It
    supports forward composition; the loader validates and binds the complete
    model before the Catalog returns closed values for analysis.
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    entity_ref = _entity_lookup(entity, parameter="entity")
    entity_id = entity_ref.path
    obj_name = name
    semantic_id = f"{entity_id}.{obj_name}"
    ref = ref_factory.measure(semantic_id)
    column_name = _require_non_empty_column(column, semantic_id=semantic_id)
    entity_domain = _domain_from_ref_id(entity_id)
    if entity_domain != resolved_domain:
        _raise(
            ErrorKind.INVALID_REF,
            f"Measure {semantic_id!r} belongs to entity in domain {entity_domain!r}, "
            f"but the active domain is {resolved_domain!r}.",
            cls=SemanticDecoratorError,
            refs=(semantic_id,),
            constraint_id=ConstraintId.REF_SHAPE,
        )
    _check_duplicate(ctx, semantic_id, MeasureIR)
    _validate_unit(unit, semantic_id, "measure")
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()
    status_id = (
        _require_ref_id(
            status_time_dimension,
            parameter="status_time_dimension",
            expected=(SemanticKind.TIME_DIMENSION,),
        )
        if status_time_dimension is not None
        else None
    )
    ir = MeasureIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        entity=entity_id,
        name=obj_name,
        ai_context=ai_ctx,
        additivity=_normalize_additivity(
            additivity,
            semantic_id=semantic_id,
            status_time_dimension=status_id,
        ),
        unit=unit,
        python_symbol=obj_name,
        location=location,
        body_ast_hash=_compute_column_hash(column_name),
        dsl_additivity=additivity,
        status_time_dimension=status_id,
    )
    _push_ir(
        ctx,
        ref,
        ir,
        ExpressionBody.for_column(column_name),
    )
    return symbol_factory.measure(ref.path)


@reject_parameters(
    "name",
    "entity",
    "domain",
    "status_time_dimension",
    "status_time_fold",
    "empty",
    "nulls",
    "zero_denominator",
    "fold",
    "time_via",
    "fanout_policy",
)
@traced(surface="semantic", operation="measure.define")
def measure(
    *,
    unit: str | None = None,
    additivity: AdditivityPolicy | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[[], Deferred | NumericRow]], NumericRow]:
    """Name a pure numeric row expression and optionally assert a business contract.

    Args:
        unit: Compatible unit assertion; None inherits the expression's unit.
        additivity: Explicit sum permission; None inherits the expression's permission.
        ai_context: Optional business meaning and guardrails from ms.ai_context(...).

    Returns:
        A decorator binding the function name to an immutable named row expression.

    Example:
        >>> @ms.measure(additivity=ms.additive_all())
        ... def order_amount():
        ...     return quantity * unit_price

    Constraints:
        The body takes no parameters and returns closed row arithmetic over constructed
        semantic quantities. A Dimension cannot become a Measure through this
        decorator. For physical or Entity-derived numeric columns, use separate
        dimension_column and measure_column declarations with different semantic
        names. Fresh local expressions and a leading docstring are allowed.
        Naming needs no loader or Session and reads no source. Arbitrary calls and
        Metric returns are rejected. To publish, save at model module top level and
        load; retrieve ``measure:<domain>.<entity>.<function_name>`` with ``catalog.get(...)``.
        The function name must match ``[a-z][a-z0-9_]*``; assertions retain time constraints.
    """

    def decorator(fn: Callable[[], Deferred | NumericRow]) -> NumericRow:
        location = _caller_location()
        try:
            _validate_segment(fn.__name__, role="Measure declaration name")
        except ValueError as error:
            raise SemanticExpressionError(
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                message="Measure declaration name is invalid.",
                expected="a function name matching [a-z][a-z0-9_]*",
                received=fn.__name__,
                action="Rename the declaration function to a lowercase snake_case name.",
                location_label=f"{location.file}:{location.line}",
            ) from error
        if unit is not None and (
            type(unit) is not str or not unit or any(not (0x21 <= ord(ch) <= 0x7E) for ch in unit)
        ):
            raise SemanticExpressionError(
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                message="Measure unit must be a printable ASCII token without whitespace.",
                expected="a non-empty unit token such as 'CNY' or '1', or None",
                received=repr(unit),
                action="Use a valid unit token, or omit unit to inherit the expression's unit.",
                location_label=f"{location.file}:{location.line}",
            )
        _build_ai_context(ai_context)
        try:
            row = construct_measure_expression(fn)

            policy: tuple[Literal["over", "all", "none"], tuple[str, ...]] | None = None
            if isinstance(additivity, AdditiveOverV1):
                policy = ("over", additivity.coordinates)
            elif isinstance(additivity, AdditiveAllV1):
                policy = ("all", additivity.exceptions)
            elif isinstance(additivity, NonAdditiveV1):
                policy = ("none", ())
            elif additivity is not None:
                raise SemanticExpressionError(
                    kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                    message="Measure additivity requires a closed additivity policy.",
                    expected="ms.additive_over(...), ms.additive_except(...), ms.additive_all(), or ms.non_additive()",
                    received=type(additivity).__name__,
                    action="Construct additivity with the public helper, or omit it.",
                )
            declared = declare(
                _as_deferred(row),
                DeclarationRecord(
                    kind="measure",
                    name=fn.__name__,
                    unit=unit,
                    pending_additivity=policy,
                    pending_assertion=True,
                    ai_context=ai_context,
                    location=DefinitionSourceLocation(location.file, location.line),
                ),
            )
            context = _merge(
                captured_context(row),
                captured_context(additivity)
                if isinstance(additivity, (AdditiveOverV1, AdditiveAllV1))
                else None,
            )
            if context.project_root is None:
                declared = declare_measure(
                    _as_deferred(row),
                    name=fn.__name__,
                    unit=unit,
                    additivity=additivity,
                    ai_context=ai_context,
                    location=location,
                )
            return RowExpression(resolver_of(declared), context, "numeric")
        except SemanticExpressionError as error:
            error.location = location
            if not error.semantic_refs:
                error.semantic_refs = (fn.__name__,)
            raise

    return decorator


@traced(surface="semantic", operation="time_dimension.from_column")
def time_dimension_column(
    *,
    name: str,
    entity: Entity | EntitySymbol,
    column: str,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
    parse: SemanticParse | None = None,
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> TimeDimensionSymbol:
    """Declare a time dimension directly from one physical column.

    Args:
        name: Semantic time dimension name.
        entity: Entity symbol returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        column: Physical source column name to read with bracket access.
        sample_interval: Optional canonical minute/hour source sampling cadence.
            Missing clock points are allowed; this does not assert complete coverage.
        parse: Optional parse variant such as ``ms.strptime(...)``.
        domain: Override the active domain namespace with a ``DomainSymbol`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with
            business meaning and agent-facing guidance.

    Returns:
        A native TimeDimension lookup symbol. The loader validates its source
        time roles and the complete physical scope before analysis.

    Constraints:
        Use ``@ms.time_dimension(...)`` when the time axis is an expression over
        one or more columns. This helper is only for direct physical columns.

    Example:
        >>> orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
        >>> log_date = ms.time_dimension_column(
        ...     name="log_date", entity=orders, column="dt",
        ...     parse=ms.strptime("%Y%m%d"),
        ... )

    Capture constraints: The result is an immutable native lookup symbol. It
    supports forward composition; the loader validates and binds the complete
    model before the Catalog returns closed values for analysis.
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    entity_ref = _entity_lookup(entity, parameter="entity")
    entity_id = entity_ref.path
    obj_name = name
    semantic_id = f"{entity_id}.{obj_name}"
    ref = ref_factory.time_dimension(semantic_id)
    column_name = _require_non_empty_column(column, semantic_id=semantic_id)
    entity_domain = _domain_from_ref_id(entity_id)
    if entity_domain != resolved_domain:
        _raise(
            ErrorKind.INVALID_REF,
            f"Time dimension {semantic_id!r} belongs to entity in domain {entity_domain!r}, "
            f"but the active domain is {resolved_domain!r}.",
            cls=SemanticDecoratorError,
            refs=(semantic_id,),
            constraint_id=ConstraintId.REF_SHAPE,
        )
    _check_duplicate(ctx, semantic_id, DimensionIR)
    _validate_time_parse(parse)
    cadence = _normalize_sample_interval_value(
        sample_interval, semantic_id=semantic_id, parse=parse
    )
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()
    ir = DimensionIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        entity=entity_id,
        name=obj_name,
        ai_context=ai_ctx,
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        sample_interval=cadence,
        parse=parse,
        python_symbol=obj_name,
        location=location,
        body_ast_hash=_compute_column_hash(column_name),
        source_column=column_name,
    )
    _push_ir(ctx, ref, ir, ExpressionBody.for_column(column_name))
    return symbol_factory.time_dimension(ref.path)


@traced(surface="semantic", operation="time_dimension.define")
def time_dimension(
    *,
    name: str | None = None,
    entity: Entity | EntitySymbol,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
    parse: SemanticParse | None = None,
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[[ibis_ir.Table], ibis_ir.Value]], TimeDimensionSymbol]:
    """Declare a time-aware dimension that carries encoding and optional source cadence.

    Time dimensions are the only dimensions usable as window axes by ``session.observe``.
    The body may return any ibis expression that represents the intended time
    axis. When ``parse`` is omitted, the parse variant is inferred from the
    column type at execution. Use ``ms.timestamp(timezone=...)`` to declare the
    source timezone for naive timestamps, or set the Datasource
    wall_clock_timezone declaration. ``catalog.validate`` and execution check physical compatibility;
    readiness does not read the schema. Use ``ms.strptime(...)`` or
    ``ms.hour_prefix(...)`` for string/integer parsing.

    Args:
        name: Dimension name. Defaults to the function name.
        entity: Owning entity ref returned by ``ms.entity(...)``.
        sample_interval: Optional canonical minute/hour source sampling cadence.
            Missing clock points are allowed; this does not assert complete coverage.
        parse: Optional parse variant. Omit for native temporal columns (the parse
            is inferred at analysis time). Use ``ms.timestamp(timezone=...)``, ``ms.strptime(format)``, or
            ``ms.hour_prefix(prefix)`` when explicit configuration is needed.
        domain: Override the active domain namespace with a ``DomainSymbol`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A decorator returning a native TimeDimension lookup symbol, with source
        time roles validated during loader binding.

    Raises:
        SemanticDecoratorError: ``entity`` is unknown, ``name`` collides, the
            body violates the AST whitelist, or the parse variant is incompatible
            with its sampled cadence.

    Example:
        >>> @ms.time_dimension(entity=orders)
        ... def created_at(orders):
        ...     return orders.created_at

    Capture constraints: The result is an immutable native lookup symbol. It
    supports forward composition; the loader validates and binds the complete
    model before the Catalog returns closed values for analysis.
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    def decorator(fn: Callable[[ibis_ir.Table], ibis_ir.Value]) -> TimeDimensionSymbol:
        obj_name = name or fn.__name__
        ds_ref = _require_ref_id(
            entity,
            parameter="entity",
            expected=(SemanticKind.ENTITY,),
        )
        semantic_id = f"{ds_ref}.{obj_name}"
        ref = ref_factory.time_dimension(semantic_id)
        ds_domain = ds_ref.split(".", 1)[0]
        if ds_domain != resolved_domain:
            _raise(
                ErrorKind.INVALID_REF,
                f"Time dimension {semantic_id!r} belongs to entity in domain {ds_domain!r}, "
                f"but the active domain is {resolved_domain!r}.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
                constraint_id=ConstraintId.REF_SHAPE,
            )
        _check_duplicate(ctx, semantic_id, DimensionIR)

        _validate_time_parse(parse)
        expression_body = compile_expression_body(
            fn,
            owning_ref=ref,
            ordered_entity_refs=(_entity_lookup(entity, parameter="entity"),),
        )
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()

        cadence = _normalize_sample_interval_value(
            sample_interval, semantic_id=semantic_id, parse=parse
        )

        ir = DimensionIR(
            semantic_id=semantic_id,
            domain=resolved_domain,
            entity=ds_ref,
            name=obj_name,
            ai_context=ai_ctx,
            is_time_dimension=True,
            kind=DimensionKind.TIME,
            sample_interval=cadence,
            parse=parse,
            python_symbol=fn.__name__,
            location=location,
            body_ast_hash=expression_body.body_ast_hash,
        )
        _push_ir(ctx, ref, ir, expression_body)
        return symbol_factory.time_dimension(ref.path)

    return decorator


_register_authoring_file(__file__)
