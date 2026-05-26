"""Authoring decorators and builders for marivo.semantic_py v1.1.

This module replaces the old decorators.py and builders.py.  All
authoring symbols (model, datasource, dataset, field, time_field,
metric, relationship, sum, ratio, weighted_average, ref, component)
are defined here.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Literal

from marivo.semantic_py.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic_py.ir import (
    AiContextIR,
    DatasetIR,
    DatasetRef,
    DatasourceIR,
    DatasourceRef,
    DecompositionIR,
    FieldIR,
    FieldRef,
    MetricIR,
    MetricRef,
    ModelIR,
    ProvenanceIR,
    RelationshipIR,
    RelationshipRef,
    SourceLocation,
    TimeFieldRef,
)
from marivo.semantic_py.loader import _LOADER_CTX, LoaderContext
from marivo.semantic_py.typing import AiContext, ComponentExpr

__all__ = [
    "DecompositionBuilder",
    "component",
    "dataset",
    "datasource",
    "field",
    "metric",
    "model",
    "ratio",
    "ref",
    "relationship",
    "sum",
    "time_field",
    "weighted_average",
]

# ---------------------------------------------------------------------------
# Component sentinel system (derived metric bodies)
# ---------------------------------------------------------------------------

#: ContextVar active only during derived metric function execution.
#: When set, ms.component() resolves against the decomposition IR.
_ACTIVE_DECOMPOSITION: ContextVar[DecompositionIR | None] = ContextVar(
    "_ACTIVE_DECOMPOSITION",
    default=None,
)


class _ComponentSentinel:
    """Leaf sentinel representing ms.component('<name>')."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"ms.component({self.name!r})"

    def __add__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("+", self, other)

    def __radd__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("+", other, self)

    def __sub__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("-", self, other)

    def __rsub__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("-", other, self)

    def __mul__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("*", self, other)

    def __rmul__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("*", other, self)

    def __truediv__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("/", self, other)

    def __rtruediv__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("/", other, self)

    def __neg__(self) -> _UnaryNegSentinel:
        return _UnaryNegSentinel(self)


class _BinOpSentinel:
    """Internal node representing arithmetic on component sentinels."""

    __slots__ = ("left", "op", "right")

    def __init__(
        self,
        op: str,
        left: ComponentExpr | int | float,
        right: ComponentExpr | int | float,
    ) -> None:
        self.op = op
        self.left = left
        self.right = right

    def __repr__(self) -> str:
        return f"({self.left!r} {self.op} {self.right!r})"

    def __add__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("+", self, other)

    def __radd__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("+", other, self)

    def __sub__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("-", self, other)

    def __rsub__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("-", other, self)

    def __mul__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("*", self, other)

    def __rmul__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("*", other, self)

    def __truediv__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("/", self, other)

    def __rtruediv__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("/", other, self)

    def __neg__(self) -> _UnaryNegSentinel:
        return _UnaryNegSentinel(self)


class _UnaryNegSentinel:
    """Internal node representing unary negation on a component sentinel."""

    __slots__ = ("operand",)

    def __init__(self, operand: ComponentExpr) -> None:
        self.operand = operand

    def __repr__(self) -> str:
        return f"(-{self.operand!r})"

    def __add__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("+", self, other)

    def __radd__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("+", other, self)

    def __sub__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("-", self, other)

    def __rsub__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("-", other, self)

    def __mul__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("*", self, other)

    def __rmul__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("*", other, self)

    def __truediv__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("/", self, other)

    def __rtruediv__(self, other: Any) -> _BinOpSentinel:
        return _BinOpSentinel("/", other, self)

    def __neg__(self) -> _UnaryNegSentinel:
        return _UnaryNegSentinel(self)


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


def _check_duplicate(ctx: LoaderContext, semantic_id: str) -> None:
    """Raise DUPLICATE_NAME if semantic_id already in pending_objects."""
    for ir, _ in ctx.pending_objects:
        if hasattr(ir, "semantic_id") and ir.semantic_id == semantic_id:
            _raise(
                ErrorKind.DUPLICATE_NAME,
                f"Name conflict: {semantic_id!r} is already declared.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
            )


def _build_ai_context(ai_context: AiContext | dict[str, Any] | None) -> AiContextIR:
    """Convert a user-provided ai_context dict/TypedDict into an AiContextIR.

    Validates keys and types; raises SemanticDecoratorError with
    INVALID_AI_CONTEXT on invalid keys or wrong types.
    """
    if ai_context is None:
        return AiContextIR()

    _valid_keys = frozenset(
        {
            "business_definition",
            "guardrails",
            "synonyms",
            "examples",
            "instructions",
            "owner_notes",
        }
    )

    if isinstance(ai_context, dict):
        # Validate keys
        invalid_keys = set(ai_context.keys()) - _valid_keys
        if invalid_keys:
            _raise(
                ErrorKind.INVALID_AI_CONTEXT,
                f"ai_context contains invalid keys: {sorted(invalid_keys)}. "
                f"Allowed keys: {sorted(_valid_keys)}.",
                cls=SemanticDecoratorError,
            )

        # Validate types
        bd = ai_context.get("business_definition")
        if bd is not None and not isinstance(bd, str):
            _raise(
                ErrorKind.INVALID_AI_CONTEXT,
                f"ai_context['business_definition'] must be str | None, got {type(bd).__name__}.",
                cls=SemanticDecoratorError,
            )

        for list_key in ("guardrails", "synonyms", "examples"):
            val = ai_context.get(list_key, [])
            if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
                _raise(
                    ErrorKind.INVALID_AI_CONTEXT,
                    f"ai_context['{list_key}'] must be list[str], got {type(val).__name__}.",
                    cls=SemanticDecoratorError,
                )

        for str_key in ("instructions", "owner_notes"):
            val = ai_context.get(str_key)
            if val is not None and not isinstance(val, str):
                _raise(
                    ErrorKind.INVALID_AI_CONTEXT,
                    f"ai_context['{str_key}'] must be str | None, got {type(val).__name__}.",
                    cls=SemanticDecoratorError,
                )

        return AiContextIR(
            business_definition=bd,
            guardrails=tuple(ai_context.get("guardrails", [])),
            synonyms=tuple(ai_context.get("synonyms", [])),
            examples=tuple(ai_context.get("examples", [])),
            instructions=ai_context.get("instructions"),
            owner_notes=ai_context.get("owner_notes"),
        )

    # AiContext TypedDict — treat as dict-like via getattr
    return AiContextIR(
        business_definition=getattr(ai_context, "business_definition", None),
        guardrails=tuple(getattr(ai_context, "guardrails", [])),
        synonyms=tuple(getattr(ai_context, "synonyms", [])),
        examples=tuple(getattr(ai_context, "examples", [])),
        instructions=getattr(ai_context, "instructions", None),
        owner_notes=getattr(ai_context, "owner_notes", None),
    )


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
    ref: DatasourceRef | DatasetRef | FieldRef | TimeFieldRef | MetricRef | str,
) -> str:
    """Extract semantic_id string from a ref object or pass through a string."""
    if isinstance(ref, str):
        return ref
    return ref.semantic_id


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
    """Declare a semantic model. Must be called inside a loader context."""
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


def datasource(
    *,
    name: str | None = None,
    backend_type: str,
    model: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> DatasourceRef:
    """Declare a datasource (backend factory). Top-level call, not a decorator."""
    ctx = _require_ctx()
    model_name = _resolve_model_name(model, ctx)

    if name is None:
        _raise(
            ErrorKind.MISSING_MODEL,
            "datasource requires a 'name' argument (it is a top-level call, not a decorator).",
            cls=SemanticDecoratorError,
        )

    semantic_id = f"{model_name}.{name}"
    _check_duplicate(ctx, semantic_id)

    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    ir = DatasourceIR(
        semantic_id=semantic_id,
        model=model_name,
        name=name,
        backend_type=backend_type,
        description=description,
        ai_context=ai_ctx,
        python_symbol=name,
        location=location,
    )
    _push_ir(ctx, ir, None)

    return DatasourceRef(semantic_id)


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def dataset(
    *,
    name: str | None = None,
    datasource: DatasourceRef | str,
    primary_key: list[str] | None = None,
    model: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], DatasetRef]:
    """Decorator: declare a dataset on top of a datasource."""
    ctx = _require_ctx()
    model_name = _resolve_model_name(model, ctx)

    def decorator(fn: Callable[..., Any]) -> DatasetRef:
        obj_name = name or fn.__name__
        semantic_id = f"{model_name}.{obj_name}"
        _check_duplicate(ctx, semantic_id)

        ds_ref = _resolve_ref_string(datasource)
        pk = tuple(primary_key) if primary_key else ()
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()

        ir = DatasetIR(
            semantic_id=semantic_id,
            model=model_name,
            name=obj_name,
            datasource=ds_ref,
            primary_key=pk,
            description=description,
            ai_context=ai_ctx,
            python_symbol=fn.__name__,
            location=location,
        )
        _push_ir(ctx, ir, fn)

        return DatasetRef(semantic_id)

    return decorator


def field(
    *,
    name: str | None = None,
    dataset: DatasetRef | str,
    model: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], FieldRef]:
    """Decorator: declare a field on a dataset."""
    ctx = _require_ctx()
    model_name = _resolve_model_name(model, ctx)

    def decorator(fn: Callable[..., Any]) -> FieldRef:
        obj_name = name or fn.__name__
        semantic_id = f"{model_name}.{obj_name}"
        _check_duplicate(ctx, semantic_id)

        ds_ref = _resolve_ref_string(dataset)
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
    data_type: Literal["date", "datetime", "timestamp"],
    granularity: Literal["year", "quarter", "month", "week", "day", "hour"],
    required_prefix: str | None = None,
    model: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], TimeFieldRef]:
    """Decorator: declare a time-aware field on a dataset."""
    ctx = _require_ctx()
    model_name = _resolve_model_name(model, ctx)

    def decorator(fn: Callable[..., Any]) -> TimeFieldRef:
        obj_name = name or fn.__name__
        semantic_id = f"{model_name}.{obj_name}"
        _check_duplicate(ctx, semantic_id)

        ds_ref = _resolve_ref_string(dataset)
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()

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
    decomposition: DecompositionBuilder,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    source_document: str | None = None,
    source_notes: str | None = None,
    provenance: Literal["python_native", "unverified"] | None = None,
    model: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], MetricRef]:
    """Decorator: declare a metric with decomposition and provenance."""
    ctx = _require_ctx()
    model_name = _resolve_model_name(model, ctx)

    def decorator(fn: Callable[..., Any]) -> MetricRef:
        obj_name = name or fn.__name__
        semantic_id = f"{model_name}.{obj_name}"
        _check_duplicate(ctx, semantic_id)

        ds_refs = _resolve_dataset_refs(datasets)
        is_derived = len(ds_refs) == 0 and bool(decomposition.components)
        body_hash = _compute_body_ast_hash(fn)
        ai_ctx = _build_ai_context(ai_context)
        location = _caller_location()

        decomp_ir = DecompositionIR(
            kind=decomposition.kind,
            components=dict(decomposition.components),
        )
        prov_ir = ProvenanceIR(
            source_sql=source_sql,
            source_dialect=source_dialect,
            source_document=source_document,
            source_notes=source_notes,
            declared_status=provenance,
        )

        ir = MetricIR(
            semantic_id=semantic_id,
            model=model_name,
            name=obj_name,
            datasets=ds_refs,
            is_derived=is_derived,
            decomposition=decomp_ir,
            provenance=prov_ir,
            description=description,
            ai_context=ai_ctx,
            body_ast_hash=body_hash,
            python_symbol=fn.__name__,
            location=location,
        )

        # For derived metrics, execute the function body with
        # _ACTIVE_DECOMPOSITION set so that ms.component() resolves.
        # The return value is the sentinel expression tree.
        if is_derived:
            token = _ACTIVE_DECOMPOSITION.set(decomp_ir)
            try:
                sentinel_tree = fn()
            finally:
                _ACTIVE_DECOMPOSITION.reset(token)
            # Store the sentinel tree in the sidecar instead of the raw callable
            _push_ir(ctx, ir, sentinel_tree)
        else:
            _push_ir(ctx, ir, fn)

        return MetricRef(semantic_id)

    return decorator


def relationship(
    *,
    name: str | None = None,
    from_: DatasetRef | str,
    to: DatasetRef | str,
    from_fields: list[FieldRef | str],
    to_fields: list[FieldRef | str],
    model: str | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> RelationshipRef:
    """Declare a relationship between two datasets. Top-level call, not a decorator."""
    ctx = _require_ctx()
    model_name = _resolve_model_name(model, ctx)

    if name is None:
        _raise(
            ErrorKind.MISSING_MODEL,
            "relationship requires a 'name' argument.",
            cls=SemanticDecoratorError,
        )

    semantic_id = f"{model_name}.{name}"
    _check_duplicate(ctx, semantic_id)

    from_ds = _resolve_ref_string(from_)
    to_ds = _resolve_ref_string(to)
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


def sum() -> DecompositionBuilder:
    """Return a sum decomposition builder."""
    return DecompositionBuilder(kind="sum")


def ratio(
    *,
    numerator: Any,
    denominator: Any,
) -> DecompositionBuilder:
    """Return a ratio decomposition builder."""
    num_id = _resolve_ref_string(numerator) if not isinstance(numerator, str) else numerator
    den_id = _resolve_ref_string(denominator) if not isinstance(denominator, str) else denominator
    return DecompositionBuilder(
        kind="ratio",
        components={"numerator": num_id, "denominator": den_id},
    )


def weighted_average(
    *,
    numerator: Any,
    weight: Any,
) -> DecompositionBuilder:
    """Return a weighted_average decomposition builder."""
    num_id = _resolve_ref_string(numerator) if not isinstance(numerator, str) else numerator
    weight_id = _resolve_ref_string(weight) if not isinstance(weight, str) else weight
    return DecompositionBuilder(
        kind="weighted_average",
        components={"numerator": num_id, "weight": weight_id},
    )


def ref(value: str) -> str:
    """Reference a semantic object by qualified name. Returns the string as-is."""
    return value


def component(name: str, /) -> _ComponentSentinel:
    """Reference a decomposition component inside a derived metric body.

    Can ONLY be called during derived metric decoration phase when
    ``_ACTIVE_DECOMPOSITION`` is set.  Returns a ``_ComponentSentinel``
    that supports arithmetic operators to build an expression tree.
    """
    decomp = _ACTIVE_DECOMPOSITION.get()
    if decomp is None:
        _raise(
            ErrorKind.OUTSIDE_DERIVED_METRIC_BODY,
            "ms.component() can only be called inside a derived metric function body.",
            cls=SemanticDecoratorError,
        )
    if not name:
        _raise(
            ErrorKind.INVALID_COMPONENT_BODY,
            "ms.component() requires a non-empty string argument.",
            cls=SemanticDecoratorError,
        )
    if name not in decomp.components:
        _raise(
            ErrorKind.INVALID_COMPONENT_NAME,
            f"ms.component({name!r}) is not a valid component name. "
            f"Available components: {sorted(decomp.components.keys())}",
            cls=SemanticDecoratorError,
        )
    return _ComponentSentinel(name)
