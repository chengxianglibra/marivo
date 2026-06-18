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
    DateParse,
    DatetimeParse,
    DimensionIR,
    DomainIR,
    EntityIR,
    HourPrefixParse,
    LinearComposition,
    MeasureIR,
    MetricIR,
    RelationshipIR,
    SnapshotVersioningIR,
    StrptimeParse,
    TimestampParse,
    ValidityVersioningIR,
    composition_components,
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

    domains: dict[str, DomainIR] = field(default_factory=dict)
    datasources: dict[str, DatasourceIR] = field(default_factory=dict)
    entities: dict[str, EntityIR] = field(default_factory=dict)
    dimensions: dict[str, DimensionIR] = field(default_factory=dict)
    measures: dict[str, MeasureIR] = field(default_factory=dict)
    metrics: dict[str, MetricIR] = field(default_factory=dict)
    relationships: dict[str, RelationshipIR] = field(default_factory=dict)


#: Maps semantic_id to the original callable (entity/dimension/metric body fn).
Sidecar = dict[str, Callable[..., Any]]


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
    if not field_ir.is_time_dimension:
        return False
    parse = field_ir.parse
    _temporal_parse_kinds = (DateParse, DatetimeParse, TimestampParse)
    if not isinstance(parse, _temporal_parse_kinds):
        return False
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
    if not field_ir.is_time_dimension:
        return None
    parse = field_ir.parse
    _temporal_parse_kinds = (DateParse, DatetimeParse, TimestampParse)
    if not isinstance(parse, _temporal_parse_kinds):
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
    # Extract data_type from parse variant for comparison
    parse = field_ir.parse
    data_type_val: str | None = None
    if parse is None:
        return None  # deferred parse — cannot validate at this stage
    if isinstance(parse, DateParse):
        data_type_val = "date"
    elif isinstance(parse, DatetimeParse):
        data_type_val = "datetime"
    elif isinstance(parse, TimestampParse):
        data_type_val = "timestamp"
    elif isinstance(parse, StrptimeParse):
        data_type_val = "strptime"
    elif isinstance(parse, HourPrefixParse):
        data_type_val = "hour_prefix"
    if data_type_val not in compatible:
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
                f"which is not allowed. Use provenance=ms.from_sql(...) on the decorator instead.",
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
                "which is no longer supported. Use ms.ratio/ms.weighted_average/ms.linear "
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
        field = registry.dimensions.get(field_id)
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


def _validate_measure_refs(registry: Registry) -> list[SemanticError]:
    """Validate that every measure's entity resolves to a known entity."""
    errors: list[SemanticError] = []
    for measure in registry.measures.values():
        if measure.entity not in registry.entities:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.ENTITY_NOT_FOUND,
                    message=f"Measure {measure.semantic_id!r} references unknown entity {measure.entity!r}.",
                    refs=(measure.semantic_id, measure.entity),
                    details={"measure": measure.semantic_id, "entity": measure.entity},
                )
            )
    return errors


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
    for f_id, f_ir in registry.dimensions.items():
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
    from marivo.semantic.ir import DimensionKind, SemiAdditive

    for metric_id, metric_ir in registry.metrics.items():
        add = metric_ir.additivity
        if not isinstance(add, SemiAdditive):
            continue
        field = registry.dimensions.get(add.over)
        if field is None or field.kind is not DimensionKind.TIME:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_STATUS_TIME_DIMENSION,
                    message=(
                        f"Metric {metric_id!r} is semi-additive over {add.over!r}, "
                        "which is not a declared time dimension."
                    ),
                    refs=(metric_id, add.over),
                    constraint_id=ConstraintId.STATUS_TIME_DIMENSION_INVALID,
                    details={"metric": metric_id, "over": add.over},
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
    for ds_id, ds_ir in registry.entities.items():
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
    for f_id, f_ir in registry.dimensions.items():
        if f_ir.entity not in registry.entities:
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
                                f_ir.entity, sorted(registry.entities.keys())
                            ),
                        },
                    )
                )

    # -- Validate measure entity refs -----------------------------------------
    errors.extend(_validate_measure_refs(registry))

    # -- Validate entity refs on metrics -------------------------------------
    for m_id, m_ir in registry.metrics.items():
        for ds_ref in m_ir.entities:
            if ds_ref not in registry.entities:
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
                                    ds_ref, sorted(registry.entities.keys())
                                ),
                            },
                        )
                    )

    # -- Validate metric additivity + tier-1 measure resolution --------------
    from marivo.semantic.ir import additivity_bucket as _bucket

    for m_id, m_ir in registry.metrics.items():
        if m_ir.metric_type == "derived":
            continue
        if m_ir.additivity is None:
            # Resolution failed: diagnose the tier-1 cause precisely.
            if m_ir.aggregation is not None:
                measure: MeasureIR | DimensionIR | None = registry.measures.get(m_ir.measure or "")
                if measure is None:
                    measure = registry.dimensions.get(m_ir.measure or "")
                if measure is None:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.UNKNOWN_MEASURE,
                            message=f"Metric {m_id!r} references unknown measure {m_ir.measure!r}.",
                            refs=(m_id, m_ir.measure or ""),
                            details={
                                "metric": m_id,
                                "measure": m_ir.measure,
                                "did_you_mean": did_you_mean(
                                    m_ir.measure or "",
                                    sorted(
                                        set(registry.dimensions.keys())
                                        | set(registry.measures.keys())
                                    ),
                                ),
                            },
                        )
                    )
                elif getattr(measure, "additivity", None) is None:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.MISSING_MEASURE_ADDITIVITY,
                            message=f"Measure {m_ir.measure!r} used by {m_id!r} must declare additivity.",
                            refs=(m_id, m_ir.measure or ""),
                            constraint_id=ConstraintId.MEASURE_ADDITIVITY_REQUIRED,
                            details={"metric": m_id, "measure": m_ir.measure},
                        )
                    )
                else:
                    agg = m_ir.aggregation
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.INVALID_MEASURE_AGGREGATION,
                            message=(
                                f"Metric {m_id!r} applies {agg!r} to non-additive measure "
                                f"{m_ir.measure!r}; use mean/min/max or a ratio."
                            ),
                            refs=(m_id, m_ir.measure or ""),
                            constraint_id=ConstraintId.MEASURE_AGGREGATION_VALID,
                            details={"metric": m_id, "measure": m_ir.measure, "aggregation": agg},
                        )
                    )
            else:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.MISSING_METRIC_ADDITIVITY,
                        message=f"Simple metric {m_id!r} must declare additivity.",
                        refs=(m_id,),
                        constraint_id=ConstraintId.METRIC_ADDITIVITY_REQUIRED,
                        details={"metric": m_id},
                    )
                )
            continue
        if len(m_ir.entities) == 0:
            continue
        if len(m_ir.entities) == 1 and m_ir.root_entity is None:
            continue
        if len(m_ir.entities) > 1 and m_ir.root_entity is None:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_METRIC_ROOT_ENTITY,
                    message=f"Multi-entity base metric {m_id!r} must declare root_entity.",
                    refs=(m_id,),
                    details={"metric": m_id, "entities": sorted(m_ir.entities)},
                )
            )
            continue
        if m_ir.root_entity is not None and m_ir.root_entity not in m_ir.entities:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_METRIC_ROOT_ENTITY,
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
        if m_ir.metric_type == "derived" and policy != "block":
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
            and m_ir.metric_type != "derived"
            and m_ir.additivity is not None
            and _bucket(m_ir.additivity) not in {"additive", "semi_additive"}
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
        if m_ir.metric_type != "simple" or m_ir.aggregation is not None:
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

    # -- Validate metric component refs in composition --------------------
    for m_id, m_ir in registry.metrics.items():
        if m_ir.composition is None:
            continue
        for comp_key, comp_ref in composition_components(m_ir.composition).items():
            if comp_ref not in registry.metrics:
                if _is_filtered_domain_ref(comp_ref, loaded_models):
                    warnings.append(
                        _filtered_domain_ref_warning(m_id, comp_ref, "Metric component")
                    )
                else:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.MISSING_METRIC_REF,
                            message=f"Metric {m_id!r} composition component "
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

        if isinstance(m_ir.composition, LinearComposition):
            from marivo.semantic.unit_algebra import linear_units_conflict

            term_units = [
                registry.metrics[t.metric].unit
                for t in m_ir.composition.terms
                if t.metric in registry.metrics
            ]
            if linear_units_conflict(term_units):
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                        message=(
                            f"Metric {m_id!r} adds incommensurable units "
                            f"{sorted(u for u in term_units if u is not None)!r}; "
                            "linear terms must share one unit."
                        ),
                        refs=(m_id,),
                        constraint_id=ConstraintId.LINEAR_UNIT_COMMENSURABLE,
                        details={
                            "metric": m_id,
                            "units": {
                                t.metric: registry.metrics[t.metric].unit
                                for t in m_ir.composition.terms
                                if t.metric in registry.metrics
                            },
                        },
                    )
                )

    # -- Validate dimension refs in relationships -----------------------------
    for r_id, r_ir in registry.relationships.items():
        if r_ir.from_entity not in registry.entities:
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
        if r_ir.to_entity not in registry.entities:
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
        # Validate dimension refs via JoinKey pairs
        for join_key in r_ir.keys:
            ff = join_key.from_key
            if ff not in registry.dimensions:
                if _is_filtered_domain_ref(ff, loaded_models):
                    warnings.append(_filtered_domain_ref_warning(r_id, ff, "Relationship"))
                else:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.MISSING_DIMENSION_REF,
                            message=f"Relationship {r_id!r} references unknown from_key {ff!r}.",
                            refs=(r_id, ff),
                            details={
                                "missing_ref": ff,
                                "did_you_mean": did_you_mean(
                                    ff, sorted(registry.dimensions.keys())
                                ),
                            },
                        )
                    )
            tf = join_key.to_key
            if tf not in registry.dimensions:
                if _is_filtered_domain_ref(tf, loaded_models):
                    warnings.append(_filtered_domain_ref_warning(r_id, tf, "Relationship"))
                else:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.MISSING_DIMENSION_REF,
                            message=f"Relationship {r_id!r} references unknown to_key {tf!r}.",
                            refs=(r_id, tf),
                            details={
                                "missing_ref": tf,
                                "did_you_mean": did_you_mean(
                                    tf, sorted(registry.dimensions.keys())
                                ),
                            },
                        )
                    )

    # -- Validate HourPrefixParse prefix cross-reference ---------------------
    for f_id, f_ir in registry.dimensions.items():
        if f_ir.is_time_dimension and isinstance(f_ir.parse, HourPrefixParse):
            prefix_name = f_ir.parse.prefix
            prefix_field = registry.dimensions.get(prefix_name)
            if prefix_field is None:
                candidates = [
                    d
                    for d in registry.dimensions.values()
                    if d.entity == f_ir.entity and d.name == prefix_name
                ]
                prefix_field = candidates[0] if len(candidates) == 1 else None
            if prefix_field is None or not prefix_field.is_time_dimension:
                if _is_filtered_domain_ref(prefix_name, loaded_models):
                    warnings.append(
                        _filtered_domain_ref_warning(f_id, prefix_name, "Time dimension")
                    )
                else:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.MISSING_DIMENSION_REF,
                            message=f"Time dimension {f_id!r} prefix "
                            f"{prefix_name!r} is not a registered time dimension.",
                            refs=(f_id, prefix_name),
                            details={
                                "missing_ref": prefix_name,
                                "did_you_mean": did_you_mean(
                                    prefix_name, sorted(registry.dimensions.keys())
                                ),
                            },
                        )
                    )

    # -- Validate at most one default time_dimension per entity --------------
    _validate_default_time_dimension_unique(errors, registry)

    # -- Cross-model cycle detection (basic) --------------------------------
    # Check for cycles in metric component references
    _detect_metric_cycles(registry, errors)

    # -- Metric provenance contract ------------------------------------------
    # SqlProvenance carries sql + dialect; verification_mode is always "sql_parity".
    # - Base metrics: SqlProvenance.sql requires a non-empty dialect
    # - Derived metrics: must not carry provenance
    for m_id, m_ir in registry.metrics.items():
        prov = m_ir.provenance
        if m_ir.metric_type == "derived":
            if prov is not None:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_VERIFICATION_MODE,
                        message=(
                            f"Derived metric {m_id!r} must omit provenance. "
                            "Verify its component metrics instead."
                        ),
                        refs=(m_id,),
                        location=m_ir.location,
                        constraint_id=ConstraintId.METRIC_VERIFICATION_MODE_VALID,
                    )
                )
            continue

        if prov is not None and not prov.dialect:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.PROVENANCE_DIALECT_MISSING,
                    message=(
                        f"Metric {m_id!r} declares provenance SQL but not a dialect. "
                        "Both are required for SQL parity verification."
                    ),
                    refs=(m_id,),
                    location=m_ir.location,
                    constraint_id=ConstraintId.PROVENANCE_DIALECT_REQUIRED,
                )
            )

    # -- Warnings -----------------------------------------------------------
    # String ref warnings: datasource names are intentionally strings in the
    # target API, and cross-file refs are common, so skip string-ref warnings.

    # Partition pushdown advisory warnings
    for f_id, f_ir in registry.dimensions.items():
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
    for f_id, f_ir in registry.dimensions.items():
        inferred = _time_dimension_dtype_advisory(
            f_ir, None if sidecar is None else sidecar.get(f_id)
        )
        if inferred is not None:
            compatible = sorted(_CAST_TARGET_TO_DECLARED.get(inferred, set()))
            parse = f_ir.parse
            declared_data_type: str | None = None
            if parse is None:
                continue  # deferred parse — skip dtype advisory
            elif isinstance(parse, DateParse):
                declared_data_type = "date"
            elif isinstance(parse, DatetimeParse):
                declared_data_type = "datetime"
            elif isinstance(parse, TimestampParse):
                declared_data_type = "timestamp"
            elif isinstance(parse, StrptimeParse):
                declared_data_type = "strptime"
            elif isinstance(parse, HourPrefixParse):
                declared_data_type = "hour_prefix"
            warnings.append(
                StructuredWarning(
                    kind=WarningKind.TIME_DIMENSION_DTYPE_ADVISORY.value,
                    message=(
                        f"Time field {f_id!r} declared data_type={declared_data_type!r} "
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
    """Detect circular references in metric composition components."""
    # Build adjacency: metric -> set of metrics it references via components
    adj: dict[str, set[str]] = {}
    for m_id, m_ir in registry.metrics.items():
        deps: set[str] = set()
        if m_ir.composition is not None:
            for comp_ref in composition_components(m_ir.composition).values():
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
