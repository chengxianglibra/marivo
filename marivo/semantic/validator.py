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
from marivo.semantic.constraints import ASTSpec, ConstraintId, get_constraint
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
    StructuredWarning,
    WarningKind,
)
from marivo.semantic.ir import (
    DatasetIR,
    FieldIR,
    MetricIR,
    ModelIR,
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

    models: dict[str, ModelIR] = field(default_factory=dict)
    datasources: dict[str, DatasourceIR] = field(default_factory=dict)
    datasets: dict[str, DatasetIR] = field(default_factory=dict)
    fields: dict[str, FieldIR] = field(default_factory=dict)
    metrics: dict[str, MetricIR] = field(default_factory=dict)
    relationships: dict[str, RelationshipIR] = field(default_factory=dict)


#: Maps semantic_id to the original callable (dataset/field/metric body fn).
Sidecar = dict[str, Callable[..., Any]]


def _normalized_time_format(value: str | None) -> str | None:
    """Normalize time format labels for validation comparisons."""
    if value is None:
        return None
    stripped = value.strip()
    if stripped.startswith("%"):
        return stripped
    return stripped.lower().replace("_", "").replace("-", "").replace(" ", "")


def _requires_required_prefix(field_ir: FieldIR) -> bool:
    """Return True for hour-only string/integer time fields."""
    if not field_ir.is_time_field or field_ir.granularity != "hour":
        return False
    if field_ir.data_type not in {"string", "integer"}:
        return False
    return _normalized_time_format(field_ir.format) in {"h", "hh", "int"}


def _resolve_required_prefix_field(
    registry: Registry,
    *,
    field_ir: FieldIR,
) -> FieldIR | None:
    if field_ir.required_prefix is None:
        return None
    direct = registry.fields.get(field_ir.required_prefix)
    if direct is not None:
        return direct
    matches = [
        candidate
        for candidate in registry.fields.values()
        if candidate.dataset == field_ir.dataset and candidate.name == field_ir.required_prefix
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


def _time_field_pushdown_advisory(field_ir: FieldIR, fn: Callable[..., Any] | None) -> bool:
    if not field_ir.is_time_field or field_ir.data_type not in _TEMPORAL_DATA_TYPES:
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
    ds_ir: DatasetIR,
    versioning: SnapshotVersioningIR,
) -> None:
    """Validate snapshot versioning metadata at assembly time."""
    partition_name = versioning.partition_field.rsplit(".", 1)[-1]
    if partition_name not in ds_ir.primary_key:
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_DATASET_VERSIONING,
                message=(
                    f"Snapshot dataset {ds_id!r} partition field "
                    f"{versioning.partition_field!r} must be part of primary_key."
                ),
                refs=(ds_id, versioning.partition_field),
                details={
                    "dataset": ds_id,
                    "field": "partition_field",
                    "partition_field": versioning.partition_field,
                    "primary_key": list(ds_ir.primary_key),
                },
            )
        )


def _validate_validity_versioning(
    errors: list[SemanticError],
    ds_id: str,
    ds_ir: DatasetIR,
    versioning: ValidityVersioningIR,
    registry: Registry,
) -> None:
    """Validate validity versioning metadata at assembly time."""
    # valid_from local name must be in primary_key
    valid_from_local = versioning.valid_from.rsplit(".", 1)[-1]
    if valid_from_local not in ds_ir.primary_key:
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_DATASET_VERSIONING,
                message=(
                    f"Validity dataset {ds_id!r} valid_from field "
                    f"{versioning.valid_from!r} must be part of primary_key."
                ),
                refs=(ds_id, versioning.valid_from),
                details={
                    "dataset": ds_id,
                    "field": "valid_from",
                    "reason": (
                        f"{versioning.valid_from!r} is not in primary_key {list(ds_ir.primary_key)}"
                    ),
                },
            )
        )

    # field-existence check: valid_from and valid_to must resolve to known fields in this dataset
    for label, field_id in (
        ("valid_from", versioning.valid_from),
        ("valid_to", versioning.valid_to),
    ):
        field = registry.fields.get(field_id)
        if field is None or field.dataset != ds_id:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_DATASET_VERSIONING,
                    message=(
                        f"Validity dataset {ds_id!r} {label} field "
                        f"{field_id!r} does not resolve to a known field on this dataset."
                    ),
                    refs=(ds_id, field_id),
                    details={
                        "dataset": ds_id,
                        "field": label,
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


def _non_root_aggregate_dataset(
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
    dataset_by_param = dict(zip(param_names, metric_ir.datasets, strict=False))
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        param = _aggregate_receiver_param_name(node)
        if param is None:
            continue
        dataset = dataset_by_param.get(param)
        if dataset is not None and dataset != metric_ir.root_dataset:
            return dataset
    return None


def assembly_validate(
    registry: Registry,
    sidecar: Sidecar | None = None,
) -> tuple[list[SemanticError], list[StructuredWarning]]:
    """Layer 3: assembly-time cross-object validation.

    Returns (errors, warnings).  Does not raise.
    """
    errors: list[SemanticError] = []
    warnings: list[StructuredWarning] = []

    # -- Validate datasource refs on datasets --------------------------------
    for ds_id, ds_ir in registry.datasets.items():
        if ds_ir.datasource not in registry.datasources:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_DATASET_REF,
                    message=f"Dataset {ds_id!r} references unknown "
                    f"datasource {ds_ir.datasource!r}.",
                    refs=(ds_id, ds_ir.datasource),
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

    # -- Validate dataset refs on fields ------------------------------------
    for f_id, f_ir in registry.fields.items():
        if f_ir.dataset not in registry.datasets:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_DATASET_REF,
                    message=f"Field {f_id!r} references unknown dataset {f_ir.dataset!r}.",
                    refs=(f_id, f_ir.dataset),
                )
            )

    # -- Validate dataset refs on metrics -----------------------------------
    for m_id, m_ir in registry.metrics.items():
        for ds_ref in m_ir.datasets:
            if ds_ref not in registry.datasets:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.MISSING_DATASET_REF,
                        message=f"Metric {m_id!r} references unknown dataset {ds_ref!r}.",
                        refs=(m_id, ds_ref),
                    )
                )

    # -- Validate base metric additivity and root_dataset -------------------
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
        if len(m_ir.datasets) == 0:
            continue
        if len(m_ir.datasets) == 1 and m_ir.root_dataset is None:
            continue
        if len(m_ir.datasets) > 1 and m_ir.root_dataset is None:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_METRIC_ROOT_DATASET,
                    message=f"Multi-dataset base metric {m_id!r} must declare root_dataset.",
                    refs=(m_id,),
                    details={"metric": m_id, "datasets": sorted(m_ir.datasets)},
                )
            )
            continue
        if m_ir.root_dataset is not None and m_ir.root_dataset not in m_ir.datasets:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_METRIC_ROOT_DATASET,
                    message=(
                        f"Metric {m_id!r} root_dataset {m_ir.root_dataset!r} "
                        "must be one of its datasets."
                    ),
                    refs=(m_id, m_ir.root_dataset),
                    details={
                        "metric": m_id,
                        "root_dataset": m_ir.root_dataset,
                        "datasets": sorted(m_ir.datasets),
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

    # -- Validate root-only aggregates for multi-dataset base metrics ----------
    for m_id, m_ir in registry.metrics.items():
        if m_ir.is_derived:
            continue
        if sidecar is not None and len(m_ir.datasets) > 1 and m_ir.root_dataset is not None:
            fn = sidecar.get(m_id)
            if callable(fn):
                offending_dataset = _non_root_aggregate_dataset(fn, metric_ir=m_ir)
                if offending_dataset is not None:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.NON_ROOT_METRIC_AGGREGATE,
                            message=(
                                f"Metric {m_id!r} aggregates a non-root dataset "
                                f"{offending_dataset!r}."
                            ),
                            refs=(m_id, offending_dataset),
                            details={
                                "metric": m_id,
                                "root_dataset": m_ir.root_dataset,
                                "offending_dataset": offending_dataset,
                            },
                        )
                    )

    # -- Validate metric component refs in decomposition --------------------
    for m_id, m_ir in registry.metrics.items():
        for comp_key, comp_ref in m_ir.decomposition.components.items():
            if comp_ref not in registry.metrics:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.MISSING_METRIC_REF,
                        message=f"Metric {m_id!r} decomposition component "
                        f"{comp_key!r} references unknown metric "
                        f"{comp_ref!r}.",
                        refs=(m_id, comp_ref),
                    )
                )

    # -- Validate field refs in relationships --------------------------------
    for r_id, r_ir in registry.relationships.items():
        # Field arity check: from_fields and to_fields must have same length
        if len(r_ir.from_fields) != len(r_ir.to_fields):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_FIELD_REF,
                    message=f"Relationship {r_id!r} has {len(r_ir.from_fields)} from_fields "
                    f"but {len(r_ir.to_fields)} to_fields. "
                    f"Field counts must match.",
                    refs=(r_id,),
                )
            )
        if r_ir.from_dataset not in registry.datasets:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_RELATIONSHIP_ENDPOINT,
                    message=f"Relationship {r_id!r} references unknown "
                    f"from_dataset {r_ir.from_dataset!r}.",
                    refs=(r_id, r_ir.from_dataset),
                )
            )
        if r_ir.to_dataset not in registry.datasets:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_RELATIONSHIP_ENDPOINT,
                    message=f"Relationship {r_id!r} references unknown "
                    f"to_dataset {r_ir.to_dataset!r}.",
                    refs=(r_id, r_ir.to_dataset),
                )
            )
        # Validate field refs
        for ff in r_ir.from_fields:
            if ff not in registry.fields:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.MISSING_FIELD_REF,
                        message=f"Relationship {r_id!r} references unknown from_field {ff!r}.",
                        refs=(r_id, ff),
                    )
                )
        for tf in r_ir.to_fields:
            if tf not in registry.fields:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.MISSING_FIELD_REF,
                        message=f"Relationship {r_id!r} references unknown to_field {tf!r}.",
                        refs=(r_id, tf),
                    )
                )

    # -- Validate hour-only time_field required_prefix -----------------------
    for f_id, f_ir in registry.fields.items():
        if _requires_required_prefix(f_ir) and not f_ir.required_prefix:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.HOUR_TIME_FIELD_PREFIX_MISSING,
                    message=f"Hour-only time field {f_id!r} requires a "
                    f"required_prefix pointing to a day-level time field.",
                    refs=(f_id,),
                )
            )
        if (
            f_ir.is_time_field
            and f_ir.required_prefix
            and (
                (prefix_field := _resolve_required_prefix_field(registry, field_ir=f_ir)) is None
                or not prefix_field.is_time_field
            )
        ):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_FIELD_REF,
                    message=f"Time field {f_id!r} required_prefix "
                    f"{f_ir.required_prefix!r} is not a registered time field.",
                    refs=(f_id, f_ir.required_prefix),
                )
            )

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
        if _time_field_pushdown_advisory(f_ir, None if sidecar is None else sidecar.get(f_id)):
            warnings.append(
                StructuredWarning(
                    kind=WarningKind.TIME_FIELD_PUSHDOWN_ADVISORY.value,
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
