"""Validation layers for marivo.semantic v1.1.

Three layers:
  1. decorator-time (inline in authoring)
  2. AST whitelist (base metric body scanning)
  3. assembly-time (cross-object reference validation)
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from marivo.datasource.ir import DatasourceIR
from marivo.introspection._fuzzy import did_you_mean
from marivo.semantic.constraints import ASTSpec, ConstraintId, get_constraint
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
    StructuredWarning,
    WarningKind,
)
from marivo.semantic.ir import (
    DimensionIR,
    DomainIR,
    EntityIR,
    MetricIR,
    RelationshipIR,
    SnapshotVersioningIR,
    ValidityVersioningIR,
)

__all__ = [
    "Registry",
    "Sidecar",
    "assembly_validate",
    "validate_decorator_call",
    "validate_metric_body_ast",
]


# ---------------------------------------------------------------------------
# Registry type
# ---------------------------------------------------------------------------


@dataclass
class Registry:
    """Holds all loaded IR objects, indexed by semantic_id."""

    models: dict[str, DomainIR] = field(default_factory=dict)
    datasources: dict[str, DatasourceIR] = field(default_factory=dict)
    datasets: dict[str, EntityIR] = field(default_factory=dict)
    fields: dict[str, DimensionIR] = field(default_factory=dict)
    metrics: dict[str, MetricIR] = field(default_factory=dict)
    relationships: dict[str, RelationshipIR] = field(default_factory=dict)


#: Maps semantic_id to the original callable (entity/dimension/metric body fn).
Sidecar = dict[str, Callable[..., Any]]


_SUBDAY_GRANULARITIES: frozenset[str] = frozenset({"hour", "minute", "second"})
_TIME_BEARING_FORMAT_HINTS: tuple[str, ...] = (
    "%H",
    "%I",
    "%k",
    "%l",
    "%M",
    "%S",
    "%T",
    "%p",
)


def _subday_granularity_needs_time(field_ir: DimensionIR) -> bool:
    """True when a sub-day granularity is declared on a field that cannot carry time."""
    if not field_ir.is_time_dimension or field_ir.granularity not in _SUBDAY_GRANULARITIES:
        return False
    if field_ir.data_type in {"datetime", "timestamp"}:
        return False
    if field_ir.data_type in {"string", "integer"}:
        # Hour-only fields with a required_prefix carry time via the prefix.
        if field_ir.granularity == "hour" and field_ir.required_prefix:
            return False
        fmt = field_ir.format or ""
        return not any(hint in fmt for hint in _TIME_BEARING_FORMAT_HINTS)
    # data_type == "date" or unset -> cannot carry sub-day time
    return True


def _requires_required_prefix(field_ir: DimensionIR) -> bool:
    """Return True for hour-only string/integer time dimensions missing a prefix.

    Hour-only dimensions are those with granularity "hour" and string/integer
    data_type that carry no date component in their own value (either no
    format or an hour-only format like %H). Such dimensions require a separate
    day-level required_prefix to supply date context.
    """
    if not field_ir.is_time_dimension or field_ir.granularity != "hour":
        return False
    if field_ir.data_type not in {"string", "integer"}:
        return False
    if field_ir.required_prefix is not None:
        return False
    fmt = field_ir.format
    if fmt is None or not fmt.startswith("%"):
        return True
    # Inspect strptime directives: hour-only if there are hour directives
    # but no date directives.
    import re

    tokens = set(re.findall(r"%[a-zA-Z]", fmt))
    date_directives = {"%Y", "%y", "%m", "%d", "%j", "%U", "%W"}
    hour_directives = {"%H", "%I", "%k", "%l", "%p", "%P"}
    has_date = bool(tokens & date_directives)
    has_hour = bool(tokens & hour_directives)
    return has_hour and not has_date


def _resolve_required_prefix_field(
    registry: Registry,
    *,
    field_ir: DimensionIR,
) -> DimensionIR | None:
    if field_ir.required_prefix is None:
        return None
    direct = registry.fields.get(field_ir.required_prefix)
    if direct is not None:
        return direct
    matches = [
        candidate
        for candidate in registry.fields.values()
        if candidate.entity == field_ir.entity and candidate.name == field_ir.required_prefix
    ]
    return matches[0] if len(matches) == 1 else None


_PARTITION_TIME_COLUMN_NAMES = {
    "dt",
    "date",
    "ds",
    "log_date",
    "event_date",
    "order_date",
    "biz_date",
    "stat_date",
    "partition_date",
    "hh",
    "hour",
    "log_hour",
    "event_hour",
}

_TEMPORAL_DATA_TYPES = {"date", "datetime", "timestamp"}
_PUSHDOWN_UNFRIENDLY_CALLS = {"cast", "as_date", "as_timestamp"}


def _source_column_name(node: ast.AST) -> str | None:
    """Return the base table column name for simple chained column expressions."""
    current = node
    while isinstance(current, ast.Call):
        current = current.func
    while isinstance(current, ast.Attribute):
        value = current.value
        if isinstance(value, ast.Name):
            return current.attr
        current = value
        while isinstance(current, ast.Call):
            current = current.func
    return None


def _has_pushdown_unfriendly_time_call(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        if child.func.attr in _PUSHDOWN_UNFRIENDLY_CALLS:
            return True
    return False


def _return_expr(fn: Callable[..., Any]) -> ast.AST | None:
    try:
        source = textwrap.dedent(inspect.getsource(fn))
        tree = ast.parse(source)
    except (OSError, TypeError, IndentationError, SyntaxError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                if isinstance(stmt, ast.Return):
                    return stmt.value
            return None
    return None


def _time_dimension_pushdown_advisory(field_ir: DimensionIR, fn: Callable[..., Any] | None) -> bool:
    if not field_ir.is_time_dimension or field_ir.data_type not in _TEMPORAL_DATA_TYPES:
        return False
    if fn is None:
        return False
    expr = _return_expr(fn)
    if expr is None or not _has_pushdown_unfriendly_time_call(expr):
        return False
    source_column = _source_column_name(expr)
    if source_column is None:
        return False
    return source_column.lower() in _PARTITION_TIME_COLUMN_NAMES


_CAST_TARGET_TO_DECLARED: dict[str, set[str]] = {
    "date": {"date"},
    "timestamp": {"datetime", "timestamp"},
    "string": {"string"},
    "int32": {"integer"},
    "int64": {"integer"},
}


def _infer_terminal_cast(expr: ast.AST) -> str | None:
    """Walk a chained expression to find the terminal type-producing call.

    For table.col.cast("timestamp").cast("date"), the terminal call
    is .cast("date") — the root Call node evaluated last in the chain.
    Also detects .as_date() → "date" and .as_timestamp() → "timestamp".
    """
    current = expr
    while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
        method = current.func.attr
        if method == "cast" and current.args:
            arg = current.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
        if method == "as_date":
            return "date"
        if method == "as_timestamp":
            return "timestamp"
        current = current.func.value
    return None


def _time_dimension_dtype_advisory(
    field_ir: DimensionIR, fn: Callable[..., Any] | None
) -> str | None:
    """Return the inferred cast target if it conflicts with declared data_type, else None."""
    if not field_ir.is_time_dimension or field_ir.data_type not in _TEMPORAL_DATA_TYPES:
        return None
    if fn is None:
        return None
    expr = _return_expr(fn)
    if expr is None:
        return None
    inferred = _infer_terminal_cast(expr)
    if inferred is None:
        return None
    compatible = _CAST_TARGET_TO_DECLARED.get(inferred)
    if compatible is None:
        return None
    if field_ir.data_type not in compatible:
        return inferred
    return None


# ---------------------------------------------------------------------------
# Layer 1: decorator-time validation
# ---------------------------------------------------------------------------


def validate_decorator_call(kind: str, payload: dict[str, Any]) -> None:
    """Layer 1: decorator-time validation.  Raises SemanticDecoratorError.

    Currently a passthrough — decorator-time validation is handled inline
    in the authoring module.  This function exists as an extension point
    for future decorator-level checks.
    """


# ---------------------------------------------------------------------------
# Layer 2: AST whitelist validation
# ---------------------------------------------------------------------------


def _ast_spec_for(constraint_id: ConstraintId) -> ASTSpec:
    constraint = get_constraint(constraint_id)
    assert constraint is not None and constraint.ast_spec is not None
    return constraint.ast_spec


_EXPR_BODY_AST_SPEC = _ast_spec_for(ConstraintId.AST_SINGLE_RETURN)

# Names that indicate a raw SQL escape hatch when used as an attribute.
_SQL_ESCAPE_ATTRS = frozenset(_EXPR_BODY_AST_SPEC.forbidden_attributes)

# ibis Table method/property names that shadow column access via dot notation.
try:
    import ibis as _ibis

    _IBIS_TABLE_ATTRS: frozenset[str] = frozenset(
        name for name in dir(_ibis.Table) if not name.startswith("_")
    )
    del _ibis
except ImportError:
    _IBIS_TABLE_ATTRS = frozenset()

# AST node types that are FORBIDDEN as statements in metric bodies.
_FORBIDDEN_STMT_TYPES: frozenset[type[ast.stmt]] = frozenset(
    {
        ast.Assign,
        ast.AugAssign,
        ast.AnnAssign,
        ast.Import,
        ast.ImportFrom,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.If,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.TryStar,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Delete,
        ast.Global,
        ast.Nonlocal,
        ast.Raise,
        ast.Assert,
        ast.Pass,
        ast.Break,
        ast.Continue,
    }
)


class _BaseMetricASTValidator(ast.NodeVisitor):
    """Walk a single-return ibis expression body AST and accumulate errors."""

    def __init__(self, fn_name: str) -> None:
        self.fn_name = fn_name
        self.errors: list[SemanticError] = []
        self._param_names: set[str] = set()
        self._parent_map: dict[ast.AST, ast.AST] = {}

    def _add_error(
        self,
        kind: ErrorKind,
        message: str,
        *,
        constraint_id: ConstraintId,
    ) -> None:
        self.errors.append(
            SemanticLoadError(
                kind=kind.value,
                message=message,
                refs=(self.fn_name,),
                constraint_id=constraint_id,
            )
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Extract parameter names and build parent map for context-sensitive checks.
        self._param_names = {arg.arg for arg in node.args.args}
        self._parent_map = {
            child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)
        }

        # Validate the function body structure

        # Must have exactly one top-level Return (no nested returns in if/else/etc.)
        # Recursively find all Return nodes in the entire function body
        all_returns: list[ast.Return] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Return):
                all_returns.append(child)

        if len(all_returns) == 0:
            self._add_error(
                ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN,
                f"Metric body of {self.fn_name!r} must contain exactly one "
                f"return statement, found none.",
                constraint_id=ConstraintId.AST_SINGLE_RETURN,
            )
        elif len(all_returns) > 1:
            self._add_error(
                ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN,
                f"Metric body of {self.fn_name!r} must contain exactly one "
                f"return statement, found {len(all_returns)}.",
                constraint_id=ConstraintId.AST_SINGLE_RETURN,
            )

        # Check for forbidden statement types anywhere in the body
        for child in ast.walk(node):
            if child is node:
                continue
            # Skip expression nodes — we only check statement nodes
            if not isinstance(child, ast.stmt):
                continue
            # Allow the single Return; every other statement violates the
            # expression-body contract.
            if isinstance(child, ast.Return):
                continue
            for forbidden_type in (*_FORBIDDEN_STMT_TYPES, ast.Expr):
                if isinstance(child, forbidden_type):
                    kind = ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN
                    constraint_id = ConstraintId.AST_SINGLE_RETURN
                    if isinstance(child, (ast.Import, ast.ImportFrom, ast.Expr)):
                        kind = ErrorKind.INVALID_COMPONENT_BODY
                        constraint_id = ConstraintId.AST_FORBIDDEN_STATEMENT
                    self._add_error(
                        kind,
                        f"Metric body of {self.fn_name!r} contains a forbidden "
                        f"{type(child).__name__} statement.",
                        constraint_id=constraint_id,
                    )
                    break

        # Walk only the function body for deeper AST checks. Decorator calls
        # are normal Python and are not part of the captured expression DSL.
        for stmt in node.body:
            self.visit(stmt)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Check for .sql / .raw_sql escape hatches
        if node.attr in _SQL_ESCAPE_ATTRS:
            self._add_error(
                ErrorKind.SQL_ESCAPE_HATCH,
                f"Metric body of {self.fn_name!r} uses .{node.attr}(), "
                f"which is not allowed. Use source_sql on the decorator instead.",
                constraint_id=ConstraintId.AST_SQL_ESCAPE_HATCH,
            )
        # Check for ibis Table attribute shadowing (e.g. orders.schema instead of orders["schema"])
        if (
            _IBIS_TABLE_ATTRS
            and isinstance(node.value, ast.Name)
            and node.value.id in self._param_names
            and node.attr in _IBIS_TABLE_ATTRS
        ):
            # Method calls like orders.filter(...) are valid ibis; only flag
            # bare attribute access (return orders.schema), not call-site func.
            parent = self._parent_map.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                self._add_error(
                    ErrorKind.IBIS_ATTR_SHADOW,
                    f"Metric body of {self.fn_name!r} accesses .{node.attr} on the "
                    f"entity table parameter '{node.value.id}', which shadows an ibis "
                    f"Table method/property. Use bracket notation instead: "
                    f'{node.value.id}["{node.attr}"].',
                    constraint_id=ConstraintId.AST_IBIS_ATTR_SHADOW,
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "ms"
            and func.attr == "component"
        ):
            self._add_error(
                ErrorKind.INVALID_COMPONENT_BODY,
                f"Metric body of {self.fn_name!r} calls ms.component(), "
                "which is no longer supported. Use ms.derived_metric(...) "
                "for body-free derived metric definitions.",
                constraint_id=ConstraintId.METRIC_COMPONENT_SCOPE,
            )
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._add_error(
            ErrorKind.INVALID_COMPONENT_BODY,
            f"Metric body of {self.fn_name!r} contains a lambda expression, which is not allowed.",
            constraint_id=ConstraintId.AST_FORBIDDEN_STATEMENT,
        )
        # Don't recurse into lambda body


def validate_metric_body_ast(
    fn: Callable[..., Any],
    mode: Literal["base"],
) -> str:
    """Layer 2: AST whitelist validation for base metric bodies.

    Returns the body AST hash for storage in MetricIR.

    Raises SemanticLoadError on validation failures.
    """
    if mode != "base":
        raise ValueError(f"unsupported metric body AST validation mode {mode!r}")

    # Compute body AST hash
    try:
        source = inspect.getsource(fn)
        source = textwrap.dedent(source)
        tree = ast.parse(source)
        # Find the function definition node
        func_node: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == fn.__name__:
                func_node = node
                break
        if func_node is None:
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    func_node = node
                    break

        if func_node is None:
            body_hash = hashlib.sha256(b"<no-function>").hexdigest()[:16]
        else:
            body_source = ast.get_source_segment(source, func_node)
            if body_source is not None:
                body_hash = hashlib.sha256(body_source.encode()).hexdigest()[:16]
            else:
                body_hash = hashlib.sha256(source.encode()).hexdigest()[:16]
    except (OSError, TypeError, IndentationError):
        body_hash = hashlib.sha256(b"<unavailable>").hexdigest()[:16]
        return body_hash

    if func_node is None:
        return body_hash

    base_validator = _BaseMetricASTValidator(fn.__name__)
    base_validator.visit(func_node)
    if base_validator.errors:
        raise base_validator.errors[0]

    return body_hash


# ---------------------------------------------------------------------------
# Layer 3: assembly-time cross-object validation
# ---------------------------------------------------------------------------


_AGGREGATE_METHODS = {"sum", "mean", "avg", "count", "nunique", "max", "min"}


def _validate_snapshot_versioning(
    errors: list[SemanticError],
    ds_id: str,
    ds_ir: EntityIR,
    versioning: SnapshotVersioningIR,
) -> None:
    """Validate snapshot versioning metadata at assembly time."""
    partition_name = versioning.partition_field.rsplit(".", 1)[-1]
    if partition_name not in ds_ir.primary_key:
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_ENTITY_VERSIONING,
                message=(
                    f"Snapshot dataset {ds_id!r} partition field "
                    f"{versioning.partition_field!r} must be part of primary_key."
                ),
                refs=(ds_id, versioning.partition_field),
                details={
                    "entity": ds_id,
                    "field": "partition_field",
                    "partition_field": versioning.partition_field,
                    "primary_key": list(ds_ir.primary_key),
                },
            )
        )


def _validate_validity_versioning(
    errors: list[SemanticError],
    ds_id: str,
    ds_ir: EntityIR,
    versioning: ValidityVersioningIR,
    registry: Registry,
) -> None:
    """Validate validity versioning metadata at assembly time."""
    # valid_from local name must be in primary_key
    valid_from_local = versioning.valid_from.rsplit(".", 1)[-1]
    if valid_from_local not in ds_ir.primary_key:
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_ENTITY_VERSIONING,
                message=(
                    f"Validity entity {ds_id!r} valid_from dimension "
                    f"{versioning.valid_from!r} must be part of primary_key."
                ),
                refs=(ds_id, versioning.valid_from),
                details={
                    "entity": ds_id,
                    "dimension": "valid_from",
                    "reason": (
                        f"{versioning.valid_from!r} is not in primary_key {list(ds_ir.primary_key)}"
                    ),
                },
            )
        )

    # dimension-existence check: valid_from and valid_to must resolve to known dimensions in this entity
    for label, field_id in (
        ("valid_from", versioning.valid_from),
        ("valid_to", versioning.valid_to),
    ):
        field = registry.fields.get(field_id)
        if field is None or field.entity != ds_id:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_ENTITY_VERSIONING,
                    message=(
                        f"Validity entity {ds_id!r} {label} dimension "
                        f"{field_id!r} does not resolve to a known dimension on this entity."
                    ),
                    refs=(ds_id, field_id),
                    details={
                        "entity": ds_id,
                        "dimension": label,
                        "ref": field_id,
                    },
                )
            )


def _aggregate_receiver_param_name(call: ast.Call) -> str | None:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr not in _AGGREGATE_METHODS:
        return None
    current: ast.AST = func.value
    while isinstance(current, (ast.Attribute, ast.Subscript, ast.Call)):
        if isinstance(current, ast.Attribute):
            current = current.value
            continue
        if isinstance(current, ast.Subscript):
            current = current.value
            continue
        if isinstance(current, ast.Call):
            current = current.func
            continue
    if isinstance(current, ast.Name):
        return current.id
    return None


def _non_root_aggregate_entity(
    fn: Callable[..., Any],
    *,
    metric_ir: MetricIR,
) -> str | None:
    try:
        source = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return None
    tree = ast.parse(source)
    func = next((node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)), None)
    if func is None:
        return None
    param_names = [arg.arg for arg in func.args.args]
    entity_by_param = dict(zip(param_names, metric_ir.entities, strict=False))
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        param = _aggregate_receiver_param_name(node)
        if param is None:
            continue
        entity = entity_by_param.get(param)
        if entity is not None and entity != metric_ir.root_entity:
            return entity
    return None


def _is_filtered_domain_ref(ref: str, loaded_models: set[str] | None) -> bool:
    """Return True if ref points to an object in a model that was filtered out."""
    if loaded_models is None or "." not in ref:
        return False
    return ref.split(".", 1)[0] not in loaded_models


def _validate_default_time_dimension_unique(
    errors: list[SemanticError],
    registry: Registry,
) -> None:
    from collections import defaultdict

    defaults_by_entity: dict[str, list[str]] = defaultdict(list)
    for f_id, f_ir in registry.fields.items():
        if f_ir.is_time_dimension and getattr(f_ir, "is_default", False):
            defaults_by_entity[f_ir.entity].append(f_id)

    for entity_id, field_ids in defaults_by_entity.items():
        if len(field_ids) > 1:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.DUPLICATE_DEFAULT_TIME_DIMENSION,
                    message=(
                        f"Entity {entity_id!r} has {len(field_ids)} time dimensions "
                        f"with is_default=True: {field_ids}. At most one is allowed."
                    ),
                    refs=tuple(field_ids),
                    constraint_id=ConstraintId.TIME_DIMENSION_DEFAULT_UNIQUE,
                    details={
                        "entity": entity_id,
                        "default_time_dimensions": field_ids,
                    },
                )
            )


def _filtered_domain_ref_warning(
    obj_id: str,
    ref: str,
    ref_kind: str,
) -> StructuredWarning:
    """Build a warning for a cross-object ref to a filtered-out domain."""
    ref_domain = ref.split(".", 1)[0]
    return StructuredWarning(
        kind="filtered_domain_ref",
        message=f"{ref_kind} {obj_id!r} references {ref!r} from filtered-out domain {ref_domain!r}.",
        refs=(obj_id, ref),
        location=None,
    )


def _validate_sampled_time_folds(registry: Registry, errors: list[SemanticError]) -> None:
    for metric_id, metric_ir in registry.metrics.items():
        root = metric_ir.root_entity or (
            metric_ir.entities[0] if len(metric_ir.entities) == 1 else None
        )
        if metric_ir.is_derived:
            if metric_ir.time_fold is not None:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.TIME_FOLD_REQUIRES_SEMI_ADDITIVE,
                        message=f"Derived metric {metric_id!r} cannot declare time_fold.",
                        refs=(metric_id,),
                        details={"metric": metric_id},
                    )
                )
            continue
        if metric_ir.time_fold is not None and metric_ir.additivity != "semi_additive":
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.TIME_FOLD_REQUIRES_SEMI_ADDITIVE,
                    message=f"Metric {metric_id!r} time_fold requires additivity='semi_additive'.",
                    refs=(metric_id,),
                    details={"metric": metric_id, "additivity": metric_ir.additivity},
                )
            )
            continue
        if metric_ir.additivity != "semi_additive":
            continue

        status_time_dimension = metric_ir.status_time_dimension
        if status_time_dimension is None:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_STATUS_TIME_DIMENSION,
                    message=f"Semi-additive metric {metric_id!r} must declare status_time_dimension.",
                    refs=(metric_id,),
                    constraint_id=ConstraintId.STATUS_TIME_DIMENSION_REQUIRED,
                    details={"metric": metric_id, "root_entity": root},
                )
            )
            continue

        status_field = registry.fields.get(status_time_dimension)
        if (
            status_field is None
            or not status_field.is_time_dimension
            or root is None
            or status_field.entity != root
        ):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_STATUS_TIME_DIMENSION,
                    message=(
                        f"Metric {metric_id!r} status_time_dimension must reference a "
                        "time dimension on its root entity."
                    ),
                    refs=(metric_id, status_time_dimension),
                    constraint_id=ConstraintId.STATUS_TIME_DIMENSION_INVALID,
                    details={
                        "metric": metric_id,
                        "root_entity": root,
                        "status_time_dimension": status_time_dimension,
                    },
                )
            )
            continue

        if status_field.sample_interval is not None and metric_ir.time_fold is None:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_TIME_FOLD,
                    message=f"Sampled semi-additive metric {metric_id!r} must declare time_fold.",
                    refs=(metric_id, status_time_dimension),
                    details={
                        "metric": metric_id,
                        "sampled_time_dimension": status_time_dimension,
                    },
                )
            )
            continue

        if metric_ir.time_fold is not None and status_field.sample_interval is None:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.TIME_FOLD_REQUIRES_SAMPLED_TIME_FIELD,
                    message=(
                        f"Metric {metric_id!r} declares time_fold but its "
                        "status_time_dimension is not sampled."
                    ),
                    refs=(metric_id, status_time_dimension),
                    details={
                        "metric": metric_id,
                        "status_time_dimension": status_time_dimension,
                    },
                )
            )


def assembly_validate(
    registry: Registry,
    sidecar: Sidecar | None = None,
    *,
    loaded_models: set[str] | None = None,
) -> tuple[list[SemanticError], list[StructuredWarning]]:
    """Layer 3: assembly-time cross-object validation.

    Returns (errors, warnings).  Does not raise.

    When *loaded_models* is provided, cross-object references to objects
    in models that were intentionally not loaded produce
    ``filtered_domain_ref`` warnings instead of errors, so the registry
    remains usable.
    """
    errors: list[SemanticError] = []
    warnings: list[StructuredWarning] = []

    # -- Validate datasource refs on entities --------------------------------
    for ds_id, ds_ir in registry.datasets.items():
        if ds_ir.datasource not in registry.datasources:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_ENTITY_REF,
                    message=f"Entity {ds_id!r} references unknown datasource {ds_ir.datasource!r}.",
                    refs=(ds_id, ds_ir.datasource),
                    details={
                        "missing_ref": ds_ir.datasource,
                        "did_you_mean": did_you_mean(
                            ds_ir.datasource, sorted(registry.datasources.keys())
                        ),
                    },
                )
            )
        # Warn on string datasource ref
        # (String refs are the norm currently, so skip warning for now.
        #  This will become meaningful when typed refs are more common.)

        versioning = ds_ir.versioning
        if versioning is not None:
            if isinstance(versioning, SnapshotVersioningIR):
                _validate_snapshot_versioning(errors, ds_id, ds_ir, versioning)
            elif isinstance(versioning, ValidityVersioningIR):
                _validate_validity_versioning(errors, ds_id, ds_ir, versioning, registry)

    # -- Validate entity refs on dimensions ----------------------------------
    for f_id, f_ir in registry.fields.items():
        if f_ir.entity not in registry.datasets:
            if _is_filtered_domain_ref(f_ir.entity, loaded_models):
                warnings.append(_filtered_domain_ref_warning(f_id, f_ir.entity, "Dimension"))
            else:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.MISSING_ENTITY_REF,
                        message=f"Dimension {f_id!r} references unknown entity {f_ir.entity!r}.",
                        refs=(f_id, f_ir.entity),
                        details={
                            "missing_ref": f_ir.entity,
                            "did_you_mean": did_you_mean(
                                f_ir.entity, sorted(registry.datasets.keys())
                            ),
                        },
                    )
                )

    # -- Validate entity refs on metrics -------------------------------------
    for m_id, m_ir in registry.metrics.items():
        for ds_ref in m_ir.entities:
            if ds_ref not in registry.datasets:
                if _is_filtered_domain_ref(ds_ref, loaded_models):
                    warnings.append(_filtered_domain_ref_warning(m_id, ds_ref, "Metric"))
                else:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.MISSING_ENTITY_REF,
                            message=f"Metric {m_id!r} references unknown entity {ds_ref!r}.",
                            refs=(m_id, ds_ref),
                            details={
                                "missing_ref": ds_ref,
                                "did_you_mean": did_you_mean(
                                    ds_ref, sorted(registry.datasets.keys())
                                ),
                            },
                        )
                    )

    # -- Validate base metric additivity and root_entity -------------------
    for m_id, m_ir in registry.metrics.items():
        if m_ir.is_derived:
            continue
        if m_ir.additivity is None:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_METRIC_ADDITIVITY,
                    message=f"Base metric {m_id!r} must declare additivity.",
                    refs=(m_id,),
                    details={"metric": m_id},
                )
            )
        if len(m_ir.entities) == 0:
            continue
        if len(m_ir.entities) == 1 and m_ir.root_entity is None:
            continue
        if len(m_ir.entities) > 1 and m_ir.root_entity is None:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_METRIC_ROOT_DATASET,
                    message=f"Multi-entity base metric {m_id!r} must declare root_entity.",
                    refs=(m_id,),
                    details={"metric": m_id, "entities": sorted(m_ir.entities)},
                )
            )
            continue
        if m_ir.root_entity is not None and m_ir.root_entity not in m_ir.entities:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_METRIC_ROOT_DATASET,
                    message=(
                        f"Metric {m_id!r} root_entity {m_ir.root_entity!r} "
                        "must be one of its entities."
                    ),
                    refs=(m_id, m_ir.root_entity),
                    details={
                        "metric": m_id,
                        "root_entity": m_ir.root_entity,
                        "entities": sorted(m_ir.entities),
                    },
                )
            )

    # -- Validate metric fanout_policy --------------------------------------
    for m_id, m_ir in registry.metrics.items():
        policy = getattr(m_ir, "fanout_policy", "block")
        if policy not in {"block", "aggregate_then_join"}:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_METRIC_FANOUT_POLICY,
                    message=(
                        f"Metric {m_id!r} fanout_policy {policy!r} must be "
                        "'block' or 'aggregate_then_join'."
                    ),
                    refs=(m_id,),
                    details={"metric": m_id, "fanout_policy": policy},
                )
            )
            continue
        if m_ir.is_derived and policy != "block":
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.DERIVED_METRIC_FANOUT_POLICY,
                    message=(
                        f"Derived metric {m_id!r} must keep fanout_policy='block'; "
                        "fan-out is authored on the component metrics."
                    ),
                    refs=(m_id,),
                    details={"metric": m_id, "fanout_policy": policy},
                )
            )
            continue
        if (
            policy == "aggregate_then_join"
            and not m_ir.is_derived
            and m_ir.additivity not in {"additive", "semi_additive"}
        ):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_METRIC_FANOUT_POLICY,
                    message=(
                        f"Metric {m_id!r} fanout_policy='aggregate_then_join' "
                        "requires additivity in {'additive', 'semi_additive'}."
                    ),
                    refs=(m_id,),
                    details={
                        "metric": m_id,
                        "fanout_policy": policy,
                        "additivity": m_ir.additivity,
                    },
                )
            )

    # -- Validate root-only aggregates for multi-entity base metrics ----------
    for m_id, m_ir in registry.metrics.items():
        if m_ir.is_derived:
            continue
        if sidecar is not None and len(m_ir.entities) > 1 and m_ir.root_entity is not None:
            fn = sidecar.get(m_id)
            if callable(fn):
                offending_entity = _non_root_aggregate_entity(fn, metric_ir=m_ir)
                if offending_entity is not None:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.NON_ROOT_METRIC_AGGREGATE,
                            message=(
                                f"Metric {m_id!r} aggregates a non-root entity "
                                f"{offending_entity!r}."
                            ),
                            refs=(m_id, offending_entity),
                            details={
                                "metric": m_id,
                                "root_entity": m_ir.root_entity,
                                "offending_entity": offending_entity,
                            },
                        )
                    )

    # -- Validate sampled semi-additive time folds ---------------------------
    _validate_sampled_time_folds(registry, errors)

    # -- Validate metric component refs in decomposition --------------------
    for m_id, m_ir in registry.metrics.items():
        for comp_key, comp_ref in m_ir.decomposition.components.items():
            if comp_ref not in registry.metrics:
                if _is_filtered_domain_ref(comp_ref, loaded_models):
                    warnings.append(
                        _filtered_domain_ref_warning(m_id, comp_ref, "Metric component")
                    )
                else:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.MISSING_METRIC_REF,
                            message=f"Metric {m_id!r} decomposition component "
                            f"{comp_key!r} references unknown metric "
                            f"{comp_ref!r}.",
                            refs=(m_id, comp_ref),
                            details={
                                "missing_ref": comp_ref,
                                "did_you_mean": did_you_mean(
                                    comp_ref, sorted(registry.metrics.keys())
                                ),
                            },
                        )
                    )

    # -- Validate dimension refs in relationships -----------------------------
    for r_id, r_ir in registry.relationships.items():
        # Dimension arity check: from_dimensions and to_dimensions must have same length
        if len(r_ir.from_dimensions) != len(r_ir.to_dimensions):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_DIMENSION_REF,
                    message=f"Relationship {r_id!r} has {len(r_ir.from_dimensions)} from_dimensions "
                    f"but {len(r_ir.to_dimensions)} to_dimensions. "
                    f"Dimension counts must match.",
                    refs=(r_id,),
                )
            )
        if r_ir.from_entity not in registry.datasets:
            if _is_filtered_domain_ref(r_ir.from_entity, loaded_models):
                warnings.append(
                    _filtered_domain_ref_warning(r_id, r_ir.from_entity, "Relationship")
                )
            else:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_RELATIONSHIP_ENDPOINT,
                        message=f"Relationship {r_id!r} references unknown "
                        f"from_entity {r_ir.from_entity!r}.",
                        refs=(r_id, r_ir.from_entity),
                    )
                )
        if r_ir.to_entity not in registry.datasets:
            if _is_filtered_domain_ref(r_ir.to_entity, loaded_models):
                warnings.append(_filtered_domain_ref_warning(r_id, r_ir.to_entity, "Relationship"))
            else:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_RELATIONSHIP_ENDPOINT,
                        message=f"Relationship {r_id!r} references unknown "
                        f"to_entity {r_ir.to_entity!r}.",
                        refs=(r_id, r_ir.to_entity),
                    )
                )
        # Validate dimension refs
        for ff in r_ir.from_dimensions:
            if ff not in registry.fields:
                if _is_filtered_domain_ref(ff, loaded_models):
                    warnings.append(_filtered_domain_ref_warning(r_id, ff, "Relationship"))
                else:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.MISSING_DIMENSION_REF,
                            message=f"Relationship {r_id!r} references unknown from_dimension {ff!r}.",
                            refs=(r_id, ff),
                            details={
                                "missing_ref": ff,
                                "did_you_mean": did_you_mean(ff, sorted(registry.fields.keys())),
                            },
                        )
                    )
        for tf in r_ir.to_dimensions:
            if tf not in registry.fields:
                if _is_filtered_domain_ref(tf, loaded_models):
                    warnings.append(_filtered_domain_ref_warning(r_id, tf, "Relationship"))
                else:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.MISSING_DIMENSION_REF,
                            message=f"Relationship {r_id!r} references unknown to_dimension {tf!r}.",
                            refs=(r_id, tf),
                            details={
                                "missing_ref": tf,
                                "did_you_mean": did_you_mean(tf, sorted(registry.fields.keys())),
                            },
                        )
                    )

    # -- Validate hour-only time_dimension required_prefix -------------------
    # -- Validate sub-day granularity requires time-bearing data_type --------
    for f_id, f_ir in registry.fields.items():
        if _requires_required_prefix(f_ir) and not f_ir.required_prefix:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.HOUR_TIME_DIMENSION_PREFIX_MISSING,
                    message=f"Hour-only time dimension {f_id!r} requires a "
                    f"required_prefix pointing to a day-level time dimension.",
                    refs=(f_id,),
                )
            )
        if _subday_granularity_needs_time(f_ir):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.SUBDAY_GRANULARITY_WITHOUT_TIME,
                    message=(
                        f"time dimension {f_id!r} declares sub-day granularity "
                        f"{f_ir.granularity!r} but its data_type {f_ir.data_type!r} cannot carry time"
                    ),
                    refs=(f_id,),
                    constraint_id=ConstraintId.SUBDAY_GRANULARITY_WITHOUT_TIME,
                    details={
                        "kind": "SubdayGranularityWithoutTime",
                        "field": f_id,
                        "granularity": f_ir.granularity,
                        "data_type": f_ir.data_type,
                    },
                )
            )
        if (
            f_ir.is_time_dimension
            and f_ir.required_prefix
            and (
                (prefix_field := _resolve_required_prefix_field(registry, field_ir=f_ir)) is None
                or not prefix_field.is_time_dimension
            )
        ):
            if _is_filtered_domain_ref(f_ir.required_prefix, loaded_models):
                warnings.append(
                    _filtered_domain_ref_warning(f_id, f_ir.required_prefix, "Time dimension")
                )
            else:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.MISSING_DIMENSION_REF,
                        message=f"Time dimension {f_id!r} required_prefix "
                        f"{f_ir.required_prefix!r} is not a registered time dimension.",
                        refs=(f_id, f_ir.required_prefix),
                        details={
                            "missing_ref": f_ir.required_prefix,
                            "did_you_mean": did_you_mean(
                                f_ir.required_prefix, sorted(registry.fields.keys())
                            ),
                        },
                    )
                )

    # -- Validate at most one default time_dimension per entity --------------
    _validate_default_time_dimension_unique(errors, registry)

    # -- Cross-model cycle detection (basic) --------------------------------
    # Check for cycles in metric component references
    _detect_metric_cycles(registry, errors)

    # -- Metric verification mode contract ----------------------------------
    for m_id, m_ir in registry.metrics.items():
        prov = m_ir.provenance
        if m_ir.is_derived:
            if (
                prov.verification_mode is not None
                or prov.source_sql is not None
                or prov.source_dialect is not None
            ):
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_VERIFICATION_MODE,
                        message=(
                            f"Derived metric {m_id!r} must omit verification_mode, "
                            "source_sql, and source_dialect. Verify its component metrics instead."
                        ),
                        refs=(m_id,),
                        location=m_ir.location,
                        constraint_id=ConstraintId.METRIC_VERIFICATION_MODE_VALID,
                    )
                )
            continue

        if prov.verification_mode not in {"sql_parity", "python_native"}:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_VERIFICATION_MODE,
                    message=(
                        f"Base metric {m_id!r} must declare verification_mode='sql_parity' "
                        "or verification_mode='python_native'."
                    ),
                    refs=(m_id,),
                    location=m_ir.location,
                    constraint_id=ConstraintId.METRIC_VERIFICATION_MODE_VALID,
                )
            )
            continue

        if prov.verification_mode == "sql_parity" and (
            not prov.source_sql or not prov.source_dialect
        ):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.SOURCE_SQL_MISSING,
                    message=(
                        f"Metric {m_id!r} uses verification_mode='sql_parity' but "
                        "does not declare both source_sql and source_dialect."
                    ),
                    refs=(m_id,),
                    location=m_ir.location,
                    constraint_id=ConstraintId.SOURCE_SQL_REQUIRED,
                )
            )
        if prov.verification_mode == "python_native" and (
            prov.source_sql is not None or prov.source_dialect is not None
        ):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_VERIFICATION_MODE,
                    message=(
                        f"Metric {m_id!r} uses verification_mode='python_native' but "
                        "declares SQL parity provenance."
                    ),
                    refs=(m_id,),
                    location=m_ir.location,
                    constraint_id=ConstraintId.METRIC_VERIFICATION_MODE_VALID,
                )
            )

    # -- Warnings -----------------------------------------------------------
    # String ref warnings: datasource names are intentionally strings in the
    # target API, and cross-file refs are common, so skip string-ref warnings.

    # Partition pushdown advisory warnings
    for f_id, f_ir in registry.fields.items():
        if _time_dimension_pushdown_advisory(f_ir, None if sidecar is None else sidecar.get(f_id)):
            warnings.append(
                StructuredWarning(
                    kind=WarningKind.TIME_DIMENSION_PUSHDOWN_ADVISORY.value,
                    message=(
                        f"Time field {f_id!r} casts or parses a partition-like source column. "
                        "If this is a day/hour partition axis, prefer a raw string/integer "
                        "time_field with date_format so window filters can use simple "
                        "partition comparisons."
                    ),
                    refs=(f_id,),
                    location=f_ir.location,
                )
            )

    # Dtype/data_type mismatch advisory warnings
    for f_id, f_ir in registry.fields.items():
        inferred = _time_dimension_dtype_advisory(
            f_ir, None if sidecar is None else sidecar.get(f_id)
        )
        if inferred is not None:
            compatible = sorted(_CAST_TARGET_TO_DECLARED.get(inferred, set()))
            warnings.append(
                StructuredWarning(
                    kind=WarningKind.TIME_DIMENSION_DTYPE_ADVISORY.value,
                    message=(
                        f"Time field {f_id!r} declared data_type={f_ir.data_type!r} "
                        f"but body .cast({inferred!r}) produces ibis dtype {inferred!r}. "
                        f"Compatible data_type values: {', '.join(compatible)}. "
                        "This mismatch causes TypeError at execution."
                    ),
                    refs=(f_id,),
                    location=f_ir.location,
                )
            )

    return errors, warnings


def _detect_metric_cycles(
    registry: Registry,
    errors: list[SemanticError],
) -> None:
    """Detect circular references in metric decomposition components."""
    # Build adjacency: metric -> set of metrics it references via components
    adj: dict[str, set[str]] = {}
    for m_id, m_ir in registry.metrics.items():
        deps: set[str] = set()
        for comp_ref in m_ir.decomposition.components.values():
            if comp_ref in registry.metrics:
                deps.add(comp_ref)
        adj[m_id] = deps

    # DFS-based cycle detection
    unvisited, in_progress, done = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(adj, unvisited)

    def dfs(node: str, path: list[str]) -> bool:
        color[node] = in_progress
        path.append(node)
        for neighbor in adj.get(node, set()):
            if color.get(neighbor) == in_progress:
                # Found a cycle
                cycle_start = path.index(neighbor)
                cycle = [*path[cycle_start:], neighbor]
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.CROSS_MODEL_CYCLE,
                        message=f"Circular metric reference detected: {' -> '.join(cycle)}",
                        refs=tuple(cycle),
                    )
                )
                return True
            if color.get(neighbor) == unvisited and dfs(neighbor, path):
                return True
        path.pop()
        color[node] = done
        return False

    for m_id in adj:
        if color[m_id] == unvisited:
            dfs(m_id, [])
