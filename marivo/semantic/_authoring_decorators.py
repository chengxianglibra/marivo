"""Entity-scoped field decorators for semantic authoring.

Internal module: public symbols are re-exported from
``marivo.semantic.authoring``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.scan import csv as _datasource_csv
from marivo.datasource.scan import json as _datasource_json
from marivo.datasource.scan import parquet as _datasource_parquet
from marivo.datasource.scan import table as _datasource_table
from marivo.semantic._authoring_context import (
    _caller_location,
    _check_duplicate,
    _column_accessor,
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
    _normalize_additivity,
    _validate_relationship_keys,
    _validate_sample_interval_granularity,
    _validate_time_parse,
    _validate_time_parse_granularity,
    _validate_unit,
)
from marivo.semantic._authoring_values import _build_ai_context
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
from marivo.semantic.refs import (
    DimensionRef,
    DomainRef,
    EntityRef,
    MeasureRef,
    RelationshipRef,
    TimeDimensionRef,
)
from marivo.semantic.typing import AiContextValue
from marivo.semantic.validator import validate_metric_body_ast


def table(name: str, /, *, database: str | tuple[str, ...] | None = None) -> TableSourceIR:
    """Build a structured table source for ``ms.entity(source=...)``."""
    return _datasource_table(name, database=database)


def parquet(
    path: str,
    /,
    *,
    hive_partitioning: bool = False,
    columns: tuple[str, ...] | list[str] | None = None,
) -> ParquetSourceIR:
    """Build a structured parquet source for ``ms.entity(source=...)``."""
    return _datasource_parquet(path, hive_partitioning=hive_partitioning, columns=columns)


def csv(
    path: str,
    /,
    *,
    header: bool = True,
    delimiter: str = ",",
    columns: tuple[str, ...] | list[str] | None = None,
) -> CsvSourceIR:
    """Build a structured CSV source for ``ms.entity(source=...)``."""
    return _datasource_csv(path, header=header, delimiter=delimiter, columns=columns)


def json(
    path: str,
    /,
    *,
    format: Literal["auto", "newline_delimited", "array"] = "auto",
) -> JsonSourceIR:
    """Build a structured JSON source for ``ms.entity(source=...)``."""
    return _datasource_json(path, format=format)


def entity(
    *,
    name: str,
    datasource: DatasourceRef,
    source: EntitySourceIR,
    primary_key: list[str] | None = None,
    versioning: SnapshotVersioningIR | ValidityVersioningIR | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> EntityRef:
    """Declare an entity over a structured physical source.

    Args:
        name: Entity name.
        datasource: Datasource ref returned by ``md.ref(...)``.
        source: Structured physical source, usually ``ms.table(...)``,
            ``ms.parquet(...)``, ``ms.csv(...)``, or ``ms.json(...)``.
        primary_key: Optional list of column names forming the primary key.
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        An ``EntityRef`` usable by ``@ms.dimension`` and ``@ms.metric``.

    Raises:
        SemanticDecoratorError: ``datasource`` is not a datasource ref, ``name``
            collides with another object, or ``source`` is not an entity source.

    Example:
        >>> orders = ms.entity(
        ...     name="orders",
        ...     datasource=md.ref("datasource.warehouse"),
        ...     source=ms.table("orders", database="sales_mart"),
        ... )
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    semantic_id = f"{resolved_domain}.{name}"
    _check_duplicate(ctx, semantic_id, EntityIR)
    if not isinstance(source, (TableSourceIR, ParquetSourceIR, CsvSourceIR, JsonSourceIR)):
        _raise(
            ErrorKind.INVALID_REF,
            "ms.entity(source=...) accepts ms.table(...), ms.parquet(...), ms.csv(...), or ms.json(...).",
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
    _push_ir(ctx, ir, None)

    return EntityRef(semantic_id)


def dimension_column(
    *,
    name: str,
    entity: EntityRef,
    column: str,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> DimensionRef:
    """Declare a categorical dimension directly from one physical column.

    Args:
        name: Semantic dimension name.
        entity: Entity ref returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        column: Physical source column name to read with bracket access.
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with
            business meaning and agent-facing guidance.

    Returns:
        A ``DimensionRef`` usable in metric bodies and analysis APIs.

    Constraints:
        Use ``@ms.dimension(...)`` when the dimension is an expression over one
        or more columns. This helper is only for direct physical columns.

    Example:
        >>> orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))
        >>> region = ms.dimension_column(name="region", entity=orders, column="region")
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    entity_ref = _require_entity_ref(entity, parameter="entity")
    entity_id = entity_ref.id
    obj_name = name
    semantic_id = f"{entity_id}.{obj_name}"
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
    )
    ref = DimensionRef(semantic_id)
    _push_ir(ctx, ir, _column_accessor(column_name))
    ctx.pending_refs.append(ref)
    return ref


def dimension(
    *,
    name: str | None = None,
    entity: EntityRef,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[..., Any]], DimensionRef]:
    """Declare a categorical dimension whose body returns an ibis expression over its entity.

    The decorated function takes the entity table and returns a single
    expression (single-return AST). Use this for both raw columns and derived
    expressions (e.g. ``table.region``).

    For quantitative measures, use ``@ms.measure(entity=..., additivity=...)``
    instead.

    Args:
        name: Dimension name. Defaults to the function name.
        entity: Owning entity ref returned by ``ms.entity(...)``.
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``DimensionRef``.

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

    def decorator(fn: Callable[..., Any]) -> DimensionRef:
        obj_name = name or fn.__name__
        entity_ref = _require_ref_id(entity, parameter="entity", expected=(EntityRef,))
        semantic_id = f"{entity_ref}.{obj_name}"
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

        validate_metric_body_ast(fn, "base", body_kind="dimension")
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
        )
        _push_ir(ctx, ir, fn)

        ref = DimensionRef(semantic_id)
        ctx.pending_refs.append(ref)
        return ref

    return decorator


def measure_column(
    *,
    name: str,
    entity: EntityRef,
    column: str,
    additivity: Additivity,
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> MeasureRef:
    """Declare a quantitative measure directly from one physical column.

    Args:
        name: Semantic measure name.
        entity: Entity ref returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        column: Physical source column name to read with bracket access.
        additivity: Whether the measure is ``"additive"``, ``"non_additive"``,
            or ``ms.semi_additive(over=..., fold=...)``.
        unit: UCUM unit token such as ``"CNY"``, ``"USD"``, ``"%"``, or ``"1"``.
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with
            business meaning and agent-facing guidance.

    Returns:
        A ``MeasureRef`` usable by ``ms.aggregate(...)`` and expression bodies.

    Constraints:
        Use ``@ms.measure(...)`` when the measure is an expression over one or
        more columns. This helper is only for direct physical columns.

    Example:
        >>> orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))
        >>> amount = ms.measure_column(
        ...     name="amount", entity=orders, column="amount",
        ...     additivity="additive", unit="CNY",
        ... )
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    entity_ref = _require_entity_ref(entity, parameter="entity")
    entity_id = entity_ref.id
    obj_name = name
    semantic_id = f"{entity_id}.{obj_name}"
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
    )
    ref = MeasureRef(semantic_id)
    _push_ir(ctx, ir, _column_accessor(column_name))
    ctx.pending_refs.append(ref)
    return ref


def measure(
    *,
    name: str | None = None,
    entity: EntityRef,
    additivity: Additivity,
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[..., Any]], MeasureRef]:
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
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``MeasureRef``.

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

    def decorator(fn: Callable[..., Any]) -> MeasureRef:
        obj_name = name or fn.__name__
        entity_ref = _require_ref_id(entity, parameter="entity", expected=(EntityRef,))
        semantic_id = f"{entity_ref}.{obj_name}"
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
        validate_metric_body_ast(fn, "base", body_kind="measure")
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
        )
        _push_ir(ctx, ir, fn)
        ref = MeasureRef(semantic_id)
        ctx.pending_refs.append(ref)
        return ref

    return decorator


def time_dimension_column(
    *,
    name: str,
    entity: EntityRef,
    column: str,
    granularity: Literal["year", "quarter", "month", "week", "day", "hour", "minute", "second"],
    parse: SemanticParse | None = None,
    is_default: bool = False,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> TimeDimensionRef:
    """Declare a time dimension directly from one physical column.

    Args:
        name: Semantic time dimension name.
        entity: Entity ref returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        column: Physical source column name to read with bracket access.
        granularity: Finest grain at which queries are meaningful.
        parse: Optional parse variant such as ``ms.strptime(...)``.
        is_default: Whether this is the default time axis for the entity.
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with
            business meaning and agent-facing guidance.

    Returns:
        A ``TimeDimensionRef`` usable for observe windows and metric bodies.

    Constraints:
        Use ``@ms.time_dimension(...)`` when the time axis is an expression over
        one or more columns. This helper is only for direct physical columns.

    Example:
        >>> orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))
        >>> log_date = ms.time_dimension_column(
        ...     name="log_date", entity=orders, column="dt",
        ...     granularity="day", parse=ms.strptime("%Y%m%d"),
        ... )
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    entity_ref = _require_entity_ref(entity, parameter="entity")
    entity_id = entity_ref.id
    obj_name = name
    semantic_id = f"{entity_id}.{obj_name}"
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
    )
    ref = TimeDimensionRef(semantic_id)
    _push_ir(ctx, ir, _column_accessor(column_name))
    ctx.pending_refs.append(ref)
    return ref


def time_dimension(
    *,
    name: str | None = None,
    entity: EntityRef,
    granularity: Literal["year", "quarter", "month", "week", "day", "hour", "minute", "second"],
    parse: SemanticParse | None = None,
    is_default: bool = False,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[..., Any]], TimeDimensionRef]:
    """Declare a time-aware dimension that carries grain and parsing metadata.

    Time dimensions are the only dimensions usable as window axes by ``session.observe``.
    The body may return any ibis expression that represents the intended time
    axis. When ``parse`` is omitted, the parse variant is inferred from the
    column type at analysis time. Use ``ms.datetime(...)``, ``ms.timestamp(...)``,
    ``ms.strptime(...)``, or ``ms.hour_prefix(...)`` to declare a parse variant
    explicitly when you need timezone, sample_interval, or string/integer parsing.

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
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``TimeDimensionRef``.

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

    def decorator(fn: Callable[..., Any]) -> TimeDimensionRef:
        obj_name = name or fn.__name__
        ds_ref = _require_ref_id(entity, parameter="entity", expected=(EntityRef,))
        semantic_id = f"{ds_ref}.{obj_name}"
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
        validate_metric_body_ast(fn, "base", body_kind="time_dimension")
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
        )
        _push_ir(ctx, ir, fn)

        ref = TimeDimensionRef(semantic_id)
        ctx.pending_refs.append(ref)
        return ref

    return decorator


def relationship(
    *,
    name: str,
    from_entity: EntityRef,
    to_entity: EntityRef,
    keys: list[JoinKey],
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> RelationshipRef:
    """Declare a join relationship between two entities.

    Top-level call (not a decorator). Used by the compiler to plan joins when a
    metric or dimension references dimensions across related entities.

    Args:
        name: Required relationship name.
        from_entity: Source entity ref.
        to_entity: Target entity ref.
        keys: List of ``ms.join_on(from_key, to_key)`` pairs.
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A ``RelationshipRef``.

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
    _check_duplicate(ctx, semantic_id, RelationshipIR)

    from_ds = _require_ref_id(from_entity, parameter="from_entity", expected=(EntityRef,))
    to_ds = _require_ref_id(to_entity, parameter="to_entity", expected=(EntityRef,))
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
    _push_ir(ctx, ir, None)

    return RelationshipRef(semantic_id)


_register_authoring_file(__file__)
