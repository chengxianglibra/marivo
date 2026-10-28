"""Entity-scoped field decorators for semantic authoring.

Internal module: public symbols are re-exported from
``marivo.semantic.authoring``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, overload

import ibis.expr.types as ibis_ir
from ibis.common.deferred import Deferred

from marivo._symbolic import (
    DatasourceSymbol,
    DimensionSymbol,
    DomainSymbol,
    EntitySymbol,
    MeasureSymbol,
    RelationshipSymbol,
    TimeDimensionSymbol,
    symbol_factory,
)
from marivo.refs import (
    SemanticKind,
)
from marivo.refs import (
    ref as ref_factory,
)
from marivo.semantic._authoring_context import (
    _caller_location,
    _check_duplicate,
    _domain_from_ref_id,
    _entity_lookup,
    _push_ir,
    _register_authoring_file,
    _require_ctx,
    _require_non_empty_column,
    _require_ref_id,
    _resolve_datasource_ref,
    _resolve_domain,
)
from marivo.semantic._authoring_retired import reject_parameters
from marivo.semantic._authoring_validation import (
    _compute_column_hash,
    _normalize_additivity,
    _normalize_sample_interval_value,
    _validate_relationship_keys,
    _validate_time_parse,
    _validate_unit,
)
from marivo.semantic._authoring_values import _build_ai_context
from marivo.semantic._dsl_authoring import AdditivityPolicy
from marivo.semantic._expression_binding import ExpressionBody, compile_expression_body
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.expressions import (
    Entity,
    TimeField,
)
from marivo.semantic.ir import (
    CsvSourceIR,
    DimensionIR,
    DimensionKind,
    EntityIR,
    EntitySourceIR,
    JoinKey,
    JsonSourceIR,
    MeasureIR,
    ParquetSourceIR,
    RelationshipIR,
    SemanticParse,
    SnapshotVersioningIR,
    TableSourceIR,
    ValidityVersioningIR,
)
from marivo.semantic.typing import AiContextValue


def _reject_named_entity_decorator() -> None:
    """Reject direct-constructor decorator syntax before Deferred can absorb it."""
    import ast
    from pathlib import Path

    location = _caller_location()
    try:
        tree = ast.parse(Path(location.file).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and any(keyword.arg == "name" for keyword in decorator.keywords)
                and decorator.lineno <= location.line <= (decorator.end_lineno or decorator.lineno)
            ):
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
    ai_context: AiContextValue | None,
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
    location = _caller_location()

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
    )
    ref = ref_factory.entity(semantic_id)
    _push_ir(ctx, ref, ir, expression_body)

    return symbol_factory.entity(ref.path)


@overload
def entity(
    *,
    name: str,
    datasource: DatasourceSymbol,
    source: EntitySourceIR,
    primary_key: list[str] | None = ...,
    versioning: SnapshotVersioningIR | ValidityVersioningIR | None = ...,
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
    domain: DomainSymbol | None = ...,
    ai_context: AiContextValue | None = ...,
) -> Callable[[Callable[[ibis_ir.Table], ibis_ir.Table]], EntitySymbol]: ...


def entity(
    *,
    name: str | None = None,
    datasource: DatasourceSymbol,
    source: EntitySourceIR,
    primary_key: list[str] | None = None,
    versioning: SnapshotVersioningIR | ValidityVersioningIR | None = None,
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
    resolved_domain = _resolve_domain(domain, ctx)
    if name is not None:
        _reject_named_entity_decorator()
        semantic_id = f"{resolved_domain}.{name}"
        return _register_entity_ir(
            semantic_id=semantic_id,
            domain=resolved_domain,
            name=name,
            python_symbol=name,
            datasource=datasource,
            source=source,
            primary_key=tuple(primary_key) if primary_key else (),
            versioning=versioning,
            ai_context=ai_context,
        )

    def decorator(fn: Callable[[ibis_ir.Table], ibis_ir.Table]) -> EntitySymbol:
        obj_name = fn.__name__
        semantic_id = f"{resolved_domain}.{obj_name}"
        return _register_entity_ir(
            semantic_id=semantic_id,
            domain=resolved_domain,
            name=obj_name,
            python_symbol=fn.__name__,
            datasource=datasource,
            source=source,
            primary_key=tuple(primary_key) if primary_key else (),
            versioning=versioning,
            ai_context=ai_context,
            expression_body=compile_expression_body(
                fn,
                owning_ref=ref_factory.entity(semantic_id),
                body_kind="entity_table",
            ),
        )

    return decorator


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
def measure_column(
    *,
    name: str,
    entity: Entity | EntitySymbol,
    column: str,
    additivity: AdditivityPolicy,
    status_time_dimension: TimeField | TimeDimensionSymbol | None = None,
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
def measure(
    *,
    unit: str | None = None,
    additivity: AdditivityPolicy | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[[], Deferred]], Deferred]:
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
        No loader, Session, source reads, arbitrary calls, or Metric returns are allowed.
        The function name must match ``[a-z][a-z0-9_]*``; assertions retain time constraints.
    """
    from marivo.refs import _validate_segment
    from marivo.semantic._expression_binding import construct_measure_expression
    from marivo.semantic.errors import SemanticExpressionError
    from marivo.semantic.expressions import declare_measure

    def decorator(fn: Callable[[], Deferred]) -> Deferred:
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
            return declare_measure(
                row,
                name=fn.__name__,
                unit=unit,
                additivity=additivity,
                ai_context=ai_context,
                location=location,
            )
        except SemanticExpressionError as error:
            error.location = location
            if not error.semantic_refs:
                error.semantic_refs = (fn.__name__,)
            raise

    return decorator


def time_dimension_column(
    *,
    name: str,
    entity: Entity | EntitySymbol,
    column: str,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
    parse: SemanticParse | None = None,
    is_default: bool = False,
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
        parse: Optional parse variant such as ``ms.strptime(...)``.
        is_default: Whether this is the default time axis for the entity.
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
        is_default=is_default,
        python_symbol=obj_name,
        location=location,
        body_ast_hash=_compute_column_hash(column_name),
        source_column=column_name,
    )
    _push_ir(ctx, ref, ir, ExpressionBody.for_column(column_name))
    return symbol_factory.time_dimension(ref.path)


def time_dimension(
    *,
    name: str | None = None,
    entity: Entity | EntitySymbol,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
    parse: SemanticParse | None = None,
    is_default: bool = False,
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[[ibis_ir.Table], ibis_ir.Value]], TimeDimensionSymbol]:
    """Declare a time-aware dimension that carries encoding and optional source cadence.

    Time dimensions are the only dimensions usable as window axes by ``session.observe``.
    The body may return any ibis expression that represents the intended time
    axis. When ``parse`` is omitted, the parse variant is inferred from the
    column type at analysis time. Use ``ms.timestamp(timezone=...)`` for a native naive source axis so readiness
    can block an undeclared datasource-timezone fallback. Use ``ms.strptime(...)``
    or ``ms.hour_prefix(...)`` for string/integer parsing.

    Args:
        name: Dimension name. Defaults to the function name.
        entity: Owning entity ref returned by ``ms.entity(...)``.
        sample_interval: Optional canonical minute/hour source sampling cadence.
        parse: Optional parse variant. Omit for native temporal columns (the parse
            is inferred at analysis time). Use ``ms.timestamp(timezone=...)``, ``ms.strptime(format)``, or
            ``ms.hour_prefix(prefix)`` when explicit configuration is needed.
        is_default: Mark this dimension as the default time axis when multiple time dimensions
            exist on the entity. At most one time dimension per entity may carry
            is_default=True. When observe() is called without time_dimension=, the default
            dimension is used automatically.
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
            is_default=is_default,
            python_symbol=fn.__name__,
            location=location,
            body_ast_hash=expression_body.body_ast_hash,
        )
        _push_ir(ctx, ref, ir, expression_body)
        return symbol_factory.time_dimension(ref.path)

    return decorator


def relationship(
    *,
    name: str,
    from_entity: Entity | EntitySymbol,
    to_entity: Entity | EntitySymbol,
    keys: list[JoinKey],
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> RelationshipSymbol:
    """Declare a join relationship between two entities.

    Top-level call (not a decorator). Used by the compiler to plan joins when a
    metric or dimension references dimensions across related entities.

    Args:
        name: Required relationship name.
        from_entity: Source entity ref.
        to_entity: Target entity ref.
        keys: List of ``ms.join_on(from_key, to_key)`` pairs.
        domain: Override the active domain namespace with a ``DomainSymbol`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A native Relationship lookup symbol for directed path parameters.

    Raises:
        SemanticDecoratorError: A ref or key shape is invalid.

    Example:
        >>> ms.relationship(
        ...     name="orders_to_customers",
        ...     from_entity=orders, to_entity=customers,
        ...     keys=[ms.join_on(customer_id, id)],
        ... )

    Constraints:
        Load derives structural multiplicity from complete endpoint identity key
        coverage. The relationship declares no global required-match policy.
        Each consumer decides whether its selected members require a match and
        what an allowed absence means. Physical multiplicity can be audited through source_health. Analysis trusts
        required matching as an unverified premise; a versioned endpoint needs an exact
        version selection before its one side can be consumed.

    Capture constraints: The result is an immutable native lookup symbol. It
    supports forward composition; the loader validates and binds the complete
    model before the Catalog returns closed values for analysis.
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    semantic_id = f"{resolved_domain}.{name}"
    ref = ref_factory.relationship(semantic_id)
    _check_duplicate(ctx, semantic_id, RelationshipIR)

    from_ds = _require_ref_id(
        from_entity,
        parameter="from_entity",
        expected=(SemanticKind.ENTITY,),
    )
    to_ds = _require_ref_id(
        to_entity,
        parameter="to_entity",
        expected=(SemanticKind.ENTITY,),
    )
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    resolved_keys = _validate_relationship_keys(tuple(keys), semantic_id=semantic_id)

    if not resolved_keys:
        _raise(
            ErrorKind.INVALID_REF,
            "ms.relationship(keys=...) requires at least one ms.join_on(from_key, to_key) pair.",
            cls=SemanticDecoratorError,
            refs=(semantic_id,),
            constraint_id=ConstraintId.REF_SHAPE,
        )

    ir = RelationshipIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        name=name,
        from_entity=from_ds,
        to_entity=to_ds,
        keys=resolved_keys,
        ai_context=ai_ctx,
        location=location,
    )
    _push_ir(ctx, ref, ir, None)

    return symbol_factory.relationship(ref.path)


_register_authoring_file(__file__)
