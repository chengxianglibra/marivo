"""Validation layers for marivo.semantic v1.1.

Three layers:
  1. decorator-time (inline in authoring)
  2. AST whitelist (metric / derived metric body scanning)
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
)
from marivo.semantic.ir import (
    DatasetIR,
    FieldIR,
    MetricIR,
    ModelIR,
    RelationshipIR,
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
_DERIVED_BODY_AST_SPEC = _ast_spec_for(ConstraintId.AST_COMPONENT_ARITHMETIC)

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
                "which is only allowed in derived metric bodies.",
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


class _DerivedMetricASTValidator(ast.NodeVisitor):
    """Walk a derived metric body AST and accumulate errors.

    Derived metrics may only contain:
    - ms.component("<literal>") calls
    - Numeric literals, None
    - Binary +, -, *, / and unary -
    - Parentheses
    """

    def __init__(self, fn_name: str) -> None:
        self.fn_name = fn_name
        self.errors: list[SemanticError] = []
        # Track Attribute nodes that are part of valid ms.component() calls
        # so visit_Attribute doesn't flag them.
        self._valid_component_attrs: set[int] = set()

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

    def _is_ms_component_attr(self, node: ast.Attribute) -> bool:
        """Check if an Attribute node represents ms.component."""
        return (
            isinstance(node.value, ast.Name) and node.value.id == "ms" and node.attr == "component"
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        body = node.body

        # Must have exactly one Return
        return_stmts = [s for s in body if isinstance(s, ast.Return)]
        if len(return_stmts) != 1:
            self._add_error(
                ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN,
                f"Derived metric body of {self.fn_name!r} must contain "
                f"exactly one return statement.",
                constraint_id=ConstraintId.AST_SINGLE_RETURN,
            )
            return

        # Check no other statement types
        for stmt in body:
            if isinstance(stmt, ast.Return):
                continue
            if isinstance(stmt, ast.Expr):
                # expression statement before return is odd, flag it
                self._add_error(
                    ErrorKind.INVALID_COMPONENT_BODY,
                    f"Derived metric body of {self.fn_name!r} contains an unexpected statement.",
                    constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
                )
            else:
                self._add_error(
                    ErrorKind.INVALID_COMPONENT_BODY,
                    f"Derived metric body of {self.fn_name!r} contains "
                    f"a forbidden {type(stmt).__name__} statement.",
                    constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
                )

        # Pre-scan: mark ms.component attribute nodes in the body as valid.
        self._mark_component_attrs(node.body)

        # Walk for expression-level checks
        for stmt in node.body:
            self.visit(stmt)

    def _mark_component_attrs(self, nodes: list[ast.stmt]) -> None:
        """Find and mark all Attribute nodes that are ms.component references."""
        for node in nodes:
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func = child.func
                    if isinstance(func, ast.Attribute) and self._is_ms_component_attr(func):
                        self._valid_component_attrs.add(id(func))

    def visit_Call(self, node: ast.Call) -> None:
        # Only ms.component("<literal>") is allowed
        func = node.func
        is_component_call = False
        if isinstance(func, ast.Attribute) and self._is_ms_component_attr(func):
            is_component_call = True
            # Validate exactly one positional string literal arg
            if len(node.args) != 1:
                self._add_error(
                    ErrorKind.INVALID_COMPONENT_BODY,
                    f"Derived metric body of {self.fn_name!r}: "
                    f"ms.component() requires exactly one string literal argument.",
                    constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
                )
            elif not (
                isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
            ):
                self._add_error(
                    ErrorKind.INVALID_COMPONENT_BODY,
                    f"Derived metric body of {self.fn_name!r}: "
                    f"ms.component() argument must be a string literal.",
                    constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
                )
            if node.keywords:
                self._add_error(
                    ErrorKind.INVALID_COMPONENT_BODY,
                    f"Derived metric body of {self.fn_name!r}: "
                    f"ms.component() does not accept keyword arguments.",
                    constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
                )
            # Don't recurse into the component call — we've validated it
            return

        if not is_component_call:
            self._add_error(
                ErrorKind.INVALID_COMPONENT_BODY,
                f"Derived metric body of {self.fn_name!r} contains a "
                f"function call that is not ms.component().",
                constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
            )
            # Still recurse to find more errors

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Allow ms.component attribute (it's part of a valid call pattern)
        if id(node) in self._valid_component_attrs:
            return
        # All other attribute access is forbidden in derived metrics
        self._add_error(
            ErrorKind.INVALID_COMPONENT_BODY,
            f"Derived metric body of {self.fn_name!r} contains attribute "
            f"access (.{node.attr}), which is not allowed.",
            constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
        )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._add_error(
            ErrorKind.INVALID_COMPONENT_BODY,
            f"Derived metric body of {self.fn_name!r} contains subscript "
            f"access, which is not allowed.",
            constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
        )

    def visit_Compare(self, node: ast.Compare) -> None:
        self._add_error(
            ErrorKind.INVALID_COMPONENT_BODY,
            f"Derived metric body of {self.fn_name!r} contains a comparison "
            f"operation, which is not allowed.",
            constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
        )

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self._add_error(
            ErrorKind.INVALID_COMPONENT_BODY,
            f"Derived metric body of {self.fn_name!r} contains a boolean "
            f"operation, which is not allowed.",
            constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
        )

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._add_error(
            ErrorKind.INVALID_COMPONENT_BODY,
            f"Derived metric body of {self.fn_name!r} contains a conditional "
            f"expression, which is not allowed.",
            constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
        )

    def visit_Constant(self, node: ast.Constant) -> None:
        # String literals (other than inside ms.component()) are forbidden.
        # But ms.component() args are handled in visit_Call and we don't
        # recurse into them, so any string Constant we see here is an error.
        if isinstance(node.value, str):
            self._add_error(
                ErrorKind.INVALID_COMPONENT_BODY,
                f"Derived metric body of {self.fn_name!r} contains a string "
                f"literal, which is not allowed (use ms.component() instead).",
                constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
            )

    def visit_BinOp(self, node: ast.BinOp) -> None:
        # Only +, -, *, / are allowed
        allowed_ops = tuple(
            getattr(ast, op_name) for op_name in _DERIVED_BODY_AST_SPEC.allowed_binops
        )
        if not isinstance(node.op, allowed_ops):
            self._add_error(
                ErrorKind.INVALID_COMPONENT_BODY,
                f"Derived metric body of {self.fn_name!r} uses "
                f"{type(node.op).__name__} operator, which is not allowed. "
                f"Only +, -, *, / are permitted.",
                constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
            )
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        # Only unary - is allowed
        allowed_ops = tuple(
            getattr(ast, op_name) for op_name in _DERIVED_BODY_AST_SPEC.allowed_unary_ops
        )
        if not isinstance(node.op, allowed_ops):
            self._add_error(
                ErrorKind.INVALID_COMPONENT_BODY,
                f"Derived metric body of {self.fn_name!r} uses "
                f"{type(node.op).__name__} operator, which is not allowed. "
                f"Only unary - is permitted.",
                constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # Bare name references (other than 'ms' which is handled via Call)
        # are forbidden in derived metrics — no dataset/field/time_field refs
        if node.id != "ms":
            self._add_error(
                ErrorKind.INVALID_COMPONENT_BODY,
                f"Derived metric body of {self.fn_name!r} references "
                f"{node.id!r}, which is not allowed. "
                f"Only ms.component() calls and arithmetic are permitted.",
                constraint_id=ConstraintId.AST_COMPONENT_ARITHMETIC,
            )


def validate_metric_body_ast(
    fn: Callable[..., Any],
    mode: Literal["base", "derived"],
) -> str:
    """Layer 2: AST whitelist validation for metric bodies.

    Returns the body AST hash for storage in MetricIR.

    Raises SemanticLoadError on validation failures.
    """
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

    # Run the appropriate validator
    if mode == "base":
        base_validator = _BaseMetricASTValidator(fn.__name__)
        base_validator.visit(func_node)
        if base_validator.errors:
            raise base_validator.errors[0]
    else:
        derived_validator = _DerivedMetricASTValidator(fn.__name__)
        derived_validator.visit(func_node)
        if derived_validator.errors:
            raise derived_validator.errors[0]

    return body_hash


# ---------------------------------------------------------------------------
# Layer 3: assembly-time cross-object validation
# ---------------------------------------------------------------------------


def assembly_validate(
    registry: Registry,
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

    # -- Warnings -----------------------------------------------------------
    # String ref warnings: datasource names are intentionally strings in the
    # target API, and cross-file refs are common, so skip string-ref warnings.

    # Unverified provenance warnings
    for m_id, m_ir in registry.metrics.items():
        prov = m_ir.provenance
        if prov.source_sql and prov.declared_status != "python_native":
            warnings.append(
                StructuredWarning(
                    kind="unverified_provenance",
                    message=f"Metric {m_id!r} has source_sql but no parity verification yet.",
                    refs=(m_id,),
                    location=None,
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
