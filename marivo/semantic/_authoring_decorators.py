"""Entity-scoped field decorators for semantic authoring.

Internal module: public symbols are re-exported from
``marivo.semantic.authoring``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from marivo.refs import (
    DatasourceKind,
    DomainKind,
    EntityKind,
    MeasureKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    TimeDimensionKind,
)
from marivo.refs import (
    DimensionKind as DimensionKindTag,
)
from marivo.semantic._authoring_context import (
    _caller_location,
    _check_duplicate,
    _domain_from_ref_id,
    _push_ir,
    _register_authoring_file,
    _require_ctx,
    _require_entity_ref,
    _require_non_empty_column,
    _require_ref_id,
    _resolve_datasource_ref,
    _resolve_domain,
)
from marivo.semantic._authoring_validation import (
    _compute_column_hash,
    _normalize_additivity,
    _validate_relationship_keys,
    _validate_sample_interval_granularity,
    _validate_time_parse,
    _validate_time_parse_granularity,
    _validate_unit,
)
from marivo.semantic._authoring_values import _build_ai_context
from marivo.semantic._expression_binding import ExpressionBody, compile_expression_body
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    Additivity,
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


def entity(
    *,
    name: str,
    datasource: Ref[DatasourceKind],
    source: EntitySourceIR,
    primary_key: list[str] | None = None,
    versioning: SnapshotVersioningIR | ValidityVersioningIR | None = None,
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[EntityKind]:
    """Declare an entity over a structured physical source.

    Args:
        name: Entity name.
        datasource: Datasource ref returned by ``ms.Ref.datasource(...)``.
        source: Structured physical source, usually ``md.table(...)``,
            ``md.parquet(...)``, ``md.csv(...)``, or ``md.json(...)``.
        primary_key: Optional list of column names forming the primary key.
        domain: Override the active domain namespace with a ``Ref[domain]`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        An ``Ref[entity]`` usable by ``@ms.dimension`` and ``@ms.metric``.

    Raises:
        SemanticDecoratorError: ``datasource`` is not a datasource ref, ``name``
            collides with another object, or ``source`` is not an entity source.

    Example:
        >>> orders = ms.entity(
        ...     name="orders",
        ...     datasource=ms.Ref.datasource("warehouse"),
        ...     source=md.table("orders", database="sales_mart"),
        ... )
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    semantic_id = f"{resolved_domain}.{name}"
    ref = Ref.entity(semantic_id)
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
    pk = tuple(primary_key) if primary_key else ()
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    ir = EntityIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        name=name,
        datasource=ds_ref,
        source=source,
        primary_key=pk,
        ai_context=ai_ctx,
        python_symbol=name,
        location=location,
        versioning=versioning,
    )
    _push_ir(ctx, ref, ir, None)

    return ref


def dimension_column(
    *,
    name: str,
    entity: Ref[EntityKind],
    column: str,
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[DimensionKindTag]:
    """Declare a categorical dimension directly from one physical column.

    Args:
        name: Semantic dimension name.
        entity: Entity ref returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        column: Physical source column name to read with bracket access.
        domain: Override the active domain namespace with a ``Ref[domain]`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with
            business meaning and agent-facing guidance.

    Returns:
        A ``Ref[dimension]`` usable in metric bodies and analysis APIs.

    Constraints:
        Use ``@ms.dimension(...)`` when the dimension is an expression over one
        or more columns. This helper is only for direct physical columns.

    Example:
        >>> orders = ms.entity(name="orders", datasource=ms.Ref.datasource("warehouse"), source=md.table("orders"))
        >>> region = ms.dimension_column(name="region", entity=orders, column="region")
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    entity_ref = _require_entity_ref(entity, parameter="entity")
    entity_id = entity_ref.path
    obj_name = name
    semantic_id = f"{entity_id}.{obj_name}"
    ref = Ref.dimension(semantic_id)
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
    )
    _push_ir(ctx, ref, ir, ExpressionBody.for_column(column_name))
    return ref


def dimension(
    *,
    name: str | None = None,
    entity: Ref[EntityKind],
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[..., Any]], Ref[DimensionKindTag]]:
    """Declare a categorical dimension whose body returns an ibis expression over its entity.

    The decorated function takes the entity table and returns a single
    expression (single-return AST). Use this for both raw columns and derived
    expressions (e.g. ``table.region``).

    For quantitative measures, use ``@ms.measure(entity=..., additivity=...)``
    instead.

    Args:
        name: Dimension name. Defaults to the function name.
        entity: Owning entity ref returned by ``ms.entity(...)``.
        domain: Override the active domain namespace with a ``Ref[domain]`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``Ref[dimension]``.

    Raises:
        SemanticDecoratorError: ``entity`` is unknown, ``name`` collides, or the
            body violates the AST whitelist.

    Example:
        >>> @ms.dimension(entity=orders)
        ... def region(orders_table):
        ...     return orders_table.region
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    def decorator(fn: Callable[..., Any]) -> Ref[DimensionKindTag]:
        obj_name = name or fn.__name__
        entity_ref = _require_ref_id(
            entity,
            parameter="entity",
            expected=(SemanticKind.ENTITY,),
        )
        semantic_id = f"{entity_ref}.{obj_name}"
        ref = Ref.dimension(semantic_id)
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
            ordered_entity_refs=(entity,),
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
        return ref

    return decorator


def measure_column(
    *,
    name: str,
    entity: Ref[EntityKind],
    column: str,
    additivity: Additivity,
    unit: str | None = None,
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[MeasureKind]:
    """Declare a quantitative measure directly from one physical column.

    Args:
        name: Semantic measure name.
        entity: Entity ref returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        column: Physical source column name to read with bracket access.
        additivity: Whether the measure is ``"additive"``, ``"non_additive"``,
            or ``ms.semi_additive(over=..., fold=...)``.
        unit: UCUM unit token such as ``"CNY"``, ``"USD"``, ``"%"``, or ``"1"``.
        domain: Override the active domain namespace with a ``Ref[domain]`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with
            business meaning and agent-facing guidance.

    Returns:
        A ``Ref[measure]`` usable by ``ms.aggregate(...)`` and expression bodies.

    Constraints:
        Use ``@ms.measure(...)`` when the measure is an expression over one or
        more columns. This helper is only for direct physical columns.

    Example:
        >>> orders = ms.entity(name="orders", datasource=ms.Ref.datasource("warehouse"), source=md.table("orders"))
        >>> amount = ms.measure_column(
        ...     name="amount", entity=orders, column="amount",
        ...     additivity="additive", unit="CNY",
        ... )
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    entity_ref = _require_entity_ref(entity, parameter="entity")
    entity_id = entity_ref.path
    obj_name = name
    semantic_id = f"{entity_id}.{obj_name}"
    ref = Ref.measure(semantic_id)
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
    ir = MeasureIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        entity=entity_id,
        name=obj_name,
        ai_context=ai_ctx,
        additivity=_normalize_additivity(additivity, semantic_id=semantic_id),
        unit=unit,
        python_symbol=obj_name,
        location=location,
        body_ast_hash=_compute_column_hash(column_name),
    )
    _push_ir(ctx, ref, ir, ExpressionBody.for_column(column_name))
    return ref


def measure(
    *,
    name: str | None = None,
    entity: Ref[EntityKind],
    additivity: Additivity,
    unit: str | None = None,
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[..., Any]], Ref[MeasureKind]]:
    """Declare a row-level quantitative measure whose expression can be aggregated.

    Measures represent quantitative facts (e.g. amount, quantity) that can be
    aggregated using ``ms.aggregate()``. The decorated function takes the entity
    table and returns a single ibis expression.

    Args:
        name: Measure name. Defaults to the function name.
        entity: Owning entity ref returned by ``ms.entity(...)``.
        additivity: Whether the measure is ``"additive"``, ``"non_additive"``,
            or ``ms.semi_additive(over=..., fold=...)``.
        unit: UCUM unit token (e.g. ``"USD"``, ``"CNY"``, ``"%"``).
        domain: Override the active domain namespace with a ``Ref[domain]`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``Ref[measure]``.

    Raises:
        SemanticDecoratorError: ``entity`` is unknown, ``name`` collides, or the
            body violates the AST whitelist.

    Example:
        >>> @ms.measure(entity=orders, additivity="additive", unit="USD")
        ... def amount(orders_table):
        ...     return orders_table.amount
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    def decorator(fn: Callable[..., Any]) -> Ref[MeasureKind]:
        obj_name = name or fn.__name__
        entity_ref = _require_ref_id(
            entity,
            parameter="entity",
            expected=(SemanticKind.ENTITY,),
        )
        semantic_id = f"{entity_ref}.{obj_name}"
        ref = Ref.measure(semantic_id)
        entity_domain = entity_ref.split(".", 1)[0]
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
        expression_body = compile_expression_body(
            fn,
            owning_ref=ref,
            ordered_entity_refs=(entity,),
        )
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()
        ir = MeasureIR(
            semantic_id=semantic_id,
            domain=resolved_domain,
            entity=entity_ref,
            name=obj_name,
            ai_context=ai_ctx,
            additivity=_normalize_additivity(additivity, semantic_id=semantic_id),
            unit=unit,
            python_symbol=fn.__name__,
            location=location,
            body_ast_hash=expression_body.body_ast_hash,
        )
        _push_ir(ctx, ref, ir, expression_body)
        return ref

    return decorator


def time_dimension_column(
    *,
    name: str,
    entity: Ref[EntityKind],
    column: str,
    granularity: Literal["year", "quarter", "month", "week", "day", "hour", "minute", "second"],
    parse: SemanticParse | None = None,
    is_default: bool = False,
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[TimeDimensionKind]:
    """Declare a time dimension directly from one physical column.

    Args:
        name: Semantic time dimension name.
        entity: Entity ref returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        column: Physical source column name to read with bracket access.
        granularity: Finest grain at which queries are meaningful.
        parse: Optional parse variant such as ``ms.strptime(...)``.
        is_default: Whether this is the default time axis for the entity.
        domain: Override the active domain namespace with a ``Ref[domain]`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with
            business meaning and agent-facing guidance.

    Returns:
        A ``Ref[time_dimension]`` usable for observe windows and metric bodies.

    Constraints:
        Use ``@ms.time_dimension(...)`` when the time axis is an expression over
        one or more columns. This helper is only for direct physical columns.

    Example:
        >>> orders = ms.entity(name="orders", datasource=ms.Ref.datasource("warehouse"), source=md.table("orders"))
        >>> log_date = ms.time_dimension_column(
        ...     name="log_date", entity=orders, column="dt",
        ...     granularity="day", parse=ms.strptime("%Y%m%d"),
        ... )
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    entity_ref = _require_entity_ref(entity, parameter="entity")
    entity_id = entity_ref.path
    obj_name = name
    semantic_id = f"{entity_id}.{obj_name}"
    ref = Ref.time_dimension(semantic_id)
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
    _validate_time_parse_granularity(semantic_id=semantic_id, granularity=granularity, parse=parse)
    _validate_sample_interval_granularity(
        semantic_id=semantic_id, granularity=granularity, parse=parse
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
        granularity=granularity,
        parse=parse,
        is_default=is_default,
        python_symbol=obj_name,
        location=location,
        body_ast_hash=_compute_column_hash(column_name),
    )
    _push_ir(ctx, ref, ir, ExpressionBody.for_column(column_name))
    return ref


def time_dimension(
    *,
    name: str | None = None,
    entity: Ref[EntityKind],
    granularity: Literal["year", "quarter", "month", "week", "day", "hour", "minute", "second"],
    parse: SemanticParse | None = None,
    is_default: bool = False,
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[..., Any]], Ref[TimeDimensionKind]]:
    """Declare a time-aware dimension that carries grain and parsing metadata.

    Time dimensions are the only dimensions usable as window axes by ``session.observe``.
    The body may return any ibis expression that represents the intended time
    axis. When ``parse`` is omitted, the parse variant is inferred from the
    column type at analysis time. Use ``ms.datetime(timezone=...)`` or
    ``ms.timestamp(timezone=...)`` for a native naive source axis so readiness
    can block an undeclared datasource-timezone fallback. Use ``ms.strptime(...)``
    or ``ms.hour_prefix(...)`` for string/integer parsing.

    Args:
        name: Dimension name. Defaults to the function name.
        entity: Owning entity ref returned by ``ms.entity(...)``.
        granularity: ``year | quarter | month | week | day | hour | minute | second`` — the
            finest grain at which queries are meaningful.
        parse: Optional parse variant. Omit for native temporal columns (the parse
            is inferred at analysis time). Use ``ms.datetime(timezone=...)``,
            ``ms.timestamp(timezone=...)``, ``ms.strptime(format)``, or
            ``ms.hour_prefix(prefix)`` when explicit configuration is needed.
        is_default: Mark this dimension as the default time axis when multiple time dimensions
            exist on the entity. At most one time dimension per entity may carry
            is_default=True. When observe() is called without time_dimension=, the default
            dimension is used automatically.
        domain: Override the active domain namespace with a ``Ref[domain]`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``Ref[time_dimension]``.

    Raises:
        SemanticDecoratorError: ``entity`` is unknown, ``name`` collides, the
            body violates the AST whitelist, or the parse variant is incompatible
            with the declared granularity.

    Example:
        >>> @ms.time_dimension(entity=orders, granularity="day")
        ... def created_at(orders):
        ...     return orders.created_at
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    def decorator(fn: Callable[..., Any]) -> Ref[TimeDimensionKind]:
        obj_name = name or fn.__name__
        ds_ref = _require_ref_id(
            entity,
            parameter="entity",
            expected=(SemanticKind.ENTITY,),
        )
        semantic_id = f"{ds_ref}.{obj_name}"
        ref = Ref.time_dimension(semantic_id)
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
            ordered_entity_refs=(entity,),
        )
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()

        _validate_time_parse_granularity(
            semantic_id=semantic_id, granularity=granularity, parse=parse
        )
        _validate_sample_interval_granularity(
            semantic_id=semantic_id, granularity=granularity, parse=parse
        )

        ir = DimensionIR(
            semantic_id=semantic_id,
            domain=resolved_domain,
            entity=ds_ref,
            name=obj_name,
            ai_context=ai_ctx,
            is_time_dimension=True,
            kind=DimensionKind.TIME,
            granularity=granularity,
            parse=parse,
            is_default=is_default,
            python_symbol=fn.__name__,
            location=location,
            body_ast_hash=expression_body.body_ast_hash,
        )
        _push_ir(ctx, ref, ir, expression_body)
        return ref

    return decorator


def relationship(
    *,
    name: str,
    from_entity: Ref[EntityKind],
    to_entity: Ref[EntityKind],
    keys: list[JoinKey],
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[RelationshipKind]:
    """Declare a join relationship between two entities.

    Top-level call (not a decorator). Used by the compiler to plan joins when a
    metric or dimension references dimensions across related entities.

    Args:
        name: Required relationship name.
        from_entity: Source entity ref.
        to_entity: Target entity ref.
        keys: List of ``ms.join_on(from_key, to_key)`` pairs.
        domain: Override the active domain namespace with a ``Ref[domain]`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A ``Ref[relationship]``.

    Raises:
        SemanticDecoratorError: ``name`` is missing, the entities are unknown, or
            ``keys`` is empty.

    Example:
        >>> ms.relationship(
        ...     name="orders_to_customers",
        ...     from_entity=orders, to_entity=customers,
        ...     keys=[ms.join_on(customer_id, id)],
        ... )
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    semantic_id = f"{resolved_domain}.{name}"
    ref = Ref.relationship(semantic_id)
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

    return ref


_register_authoring_file(__file__)
