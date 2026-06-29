"""Authoring decorators and builders for marivo.semantic v1.1.

All authoring symbols (domain, entity, dimension, measure, metric,
time_dimension, aggregate, ratio, weighted_average, linear,
semi_additive, relationship, ref, from_sql, join_on) are defined here.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
from collections.abc import Callable
from collections.abc import Sequence as _Sequence
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.scan import csv as _datasource_csv
from marivo.datasource.scan import parquet as _datasource_parquet
from marivo.datasource.scan import table as _datasource_table
from marivo.refs import SemanticRef, SymbolKind
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    Additivity,
    AggKind,
    AiContextIR,
    Composition,
    CsvSourceIR,
    DateParse,
    DatetimeParse,
    DimensionIR,
    DimensionKind,
    DomainIR,
    EntityIR,
    EntitySourceIR,
    HourPrefixParse,
    JoinKey,
    LinearComposition,
    LinearTerm,
    MeasureIR,
    MetricIR,
    ParquetSourceIR,
    RatioComposition,
    RelationshipIR,
    SampleIntervalIR,
    SemanticParse,
    SemiAdditive,
    SnapshotVersioningIR,
    SourceLocation,
    SqlProvenance,
    StrptimeParse,
    TableSourceIR,
    TimeFoldIR,
    TimestampParse,
    ValidityVersioningIR,
    WeightedAverageComposition,
    is_time_bearing_format,
)
from marivo.semantic.loader import _LOADER_CTX, LoaderContext
from marivo.semantic.refs import (
    DimensionRef,
    DomainRef,
    EntityRef,
    MeasureRef,
    MetricRef,
    RelationshipRef,
    TimeDimensionRef,
    make_ref,
)
from marivo.semantic.time_format import normalize_strptime
from marivo.semantic.typing import AiContextValue
from marivo.semantic.validator import validate_metric_body_ast

__all__ = [
    "DomainRef",
    "aggregate",
    "ai_context",
    "count",
    "csv",
    "datetime",
    "dimension",
    "dimension_column",
    "domain",
    "entity",
    "from_sql",
    "hour_prefix",
    "join_on",
    "linear",
    "measure",
    "measure_column",
    "metric",
    "parquet",
    "ratio",
    "ref",
    "relationship",
    "semi_additive",
    "snapshot",
    "strptime",
    "table",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
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
        return explicit.id
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
    return type(ir).__name__


def _check_duplicate(
    ctx: LoaderContext,
    semantic_id: str,
    ir_type: type[EntityIR | DimensionIR | MeasureIR | MetricIR | RelationshipIR],
) -> None:
    """Raise DUPLICATE_NAME if semantic_id already in pending_objects of the same kind.

    Also checks for cross-kind collisions between DimensionIR and MeasureIR,
    which share the entity-qualified namespace (``<domain>.<entity>.<field>``).
    """
    # DimensionIR and MeasureIR share the same semantic_id namespace, so a
    # dimension "sales.orders.amount" and a measure "sales.orders.amount" would
    # silently collide at the catalog/assembly layer. Other kinds (EntityIR,
    # MetricIR, RelationshipIR) use the domain-qualified namespace and are
    # intentionally allowed to share names with different kinds.
    _cross_kinds: set[type[DimensionIR | MeasureIR]] = {DimensionIR, MeasureIR}
    for ir, _ in ctx.pending_objects:
        if not isinstance(ir, (EntityIR, DimensionIR, MeasureIR, MetricIR, RelationshipIR)):
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
        # Cross-kind collision: only dimension vs measure (same entity-qualified namespace)
        if ir_type in _cross_kinds and type(ir) in _cross_kinds:
            _raise(
                ErrorKind.DUPLICATE_NAME,
                f"Name conflict: {semantic_id!r} is already claimed by a {existing_kind}. "
                f"Use a different name for this object.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
            )


def ai_context(
    *,
    business_definition: str | None = None,
    guardrails: _Sequence[str] | None = None,
    synonyms: _Sequence[str] | None = None,
    examples: _Sequence[str] | None = None,
    instructions: str | None = None,
    owner_notes: str | None = None,
) -> AiContextValue:
    """Construct a validated AiContext for semantic objects.

    Provides typed, IDE-friendly construction of AI context with eager
    validation.  Invalid key names are caught at call time by Python's
    keyword argument checking; value-type mismatches raise
    ``SemanticDecoratorError`` with ``[invalid_ai_context]`` including
    the caller's file and line.

    Args:
        business_definition: Plain-language description of what the object represents.
        guardrails: Constraints on how the object should be used.
        synonyms: Alternative names for the object.
        examples: Example questions or use cases.
        instructions: Usage guidance for AI agents.
        owner_notes: Team or ownership notes.

    Returns:
        A validated ``AiContextValue`` for use with ``ai_context=`` parameters.

    Example:
        >>> ctx = ms.ai_context(
        ...     business_definition="Total revenue from all orders",
        ...     guardrails=["Do not use for margin calculations"],
        ...     synonyms=["total_sales"],
        ... )
        >>> revenue = ms.aggregate(name="revenue", measure=amount, agg="sum", ai_context=ctx)

    Raises:
        SemanticDecoratorError: If any value has the wrong type.
    """
    location = _user_caller_location()

    # Validate list-type fields
    for label, value in (
        ("guardrails", guardrails),
        ("synonyms", synonyms),
        ("examples", examples),
    ):
        if value is None:
            continue
        if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
            _raise(
                ErrorKind.INVALID_AI_CONTEXT,
                f"ms.ai_context({label}=...) requires list[str] or tuple[str, ...], "
                f"got {type(value).__name__}.",
                cls=SemanticDecoratorError,
                location=location,
            )

    # Validate string-type fields
    for label, value in (
        ("business_definition", business_definition),
        ("instructions", instructions),
        ("owner_notes", owner_notes),
    ):
        if value is None:
            continue
        if not isinstance(value, str):
            _raise(
                ErrorKind.INVALID_AI_CONTEXT,
                f"ms.ai_context({label}=...) requires str, got {type(value).__name__}.",
                cls=SemanticDecoratorError,
                location=location,
            )

    return AiContextValue(
        business_definition=business_definition,
        guardrails=tuple(guardrails) if guardrails is not None else (),
        synonyms=tuple(synonyms) if synonyms is not None else (),
        examples=tuple(examples) if examples is not None else (),
        instructions=instructions,
        owner_notes=owner_notes,
    )


def _build_ai_context(ai_context: AiContextValue | None) -> AiContextIR:
    """Convert a validated AiContextValue into an AiContextIR.

    Rejects raw dicts with a teachable error directing the user to
    ``ms.ai_context(...)``.  Since ``AiContextValue`` is validated at
    construction time by ``ms.ai_context()`` or ``__post_init__``, no
    further validation is needed for genuine ``AiContextValue`` instances.
    """
    if ai_context is None:
        return AiContextIR()
    if not isinstance(ai_context, AiContextValue):
        _raise(
            ErrorKind.INVALID_AI_CONTEXT,
            "ai_context no longer accepts raw dicts. "
            "Use ms.ai_context(business_definition=..., guardrails=[...]) "
            "to construct an AiContextValue.",
            cls=SemanticDecoratorError,
        )
    return AiContextIR(
        business_definition=ai_context.business_definition,
        guardrails=ai_context.guardrails,
        synonyms=ai_context.synonyms,
        examples=ai_context.examples,
        instructions=ai_context.instructions,
        owner_notes=ai_context.owner_notes,
    )


def _validate_metric_provenance(provenance: SqlProvenance | None) -> None:
    if provenance is None:
        return
    if not isinstance(provenance, SqlProvenance):
        _raise(
            ErrorKind.INVALID_REF,
            "metric provenance must be constructed with ms.from_sql(sql=..., dialect=...).",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )


def _validate_time_parse(parse: SemanticParse | None) -> None:
    if parse is None:
        return
    if not isinstance(
        parse,
        (DateParse, DatetimeParse, TimestampParse, StrptimeParse, HourPrefixParse),
    ):
        _raise(
            ErrorKind.INVALID_REF,
            "time_dimension(parse=...) accepts ms.datetime(...), ms.timestamp(...), "
            "ms.strptime(...), ms.hour_prefix(...), or None.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )


def _validate_relationship_keys(
    keys: tuple[object, ...], *, semantic_id: str
) -> tuple[JoinKey, ...]:
    resolved: list[JoinKey] = []
    for key in keys:
        if not isinstance(key, JoinKey):
            _raise(
                ErrorKind.INVALID_REF,
                "ms.relationship(keys=...) accepts only ms.join_on(from_key, to_key) values.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
                constraint_id=ConstraintId.REF_SHAPE,
            )
        resolved.append(key)
    return tuple(resolved)


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
    if data_type not in {"datetime", "timestamp", "string", "integer"}:
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            "sample_interval is only supported on datetime, timestamp, string, or integer time dimensions.",
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


def _validate_timezone(timezone: str) -> None:
    """Validate that timezone is a valid IANA timezone name."""
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        _raise(
            ErrorKind.INVALID_REF,
            f"timezone {timezone!r} is not a valid IANA timezone name.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )


def _normalize_sample_interval_value(
    sample_interval: tuple[int, Literal["minute", "hour"]] | None,
) -> SampleIntervalIR | None:
    """Normalize a sample_interval tuple from a parse builder, using a synthetic semantic_id."""
    if sample_interval is None:
        return None
    _count, unit = sample_interval
    return _normalize_sample_interval(
        sample_interval,
        semantic_id="<parse>",
        data_type="datetime",
        granularity=unit,
    )


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
        "or ms.semi_additive(over=..., fold=...). "
        "Note: use underscores, not hyphens (e.g. 'non_additive', not 'non-additive').",
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _caller_location() -> SourceLocation:
    """Best-effort source location from the caller's frame.

    Walks up 1 frame: ``_caller_location`` -> caller (decorator).
    Reports the file/line of the decorator call site.
    """
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


def _user_caller_location() -> SourceLocation:
    """Best-effort source location of the user's call site.

    Walks up 2 frames: ``_user_caller_location`` -> internal wrapper
    (e.g. ``ai_context``) -> user code.  Reports the file/line where
    the user called the public function.
    """
    frame = inspect.currentframe()
    try:
        if frame is not None and frame.f_back is not None:
            wrapper_frame = frame.f_back
            if wrapper_frame.f_back is not None:
                user_frame = wrapper_frame.f_back
                filename = user_frame.f_code.co_filename
                lineno = user_frame.f_lineno
                return SourceLocation(file=filename, line=lineno)
            # Fallback: report the wrapper itself
            filename = wrapper_frame.f_code.co_filename
            lineno = wrapper_frame.f_lineno
            return SourceLocation(file=filename, line=lineno)
    except AttributeError:
        pass
    return SourceLocation(file="<unknown>", line=0)


def _format_expected_ref(expected: tuple[type[SemanticRef], ...]) -> str:
    names = []
    for cls in expected:
        name = cls.__name__
        article = "an" if name[0].lower() in {"a", "e", "i", "o", "u"} else "a"
        names.append(f"{article} {name}")
    return " or ".join(names)


def _require_ref_id(
    ref: object,
    *,
    parameter: str,
    expected: tuple[type[SemanticRef], ...],
) -> str:
    if isinstance(ref, expected):
        return ref.id
    expected_label = _format_expected_ref(expected)
    received = getattr(ref, "id", ref)
    _raise(
        ErrorKind.INVALID_REF,
        f"{parameter} must be {expected_label}; got {type(ref).__name__}: {received!r}. "
        "Pass the Ref object returned by the semantic authoring call, import a declared "
        "Ref from another model, or use ms.ref('<kind>.<semantic_id>') for explicit "
        "forward/cross-file references.",
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _domain_from_ref_id(ref_id: str) -> str:
    return ref_id.split(".", 1)[0]


def _require_entity_ref(ref: EntityRef, *, parameter: str) -> EntityRef:
    if isinstance(ref, EntityRef):
        return ref
    _require_ref_id(ref, parameter=parameter, expected=(EntityRef,))


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


def _resolve_datasource_ref(ref: DatasourceRef) -> str:
    """Extract canonical datasource id from a datasource ref."""
    if isinstance(ref, DatasourceRef):
        return ref.id
    _raise(
        ErrorKind.INVALID_REF,
        'ms.entity(datasource=...) accepts a datasource ref from md.ref("datasource.warehouse"). '
        "Do not pass a bare string such as 'warehouse' or 'datasource.warehouse'.",
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _resolve_entity_refs(refs: list[EntityRef] | None) -> tuple[str, ...]:
    """Convert a list of entity refs to tuple of semantic_ids."""
    if refs is None:
        return ()
    return tuple(
        _require_ref_id(r, parameter=f"entities[{idx}]", expected=(EntityRef,))
        for idx, r in enumerate(refs)
    )


def _push_ir(ctx: LoaderContext, ir: Any, callable_: Callable[..., Any] | None) -> None:
    """Push an (IR, callable) pair onto ctx.pending_objects."""
    ctx.pending_objects.append((ir, callable_))


# ---------------------------------------------------------------------------
# Top-level calls
# ---------------------------------------------------------------------------


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
    fold: str | tuple[Literal["quantile"], float] | None = None,
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
            ``("quantile", q)``. Same fold as ``ms.semi_additive(over, fold)``;
            collapses the ``over`` time axis. Distinct from
            ``agg=("percentile", q)``, which aggregates across rows in each
            query group rather than along the time axis.
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
    # measure ids are entity-qualified: "<domain>.<entity>.<column>"
    entity_id = measure_id.rsplit(".", 1)[0]
    obj_name = name
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
        provenance=None,
        ai_context=ai_ctx,
        body_ast_hash=_compute_agg_hash(measure_id, agg, fold_ir),
        python_symbol=obj_name,
        location=location,
        root_entity=entity_id,
        fold_override=fold_ir,
        unit=unit,
        aggregation_target=measure_id,
        aggregation_target_kind="measure",
    )
    _push_ir(ctx, metric_ir, None)
    return MetricRef(semantic_id)


def count(
    *,
    name: str,
    entity: EntityRef,
    ai_context: AiContextValue | None = None,
) -> MetricRef:
    """Declare a row-count metric for an entity.

    Args:
        name: Metric name inside the entity's domain.
        entity: Entity ref returned by ``ms.entity(...)``. Strings are rejected
            so agents do not guess raw semantic ids.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra
            agent-facing hints.

    Returns:
        A ``MetricRef`` for the count metric.

    Example:
        >>> orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))
        >>> order_count = ms.count(name="order_count", entity=orders)

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
        body_ast_hash=_compute_agg_hash(entity_id, "count", None),
        python_symbol=name,
        location=location,
        root_entity=entity_id,
        aggregation_target=entity_id,
        aggregation_target_kind="entity",
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


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


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
        source: Structured physical source, usually ``ms.table(...)`` or
            ``ms.parquet(...)``, or ``ms.csv(...)``.
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
    if not isinstance(source, (TableSourceIR, ParquetSourceIR, CsvSourceIR)):
        _raise(
            ErrorKind.INVALID_REF,
            "ms.entity(source=...) accepts ms.table(...), ms.parquet(...), or ms.csv(...).",
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


def _validate_time_parse_granularity(
    *,
    semantic_id: str,
    granularity: str,
    parse: SemanticParse | None,
) -> None:
    """Validate that the parse variant is compatible with the declared granularity."""
    if parse is None:
        return
    if isinstance(parse, HourPrefixParse) and granularity != "hour":
        _raise(
            ErrorKind.INVALID_REF,
            f"time dimension {semantic_id!r}: ms.hour_prefix(...) requires granularity='hour'.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.TIME_GRANULARITY_PARSE_COMPATIBLE,
        )
    if isinstance(parse, DateParse) and granularity in {"hour", "minute", "second"}:
        _raise(
            ErrorKind.INVALID_REF,
            f"time dimension {semantic_id!r}: granularity={granularity!r} requires ms.datetime(...) or ms.timestamp(...).",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.TIME_GRANULARITY_PARSE_COMPATIBLE,
        )
    if isinstance(parse, HourPrefixParse) and granularity in {"minute", "second"}:
        _raise(
            ErrorKind.INVALID_REF,
            f"time dimension {semantic_id!r}: granularity={granularity!r} requires ms.datetime(...) or ms.timestamp(...).",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.TIME_GRANULARITY_PARSE_COMPATIBLE,
        )
    if isinstance(parse, StrptimeParse) and granularity in {"hour", "minute", "second"}:
        import re as _re

        fmt = parse.format or ""
        date_directives = {"%Y", "%y", "%m", "%d", "%j", "%U", "%W"}
        time_directives = {"%H", "%I", "%k", "%l", "%M", "%S", "%T", "%p"}
        tokens = set(_re.findall(r"%[a-zA-Z]", fmt))
        if not tokens & time_directives:
            _raise(
                ErrorKind.INVALID_REF,
                f"time dimension {semantic_id!r}: granularity={granularity!r} requires a time-bearing "
                f"format, but strptime format {fmt!r} has no hour/minute/second directives.",
                refs=(semantic_id,),
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.TIME_GRANULARITY_PARSE_COMPATIBLE,
            )
        if parse.sample_interval is not None and not tokens & date_directives:
            _raise(
                ErrorKind.INVALID_SAMPLE_INTERVAL,
                f"time dimension {semantic_id!r}: sampled strptime format {fmt!r} must include date context.",
                refs=(semantic_id,),
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
            )


def _validate_sample_interval_granularity(
    *,
    semantic_id: str,
    granularity: str,
    parse: SemanticParse | None,
) -> None:
    """Validate that sample_interval unit is not coarser than the declared granularity."""
    if parse is None:
        return
    sample_ir: SampleIntervalIR | None = None
    data_type_str: str | None = None
    if isinstance(parse, DatetimeParse):
        sample_ir = parse.sample_interval
        data_type_str = "datetime"
    elif isinstance(parse, TimestampParse):
        sample_ir = parse.sample_interval
        data_type_str = "timestamp"
    elif isinstance(parse, StrptimeParse):
        sample_ir = parse.sample_interval
        data_type_str = "strptime"
    elif isinstance(parse, HourPrefixParse):
        sample_ir = parse.sample_interval
        data_type_str = "hour_prefix"
    if sample_ir is None or data_type_str is None:
        return
    # Re-run the granularity check from _normalize_sample_interval with the real granularity
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
    if rank.get(granularity, 99) > rank.get(sample_ir.unit, 0):
        allowed = [
            g for g, r in sorted(rank.items(), key=lambda kv: kv[1]) if r <= rank[sample_ir.unit]
        ]
        allowed_list = ", ".join(repr(g) for g in allowed)
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            f"time dimension {semantic_id!r}: physical granularity {granularity!r} cannot "
            f"be coarser than sample_interval unit {sample_ir.unit!r}. Set granularity to {sample_ir.unit!r} or "
            f"finer (one of: {allowed_list}).",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )


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


# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------


def snapshot(
    *,
    partition_field: DimensionRef | TimeDimensionRef,
    grain: Literal["day"],
    timezone: str | None = None,
    format: str | None = None,
) -> SnapshotVersioningIR:
    """Declare daily snapshot partition versioning for an entity."""
    partition_ref = _require_ref_id(
        partition_field,
        parameter="partition_field",
        expected=(DimensionRef, TimeDimensionRef),
    )
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
    valid_from: DimensionRef | TimeDimensionRef,
    valid_to: DimensionRef | TimeDimensionRef,
    interval: Literal["closed_open", "closed_closed"],
    open_end: tuple[str | None, ...],
    timezone: str | None = None,
) -> ValidityVersioningIR:
    """Declare SCD2 validity interval versioning for an entity.

    Args:
        valid_from: Dimension or time-dimension ref for the interval start column.
        valid_to: Dimension or time-dimension ref for the interval end column.
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
    valid_from_ref = _require_ref_id(
        valid_from,
        parameter="valid_from",
        expected=(DimensionRef, TimeDimensionRef),
    )
    valid_to_ref = _require_ref_id(
        valid_to,
        parameter="valid_to",
        expected=(DimensionRef, TimeDimensionRef),
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
    over: TimeDimensionRef,
    fold: str | tuple[Literal["quantile"], float],
) -> SemiAdditive:
    """Declare a semi-additive nature: additive off the ``over`` time axis, folded by ``fold``.

    ``over`` must be a ``TimeDimensionRef`` returned by ``@ms.time_dimension``.
    Use as the ``additivity=`` value on a measure or a metric::

        @ms.measure(entity=inventory,
                    additivity=ms.semi_additive(over=snapshot_date, fold="last"))
        def quantity(inventory):
            return inventory.qty
    """
    if not isinstance(over, TimeDimensionRef):
        received = getattr(over, "id", over)
        _raise(
            ErrorKind.INVALID_REF,
            "ms.semi_additive(...) over must be a TimeDimensionRef returned by "
            f"@ms.time_dimension(...); got {type(over).__name__}: {received!r}.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    over_id = over.id
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
    ai_context: AiContextValue | None,
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
        provenance=None,
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
    numerator: MetricRef,
    denominator: MetricRef,
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> MetricRef:
    """Declare a derived ratio metric (no body). Override the unit derived from the components at load.

    Example::

        loss_rate = ms.ratio(name="loss_rate", numerator=lost, denominator=total, unit="1")
    """
    return _derived(
        name=name,
        composition=RatioComposition(
            numerator=_require_ref_id(numerator, parameter="numerator", expected=(MetricRef,)),
            denominator=_require_ref_id(
                denominator, parameter="denominator", expected=(MetricRef,)
            ),
        ),
        unit=unit,
        domain=domain,
        ai_context=ai_context,
    )


def weighted_average(
    *,
    name: str,
    value: MetricRef,
    weight: MetricRef,
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> MetricRef:
    """Declare a derived weighted-average metric (no body). Override the unit derived from the components at load.

    Roles are ``value`` / ``weight``."""
    return _derived(
        name=name,
        composition=WeightedAverageComposition(
            value=_require_ref_id(value, parameter="value", expected=(MetricRef,)),
            weight=_require_ref_id(weight, parameter="weight", expected=(MetricRef,)),
        ),
        unit=unit,
        domain=domain,
        ai_context=ai_context,
    )


def linear(
    *,
    name: str,
    add: list[MetricRef],
    subtract: list[MetricRef] | tuple[MetricRef, ...] = (),
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> MetricRef:
    """Declare a derived linear metric (no body): sum of ``add`` minus ``subtract``. Override the unit derived from the components at load.

    Example::

        net_revenue = ms.linear(name="net_revenue", add=[gross], subtract=[refunds])
    """
    terms = tuple(
        LinearTerm("+", _require_ref_id(m, parameter=f"add[{idx}]", expected=(MetricRef,)))
        for idx, m in enumerate(add)
    ) + tuple(
        LinearTerm(
            "-",
            _require_ref_id(m, parameter=f"subtract[{idx}]", expected=(MetricRef,)),
        )
        for idx, m in enumerate(subtract)
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
        ai_context=ai_context,
    )


def ref(id: str) -> SemanticRef:
    """Return a typed semantic ref for forward / cross-domain references.

    Prefer importing refs returned by authoring calls. Use this explicit
    fallback only for generated definitions, forward references, import cycles,
    or protected model boundaries.
    """
    if not isinstance(id, str):
        _raise(
            ErrorKind.INVALID_REF,
            "ms.ref(...) requires a string in '<kind>.<semantic_id>' format.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    kind_raw, separator, semantic_id = id.partition(".")
    if not separator or not kind_raw or not semantic_id or ".." in semantic_id:
        _raise(
            ErrorKind.INVALID_REF,
            "ms.ref(...) requires '<kind>.<semantic_id>', for example "
            "'metric.sales.revenue' or 'dimension.sales.orders.region'.",
            refs=(id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    try:
        kind = SymbolKind(kind_raw)
    except ValueError:
        allowed = ", ".join(sorted(k.value for k in SymbolKind))
        _raise(
            ErrorKind.INVALID_REF,
            f"ms.ref(...) kind {kind_raw!r} is not supported; expected one of: {allowed}.",
            refs=(id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    try:
        return make_ref(semantic_id, kind)
    except ValueError as exc:
        _raise(
            ErrorKind.INVALID_REF,
            f"ms.ref(...) could not build {kind.value} ref from {semantic_id!r}: {exc}",
            refs=(id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )


def from_sql(*, sql: str, dialect: str) -> SqlProvenance:
    """Declare SQL parity provenance for a Python metric body.

    Use as the ``provenance=`` value on ``@ms.metric(...)``::

        @ms.metric(entities=[orders], additivity="additive",
                   provenance=ms.from_sql(sql="select sum(amount) from orders", dialect="duckdb"))
        def revenue(orders_table):
            return orders_table.amount.sum()
    """
    return SqlProvenance(sql=sql, dialect=dialect)


def join_on(
    from_key: DimensionRef | TimeDimensionRef,
    to_key: DimensionRef | TimeDimensionRef,
    /,
) -> JoinKey:
    """Build one relationship key pair for ``ms.relationship(keys=[...])``.

    Each call creates one (from_key, to_key) pairing. Pass a list of
    ``ms.join_on(...)`` calls to ``keys=``.

    Example::

        ms.relationship(
            name="orders_to_customers",
            from_entity=orders, to_entity=customers,
            keys=[ms.join_on(customer_id, id)],
        )
    """
    return JoinKey(
        from_key=_require_ref_id(
            from_key,
            parameter="from_key",
            expected=(DimensionRef, TimeDimensionRef),
        ),
        to_key=_require_ref_id(
            to_key,
            parameter="to_key",
            expected=(DimensionRef, TimeDimensionRef),
        ),
    )


# ---------------------------------------------------------------------------
# Time parse variant constructors
# ---------------------------------------------------------------------------


def datetime(
    *,
    timezone: str | None = None,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
) -> DatetimeParse:
    """Declare an already-temporal datetime column parse.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column is a native datetime type.

    Args:
        timezone: Optional IANA timezone name. When omitted, Marivo interprets
            the naive source column in the datasource engine default timezone.
        sample_interval: Optional periodic sampling interval for sampled time
            dimensions, e.g. ``(5, "minute")`` or ``(1, "hour")``.

    Returns:
        A ``DatetimeParse`` value object.

    Raises:
        SemanticDecoratorError: ``timezone`` is not a valid IANA name.

    Example:
        >>> @ms.time_dimension(entity=events, granularity="minute",
        ...                    parse=ms.datetime(timezone="UTC"))
        ... def ts(events):
        ...     return events.ts
    """
    if timezone is not None:
        _validate_timezone(timezone)
    return DatetimeParse(
        timezone=timezone,
        sample_interval=_normalize_sample_interval_value(sample_interval),
    )


def timestamp(
    *,
    timezone: str | None = None,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
) -> TimestampParse:
    """Declare an already-temporal timestamp column parse.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column is a native timestamp type.

    Args:
        timezone: Optional IANA timezone name. When omitted, Marivo interprets
            the naive source column in the datasource engine default timezone.
        sample_interval: Optional periodic sampling interval for sampled time
            dimensions, e.g. ``(5, "minute")`` or ``(1, "hour")``.

    Returns:
        A ``TimestampParse`` value object.

    Raises:
        SemanticDecoratorError: ``timezone`` is not a valid IANA name.

    Example:
        >>> @ms.time_dimension(entity=events, granularity="second",
        ...                    parse=ms.timestamp(timezone="UTC"))
        ... def ts(events):
        ...     return events.ts
    """
    if timezone is not None:
        _validate_timezone(timezone)
    return TimestampParse(
        timezone=timezone,
        sample_interval=_normalize_sample_interval_value(sample_interval),
    )


def strptime(
    format: str,
    /,
    *,
    timezone: str | None = None,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
) -> StrptimeParse:
    """Declare a string/integer strptime parse.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column is a string or integer that must be parsed with a Python
    strptime format. The physical column type (string or integer) is inferred
    from the ibis expression at analysis time.

    Args:
        format: Canonical Python strptime format string (e.g. ``"%Y%m%d"``,
            ``"%Y-%m-%d %H:%M:%S"``). Must be ``%``-prefixed.
        timezone: Optional IANA timezone for time-bearing formats.
        sample_interval: Optional periodic sampling interval for sampled time
            dimensions, e.g. ``(5, "minute")`` or ``(1, "hour")``.

    Returns:
        A ``StrptimeParse`` value object.

    Raises:
        SemanticDecoratorError: ``format`` is not a valid strptime format, or
            ``timezone`` is not a valid IANA name.

    Example:
        >>> @ms.time_dimension(entity=orders, granularity="day",
        ...                    parse=ms.strptime("%Y%m%d"))
        ... def dt(orders):
        ...     return orders.dt
    """
    normalized = normalize_strptime(format)
    if timezone is not None:
        _validate_timezone(timezone)
        if not is_time_bearing_format(normalized):
            _raise(
                ErrorKind.INVALID_REF,
                "timezone is only supported for time-bearing strptime formats, not date-only formats.",
                cls=SemanticDecoratorError,
                details={"field": "timezone", "format": normalized},
            )
    return StrptimeParse(
        format=normalized,
        timezone=timezone,
        sample_interval=_normalize_sample_interval_value(
            sample_interval,
        ),
    )


def hour_prefix(
    prefix: TimeDimensionRef,
    /,
    *,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
) -> HourPrefixParse:
    """Declare an hour-only partition parse using a day prefix column.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column encodes only the hour component (e.g. ``"01"``, ``"23"``)
    and must be combined with a day-level time dimension prefix. The physical
    column type (string or integer) is inferred from the ibis expression at
    analysis time.

    Args:
        prefix: The ``TimeDimensionRef`` of a day-level time dimension that
            supplies the date context for this hour column.
        sample_interval: Optional ``(count, unit)`` declaring the periodic
            sampling cadence (e.g. ``(1, "hour")`` for hourly samples).
            When set, the time dimension can serve as a sampled-fold axis.

    Returns:
        An ``HourPrefixParse`` value object.

    Example:
        >>> @ms.time_dimension(entity=logs, granularity="day")
        ... def dt(logs):
        ...     return logs.dt
        >>> @ms.time_dimension(entity=logs, granularity="hour",
        ...                    parse=ms.hour_prefix(dt))
        ... def hh(logs):
        ...     return logs.hh
        >>> @ms.time_dimension(entity=logs, granularity="hour",
        ...                    parse=ms.hour_prefix(dt,
        ...                                        sample_interval=(1, "hour")))
        ... def hh(logs):
        ...     return logs.hh
    """
    if not isinstance(prefix, TimeDimensionRef):
        received = getattr(prefix, "id", prefix)
        _raise(
            ErrorKind.INVALID_REF,
            "ms.hour_prefix(...) prefix must be a TimeDimensionRef returned by "
            f"@ms.time_dimension(...); got {type(prefix).__name__}: {received!r}.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    return HourPrefixParse(
        prefix=prefix.id,
        sample_interval=_normalize_sample_interval_value(
            sample_interval,
        ),
    )
