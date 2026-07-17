"""Top-level domain and tier-1 metric declarations for semantic authoring.

Internal module: public symbols are re-exported from
``marivo.semantic.authoring``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from marivo.semantic._authoring_context import (
    _caller_location,
    _check_duplicate,
    _domain_from_ref_id,
    _push_ir,
    _register_authoring_file,
    _require_ctx,
    _require_entity_ref,
    _require_ref_id,
    _resolve_domain,
    _resolve_entity_refs,
)
from marivo.semantic._authoring_validation import (
    _compute_agg_hash,
    _normalize_additivity,
    _normalize_time_fold,
    _validate_metric_provenance,
    _validate_unit,
)
from marivo.semantic._authoring_values import _build_ai_context
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    Additivity,
    AggKind,
    DomainIR,
    MetricIR,
    SqlProvenance,
    WhereFilter,
    WhereValue,
)
from marivo.semantic.refs import DomainRef, EntityRef, MeasureRef, MetricRef
from marivo.semantic.typing import AiContextValue
from marivo.semantic.validator import validate_metric_body_ast


def domain(
    *,
    name: str,
    owner: str,
    default: bool = True,
    ai_context: AiContextValue | None = None,
) -> DomainRef:
    """Declare a semantic domain namespace inside a project file.

    A domain groups entities, dimensions, metrics, and relationships under a single
    qualified name (``<domain>.<object>``). Must be called at module top-level
    inside a ``models/semantic/<model>/*.py`` project file.

    Args:
        name: Domain namespace, e.g. ``"sales"``.
        owner: Human owner accountable for this domain's semantic correctness
            and quality.
        default: If True, subsequent decorators in this file resolve to this
            domain when no explicit ``domain=`` kwarg is passed.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra
            agent-facing hints.

    Returns:
        A ``DomainRef`` that can be passed as the ``domain=`` kwarg to other
        decorators to override the default domain context.

    Raises:
        OutsideLoaderContextError: Called outside a semantic loader pass.
        SemanticDecoratorError: ``name`` collides with another domain in the project.

    Example:
        >>> import marivo.semantic as ms
        >>> sales = ms.domain(name="sales", owner="Mina Zhang", default=True)
    """
    ctx = _require_ctx()
    if not isinstance(owner, str) or not owner.strip():
        _raise(
            ErrorKind.INVALID_DOMAIN_OWNER,
            f"{name!r}: owner must be a non-empty string; got {owner!r}.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.DOMAIN_OWNER_REQUIRED,
        )
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    ir = DomainIR(
        name=name,
        owner=owner,
        default=default,
        ai_context=ai_ctx,
        location=location,
    )
    _push_ir(ctx, ir, None)

    if default:
        ctx.default_domain = name

    return DomainRef(semantic_id=name)


def aggregate(
    *,
    name: str,
    measure: MeasureRef,
    agg: AggKind,
    fold: str | tuple[Literal["percentile"], float] | None = None,
    filter: WhereFilter | None = None,
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> MetricRef:
    """Declare a tier-1 simple metric: an aggregation over a measure.

    The metric inherits its additivity nature from ``measure`` (resolved at load);
    ``fold`` overrides the time-fold for semi-additive measures only. No function body.

    Args:
        name: Metric name (required).
        measure: Measure to aggregate (``MeasureRef``).
        agg: Aggregation kind: ``"sum"``, ``"count"``, ``"count_distinct"``,
            ``"min"``, ``"max"``, ``"mean"``, ``"median"``, or
            ``("percentile", q)`` for the q-th percentile across rows in each
            query group.
        fold: Time-axis fold override for semi-additive measures:
            ``"mean"``, ``"min"``, ``"max"``, ``"first"``, ``"last"``, or
            ``("percentile", q)``. Same fold as ``ms.semi_additive(over, fold)``;
            collapses the ``over`` time axis. Distinct from
            ``agg=("percentile", q)``, which aggregates across rows in each
            query group rather than along the time axis.
        filter: Optional ``ms.where(col=value, ...)`` to aggregate only matching
            rows (e.g. a subset sum). ``None`` aggregates all rows.
        unit: Override the unit derived from ``measure`` at load. Leave None to
            inherit the measure's unit (count/count_distinct derive nothing).
        domain: Override the active domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Example:
        >>> revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
        >>> inventory = ms.aggregate(name="inventory", measure=quantity, agg="sum", fold="last")
        >>> p95_latency = ms.aggregate(name="p95_latency", measure=latency, agg=("percentile", 0.95))
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    measure_id = _require_ref_id(measure, parameter="measure", expected=(MeasureRef,))
    entity_id = measure_id.rsplit(".", 1)[0]
    obj_name = name
    semantic_id = f"{resolved_domain}.{obj_name}"
    _check_duplicate(ctx, semantic_id, MetricIR)
    _validate_unit(unit, semantic_id)
    fold_ir = _normalize_time_fold(fold, semantic_id=semantic_id) if fold is not None else None
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()
    filter_pairs = _resolve_filter_pairs(filter)
    metric_ir = MetricIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        name=obj_name,
        metric_type="simple",
        entities=(entity_id,),
        aggregation=agg,
        measure=measure_id,
        composition=None,
        additivity=None,
        provenance=None,
        ai_context=ai_ctx,
        body_ast_hash=_compute_agg_hash(measure_id, agg, fold_ir, filter=filter_pairs),
        python_symbol=obj_name,
        location=location,
        root_entity=entity_id,
        fold_override=fold_ir,
        unit=unit,
        aggregation_target=measure_id,
        aggregation_target_kind="measure",
        filter=filter_pairs,
    )
    _push_ir(ctx, metric_ir, None)
    return MetricRef(semantic_id)


def _resolve_filter_pairs(filter: WhereFilter | None) -> tuple[tuple[str, WhereValue], ...] | None:
    """Validate and unwrap a ``filter=`` argument into IR predicate pairs.

    Non-None values must be a :class:`WhereFilter` from ``ms.where(...)``; raw
    dicts/tuples/strings raise a typed ``SemanticDecoratorError`` pointing at
    ``ms.where(...)`` instead of a generic load failure. See MR !29 review P2.
    """
    if filter is None:
        return None
    if not isinstance(filter, WhereFilter):
        _raise(
            ErrorKind.INVALID_REF,
            "filter must be a WhereFilter built by ms.where(col=value, ...); "
            f"got {type(filter).__name__}.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    return filter.conditions


def where(**conditions: WhereValue) -> WhereFilter:
    """Build an AND-joined equality filter for ``ms.count`` / ``ms.aggregate``.

    Each keyword is a column on the target entity mapped to the value it must
    equal, e.g. ``ms.where(state="FAILED")``. Use this to express subset counts
    (failure rate, error rate, success rate) without a hand-written metric body.

    Args:
        **conditions: One or more ``column=value`` equality predicates (str,
            int, float, or bool values). ``None`` is not supported.

    Returns:
        A :class:`WhereFilter` to pass as ``filter=``.

    Example:
        >>> failed = ms.count(name="failed_count", entity=queries, filter=ms.where(state="FAILED"))
    """
    if not conditions:
        _raise(
            ErrorKind.INVALID_REF,
            "ms.where requires at least one column=value equality condition",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    normalized: list[tuple[str, WhereValue]] = []
    for column, value in conditions.items():
        if value is None:
            _raise(
                ErrorKind.INVALID_REF,
                f"ms.where column {column!r} does not support None; "
                "use a concrete value or a custom @ms.metric body for IS NULL.",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.REF_SHAPE,
            )
        if not isinstance(value, str | int | float | bool):
            _raise(
                ErrorKind.INVALID_REF,
                f"ms.where column {column!r} value must be str/int/float/bool, "
                f"got {type(value).__name__}",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.REF_SHAPE,
            )
        normalized.append((str(column), value))
    return WhereFilter(conditions=tuple(normalized))


def count(
    *,
    name: str,
    entity: EntityRef,
    filter: WhereFilter | None = None,
    ai_context: AiContextValue | None = None,
) -> MetricRef:
    """Declare a row-count metric for an entity.

    Args:
        name: Metric name inside the entity's domain.
        entity: Entity ref returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        filter: Optional ``ms.where(col=value, ...)`` to count only matching rows
            (e.g. a failure/error subset). ``None`` counts all rows.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra
            agent-facing hints.

    Returns:
        A ``MetricRef`` for the count metric.

    Example:
        >>> orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=md.table("orders"))
        >>> order_count = ms.count(name="order_count", entity=orders)
        >>> failed_count = ms.count(name="failed_count", entity=orders, filter=ms.where(state="FAILED"))

    Constraints:
        Counts rows of the target entity. Use ``ms.aggregate(...)`` for measure
        aggregation and ``@ms.metric(...)`` for custom expressions.
    """
    ctx = _require_ctx()
    entity_ref = _require_entity_ref(entity, parameter="entity")
    entity_id = entity_ref.id
    resolved_domain = _domain_from_ref_id(entity_id)
    semantic_id = f"{resolved_domain}.{name}"
    _check_duplicate(ctx, semantic_id, MetricIR)
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()
    filter_pairs = _resolve_filter_pairs(filter)
    metric_ir = MetricIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        name=name,
        metric_type="simple",
        entities=(entity_id,),
        aggregation="count",
        measure=None,
        composition=None,
        additivity=None,
        provenance=None,
        ai_context=ai_ctx,
        body_ast_hash=_compute_agg_hash(entity_id, "count", None, filter=filter_pairs),
        python_symbol=name,
        location=location,
        root_entity=entity_id,
        aggregation_target=entity_id,
        aggregation_target_kind="entity",
        filter=filter_pairs,
    )
    _push_ir(ctx, metric_ir, None)
    return MetricRef(semantic_id)


def metric(
    *,
    name: str | None = None,
    entities: list[EntityRef],
    additivity: Additivity,
    root_entity: EntityRef | None = None,
    fanout_policy: Literal["block", "aggregate_then_join"] = "block",
    unit: str | None = None,
    provenance: SqlProvenance | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[..., Any]], MetricRef]:
    """Declare a metric from an ibis body. Declares ``additivity`` directly.

    Args:
        name: Metric name. Defaults to the function name.
        entities: List of entity refs.
        additivity: ``"additive"``, ``"non_additive"``, or ``ms.semi_additive(over, fold)``.
        root_entity: Required when more than one entity is provided.
        fanout_policy: ``"block"`` (default) or ``"aggregate_then_join"``.
        unit: UCUM unit token.
        provenance: Optional ``SqlProvenance`` from ``ms.from_sql(sql=..., dialect=...)``.
        domain: Override the active domain namespace.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``MetricRef``.

    Example:
        >>> @ms.metric(entities=[orders], additivity="additive")
        ... def gmv(orders):
        ...     return (orders.price * orders.qty).sum()
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    def decorator(fn: Callable[..., Any]) -> MetricRef:
        obj_name = name or fn.__name__
        semantic_id = f"{resolved_domain}.{obj_name}"
        _check_duplicate(ctx, semantic_id, MetricIR)
        _validate_unit(unit, semantic_id)
        _validate_metric_provenance(provenance)
        entity_refs = _resolve_entity_refs(entities)
        if len(entity_refs) == 0:
            _raise(
                ErrorKind.MISSING_ENTITIES,
                "@ms.metric(...) requires non-empty entities.",
                refs=(semantic_id,),
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.METRIC_ENTITIES_REQUIRED,
            )
        body_hash = validate_metric_body_ast(fn, "base", body_kind="metric")
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()
        root_ref = (
            _require_ref_id(root_entity, parameter="root_entity", expected=(EntityRef,))
            if root_entity is not None
            else None
        )
        if root_ref is None and len(entity_refs) == 1:
            root_ref = entity_refs[0]
        if root_ref is None:
            _raise(
                ErrorKind.MISSING_METRIC_ROOT_ENTITY,
                "@ms.metric(...) with more than one entity requires root_entity=...",
                refs=(semantic_id,),
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.METRIC_ROOT_ENTITY_REQUIRED,
            )
        metric_ir = MetricIR(
            semantic_id=semantic_id,
            domain=resolved_domain,
            name=obj_name,
            metric_type="simple",
            entities=entity_refs,
            aggregation=None,
            measure=None,
            composition=None,
            additivity=_normalize_additivity(additivity, semantic_id=semantic_id),
            provenance=provenance,
            ai_context=ai_ctx,
            body_ast_hash=body_hash,
            python_symbol=fn.__name__,
            location=location,
            root_entity=root_ref,
            fanout_policy=fanout_policy,
            unit=unit,
        )
        _push_ir(ctx, metric_ir, fn)
        return MetricRef(semantic_id)

    return decorator


_register_authoring_file(__file__)
