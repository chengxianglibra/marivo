"""Authoring decorators and builders for marivo.semantic v1.1.

All authoring symbols (domain, entity, dimension, time_dimension,
aggregate, simple_metric, ratio, weighted_average, linear,
semi_additive, relationship, ref) are defined here.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
from collections.abc import Callable
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.scan import file as _datasource_file
from marivo.datasource.scan import table as _datasource_table
from marivo.datasource.typing import _build_ai_context as _shared_build_ai_context
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    Additivity,
    AggKind,
    AiContextIR,
    Composition,
    DimensionIR,
    DimensionKind,
    DimensionRef,
    DomainIR,
    DomainRef,
    EntityIR,
    EntityRef,
    EntitySourceIR,
    FileSourceIR,
    LinearComposition,
    LinearTerm,
    MetricIR,
    MetricRef,
    ProvenanceIR,
    RatioComposition,
    RelationshipIR,
    RelationshipRef,
    SampleIntervalIR,
    SemiAdditive,
    SnapshotVersioningIR,
    SourceLocation,
    TableSourceIR,
    TimeDimensionRef,
    TimeFoldIR,
    ValidityVersioningIR,
    WeightedAverageComposition,
)
from marivo.semantic.loader import _LOADER_CTX, LoaderContext
from marivo.semantic.time_format import normalize_strptime
from marivo.semantic.typing import AiContext
from marivo.semantic.validator import validate_metric_body_ast

__all__ = [
    "DomainRef",
    "aggregate",
    "dimension",
    "domain",
    "entity",
    "file",
    "linear",
    "ratio",
    "ref",
    "relationship",
    "semi_additive",
    "simple_metric",
    "snapshot",
    "table",
    "time_dimension",
    "validity",
    "weighted_average",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_ctx() -> LoaderContext:
    """Get the current LoaderContext or raise OutsideLoaderContextError."""
    ctx = _LOADER_CTX.get()
    if ctx is None:
        _raise(
            ErrorKind.OUTSIDE_LOADER_CONTEXT,
            "Semantic decorators can only be used inside files loaded by the semantic project loader.",
            cls=SemanticDecoratorError,
        )
    return ctx


def _resolve_domain(explicit: DomainRef | None, ctx: LoaderContext) -> str:
    """Resolve the domain name: explicit ref > default_domain > error."""
    if isinstance(explicit, DomainRef):
        return explicit.semantic_id
    if explicit is not None:
        return explicit
    if ctx.default_domain is not None:
        return ctx.default_domain
    _raise(
        ErrorKind.MISSING_DOMAIN,
        "No domain name specified and no default domain is set. "
        "Call ms.domain(name=...) before declaring semantic objects.",
        cls=SemanticDecoratorError,
    )


def _check_duplicate(
    ctx: LoaderContext,
    semantic_id: str,
    ir_type: type[EntityIR | DimensionIR | MetricIR | RelationshipIR],
) -> None:
    """Raise DUPLICATE_NAME if semantic_id already in pending_objects of the same kind."""
    for ir, _ in ctx.pending_objects:
        if isinstance(ir, ir_type) and ir.semantic_id == semantic_id:
            _raise(
                ErrorKind.DUPLICATE_NAME,
                f"Name conflict: {semantic_id!r} is already declared.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
            )


def _semantic_ai_context_error(message: str, details: dict[str, Any]) -> None:
    _raise(ErrorKind.INVALID_AI_CONTEXT, message, cls=SemanticDecoratorError)


def _build_ai_context(ai_context: AiContext | dict[str, Any] | None) -> AiContextIR:
    """Convert a user-provided ai_context dict/TypedDict into an AiContextIR.

    Validates keys and types; raises SemanticDecoratorError with
    INVALID_AI_CONTEXT on invalid keys or wrong types.
    """
    return _shared_build_ai_context(ai_context, on_error=_semantic_ai_context_error)


def _compute_body_ast_hash(fn: Callable[..., Any]) -> str:
    """Compute a SHA-256 hash of the function body AST."""
    try:
        source = inspect.getsource(fn)
        # Dedent to handle functions defined inside decorators/tests
        source = textwrap.dedent(source)
        tree = ast.parse(source)
        # Find the function definition node
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Hash just the body statements (not the decorator or signature)
                body_source = ast.get_source_segment(source, node)
                if body_source is not None:
                    return hashlib.sha256(body_source.encode()).hexdigest()[:16]
        # Fallback: hash the entire source
        return hashlib.sha256(source.encode()).hexdigest()[:16]
    except (OSError, TypeError, IndentationError):
        return hashlib.sha256(b"<unavailable>").hexdigest()[:16]


def _compute_agg_hash(measure_id: str, agg: Any, fold: TimeFoldIR | None) -> str:
    payload = repr(
        {"measure": measure_id, "agg": agg, "fold": (fold.kind, fold.q) if fold else None}
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _build_metric_provenance(
    *,
    source_sql: str | None,
    source_dialect: str | None,
) -> ProvenanceIR:
    return ProvenanceIR(
        source_sql=source_sql,
        source_dialect=source_dialect,
    )


def _validate_unit(unit: str | None, semantic_id: str, object_kind: str = "metric") -> None:
    if unit is None:
        return
    if unit == "" or any(not (0x21 <= ord(ch) <= 0x7E) for ch in unit):
        _raise(
            ErrorKind.INVALID_REF,
            f"{object_kind} {semantic_id!r}: unit must be a non-empty token of printable "
            f"ASCII without whitespace (UCUM case-sensitive code such as 'CNY', "
            f"'%', '1', 'ms', '{{order}}'); got {unit!r}.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
        )


def _normalize_sample_interval(
    sample_interval: tuple[int, str] | None,
    *,
    semantic_id: str,
    data_type: str,
    granularity: str,
) -> SampleIntervalIR | None:
    if sample_interval is None:
        return None
    count, unit = sample_interval
    if unit not in {"minute", "hour"} or count <= 0:
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            "sample_interval must use a positive minute or hour interval.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )
    seconds = count * (60 if unit == "minute" else 3600)
    if 86400 % seconds != 0:
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            "sample_interval must divide one day evenly.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )
    if data_type not in {"datetime", "timestamp"}:
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            "sample_interval is only supported on datetime or timestamp time dimensions.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )
    rank = {
        "second": 0,
        "minute": 1,
        "hour": 2,
        "day": 3,
        "week": 4,
        "month": 5,
        "quarter": 6,
        "year": 7,
    }
    if rank[granularity] > rank[unit]:
        allowed = [g for g, r in sorted(rank.items(), key=lambda kv: kv[1]) if r <= rank[unit]]
        allowed_list = ", ".join(repr(g) for g in allowed)
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            f"time dimension {semantic_id!r}: physical granularity {granularity!r} cannot "
            f"be coarser than sample_interval unit {unit!r}. Set granularity to {unit!r} or "
            f"finer (one of: {allowed_list}).",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )
    return SampleIntervalIR(count=count, unit=unit)  # type: ignore[arg-type]


def _normalize_time_fold(
    time_fold: str | tuple[str, float] | None,
    *,
    semantic_id: str,
) -> TimeFoldIR | None:
    if time_fold is None:
        return None
    if isinstance(time_fold, str):
        if time_fold not in {"mean", "min", "max", "first", "last"}:
            _raise(
                ErrorKind.INVALID_TIME_FOLD,
                f"time_fold {time_fold!r} is not supported.",
                refs=(semantic_id,),
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.TIME_FOLD_VALID,
            )
        return TimeFoldIR(kind=time_fold)  # type: ignore[arg-type]
    kind, q = time_fold
    if kind != "quantile" or not isinstance(q, (float, int)) or not 0 < float(q) < 1:
        _raise(
            ErrorKind.INVALID_TIME_FOLD,
            "quantile time_fold must be ('quantile', q) with 0 < q < 1.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.TIME_FOLD_VALID,
        )
    return TimeFoldIR(kind="quantile", q=float(q))


def _normalize_additivity(additivity: Additivity, *, semantic_id: str) -> Additivity:
    """Validate an Additivity value (literal or SemiAdditive variant)."""
    if isinstance(additivity, SemiAdditive):
        return additivity
    if additivity in ("additive", "non_additive"):
        return additivity
    _raise(
        ErrorKind.INVALID_REF,
        f"Metric {semantic_id!r}: additivity must be 'additive', 'non_additive', "
        "or ms.semi_additive(over=..., fold=...).",
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _caller_location() -> SourceLocation:
    """Best-effort source location from the caller's frame."""
    frame = inspect.currentframe()
    # Walk up: _caller_location -> decorator
    try:
        if frame is not None and frame.f_back is not None:
            caller_frame = frame.f_back
            if caller_frame is not None:
                filename = caller_frame.f_code.co_filename
                lineno = caller_frame.f_lineno
                return SourceLocation(file=filename, line=lineno)
    except AttributeError:
        pass
    return SourceLocation(file="<unknown>", line=0)


def _resolve_ref_string(
    ref: EntityRef
    | DimensionRef
    | TimeDimensionRef
    | MetricRef
    | RelationshipRef
    | DatasourceRef
    | str,
) -> str:
    """Extract semantic_id string from a ref object or pass through a string."""
    if isinstance(ref, str):
        return ref
    return ref.semantic_id


def _resolve_datasource_ref(ref: DatasourceRef | str) -> str:
    """Extract global datasource short name from a datasource ref or string."""
    if isinstance(ref, str):
        return ref
    if isinstance(ref, DatasourceRef):
        return ref.semantic_id
    _raise(
        ErrorKind.INVALID_REF,
        "ms.entity(datasource=...) accepts a datasource ref or global datasource name string.",
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _resolve_dimension_refs(refs: list[DimensionRef | str]) -> tuple[str, ...]:
    """Convert a list of dimension refs/strings to tuple of semantic_ids."""
    return tuple(_resolve_ref_string(r) for r in refs)


def _resolve_entity_refs(refs: list[EntityRef | str] | None) -> tuple[str, ...]:
    """Convert a list of entity refs/strings to tuple of semantic_ids."""
    if refs is None:
        return ()
    return tuple(_resolve_ref_string(r) for r in refs)


def _push_ir(ctx: LoaderContext, ir: Any, callable_: Callable[..., Any] | None) -> None:
    """Push an (IR, callable) pair onto ctx.pending_objects."""
    ctx.pending_objects.append((ir, callable_))


# ---------------------------------------------------------------------------
# Top-level calls
# ---------------------------------------------------------------------------


def domain(
    *,
    name: str,
    default: bool = True,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> DomainRef:
    """Declare a semantic domain namespace inside a project file.

    A domain groups entities, dimensions, metrics, and relationships under a single
    qualified name (``<domain>.<object>``). Must be called at module top-level
    inside a ``models/semantic/<model>/*.py`` project file.

    Args:
        name: Domain namespace, e.g. ``"sales"``.
        default: If True, subsequent decorators in this file resolve to this
            domain when no explicit ``domain=`` kwarg is passed.
        description: Free-text description; surfaced in agent/help output.
        ai_context: Optional ``AiContext`` (or compatible dict) with extra
            agent-facing hints.

    Returns:
        A ``DomainRef`` that can be passed as the ``domain=`` kwarg to other
        decorators to override the default domain context.

    Raises:
        OutsideLoaderContextError: Called outside a semantic loader pass.
        SemanticDecoratorError: ``name`` collides with another domain in the project.

    Example:
        >>> import marivo.semantic as ms
        >>> sales = ms.domain(name="sales", default=True)
    """
    ctx = _require_ctx()
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    ir = DomainIR(
        name=name,
        description=description,
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
    measure: DimensionRef | str,
    agg: AggKind,
    fold: str | tuple[Literal["quantile"], float] | None = None,
    name: str | None = None,
    unit: str | None = None,
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> MetricRef:
    """Declare a tier-1 simple metric: an aggregation over a measure dimension.

    The metric inherits its additivity nature from ``measure`` (resolved at load);
    ``fold`` overrides the time-fold for semi-additive measures only. No function body.

    Args:
        measure: Measure dimension to aggregate.
        agg: Aggregation kind (``"sum"``, ``"mean"``, ``"count"``, etc.).
        fold: Time-fold override for semi-additive measures.
        name: Metric name. Defaults to the measure's column name.
        unit: Override the unit derived from ``measure`` at load. Leave None to
            inherit the measure's unit (count/count_distinct derive nothing).
        domain: Override the active domain.
        description: Free-text description.
        ai_context: Optional ``AiContext`` with extra agent-facing hints.

    Example:
        >>> revenue = ms.aggregate(measure=amount, agg="sum")
        >>> average_inventory = ms.aggregate(measure=quantity, agg="sum", fold="avg")
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    measure_id = _resolve_ref_string(measure)
    # measure dimension ids are entity-qualified: "<domain>.<entity>.<column>"
    entity_id = measure_id.rsplit(".", 1)[0]
    obj_name = name or measure_id.rsplit(".", 1)[-1]
    semantic_id = f"{resolved_domain}.{obj_name}"
    _check_duplicate(ctx, semantic_id, MetricIR)
    _validate_unit(unit, semantic_id)
    fold_ir = _normalize_time_fold(fold, semantic_id=semantic_id) if fold is not None else None
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()
    metric_ir = MetricIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        name=obj_name,
        metric_type="simple",
        entities=(entity_id,),
        aggregation=agg,
        measure=measure_id,
        composition=None,
        additivity=None,  # resolved at load: downgrade(measure.additivity, agg) + fold override
        provenance=_build_metric_provenance(source_sql=None, source_dialect=None),
        description=description,
        ai_context=ai_ctx,
        body_ast_hash=_compute_agg_hash(measure_id, agg, fold_ir),
        python_symbol=obj_name,
        location=location,
        root_entity=entity_id,
        fold_override=fold_ir,
        unit=unit,
    )
    _push_ir(ctx, metric_ir, None)
    return MetricRef(semantic_id)


def simple_metric(
    *,
    name: str | None = None,
    entities: list[EntityRef | str],
    additivity: Additivity,
    root_entity: EntityRef | str | None = None,
    fanout_policy: Literal["block", "aggregate_then_join"] = "block",
    unit: str | None = None,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], MetricRef]:
    """Declare a tier-2 simple metric from an ibis body. Declares ``additivity`` directly.

    Tier-2 metrics have no measure to derive from; declare unit directly.

    Example:
        >>> @ms.simple_metric(entities=[orders], additivity="additive")
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
        ds_refs = _resolve_entity_refs(entities)
        if len(ds_refs) == 0:
            _raise(
                ErrorKind.MISSING_DATASETS,
                "@ms.simple_metric(...) requires non-empty entities.",
                refs=(semantic_id,),
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.METRIC_DATASETS_REQUIRED,
            )
        body_hash = validate_metric_body_ast(fn, "base")
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()
        prov_ir = _build_metric_provenance(source_sql=source_sql, source_dialect=source_dialect)
        root_ref = _resolve_ref_string(root_entity) if root_entity is not None else None
        if root_ref is None and len(ds_refs) == 1:
            root_ref = ds_refs[0]
        metric_ir = MetricIR(
            semantic_id=semantic_id,
            domain=resolved_domain,
            name=obj_name,
            metric_type="simple",
            entities=ds_refs,
            aggregation=None,
            measure=None,
            composition=None,
            additivity=_normalize_additivity(additivity, semantic_id=semantic_id),
            provenance=prov_ir,
            description=description,
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


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def table(name: str, /, *, database: str | tuple[str, ...] | None = None) -> TableSourceIR:
    """Build a structured table source for ``ms.entity(source=...)``."""
    return _datasource_table(name, database=database)


def file(
    path: str,
    /,
    *,
    format: Literal["parquet", "csv"],
    **options: Any,
) -> FileSourceIR:
    """Build a structured file source for ``ms.entity(source=...)``."""
    try:
        return _datasource_file(path, format=format, **options)
    except ValueError as exc:
        _raise(
            ErrorKind.INVALID_REF,
            str(exc).replace("md.file", "ms.file"),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )


def entity(
    *,
    name: str,
    datasource: DatasourceRef | str,
    source: EntitySourceIR,
    primary_key: list[str] | None = None,
    versioning: SnapshotVersioningIR | ValidityVersioningIR | None = None,
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> EntityRef:
    """Declare an entity over a structured physical source.

    Args:
        name: Entity name.
        datasource: Datasource ref returned by ``md.ref(...)`` or a global
            datasource name string declared in ``models/datasources/*.py``.
        source: Structured physical source, usually ``ms.table(...)`` or
            ``ms.file(...)``.
        primary_key: Optional list of column names forming the primary key.
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        description: Free-text description; surfaced in agent/help output.
        ai_context: Optional ``AiContext`` with extra agent-facing hints.

    Returns:
        An ``EntityRef`` usable by ``@ms.dimension`` and ``@ms.metric``.

    Raises:
        SemanticDecoratorError: ``datasource`` is not a datasource ref or string, ``name``
            collides with another object, or ``source`` is not an entity source.

    Example:
        >>> orders = ms.entity(
        ...     name="orders",
        ...     datasource="warehouse",
        ...     source=ms.table("orders", database="sales_mart"),
        ... )
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    semantic_id = f"{resolved_domain}.{name}"
    _check_duplicate(ctx, semantic_id, EntityIR)
    if not isinstance(source, (TableSourceIR, FileSourceIR)):
        _raise(
            ErrorKind.INVALID_REF,
            "ms.entity(source=...) accepts ms.table(...) or ms.file(...).",
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
        description=description,
        ai_context=ai_ctx,
        python_symbol=name,
        location=location,
        versioning=versioning,
    )
    _push_ir(ctx, ir, None)

    return EntityRef(semantic_id)


def dimension(
    *,
    name: str | None = None,
    entity: EntityRef | str,
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
    kind: Literal["categorical", "measure"] = "categorical",
    additivity: Additivity | None = None,
    unit: str | None = None,
) -> Callable[[Callable[..., Any]], DimensionRef]:
    """Declare a dimension whose body returns an ibis expression over its entity.

    The decorated function takes the entity table and returns a single
    expression (single-return AST). Use this for both raw columns and derived
    expressions (e.g. ``table.amount * 100``).

    Args:
        name: Dimension name. Defaults to the function name.
        entity: Owning entity, either an ``EntityRef`` or a qualified
            ``"<domain>.<entity>"`` string.
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        description: Free-text description.
        ai_context: Optional ``AiContext`` with extra agent-facing hints.
        kind: ``"categorical"`` for qualitative/grouping dimensions (default) or
            ``"measure"`` for quantitative/fact dimensions.
        unit: UCUM unit token for a measure dimension (the authoritative declaration
            site). Only valid when ``kind="measure"``.

    Returns:
        A decorator that returns a ``DimensionRef``.

    Raises:
        SemanticDecoratorError: ``entity`` is unknown, ``name`` collides, or the
            body violates the AST whitelist.

    Example:
        >>> @ms.dimension(name="amount_cents", entity=orders)
        ... def amount_cents(orders):
        ...     return orders.amount * 100
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    if kind not in ("categorical", "measure"):
        _raise(
            ErrorKind.INVALID_REF,
            f"Dimension kind must be 'categorical' or 'measure', got {kind!r}.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    if additivity is not None and kind != "measure":
        _raise(
            ErrorKind.INVALID_REF,
            "additivity is only valid on kind='measure' dimensions.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    if unit is not None and kind != "measure":
        _raise(
            ErrorKind.INVALID_REF,
            "unit is only valid on kind='measure' dimensions.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )

    def decorator(fn: Callable[..., Any]) -> DimensionRef:
        obj_name = name or fn.__name__
        ds_ref = _resolve_ref_string(entity)
        semantic_id = f"{ds_ref}.{obj_name}"
        ds_domain = ds_ref.split(".", 1)[0]
        if ds_domain != resolved_domain:
            _raise(
                ErrorKind.INVALID_REF,
                f"Dimension {semantic_id!r} belongs to entity in domain {ds_domain!r}, "
                f"but the active domain is {resolved_domain!r}.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
                constraint_id=ConstraintId.REF_SHAPE,
            )
        _check_duplicate(ctx, semantic_id, DimensionIR)
        _validate_unit(unit, semantic_id, "measure dimension")

        validate_metric_body_ast(fn, "base")
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()

        ir = DimensionIR(
            semantic_id=semantic_id,
            domain=resolved_domain,
            entity=ds_ref,
            name=obj_name,
            description=description,
            ai_context=ai_ctx,
            is_time_dimension=False,
            kind=DimensionKind(kind),
            data_type=None,
            granularity=None,
            required_prefix=None,
            python_symbol=fn.__name__,
            location=location,
            additivity=_normalize_additivity(additivity, semantic_id=semantic_id)
            if additivity is not None
            else None,
            unit=unit,
        )
        _push_ir(ctx, ir, fn)

        ref = DimensionRef(semantic_id)
        ctx.pending_refs.append(ref)
        return ref

    return decorator


def time_dimension(
    *,
    name: str | None = None,
    entity: EntityRef | str,
    data_type: Literal["date", "datetime", "timestamp", "string", "integer"],
    granularity: Literal["year", "quarter", "month", "week", "day", "hour", "minute", "second"],
    date_format: str | None = None,
    required_prefix: str | None = None,
    timezone: str | None = None,
    is_default: bool = False,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], TimeDimensionRef]:
    """Declare a time-aware dimension that carries grain and parsing metadata.

    Time dimensions are the only dimensions usable as window axes by ``session.observe``.
    The body may return any ibis expression that represents the intended time
    axis. For sortable day/hour partition columns, prefer returning the raw
    column with ``data_type="string"`` or ``data_type="integer"`` plus
    ``date_format`` so window predicates can remain pushdown-friendly.

    Args:
        name: Dimension name. Defaults to the function name.
        entity: Owning entity (``EntityRef`` or qualified string).
        data_type: ``date | datetime | timestamp | string | integer``.
        granularity: ``year | quarter | month | week | day | hour | minute | second`` — the
            finest grain at which queries are meaningful.
        date_format: Canonical Python strptime format string (e.g. ``"%Y%m%d"``,
            ``"%Y-%m-%d"``, ``"%Y%m%d%H"``, ``"%Y-%m-%d %H:%M:%S"``). Required
            when ``data_type="string"`` or ``data_type="integer"`` without
            ``required_prefix``; forbidden otherwise (temporal ``data_type`` or
            hour-only dimensions with ``required_prefix``). Shorthand aliases like
            ``"yyyymmdd"`` are no longer accepted — write the ``%``-prefixed
            strptime form.
        required_prefix: Optional fixed prefix the source value must start with.
        timezone: Optional IANA timezone for timestamp-like values. For naive
            timestamp expressions and time-bearing string/integer formats,
            Marivo interprets source values in this timezone before converting
            them to the analysis session timezone for windowing and bucketing.
            Day partition encodings such as ``"%Y%m%d"`` should omit it so
            predicates stay as raw partition comparisons. Omitting
            ``timezone`` for ``data_type="datetime"`` or ``"timestamp"``
            (or time-bearing string/integer formats) triggers a blocking
            readiness issue — the agent must declare the data timezone
            explicitly to avoid silently incorrect bucketing.
        is_default: Mark this dimension as the default time axis when multiple time dimensions
            exist on the entity. At most one time dimension per entity may carry
            is_default=True. When observe() is called without time_dimension=, the default
            dimension is used automatically.
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        description: Free-text description.
        ai_context: Optional ``AiContext`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``TimeDimensionRef``.

    Raises:
        SemanticDecoratorError: ``entity`` is unknown, ``name`` collides, or the
            body violates the AST whitelist.

    Example:
        >>> @ms.time_dimension(name="created_at", entity=orders,
        ...                data_type="datetime", granularity="day")
        ... def created_at(orders):
        ...     return orders.created_at
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    def decorator(fn: Callable[..., Any]) -> TimeDimensionRef:
        obj_name = name or fn.__name__
        ds_ref = _resolve_ref_string(entity)
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

        validate_metric_body_ast(fn, "base")
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()

        if timezone is not None:
            try:
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError:
                _raise(
                    ErrorKind.INVALID_REF,
                    f"timezone {timezone!r} is not a valid IANA timezone name.",
                    refs=(semantic_id,),
                    cls=SemanticDecoratorError,
                )

        # Enforce the strptime-only author surface:
        #   - temporal data_types forbid date_format (column is already temporal)
        #   - hour-only dimensions (required_prefix) forbid date_format
        #   - string/integer without required_prefix REQUIRE a canonical strptime
        if date_format is not None:
            if data_type in {"date", "datetime", "timestamp"}:
                _raise(
                    ErrorKind.INVALID_REF,
                    f"time dimension {semantic_id!r}: date_format is not allowed when "
                    f"data_type is {data_type!r} (column is already temporal).",
                    refs=(semantic_id,),
                    cls=SemanticDecoratorError,
                )
            if required_prefix is not None:
                _raise(
                    ErrorKind.INVALID_REF,
                    f"time dimension {semantic_id!r}: date_format is not allowed on "
                    f"hour-only dimensions (those that use required_prefix).",
                    refs=(semantic_id,),
                    cls=SemanticDecoratorError,
                )
            try:
                normalized_format = normalize_strptime(date_format)
            except ValueError as exc:
                _raise(
                    ErrorKind.INVALID_REF,
                    f"time dimension {semantic_id!r}: {exc}",
                    refs=(semantic_id,),
                    cls=SemanticDecoratorError,
                )
        else:
            if data_type in {"string", "integer"} and required_prefix is None:
                _raise(
                    ErrorKind.INVALID_REF,
                    f"time dimension {semantic_id!r}: data_type {data_type!r} requires "
                    f"a strptime date_format (e.g. '%Y%m%d') or a required_prefix.",
                    refs=(semantic_id,),
                    cls=SemanticDecoratorError,
                )
            normalized_format = None

        ir = DimensionIR(
            semantic_id=semantic_id,
            domain=resolved_domain,
            entity=ds_ref,
            name=obj_name,
            description=description,
            ai_context=ai_ctx,
            is_time_dimension=True,
            kind=DimensionKind.TIME,
            data_type=data_type,
            granularity=granularity,
            required_prefix=required_prefix,
            python_symbol=fn.__name__,
            location=location,
            format=normalized_format,
            timezone=timezone,
            is_default=is_default,
            sample_interval=_normalize_sample_interval(
                sample_interval,
                semantic_id=semantic_id,
                data_type=data_type,
                granularity=granularity,
            ),
        )
        _push_ir(ctx, ir, fn)

        ref = TimeDimensionRef(semantic_id)
        ctx.pending_refs.append(ref)
        return ref

    return decorator


def relationship(
    *,
    name: str | None = None,
    from_entity: EntityRef | str,
    to_entity: EntityRef | str,
    from_dimensions: list[DimensionRef | str],
    to_dimensions: list[DimensionRef | str],
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> RelationshipRef:
    """Declare a join relationship between two entities.

    Top-level call (not a decorator). Used by the compiler to plan joins when a
    metric or dimension references dimensions across related entities.

    Args:
        name: Required relationship name (no default).
        from_entity: Source entity (``EntityRef`` or qualified string).
        to_entity: Target entity (``EntityRef`` or qualified string).
        from_dimensions: Columns on ``from_entity`` (``DimensionRef`` / qualified strings).
        to_dimensions: Columns on ``to_entity`` — must align positionally with ``from_dimensions``.
        domain: Override the active domain namespace with a ``DomainRef`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        description: Free-text description.
        ai_context: Optional ``AiContext`` with extra agent-facing hints.

    Returns:
        A ``RelationshipRef``.

    Raises:
        SemanticDecoratorError: ``name`` is missing, the entities are unknown, or
            ``from_dimensions`` / ``to_dimensions`` lengths disagree.

    Example:
        >>> ms.relationship(
        ...     name="orders_to_customers",
        ...     from_entity=orders, to_entity=customers,
        ...     from_dimensions=["customer_id"], to_dimensions=["id"],
        ... )
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    if name is None:
        _raise(
            ErrorKind.MISSING_DOMAIN,
            "relationship requires a 'name' argument.",
            cls=SemanticDecoratorError,
        )

    semantic_id = f"{resolved_domain}.{name}"
    _check_duplicate(ctx, semantic_id, RelationshipIR)

    from_ds = _resolve_ref_string(from_entity)
    to_ds = _resolve_ref_string(to_entity)
    from_f = _resolve_dimension_refs(from_dimensions)
    to_f = _resolve_dimension_refs(to_dimensions)
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    ir = RelationshipIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        name=name,
        from_entity=from_ds,
        to_entity=to_ds,
        from_dimensions=from_f,
        to_dimensions=to_f,
        description=description,
        ai_context=ai_ctx,
        location=location,
    )
    _push_ir(ctx, ir, None)

    return RelationshipRef(semantic_id)


# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------


def snapshot(
    *,
    partition_field: DimensionRef | TimeDimensionRef | str,
    grain: Literal["day"],
    timezone: str | None = None,
    format: str | None = None,
) -> SnapshotVersioningIR:
    """Declare daily snapshot partition versioning for an entity."""
    if isinstance(partition_field, (DimensionRef, TimeDimensionRef)):
        partition_ref = partition_field.semantic_id
    else:
        partition_ref = partition_field
    if grain != "day":
        _raise(
            ErrorKind.INVALID_REF,
            "snapshot versioning currently supports only grain='day'.",
            cls=SemanticDecoratorError,
        )
    if timezone is not None:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            _raise(
                ErrorKind.INVALID_REF,
                f"timezone {timezone!r} is not a valid IANA timezone name.",
                cls=SemanticDecoratorError,
            )
    return SnapshotVersioningIR(
        kind="snapshot",
        partition_field=partition_ref,
        grain="day",
        timezone=timezone,
        format=format,
    )


def validity(
    *,
    valid_from: DimensionRef | str,
    valid_to: DimensionRef | str,
    interval: Literal["closed_open", "closed_closed"],
    open_end: tuple[Any, ...],
    timezone: str | None = None,
) -> ValidityVersioningIR:
    """Declare SCD2 validity interval versioning for an entity.

    Args:
        valid_from: Dimension semantic id (or DimensionRef) for the interval start column.
        valid_to: Dimension semantic id (or DimensionRef) for the interval end column.
        interval: ``"closed_open"`` (``[valid_from, valid_to)``) or
            ``"closed_closed"`` (``[valid_from, valid_to]``).
        open_end: Non-empty tuple of sentinel values that mean "still current"
            in the ``valid_to`` column. Use ``None`` for SQL NULL, or a string
            sentinel such as ``"9999-12-31"``.
        timezone: Optional IANA timezone name for anchor date casting.

    Returns:
        A ``ValidityVersioningIR`` for use in ``ms.entity(versioning=...)``.

    Raises:
        SemanticDecoratorError: ``interval`` is not one of the two allowed values,
            ``open_end`` is empty, or ``timezone`` is not a valid IANA name.
    """
    if interval not in ("closed_open", "closed_closed"):
        _raise(
            ErrorKind.INVALID_ENTITY_VERSIONING,
            f"validity versioning interval must be 'closed_open' or 'closed_closed', "
            f"got {interval!r}.",
            cls=SemanticDecoratorError,
            details={"field": "interval", "reason": f"unsupported interval value {interval!r}"},
        )
    if not open_end:
        _raise(
            ErrorKind.INVALID_ENTITY_VERSIONING,
            "validity versioning open_end must be a non-empty tuple.",
            cls=SemanticDecoratorError,
            details={"field": "open_end", "reason": "empty tuple is not allowed"},
        )
    if timezone is not None:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            _raise(
                ErrorKind.INVALID_ENTITY_VERSIONING,
                f"timezone {timezone!r} is not a valid IANA timezone name.",
                cls=SemanticDecoratorError,
                details={"field": "timezone", "reason": f"unknown IANA timezone {timezone!r}"},
            )
    valid_from_ref = (
        valid_from.semantic_id
        if isinstance(valid_from, (DimensionRef, TimeDimensionRef))
        else valid_from
    )
    valid_to_ref = (
        valid_to.semantic_id if isinstance(valid_to, (DimensionRef, TimeDimensionRef)) else valid_to
    )
    return ValidityVersioningIR(
        kind="validity",
        valid_from=valid_from_ref,
        valid_to=valid_to_ref,
        interval=interval,
        open_end=open_end,
        timezone=timezone,
    )


def semi_additive(
    *,
    over: TimeDimensionRef | str,
    fold: str | tuple[Literal["quantile"], float],
) -> SemiAdditive:
    """Declare a semi-additive nature: additive off the ``over`` time axis, folded by ``fold``.

    Use as the ``additivity=`` value on a measure dimension or a tier-2 simple metric::

        @ms.dimension(kind="measure", entity=inventory,
                      additivity=ms.semi_additive(over=snapshot_date, fold="last"))
        def quantity(inventory):
            return inventory.qty
    """
    over_id = _resolve_ref_string(over)
    fold_ir = _normalize_time_fold(fold, semantic_id=over_id)
    if fold_ir is None:
        _raise(
            ErrorKind.INVALID_REF,
            "ms.semi_additive(...) requires a fold (e.g. 'last', 'max', ('quantile', 0.9)).",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    return SemiAdditive(over=over_id, fold=fold_ir)


def _compute_composition_hash(composition: Composition) -> str:
    if isinstance(composition, RatioComposition):
        text = repr(("ratio", composition.numerator, composition.denominator))
    elif isinstance(composition, WeightedAverageComposition):
        text = repr(("weighted_average", composition.value, composition.weight))
    else:  # LinearComposition
        text = repr(("linear", tuple((t.sign, t.metric) for t in composition.terms)))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _derived(
    *,
    name: str,
    composition: Composition,
    unit: str | None,
    domain: DomainRef | None,
    description: str | None,
    ai_context: AiContext | dict[str, Any] | None,
) -> MetricRef:
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    semantic_id = f"{resolved_domain}.{name}"
    _check_duplicate(ctx, semantic_id, MetricIR)
    _validate_unit(unit, semantic_id)
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()
    metric_ir = MetricIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        name=name,
        metric_type="derived",
        entities=(),
        aggregation=None,
        measure=None,
        composition=composition,
        additivity=None,  # propagated at load from components
        provenance=_build_metric_provenance(source_sql=None, source_dialect=None),
        description=description,
        ai_context=ai_ctx,
        body_ast_hash=_compute_composition_hash(composition),
        python_symbol=name,
        location=location,
        unit=unit,
    )
    _push_ir(ctx, metric_ir, None)
    return MetricRef(semantic_id)


def ratio(
    *,
    name: str,
    numerator: MetricRef | str,
    denominator: MetricRef | str,
    unit: str | None = None,
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> MetricRef:
    """Declare a derived ratio metric (no body). Override the unit derived from the components at load.

    Example::

    loss_rate = ms.ratio(name="loss_rate", numerator=lost, denominator=total, unit="1")
    """
    return _derived(
        name=name,
        composition=RatioComposition(
            numerator=_resolve_ref_string(numerator),
            denominator=_resolve_ref_string(denominator),
        ),
        unit=unit,
        domain=domain,
        description=description,
        ai_context=ai_context,
    )


def weighted_average(
    *,
    name: str,
    value: MetricRef | str,
    weight: MetricRef | str,
    unit: str | None = None,
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> MetricRef:
    """Declare a derived weighted-average metric (no body). Override the unit derived from the components at load.

    Roles are ``value`` / ``weight``."""
    return _derived(
        name=name,
        composition=WeightedAverageComposition(
            value=_resolve_ref_string(value),
            weight=_resolve_ref_string(weight),
        ),
        unit=unit,
        domain=domain,
        description=description,
        ai_context=ai_context,
    )


def linear(
    *,
    name: str,
    add: list[MetricRef | str],
    subtract: list[MetricRef | str] | tuple[MetricRef | str, ...] = (),
    unit: str | None = None,
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> MetricRef:
    """Declare a derived linear metric (no body): sum of ``add`` minus ``subtract``. Override the unit derived from the components at load.

    Example::

        net_revenue = ms.linear(name="net_revenue", add=[gross], subtract=[refunds])
    """
    terms = tuple(LinearTerm("+", _resolve_ref_string(m)) for m in add) + tuple(
        LinearTerm("-", _resolve_ref_string(m)) for m in subtract
    )
    if len(terms) < 2:
        _raise(
            ErrorKind.INVALID_REF,
            f"ms.linear(name={name!r}) requires at least two metric terms.",
            refs=(name,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    return _derived(
        name=name,
        composition=LinearComposition(terms=terms),
        unit=unit,
        domain=domain,
        description=description,
        ai_context=ai_context,
    )


def ref(id: str) -> str:
    """Reference a semantic object by qualified ``"<domain>.<object>"`` string.

    Pass-through helper: it returns ``id`` unchanged but makes intent
    explicit at the call site (``datasets=[ms.ref("sales.orders")]``).
    """
    return id
