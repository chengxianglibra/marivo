"""Authoring decorators and builders for marivo.semantic v1.1.

All authoring symbols (model, dataset, field, time_field, metric,
relationship, sum, ratio, weighted_average, ref, derived_metric) are
defined here.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.typing import _build_ai_context as _shared_build_ai_context
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    AiContextIR,
    DatasetIR,
    DatasetRef,
    DatasetSourceIR,
    DecompositionIR,
    FieldIR,
    FieldRef,
    FileSourceIR,
    MetricIR,
    MetricRef,
    ModelIR,
    ProvenanceIR,
    RelationshipIR,
    RelationshipRef,
    SnapshotVersioningIR,
    SourceLocation,
    TableSourceIR,
    TimeFieldRef,
    ValidityVersioningIR,
)
from marivo.semantic.loader import _LOADER_CTX, LoaderContext
from marivo.semantic.typing import AiContext
from marivo.semantic.validator import validate_metric_body_ast

__all__ = [
    "DecompositionBuilder",
    "dataset",
    "derived_metric",
    "field",
    "file",
    "metric",
    "model",
    "ratio",
    "ref",
    "relationship",
    "snapshot",
    "sum",
    "table",
    "time_field",
    "validity",
    "weighted_average",
]


# ---------------------------------------------------------------------------
# DecompositionBuilder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecompositionBuilder:
    """Precursor to DecompositionIR, returned by ms.sum/ratio/weighted_average."""

    kind: Literal["sum", "ratio", "weighted_average"]
    components: dict[str, str] = dc_field(default_factory=dict)


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


def _resolve_model_name(explicit: str | None, ctx: LoaderContext) -> str:
    """Resolve the model name: explicit > default_model > error."""
    if explicit is not None:
        return explicit
    if ctx.default_model is not None:
        return ctx.default_model
    _raise(
        ErrorKind.MISSING_MODEL,
        "No model name specified and no default model is set. "
        "Call ms.model(name=...) before declaring semantic objects.",
        cls=SemanticDecoratorError,
    )


def _check_duplicate(
    ctx: LoaderContext,
    semantic_id: str,
    ir_type: type[DatasetIR | FieldIR | MetricIR | RelationshipIR],
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


def _compute_decomposition_ast_hash(decomposition: DecompositionBuilder) -> str:
    """Compute a stable hash from canonical derived metric structure."""
    payload = repr(
        {
            "kind": decomposition.kind,
            "components": tuple(sorted(decomposition.components.items())),
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _build_metric_provenance(
    *,
    source_sql: str | None,
    source_dialect: str | None,
    source_document: str | None,
    source_notes: str | None,
    verification_mode: Literal["sql_parity", "python_native"] | None,
) -> ProvenanceIR:
    return ProvenanceIR(
        source_sql=source_sql,
        source_dialect=source_dialect,
        source_document=source_document,
        source_notes=source_notes,
        verification_mode=verification_mode,
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
    ref: DatasetRef | FieldRef | TimeFieldRef | MetricRef | RelationshipRef | DatasourceRef | str,
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
        "ms.dataset(datasource=...) accepts a datasource ref or global datasource name string.",
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _resolve_field_refs(refs: list[FieldRef | str]) -> tuple[str, ...]:
    """Convert a list of field refs/strings to tuple of semantic_ids."""
    return tuple(_resolve_ref_string(r) for r in refs)


def _resolve_dataset_refs(refs: list[DatasetRef | str] | None) -> tuple[str, ...]:
    """Convert a list of dataset refs/strings to tuple of semantic_ids."""
    if refs is None:
        return ()
    return tuple(_resolve_ref_string(r) for r in refs)


def _push_ir(ctx: LoaderContext, ir: Any, callable_: Callable[..., Any] | None) -> None:
    """Push an (IR, callable) pair onto ctx.pending_objects."""
    ctx.pending_objects.append((ir, callable_))


# ---------------------------------------------------------------------------
# Top-level calls
# ---------------------------------------------------------------------------


def model(
    *,
    name: str,
    default: bool = True,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> None:
    """Declare a semantic model namespace inside a project file.

    A model groups datasets, fields, metrics, and relationships under a single
    qualified name (``<model>.<object>``). Must be called at module top-level
    inside a ``.marivo/semantic/*.py`` project file.

    Args:
        name: Model namespace, e.g. ``"sales"``.
        default: If True, subsequent decorators in this file resolve to this
            model when no explicit ``model=`` kwarg is passed.
        description: Free-text description; surfaced in agent/help output.
        ai_context: Optional ``AiContext`` (or compatible dict) with extra
            agent-facing hints.

    Returns:
        None. This is a namespace declaration, not a value-producing call.
        Subsequent decorators in this file resolve to this model.

    Raises:
        OutsideLoaderContextError: Called outside a semantic loader pass.
        SemanticDecoratorError: ``name`` collides with another model in the project.

    Example:
        >>> import marivo.semantic as ms
        >>> ms.model(name="sales", default=True)
    """
    ctx = _require_ctx()
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    ir = ModelIR(
        name=name,
        description=description,
        default=default,
        ai_context=ai_ctx,
        location=location,
    )
    _push_ir(ctx, ir, None)

    if default:
        ctx.default_model = name


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def table(name: str, /, *, database: str | tuple[str, ...] | None = None) -> TableSourceIR:
    """Build a structured table source for ``ms.dataset(source=...)``."""
    return TableSourceIR(table=name, database=database)


def file(
    path: str,
    /,
    *,
    format: Literal["parquet", "csv"],
    **options: Any,
) -> FileSourceIR:
    """Build a structured file source for ``ms.dataset(source=...)``."""
    if format not in ("parquet", "csv"):
        _raise(
            ErrorKind.INVALID_REF,
            "ms.file(format=...) format must be 'parquet' or 'csv'.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    return FileSourceIR(path=path, format=format, options=dict(options))


def dataset(
    *,
    name: str,
    datasource: DatasourceRef | str,
    source: DatasetSourceIR,
    primary_key: list[str] | None = None,
    versioning: SnapshotVersioningIR | ValidityVersioningIR | None = None,
    model_name: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> DatasetRef:
    """Declare a dataset over a structured physical source.

    Args:
        name: Dataset name.
        datasource: Datasource ref returned by ``md.ref(...)`` or a global
            datasource name string declared in ``.marivo/datasource/*.py``.
        source: Structured physical source, usually ``ms.table(...)`` or
            ``ms.file(...)``.
        primary_key: Optional list of column names forming the primary key.
        model_name: Override the active model namespace. Defaults to the file's
            default model.
        description: Free-text description; surfaced in agent/help output.
        ai_context: Optional ``AiContext`` with extra agent-facing hints.

    Returns:
        A ``DatasetRef`` usable by ``@ms.field`` and ``@ms.metric``.

    Raises:
        SemanticDecoratorError: ``datasource`` is not a datasource ref or string, ``name``
            collides with another object, or ``source`` is not a dataset source.

    Example:
        >>> orders = ms.dataset(
        ...     name="orders",
        ...     datasource="warehouse",
        ...     source=ms.table("orders", database="sales_mart"),
        ... )
    """
    ctx = _require_ctx()
    model_name = _resolve_model_name(model_name, ctx)
    semantic_id = f"{model_name}.{name}"
    _check_duplicate(ctx, semantic_id, DatasetIR)
    if not isinstance(source, (TableSourceIR, FileSourceIR)):
        _raise(
            ErrorKind.INVALID_REF,
            "ms.dataset(source=...) accepts ms.table(...) or ms.file(...).",
            cls=SemanticDecoratorError,
            refs=(semantic_id,),
            constraint_id=ConstraintId.REF_SHAPE,
        )

    ds_ref = _resolve_datasource_ref(datasource)
    pk = tuple(primary_key) if primary_key else ()
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    ir = DatasetIR(
        semantic_id=semantic_id,
        model=model_name,
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

    return DatasetRef(semantic_id)


def field(
    *,
    name: str | None = None,
    dataset: DatasetRef | str,
    model_name: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], FieldRef]:
    """Declare a field whose body returns an ibis expression over its dataset.

    The decorated function takes the dataset table and returns a single
    expression (single-return AST). Use this for both raw columns and derived
    expressions (e.g. ``table.amount * 100``).

    Args:
        name: Field name. Defaults to the function name.
        dataset: Owning dataset, either a ``DatasetRef`` or a qualified
            ``"<model>.<dataset>"`` string.
        model_name: Override the active model namespace.
        description: Free-text description.
        ai_context: Optional ``AiContext`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``FieldRef``.

    Raises:
        SemanticDecoratorError: ``dataset`` is unknown, ``name`` collides, or the
            body violates the AST whitelist.

    Example:
        >>> @ms.field(name="amount_cents", dataset=orders)
        ... def amount_cents(orders):
        ...     return orders.amount * 100
    """
    ctx = _require_ctx()
    model_name = _resolve_model_name(model_name, ctx)

    def decorator(fn: Callable[..., Any]) -> FieldRef:
        obj_name = name or fn.__name__
        ds_ref = _resolve_ref_string(dataset)
        semantic_id = f"{ds_ref}.{obj_name}"
        ds_model = ds_ref.split(".", 1)[0]
        if ds_model != model_name:
            _raise(
                ErrorKind.INVALID_REF,
                f"Field {semantic_id!r} belongs to dataset in model {ds_model!r}, "
                f"but the active model is {model_name!r}.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
                constraint_id=ConstraintId.REF_SHAPE,
            )
        _check_duplicate(ctx, semantic_id, FieldIR)

        validate_metric_body_ast(fn, "base")
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()

        ir = FieldIR(
            semantic_id=semantic_id,
            model=model_name,
            dataset=ds_ref,
            name=obj_name,
            description=description,
            ai_context=ai_ctx,
            is_time_field=False,
            data_type=None,
            granularity=None,
            required_prefix=None,
            python_symbol=fn.__name__,
            location=location,
        )
        _push_ir(ctx, ir, fn)

        ref = FieldRef(semantic_id)
        ctx.pending_refs.append(ref)
        return ref

    return decorator


def time_field(
    *,
    name: str | None = None,
    dataset: DatasetRef | str,
    data_type: Literal["date", "datetime", "timestamp", "string", "integer"],
    granularity: Literal["year", "quarter", "month", "week", "day", "hour", "minute", "second"],
    date_format: str | None = None,
    required_prefix: str | None = None,
    timezone: str | None = None,
    model_name: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], TimeFieldRef]:
    """Declare a time-aware field that carries grain and parsing metadata.

    Time fields are the only fields usable as window axes by ``session.observe``.
    The body may return any ibis expression that represents the intended time
    axis. For sortable day/hour partition columns, prefer returning the raw
    column with ``data_type="string"`` or ``data_type="integer"`` plus
    ``date_format`` so window predicates can remain pushdown-friendly.

    Args:
        name: Field name. Defaults to the function name.
        dataset: Owning dataset (``DatasetRef`` or qualified string).
        data_type: ``date | datetime | timestamp | string | integer``.
        granularity: ``year | quarter | month | week | day | hour | minute | second`` — the
            finest grain at which queries are meaningful.
        date_format: Required when ``data_type="string"`` or ``data_type="integer"``.
            Accepts shorthand aliases (``"yyyymmdd"``, ``"yyyy-mm-dd"``,
            ``"yyyymmddhh"``, ``"yyyymmdd-hh"``, ``"yyyy-mm-dd-hh"``,
            ``"yyyymmddthh"``) or any strptime-compatible format string
            (e.g. ``"%Y-%m-%d"``, ``"%Y/%m/%d"``, ``"%Y%m%d%H"``,
            ``"%Y-%m-%d %H:%M:%S"``).
        required_prefix: Optional fixed prefix the source value must start with.
        model_name: Override the active model namespace.
        description: Free-text description.
        ai_context: Optional ``AiContext`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``TimeFieldRef``.

    Raises:
        SemanticDecoratorError: ``dataset`` is unknown, ``name`` collides, or the
            body violates the AST whitelist.

    Example:
        >>> @ms.time_field(name="created_at", dataset=orders,
        ...                data_type="datetime", granularity="day")
        ... def created_at(orders):
        ...     return orders.created_at
    """
    ctx = _require_ctx()
    model_name = _resolve_model_name(model_name, ctx)

    def decorator(fn: Callable[..., Any]) -> TimeFieldRef:
        obj_name = name or fn.__name__
        ds_ref = _resolve_ref_string(dataset)
        semantic_id = f"{ds_ref}.{obj_name}"
        ds_model = ds_ref.split(".", 1)[0]
        if ds_model != model_name:
            _raise(
                ErrorKind.INVALID_REF,
                f"Time field {semantic_id!r} belongs to dataset in model {ds_model!r}, "
                f"but the active model is {model_name!r}.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
                constraint_id=ConstraintId.REF_SHAPE,
            )
        _check_duplicate(ctx, semantic_id, FieldIR)

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

        ir = FieldIR(
            semantic_id=semantic_id,
            model=model_name,
            dataset=ds_ref,
            name=obj_name,
            description=description,
            ai_context=ai_ctx,
            is_time_field=True,
            data_type=data_type,
            granularity=granularity,
            required_prefix=required_prefix,
            python_symbol=fn.__name__,
            location=location,
            format=date_format,
            timezone=timezone,
        )
        _push_ir(ctx, ir, fn)

        ref = TimeFieldRef(semantic_id)
        ctx.pending_refs.append(ref)
        return ref

    return decorator


def metric(
    *,
    name: str | None = None,
    datasets: list[DatasetRef | str] | None = None,
    root_dataset: DatasetRef | str | None = None,
    additivity: Literal["additive", "semi_additive", "non_additive"] | None = None,
    fanout_policy: Literal["block", "aggregate_then_join"] = "block",
    decomposition: DecompositionBuilder,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    source_document: str | None = None,
    source_notes: str | None = None,
    verification_mode: Literal["sql_parity", "python_native"] | None = None,
    model_name: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], MetricRef]:
    """Declare a dataset-backed base metric.

    ``source_sql`` / ``source_dialect`` / ``source_document`` / ``source_notes``
    are persisted into ``Provenance`` on the IR.

    Args:
        name: Metric name. Defaults to the function name.
        datasets: Non-empty list of ``DatasetRef`` / qualified strings.
        decomposition: ``ms.sum()`` / ``ms.ratio(numerator=..., denominator=...)``
            / ``ms.weighted_average(...)`` builder.
        source_sql: Original SQL definition, persisted to provenance.
        source_dialect: SQL dialect tag for ``source_sql``.
        source_document: External doc reference for the metric.
        source_notes: Free-form provenance notes.
        verification_mode: ``"sql_parity"`` or ``"python_native"``. Required
            for loaded base metrics; ``"sql_parity"`` requires ``source_sql`` and
            ``source_dialect``.
        model_name: Override the active model namespace.
        description: Free-text description.
        ai_context: Optional ``AiContext`` with extra agent-facing hints.

    Returns:
        A decorator that returns a ``MetricRef``.

    Raises:
        SemanticDecoratorError: ``datasets`` is empty, name collides, or the body
            violates the AST whitelist.

    Example:
        >>> @ms.metric(name="revenue", datasets=[orders], decomposition=ms.sum())
        ... def revenue(orders):
        ...     return orders.amount.sum()
    """
    ctx = _require_ctx()
    model_name = _resolve_model_name(model_name, ctx)

    def decorator(fn: Callable[..., Any]) -> MetricRef:
        obj_name = name or fn.__name__
        semantic_id = f"{model_name}.{obj_name}"
        _check_duplicate(ctx, semantic_id, MetricIR)

        ds_refs = _resolve_dataset_refs(datasets)
        if len(ds_refs) == 0:
            _raise(
                ErrorKind.MISSING_DATASETS,
                "@ms.metric(...) requires non-empty datasets. "
                "Use ms.derived_metric(...) for body-free derived metrics.",
                refs=(semantic_id,),
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.METRIC_DATASETS_REQUIRED,
            )
        body_hash = validate_metric_body_ast(fn, "base")
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()

        decomp_ir = DecompositionIR(
            kind=decomposition.kind,
            components=dict(decomposition.components),
        )
        prov_ir = _build_metric_provenance(
            source_sql=source_sql,
            source_dialect=source_dialect,
            source_document=source_document,
            source_notes=source_notes,
            verification_mode=verification_mode,
        )

        root_ref = _resolve_ref_string(root_dataset) if root_dataset is not None else None
        resolved_root_ref = root_ref
        if resolved_root_ref is None and len(ds_refs) == 1:
            resolved_root_ref = ds_refs[0]

        ir = MetricIR(
            semantic_id=semantic_id,
            model=model_name,
            name=obj_name,
            datasets=ds_refs,
            is_derived=False,
            decomposition=decomp_ir,
            provenance=prov_ir,
            description=description,
            ai_context=ai_ctx,
            body_ast_hash=body_hash,
            python_symbol=fn.__name__,
            location=location,
            additivity=additivity,
            root_dataset=resolved_root_ref,
            fanout_policy=fanout_policy,
        )

        _push_ir(ctx, ir, fn)

        return MetricRef(semantic_id)

    return decorator


def derived_metric(
    *,
    name: str,
    decomposition: DecompositionBuilder,
    additivity: Literal["additive", "semi_additive", "non_additive"] | None = None,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    source_document: str | None = None,
    source_notes: str | None = None,
    verification_mode: Literal["sql_parity", "python_native"] | None = None,
    model_name: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> MetricRef:
    """Declare a body-free derived metric from canonical decomposition structure."""
    ctx = _require_ctx()
    model_name = _resolve_model_name(model_name, ctx)
    semantic_id = f"{model_name}.{name}"
    _check_duplicate(ctx, semantic_id, MetricIR)

    if decomposition.kind not in ("ratio", "weighted_average") or not decomposition.components:
        _raise(
            ErrorKind.INVALID_DECOMPOSITION,
            "ms.derived_metric(...) requires a ratio or weighted_average decomposition with components.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.DECOMPOSITION_SHAPE,
        )
    if additivity is not None and additivity != "non_additive":
        _raise(
            ErrorKind.INVALID_DECOMPOSITION,
            "ms.derived_metric(...) additivity must be omitted or 'non_additive'.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.DECOMPOSITION_SHAPE,
        )

    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()
    decomp_ir = DecompositionIR(
        kind=decomposition.kind,
        components=dict(decomposition.components),
    )
    prov_ir = _build_metric_provenance(
        source_sql=source_sql,
        source_dialect=source_dialect,
        source_document=source_document,
        source_notes=source_notes,
        verification_mode=verification_mode,
    )

    ir = MetricIR(
        semantic_id=semantic_id,
        model=model_name,
        name=name,
        datasets=(),
        is_derived=True,
        decomposition=decomp_ir,
        provenance=prov_ir,
        description=description,
        ai_context=ai_ctx,
        body_ast_hash=_compute_decomposition_ast_hash(decomposition),
        python_symbol=name,
        location=location,
        additivity=additivity,
        root_dataset=None,
        fanout_policy="block",
    )
    _push_ir(ctx, ir, None)

    return MetricRef(semantic_id)


def relationship(
    *,
    name: str | None = None,
    from_dataset: DatasetRef | str,
    to_dataset: DatasetRef | str,
    from_fields: list[FieldRef | str],
    to_fields: list[FieldRef | str],
    model_name: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> RelationshipRef:
    """Declare a join relationship between two datasets.

    Top-level call (not a decorator). Used by the compiler to plan joins when a
    metric or field references fields across related datasets.

    Args:
        name: Required relationship name (no default).
        from_dataset: Source dataset (``DatasetRef`` or qualified string).
        to_dataset: Target dataset (``DatasetRef`` or qualified string).
        from_fields: Columns on ``from_dataset`` (``FieldRef`` / qualified strings).
        to_fields: Columns on ``to_dataset`` — must align positionally with ``from_fields``.
        model_name: Override the active model namespace.
        description: Free-text description.
        ai_context: Optional ``AiContext`` with extra agent-facing hints.

    Returns:
        A ``RelationshipRef``.

    Raises:
        SemanticDecoratorError: ``name`` is missing, the datasets are unknown, or
            ``from_fields`` / ``to_fields`` lengths disagree.

    Example:
        >>> ms.relationship(
        ...     name="orders_to_customers",
        ...     from_dataset=orders, to_dataset=customers,
        ...     from_fields=["customer_id"], to_fields=["id"],
        ... )
    """
    ctx = _require_ctx()
    model_name = _resolve_model_name(model_name, ctx)

    if name is None:
        _raise(
            ErrorKind.MISSING_MODEL,
            "relationship requires a 'name' argument.",
            cls=SemanticDecoratorError,
        )

    semantic_id = f"{model_name}.{name}"
    _check_duplicate(ctx, semantic_id, RelationshipIR)

    from_ds = _resolve_ref_string(from_dataset)
    to_ds = _resolve_ref_string(to_dataset)
    from_f = _resolve_field_refs(from_fields)
    to_f = _resolve_field_refs(to_fields)
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    ir = RelationshipIR(
        semantic_id=semantic_id,
        model=model_name,
        name=name,
        from_dataset=from_ds,
        to_dataset=to_ds,
        from_fields=from_f,
        to_fields=to_f,
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
    partition_field: FieldRef | TimeFieldRef | str,
    grain: Literal["day"],
    timezone: str | None = None,
    format: str | None = None,
) -> SnapshotVersioningIR:
    """Declare daily snapshot partition versioning for a dataset."""
    if isinstance(partition_field, (FieldRef, TimeFieldRef)):
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
    valid_from: FieldRef | str,
    valid_to: FieldRef | str,
    interval: Literal["closed_open", "closed_closed"],
    open_end: tuple[Any, ...],
    timezone: str | None = None,
) -> ValidityVersioningIR:
    """Declare SCD2 validity interval versioning for a dataset.

    Args:
        valid_from: Field semantic id (or FieldRef) for the interval start column.
        valid_to: Field semantic id (or FieldRef) for the interval end column.
        interval: ``"closed_open"`` (``[valid_from, valid_to)``) or
            ``"closed_closed"`` (``[valid_from, valid_to]``).
        open_end: Non-empty tuple of sentinel values that mean "still current"
            in the ``valid_to`` column. Use ``None`` for SQL NULL, or a string
            sentinel such as ``"9999-12-31"``.
        timezone: Optional IANA timezone name for anchor date casting.

    Returns:
        A ``ValidityVersioningIR`` for use in ``ms.dataset(versioning=...)``.

    Raises:
        SemanticDecoratorError: ``interval`` is not one of the two allowed values,
            ``open_end`` is empty, or ``timezone`` is not a valid IANA name.
    """
    if interval not in ("closed_open", "closed_closed"):
        _raise(
            ErrorKind.INVALID_DATASET_VERSIONING,
            f"validity versioning interval must be 'closed_open' or 'closed_closed', "
            f"got {interval!r}.",
            cls=SemanticDecoratorError,
            details={"field": "interval", "reason": f"unsupported interval value {interval!r}"},
        )
    if not open_end:
        _raise(
            ErrorKind.INVALID_DATASET_VERSIONING,
            "validity versioning open_end must be a non-empty tuple.",
            cls=SemanticDecoratorError,
            details={"field": "open_end", "reason": "empty tuple is not allowed"},
        )
    if timezone is not None:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            _raise(
                ErrorKind.INVALID_DATASET_VERSIONING,
                f"timezone {timezone!r} is not a valid IANA timezone name.",
                cls=SemanticDecoratorError,
                details={"field": "timezone", "reason": f"unknown IANA timezone {timezone!r}"},
            )
    valid_from_ref = (
        valid_from.semantic_id if isinstance(valid_from, (FieldRef, TimeFieldRef)) else valid_from
    )
    valid_to_ref = (
        valid_to.semantic_id if isinstance(valid_to, (FieldRef, TimeFieldRef)) else valid_to
    )
    return ValidityVersioningIR(
        kind="validity",
        valid_from=valid_from_ref,
        valid_to=valid_to_ref,
        interval=interval,
        open_end=open_end,
        timezone=timezone,
    )


def sum() -> DecompositionBuilder:
    """Sum decomposition: aggregate metric over its dataset row set.

    Pass to ``@ms.metric(decomposition=ms.sum())`` for aggregate metrics whose
    body is a reduction (``orders.amount.sum()``).
    """
    return DecompositionBuilder(kind="sum")


def ratio(
    *,
    numerator: Any,
    denominator: Any,
) -> DecompositionBuilder:
    """Ratio decomposition for body-free ``ms.derived_metric`` declarations.

    ``numerator`` and ``denominator`` are ``MetricRef`` / qualified string
    references to other metrics. Pass the returned builder directly to
    ``ms.derived_metric(...)``.

    Example:
        >>> ms.derived_metric(
        ...     name="aov",
        ...     decomposition=ms.ratio(numerator=revenue, denominator=orders_count),
        ... )
    """
    num_id = _resolve_ref_string(numerator) if not isinstance(numerator, str) else numerator
    den_id = _resolve_ref_string(denominator) if not isinstance(denominator, str) else denominator
    return DecompositionBuilder(
        kind="ratio",
        components={"numerator": num_id, "denominator": den_id},
    )


def weighted_average(
    *,
    value: Any,
    weight: Any,
) -> DecompositionBuilder:
    """Weighted-average decomposition for body-free derived metrics.

    Both ``value`` and ``weight`` are ``MetricRef`` / qualified string
    references. Pass the returned builder directly to ``ms.derived_metric(...)``
    when the underlying ratio needs to be averaged by an additive weight.
    """
    num_id = _resolve_ref_string(value) if not isinstance(value, str) else value
    weight_id = _resolve_ref_string(weight) if not isinstance(weight, str) else weight
    return DecompositionBuilder(
        kind="weighted_average",
        components={"numerator": num_id, "weight": weight_id},
    )


def ref(id: str) -> str:
    """Reference a semantic object by qualified ``"<model>.<object>"`` string.

    Pass-through helper: it returns ``id`` unchanged but makes intent
    explicit at the call site (``datasets=[ms.ref("sales.orders")]``).
    """
    return id
