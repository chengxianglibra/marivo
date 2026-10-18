"""Validation layers for marivo.semantic v1.1.

Three layers:
  1. decorator-time (inline in authoring)
  2. AST whitelist (base metric body scanning)
  3. assembly-time (cross-object reference validation)
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import inspect
import textwrap
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, NoReturn, cast
from zoneinfo import ZoneInfo

from ibis.common.deferred import Deferred

from marivo.datasource.ir import (
    CsvSourceIR,
    DatasourceIR,
    EntitySourceIR,
    JsonQueryParamValue,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
)
from marivo.introspection._fuzzy import did_you_mean
from marivo.refs import RefPayloadV1, SemanticKind, _create_ref
from marivo.refs import ref as ref_factory
from marivo.semantic.constraints import ASTSpec, ConstraintId, get_constraint
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticExpressionError,
    SemanticLoadError,
    StructuredWarning,
    WarningKind,
    repair,
)
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    DimensionIR,
    DomainIR,
    EntityIR,
    HourPrefixParse,
    MeasureIR,
    PeriodCalendarIR,
    RelationshipIR,
    SnapshotVersioningIR,
    SourceLocation,
    StrptimeParse,
    TargetDimensionContract,
    TargetEntityContract,
    TargetRelationshipContract,
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
    TemporalSetIR,
    TimestampParse,
    ValidityVersioningIR,
    WorkScheduleIR,
    is_time_bearing_format,
)

if TYPE_CHECKING:
    from marivo.semantic._expression_binding import CompiledExpressionSidecar

__all__ = [
    "Registry",
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
    relationships: dict[str, RelationshipIR] = field(default_factory=dict)
    period_calendars: dict[str, PeriodCalendarIR] = field(default_factory=dict)
    temporal_sets: dict[str, TemporalSetIR] = field(default_factory=dict)
    work_schedules: dict[str, WorkScheduleIR] = field(default_factory=dict)
    # Published pure declarations, keyed by their qualified full name.
    declared_measures: dict[str, Deferred] = field(default_factory=dict)
    declared_metrics: dict[str, Deferred] = field(default_factory=dict)
    _frozen: bool = field(default=False, init=False, repr=False)

    def freeze(self) -> None:
        """Freeze every registry map after successful whole-graph validation."""
        if self._frozen:
            return
        for name in (
            "domains",
            "datasources",
            "entities",
            "dimensions",
            "measures",
            "relationships",
            "period_calendars",
            "temporal_sets",
            "work_schedules",
            "declared_measures",
            "declared_metrics",
        ):
            value = dict(getattr(self, name))
            object.__setattr__(self, name, MappingProxyType(value))
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("compiled semantic registry is immutable")
        object.__setattr__(self, name, value)


def _target_error(
    *,
    ref: str,
    expected: str,
    received: str,
    action: str,
    kind: ErrorKind | str = "invalid_target_semantics",
    constraint_id: ConstraintId | None = None,
    location: SourceLocation | None = None,
) -> NoReturn:
    raise SemanticLoadError(
        kind=kind,
        message="The declaration cannot supply the typed analysis semantic contract.",
        refs=(ref,),
        location=location,
        expected=expected,
        received=received,
        hint=action,
        constraint_id=constraint_id,
        repair=repair(kind="reauthor", canonical_id="entity", action=action),
    )


def _target_columns(entity: EntityIR) -> tuple[tuple[str, str], ...]:
    source = entity.source
    if isinstance(source, TableSourceIR | CsvSourceIR | JsonSourceIR):
        names = tuple(name for name, _source in source.columns)
    elif isinstance(source, ParquetSourceIR):
        names = tuple(source.columns or ())
    else:
        names = ()
    return tuple((name, "unknown") for name in names)


def _snapshot_target_source(source: EntitySourceIR) -> EntitySourceIR:
    """Detach nested authoring sequences from the captured source declaration."""
    if isinstance(source, JsonSourceIR):
        query: list[tuple[str, JsonQueryParamValue]] = []
        for name, value in source.query_params:
            frozen_value = (
                tuple(value)
                if isinstance(value, Sequence) and not isinstance(value, str)
                else value
            )
            query.append((name, frozen_value))
        return replace(
            source,
            columns=tuple((name, path) for name, path in source.columns),
            query_params=tuple(query),
            body_params=tuple((tuple(path), parameter) for path, parameter in source.body_params),
        )
    if isinstance(source, CsvSourceIR):
        return replace(source, columns=tuple(source.columns))
    if isinstance(source, ParquetSourceIR):
        return replace(
            source, columns=tuple(source.columns) if source.columns is not None else None
        )
    return replace(source, columns=tuple(source.columns))


def normalize_target_dimension(
    registry: Registry, dimension_id: str, *, prepared_column: str | None = None
) -> TargetDimensionContract:
    """Normalize a declared column Dimension without compiling or reading its source."""
    dimension = registry.dimensions.get(dimension_id)
    if dimension is None:
        _target_error(
            ref=dimension_id,
            expected="a loaded Dimension",
            received="not loaded",
            action="Use a Dimension from the current Registry.",
        )
    entity = registry.entities.get(dimension.entity)
    source_column = prepared_column or dimension.source_column
    if entity is None or source_column is None:
        _target_error(
            ref=dimension_id,
            expected="a declared direct source column on a loaded Entity",
            received="missing declared source-column facts",
            action="Declare a direct-column Dimension on a source field, and include it in columns=... when the source is projected.",
        )
    columns = dict(_target_columns(entity))
    projected_names = set(columns)
    if prepared_column is None and projected_names and source_column not in projected_names:
        _target_error(
            ref=dimension_id,
            expected="a logical source column exposed by the projection",
            received=f"{dimension.source_column!r} is not projected",
            action="Add the Dimension source column to columns=... or omit the projection.",
        )
    dimension_ref = (
        _create_ref(SemanticKind.TIME_DIMENSION, dimension_id)
        if dimension.is_time_dimension
        else _create_ref(SemanticKind.DIMENSION, dimension_id)
    )
    parse = dimension.parse
    if isinstance(parse, DateParse) or (
        isinstance(parse, StrptimeParse) and not is_time_bearing_format(parse.format)
    ):
        logical_type = "date"
    elif dimension.is_time_dimension:
        logical_type = "timestamp"
    else:
        logical_type = "unknown"
    return TargetDimensionContract(
        ref=RefPayloadV1.from_ref(dimension_ref),
        entity_ref=RefPayloadV1.from_ref(_create_ref(SemanticKind.ENTITY, entity.semantic_id)),
        source_column=source_column,
        logical_type=logical_type,
        nullable=True,
        is_time_dimension=dimension.is_time_dimension,
        granularity=dimension.granularity,
        parse=parse,
        is_default=dimension.is_default,
        timezone=(
            parse.timezone
            if isinstance(parse, DatetimeParse | TimestampParse | StrptimeParse)
            else None
        ),
    )


def _target_time_axis(registry: Registry, entity: EntityIR, path: str) -> TargetDimensionContract:
    axis = normalize_target_dimension(registry, path)
    if axis.entity_ref.path != entity.semantic_id or not axis.is_time_dimension:
        _target_error(
            ref=entity.semantic_id,
            expected="a temporal Dimension on the same Entity",
            received="a foreign or non-temporal version axis",
            action="Reference a declared temporal Dimension owned by this Entity.",
        )
    return axis


def normalize_target_entity(registry: Registry, entity_id: str) -> TargetEntityContract:
    """Derive stable identity K and independent version-row facts for authoring and analysis."""
    entity = registry.entities.get(entity_id)
    if entity is None:
        _target_error(
            ref=entity_id,
            expected="a loaded Entity",
            received="not loaded",
            action="Use an Entity from the current Registry.",
        )
    columns = _target_columns(entity)
    key = entity.primary_key
    seen_keys: set[str] = set()
    duplicate_keys: list[str] = []
    for name in key:
        if name in seen_keys and name not in duplicate_keys:
            duplicate_keys.append(name)
        seen_keys.add(name)
    if duplicate_keys:
        _target_error(
            ref=entity_id,
            expected="each identity key listed once in primary_key",
            received=f"duplicate identity keys {tuple(duplicate_keys)!r}",
            action=f"Remove {tuple(duplicate_keys)!r} from repeated positions in primary_key.",
            kind=ErrorKind.DUPLICATE_IDENTITY_KEY,
            constraint_id=ConstraintId.ENTITY_IDENTITY_KEY_UNIQUE,
            location=entity.location,
        )
    projected_names = set(dict(columns))
    if isinstance(entity.source, ParquetSourceIR) and entity.source.columns is not None:
        projected_names = set(entity.source.columns)
    missing_columns = tuple(name for name in key if projected_names and name not in projected_names)
    if missing_columns:
        _target_error(
            ref=entity_id,
            expected="every identity key to be exposed by the source projection",
            received=f"identity columns absent from projection {missing_columns!r}",
            action="Add these identity columns to columns=... or omit the projection.",
            kind=ErrorKind.MISSING_IDENTITY_KEY_COLUMN,
            constraint_id=ConstraintId.ENTITY_IDENTITY_KEY_PROJECTED,
            location=entity.location,
        )
    signature = tuple((name, "unknown") for name in key)
    version: TargetSnapshotVersion | TargetValidityVersion | None = None
    row_key = key
    authored = entity.versioning
    if isinstance(authored, SnapshotVersioningIR):
        axis = _target_time_axis(registry, entity, authored.partition_field)
        if axis.source_column in key:
            _target_error(
                ref=entity_id,
                expected="stable identity K separate from snapshot coordinate",
                received=(
                    f"snapshot coordinate {axis.source_column!r} included in primary_key={key!r}"
                ),
                action=f"Remove {axis.source_column!r} from primary_key; keep it in versioning.",
                kind=ErrorKind.IDENTITY_VERSION_OVERLAP,
                constraint_id=ConstraintId.ENTITY_VERSION_KEY_SEPARATE,
                location=entity.location,
            )
        version = TargetSnapshotVersion(
            axis.ref,
            axis.source_column,
            axis.logical_type,
            authored.timezone or axis.timezone,
            authored.format,
        )
        row_key = (*key, axis.source_column) if key else ()
    elif isinstance(authored, ValidityVersioningIR):
        start = _target_time_axis(registry, entity, authored.valid_from)
        end = _target_time_axis(registry, entity, authored.valid_to)
        if start.source_column == end.source_column:
            _target_error(
                ref=entity_id,
                expected="distinct validity bounds separate from K",
                received=f"validity bounds share source column {start.source_column!r}",
                action="Declare two distinct validity axes.",
            )
        overlapping = tuple(
            column for column in (start.source_column, end.source_column) if column in key
        )
        if overlapping:
            _target_error(
                ref=entity_id,
                expected="stable identity K separate from validity coordinates",
                received=f"validity coordinates {overlapping!r} included in primary_key={key!r}",
                action=f"Remove {overlapping!r} from primary_key; keep them in versioning.",
                kind=ErrorKind.IDENTITY_VERSION_OVERLAP,
                constraint_id=ConstraintId.ENTITY_VERSION_KEY_SEPARATE,
                location=entity.location,
            )
        version = TargetValidityVersion(
            start.ref,
            end.ref,
            start.source_column,
            end.source_column,
            authored.interval,
            authored.open_end,
            authored.timezone or start.timezone,
        )
        row_key = (*key, start.source_column) if key else ()
    datasource = registry.datasources.get(entity.datasource)
    if datasource is None:
        _target_error(
            ref=entity_id,
            expected="a loaded declared datasource",
            received="datasource not loaded",
            action="Load the Entity's declared datasource before constructing its contract.",
        )
    from marivo.semantic._physical_identity import entity_dependency_fingerprint

    dependency_fingerprint = entity_dependency_fingerprint(registry, entity_id)
    credential_slots = tuple(sorted(datasource.env_refs))
    return TargetEntityContract(
        ref=RefPayloadV1.from_ref(_create_ref(SemanticKind.ENTITY, entity_id)),
        datasource_ref=RefPayloadV1.from_ref(
            _create_ref(SemanticKind.DATASOURCE, entity.datasource)
        ),
        dependency_fingerprint=dependency_fingerprint,
        source=_snapshot_target_source(entity.source),
        primary_key=key,
        identity_signature=signature,
        version_row_key=row_key,
        columns=columns,
        version=version,
        credential_slots=credential_slots,
    )


def normalize_target_relationship(
    registry: Registry, relationship_id: str
) -> TargetRelationshipContract:
    """Resolve directed keys and structural multiplicity without source I/O."""
    relationship = registry.relationships[relationship_id]
    left_entity = registry.entities.get(relationship.from_entity)
    right_entity = registry.entities.get(relationship.to_entity)

    def invalid(expected: str, received: str, action: str) -> NoReturn:
        raise SemanticLoadError(
            kind=ErrorKind.INVALID_RELATIONSHIP_MAPPING,
            message=f"Relationship {relationship_id!r} has an invalid directed mapping.",
            refs=(relationship_id,),
            location=relationship.location,
            expected=expected,
            received=received,
            hint=action,
            constraint_id=ConstraintId.RELATIONSHIP_MAPPING,
            repair=repair(kind="reauthor", canonical_id="relationship", action=action),
        )

    if left_entity is None or right_entity is None:
        invalid(
            "two loaded Entity endpoints",
            f"{relationship.from_entity!r} -> {relationship.to_entity!r}",
            "Declare both endpoint Entities before loading the Relationship.",
        )
    if not relationship.keys:
        invalid("one or more join key pairs", "empty keys", "Add ms.join_on(...) pairs.")
    columns: list[tuple[str, str]] = []
    for pair in relationship.keys:
        left = registry.dimensions.get(pair.from_key)
        right = registry.dimensions.get(pair.to_key)
        if (
            left is None
            or right is None
            or left.entity != relationship.from_entity
            or right.entity != relationship.to_entity
            or left.source_column is None
            or right.source_column is None
        ):
            invalid(
                "direct-column Dimensions on the exact from/to Entity endpoints",
                f"{pair.from_key!r} -> {pair.to_key!r}",
                "Bind each join key to a direct-column Dimension on its declared endpoint.",
            )
        columns.append((left.source_column, right.source_column))
    from_columns = tuple(left for left, _right in columns)
    to_columns = tuple(right for _left, right in columns)
    if len(set(from_columns)) != len(columns) or len(set(to_columns)) != len(columns):
        invalid(
            "each endpoint key column used once",
            repr(columns),
            "Remove repeated source or target key columns from the relationship keys.",
        )
    from_covers_key = bool(left_entity.primary_key) and set(left_entity.primary_key).issubset(
        from_columns
    )
    to_covers_key = bool(right_entity.primary_key) and set(right_entity.primary_key).issubset(
        to_columns
    )
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]
    if from_covers_key and to_covers_key:
        cardinality = "one_to_one"
    elif from_covers_key:
        cardinality = "one_to_many"
    elif to_covers_key:
        cardinality = "many_to_one"
    else:
        cardinality = "many_to_many"
    return TargetRelationshipContract(
        ref=RefPayloadV1.from_ref(_create_ref(SemanticKind.RELATIONSHIP, relationship_id)),
        from_entity_ref=RefPayloadV1.from_ref(
            _create_ref(SemanticKind.ENTITY, relationship.from_entity)
        ),
        to_entity_ref=RefPayloadV1.from_ref(
            _create_ref(SemanticKind.ENTITY, relationship.to_entity)
        ),
        role=relationship.name,
        keys=tuple(columns),
        cardinality=cardinality,
        from_version_resolution_required=left_entity.versioning is not None,
        to_version_resolution_required=right_entity.versioning is not None,
    )


def normalize_target_version_selection(
    entity: TargetEntityContract,
    *,
    boundary: datetime,
    interpretation: Literal["instant", "before_endpoint"],
) -> TargetSnapshotSelection | TargetValiditySelection:
    """Resolve exact period/comparison facts without observing available versions."""
    version = entity.version
    if version is None or interpretation not in {"instant", "before_endpoint"}:
        _target_error(
            ref=entity.ref.path,
            expected="versioning and an exact boundary interpretation",
            received="unversioned Entity or unsupported interpretation",
            action="Supply a versioned Entity and an instant or before-endpoint boundary.",
        )
    zone = ZoneInfo(version.timezone or "UTC")
    localized = (
        boundary.replace(tzinfo=zone) if boundary.tzinfo is None else boundary.astimezone(zone)
    )
    if isinstance(version, TargetSnapshotVersion):
        period = localized.date()
        if (
            interpretation == "before_endpoint"
            and localized.timetz().replace(tzinfo=None) == time()
        ):
            period -= timedelta(days=1)
        return TargetSnapshotSelection(version.coordinate_ref, period.isoformat(), interpretation)
    return TargetValiditySelection(
        version.valid_from_ref,
        version.valid_to_ref,
        localized.isoformat(),
        "lt" if interpretation == "before_endpoint" else "le",
        "ge"
        if interpretation == "before_endpoint" or version.interval == "closed_closed"
        else "gt",
        version.open_end,
        interpretation,
    )


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


def _temporal_set_encoding(field: DimensionIR | None) -> Literal["date", "timestamp"] | None:
    """Return the closed civil-date/timestamp encoding for one time field.

    An omitted parse is resolved from the physical datasource at runtime, so
    the assembly validator leaves that case open.  ``strptime`` formats carry
    the same distinction explicitly; hour-prefix parsing is intentionally
    left unknown because it needs a separate date-bearing prefix field.
    """
    if field is None or not field.is_time_dimension:
        return None
    parse = field.parse
    if isinstance(parse, DateParse):
        return "date"
    if isinstance(parse, (DatetimeParse, TimestampParse)):
        return "timestamp"
    if isinstance(parse, StrptimeParse):
        return "timestamp" if is_time_bearing_format(parse.format) else "date"
    return None


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


def _return_expr(
    fn: Callable[..., Any],
    function: ast.FunctionDef | None = None,
) -> ast.AST | None:
    if function is None:
        try:
            source = textwrap.dedent(inspect.getsource(fn))
            tree = ast.parse(source)
        except (OSError, TypeError, IndentationError, SyntaxError):
            return None
        function = next(
            (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)), None
        )
    if function is None:
        return None
    locals_by_name = {
        stmt.targets[0].id: stmt.value
        for stmt in function.body
        if isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    }
    value = next((stmt.value for stmt in function.body if isinstance(stmt, ast.Return)), None)
    seen: set[str] = set()
    while isinstance(value, ast.Name) and value.id in locals_by_name and value.id not in seen:
        seen.add(value.id)
        value = locals_by_name[value.id]
    return value


def _time_dimension_pushdown_advisory(
    field_ir: DimensionIR,
    fn: Callable[..., Any] | None,
    function: ast.FunctionDef | None = None,
) -> bool:
    if not field_ir.is_time_dimension:
        return False
    parse = field_ir.parse
    _temporal_parse_kinds = (DateParse, DatetimeParse, TimestampParse)
    if not isinstance(parse, _temporal_parse_kinds):
        return False
        return False
    if fn is None:
        return False
    expr = _return_expr(fn, function)
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
    field_ir: DimensionIR,
    fn: Callable[..., Any] | None,
    function: ast.FunctionDef | None = None,
) -> str | None:
    """Return a cast target that conflicts with the Dimension's declared parse result."""
    if not field_ir.is_time_dimension:
        return None
    parse = field_ir.parse
    _temporal_parse_kinds = (DateParse, DatetimeParse, TimestampParse)
    if not isinstance(parse, _temporal_parse_kinds):
        return None
    if fn is None:
        return None
    expr = _return_expr(fn, function)
    if expr is None:
        return None
    inferred = _infer_terminal_cast(expr)
    if inferred is None:
        return None
    compatible = _CAST_TARGET_TO_DECLARED.get(inferred)
    if compatible is None:
        return None
    # The parse declaration determines the semantic expression's expected kind.
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
_AST_TRY_STAR = cast("type[ast.stmt]", getattr(ast, "TryStar", ast.Try))

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
        _AST_TRY_STAR,
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

    def __init__(
        self,
        fn_name: str,
        *,
        body_label: str = "Metric body",
        symbols: frozenset[str] | None = None,
        pure_declaration: bool = False,
    ) -> None:
        self.fn_name = fn_name
        self.body_label = body_label
        self._symbols = symbols
        self._pure_declaration = pure_declaration
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
        if self._pure_declaration:
            self.errors.append(
                SemanticExpressionError(
                    kind=ErrorKind.INVALID_COMPONENT_BODY,
                    message=message,
                    refs=(self.fn_name,),
                    expected="a zero-argument body with fresh locals and one final expression return",
                    received=message,
                    action="Remove control flow and mutation; return the constructed expression directly.",
                )
            )
            return
        self.errors.append(
            SemanticLoadError(
                kind=kind.value,
                message=message,
                refs=(self.fn_name,),
                constraint_id=constraint_id,
            )
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self._pure_declaration and (
            node.args.posonlyargs
            or node.args.args
            or node.args.kwonlyargs
            or node.args.vararg is not None
            or node.args.kwarg is not None
        ):
            self._local_error(
                node,
                "a zero-argument declaration over already constructed fields",
                ast.unparse(node.args),
            )
        # Extract parameter names and build parent map for context-sensitive checks.
        self._param_names = {arg.arg for arg in (*node.args.posonlyargs, *node.args.args)}
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
                f"{self.body_label} of {self.fn_name!r} must contain exactly one "
                f"return statement, found none.",
                constraint_id=ConstraintId.AST_SINGLE_RETURN,
            )
        elif len(all_returns) > 1:
            self._add_error(
                ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN,
                f"{self.body_label} of {self.fn_name!r} must contain exactly one "
                f"return statement, found {len(all_returns)}.",
                constraint_id=ConstraintId.AST_SINGLE_RETURN,
            )

        # Check for forbidden statement types anywhere in the body
        leading_docstring = node.body[0] if node.body and _is_docstring_stmt(node.body[0]) else None
        for child in ast.walk(node):
            if child is node:
                continue
            # Skip expression nodes — we only check statement nodes
            if not isinstance(child, ast.stmt):
                continue
            if child is leading_docstring:
                continue
            # Allow the single Return; every other statement violates the
            # expression-body contract.
            if isinstance(child, ast.Return):
                continue
            if self._symbols is not None and isinstance(child, ast.Assign) and child in node.body:
                continue
            for forbidden_type in (*_FORBIDDEN_STMT_TYPES, ast.Expr):
                if isinstance(child, forbidden_type):
                    # Every statement that reaches this loop is a forbidden
                    # statement: returns are skipped above and wrong return
                    # counts are handled separately (AST_SINGLE_RETURN). Classify
                    # them uniformly so the hint can route the author to the
                    # body-free constructors and ibis conditionals.
                    self._add_error(
                        ErrorKind.INVALID_COMPONENT_BODY,
                        f"{self.body_label} of {self.fn_name!r} contains a forbidden "
                        f"{type(child).__name__} statement.",
                        constraint_id=ConstraintId.AST_FORBIDDEN_STATEMENT,
                    )
                    break

        if self._symbols is not None:
            statements = [stmt for stmt in node.body if stmt is not leading_docstring]
            if statements and not isinstance(statements[-1], ast.Return):
                self._local_error(
                    statements[-1], "a final return expression", "statement after return"
                )
            defined = set(self._param_names) | set(self._symbols)
            for stmt in statements:
                if isinstance(stmt, ast.Assign):
                    if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                        self._local_error(
                            stmt, "one fresh local name", "non-name or multiple targets"
                        )
                        continue
                    name = stmt.targets[0].id
                    if name in defined:
                        self._local_error(
                            stmt, "one fresh local name", f"reassignment or shadowing of {name!r}"
                        )
                    self._validate_local_expression(stmt.value, defined)
                    defined.add(name)
                elif isinstance(stmt, ast.Return):
                    if stmt.value is None:
                        self._local_error(
                            stmt,
                            "a final stage-compatible return expression",
                            "return without a value",
                        )
                    else:
                        self._validate_local_expression(stmt.value, defined)

        # Walk only the function body for deeper AST checks. Decorator calls
        # are normal Python and are not part of the captured expression DSL.
        for stmt in node.body:
            self.visit(stmt)

    def _local_error(self, node: ast.AST, expected: str, received: str) -> None:
        if self._pure_declaration:
            self.errors.append(
                SemanticExpressionError(
                    kind=ErrorKind.INVALID_COMPONENT_BODY,
                    message=f"{self.body_label} of {self.fn_name!r} has an invalid local expression.",
                    refs=(self.fn_name,),
                    expected=expected,
                    received=received,
                    action="Use fresh local names in definition order and finish with one expression return.",
                    location_label=f"line {getattr(node, 'lineno', 0)}, column {getattr(node, 'col_offset', 0)}",
                )
            )
            return
        self.errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_COMPONENT_BODY,
                message=f"{self.body_label} of {self.fn_name!r} has an invalid local expression at "
                f"line {getattr(node, 'lineno', 0)}, column {getattr(node, 'col_offset', 0)}.",
                refs=(self.fn_name,),
                expected=expected,
                received=received,
                hint="Use fresh local names in definition order and finish with one Ibis return expression.",
            )
        )

    def _validate_local_expression(self, value: ast.expr, defined: set[str]) -> None:
        if self._pure_declaration:
            # Typed expression binding validates the closed expression grammar and receivers.
            return
        for child in ast.walk(value):
            if (
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Load)
                and child.id not in defined
            ):
                self._local_error(
                    child, "a parameter, existing symbol, or previously defined local", child.id
                )
            if isinstance(
                child,
                (
                    ast.NamedExpr,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                    ast.Yield,
                    ast.YieldFrom,
                    ast.Await,
                ),
            ):
                self._local_error(
                    child,
                    "an expression without nested bindings or control flow",
                    type(child).__name__,
                )
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id not in (self._symbols or ())
            ):
                self._local_error(
                    child,
                    "an existing expression callable",
                    f"local callable {child.func.id!r}",
                )
        if (
            isinstance(value, ast.Name)
            and value.id in (self._symbols or frozenset()) | self._param_names
        ):
            self._local_error(value, "an Ibis value expression", f"bare symbol {value.id!r}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._pure_declaration:
            return
        # Check for .sql / .raw_sql escape hatches
        if node.attr in _SQL_ESCAPE_ATTRS:
            self._add_error(
                ErrorKind.SQL_ESCAPE_HATCH,
                f"{self.body_label} of {self.fn_name!r} uses .{node.attr}(), "
                f"which is not allowed. Use an Ibis expression body instead.",
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
                    f"{self.body_label} of {self.fn_name!r} accesses .{node.attr} on the "
                    f"entity table parameter '{node.value.id}', which shadows an ibis "
                    f"Table method/property. Use bracket notation instead: "
                    f'{node.value.id}["{node.attr}"].',
                    constraint_id=ConstraintId.AST_IBIS_ATTR_SHADOW,
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._pure_declaration:
            return
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "ms"
            and func.attr == "component"
        ):
            self._add_error(
                ErrorKind.INVALID_COMPONENT_BODY,
                f"{self.body_label} of {self.fn_name!r} calls ms.component(), "
                "which is no longer supported. Use completed Metric arithmetic "
                "over constructed semantic inputs.",
                constraint_id=ConstraintId.METRIC_COMPONENT_SCOPE,
            )
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._add_error(
            ErrorKind.INVALID_COMPONENT_BODY,
            f"{self.body_label} of {self.fn_name!r} contains a lambda expression, which is not allowed.",
            constraint_id=ConstraintId.AST_FORBIDDEN_STATEMENT,
        )
        # Don't recurse into lambda body


def _is_docstring_stmt(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def validate_declaration_body_ast(function: ast.FunctionDef, *, symbols: frozenset[str]) -> None:
    """Apply shared statement/local-binding rules to a pure expression declaration."""
    validator = _BaseMetricASTValidator(
        function.name,
        body_label="Expression declaration",
        symbols=symbols,
        pure_declaration=True,
    )
    validator.visit(function)
    if validator.errors:
        raise validator.errors[0]


def _body_hash_without_docstring(func_node: ast.FunctionDef) -> str:
    body = list(func_node.body)
    if body and _is_docstring_stmt(body[0]):
        body = body[1:]
    normalized = ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


_BODY_KIND_LABELS = {
    "dimension": "Dimension body",
    "time_dimension": "Time dimension body",
    "measure": "Measure body",
    "metric": "Metric body",
}


def validate_metric_body_ast(
    fn: Callable[..., Any],
    mode: Literal["base"],
    *,
    body_kind: Literal["dimension", "time_dimension", "measure", "metric"] = "metric",
    _function: ast.FunctionDef | None = None,
) -> str:
    """Layer 2: AST whitelist validation for base metric bodies.

    Returns the normalized body AST hash for the compiled expression sidecar.

    Raises SemanticLoadError on validation failures.
    """
    if mode != "base":
        raise ValueError(f"unsupported metric body AST validation mode {mode!r}")
    body_label = _BODY_KIND_LABELS[body_kind]

    if _function is not None:
        func_node = _function
        body_hash = _body_hash_without_docstring(func_node)
    else:
        # Compute body AST hash
        try:
            source = inspect.getsource(fn)
            source = textwrap.dedent(source)
            tree = ast.parse(source)
            # Find the function definition node
            func_node = None
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
                body_hash = _body_hash_without_docstring(func_node)
        except (OSError, TypeError, IndentationError):
            body_hash = hashlib.sha256(b"<unavailable>").hexdigest()[:16]
            return body_hash

    if func_node is None:
        return body_hash

    symbols: frozenset[str] | None = None
    if any(isinstance(stmt, ast.Assign) for stmt in func_node.body):
        closure = inspect.getclosurevars(fn)
        symbols = frozenset((*fn.__globals__, *closure.nonlocals, *vars(builtins)))
    base_validator = _BaseMetricASTValidator(fn.__name__, body_label=body_label, symbols=symbols)
    base_validator.visit(func_node)
    if base_validator.errors:
        raise base_validator.errors[0]

    return body_hash


def validate_entity_table_body_ast(fn: Callable[..., Any]) -> str:
    """Layer 2: AST validation for one Entity Table-expression body.

    The decorated function must take exactly one positional parameter and
    contain one optional docstring plus one ``return`` of an Ibis expression
    built from that parameter, supported Ibis expression symbols, and inline
    literals. Assignments, helper/nested functions, lambdas, control flow,
    SQL/execution escape hatches, and ``ms.bind`` are rejected. Returns the
    body AST hash for the compiled-expression sidecar.

    Raises SemanticLoadError on validation failures.
    """
    try:
        source = textwrap.dedent(inspect.getsource(fn))
        tree = ast.parse(source)
    except (OSError, TypeError, IndentationError, SyntaxError) as exc:
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=f"Entity body {fn.__name__!r} source could not be inspected.",
            refs=(fn.__name__,),
            expected="inspectable Python source for the decorated function",
            received=type(exc).__name__,
        ) from exc
    func_node = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == fn.__name__
        ),
        None,
    )
    if func_node is None:
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=f"Entity body {fn.__name__!r} has no function definition.",
            refs=(fn.__name__,),
            expected="one function with one return",
            received="missing function",
        )

    # One positional parameter, no defaults, no variadic/keyword forms.
    args = func_node.args
    parameter_count = len(args.posonlyargs) + len(args.args)
    shape_errors: list[str] = []
    if parameter_count != 1:
        shape_errors.append(f"found {parameter_count} positional parameters")
    if args.vararg is not None or args.kwarg is not None:
        shape_errors.append("variadic parameters are not allowed")
    if args.kwonlyargs:
        shape_errors.append("keyword-only parameters are not allowed")
    if any(args.defaults) or any(args.kw_defaults):
        shape_errors.append("parameter defaults are not allowed")
    if shape_errors:
        raise SemanticLoadError(
            kind=ErrorKind.COMPILE_ERROR,
            message=(
                f"Entity body {fn.__name__!r} must take exactly one positional "
                f"Table parameter: {'; '.join(shape_errors)}."
            ),
            refs=(fn.__name__,),
            expected="def <name>(raw): return <one Ibis Table expression>",
            received="; ".join(shape_errors),
        )

    base_validator = _BaseMetricASTValidator(fn.__name__, body_label="Entity body")
    base_validator.visit(func_node)
    if base_validator.errors:
        raise base_validator.errors[0]

    # Entity-specific expression nodes: assignment expressions (walrus) bind
    # local names and comprehensions iterate or rebind inside the expression,
    # so both violate the single-expression contract even though they are not
    # statements the shared validator checks.
    for node in ast.walk(func_node):
        if isinstance(node, ast.NamedExpr):
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_COMPONENT_BODY,
                message=(
                    f"Entity body {fn.__name__!r} contains an assignment expression "
                    f"({ast.unparse(node)}), which is not allowed. Inline the value or "
                    "restructure as one expression."
                ),
                refs=(fn.__name__,),
                expected="one return expression without name binding",
                received=ast.unparse(node),
                constraint_id=ConstraintId.AST_FORBIDDEN_STATEMENT,
            )
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            raise SemanticLoadError(
                kind=ErrorKind.INVALID_COMPONENT_BODY,
                message=(
                    f"Entity body {fn.__name__!r} contains a {type(node).__name__}, which "
                    "is not allowed. Build the expression with Ibis methods over the "
                    "injected parameter instead of iterating Python collections."
                ),
                refs=(fn.__name__,),
                expected="one return expression without iteration",
                received=ast.unparse(node),
                constraint_id=ConstraintId.AST_FORBIDDEN_STATEMENT,
            )
    return _body_hash_without_docstring(func_node)


# ---------------------------------------------------------------------------
# Layer 3: assembly-time cross-object validation
# ---------------------------------------------------------------------------


_AGGREGATE_METHODS = {"sum", "mean", "avg", "count", "nunique", "max", "min"}


def _validate_entity_versioning(
    errors: list[SemanticError], entity_id: str, registry: Registry
) -> None:
    """Validate stable identity and separate version coordinates with the sole normalizer."""
    try:
        normalize_target_entity(registry, entity_id)
    except SemanticError as error:
        errors.append(error)


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


def _validate_dsl_declarations(registry: Registry) -> list[SemanticError]:
    """Validate physical Measure additivity coordinates and status-time ownership."""
    from marivo.semantic._dsl_authoring import AdditiveAllV1, AdditiveOverV1

    errors: list[SemanticError] = []
    for measure in registry.measures.values():
        policy = measure.dsl_additivity
        coordinates = (
            policy.exceptions
            if isinstance(policy, AdditiveAllV1)
            else policy.coordinates
            if isinstance(policy, AdditiveOverV1)
            else ()
        )
        for coordinate in coordinates:
            dimension = registry.dimensions.get(coordinate)
            if dimension is None or dimension.entity != measure.entity:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_REF,
                        message=f"{measure.semantic_id!r} names a non-native additivity coordinate {coordinate!r}.",
                        refs=(measure.semantic_id, coordinate),
                        expected=f"Dimension on {measure.entity}",
                        received=coordinate,
                        hint="Declare a coordinate on the computation root or revise the additivity policy.",
                    )
                )
        axis = measure.status_time_dimension
        if axis is not None:
            dimension = registry.dimensions.get(axis)
            if (
                dimension is None
                or not dimension.is_time_dimension
                or dimension.entity != measure.entity
            ):
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_REF,
                        message=f"{measure.semantic_id!r} has an invalid status time dimension {axis!r}.",
                        refs=(measure.semantic_id, axis),
                        expected=f"TimeDimension on {measure.entity}",
                        received=axis,
                        hint="Bind the Measure's native status-time axis.",
                    )
                )
    return errors


def _validate_expression_bindings(
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
) -> list[SemanticError]:
    """Validate every compiled field binding against exact positional ownership."""
    errors: list[SemanticError] = []
    for owning_ref, body in sidecar.bodies.items():
        entity_ids: tuple[str, ...]
        if owning_ref.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
            dimension = registry.dimensions.get(owning_ref.path)
            entity_ids = () if dimension is None else (dimension.entity,)
        elif owning_ref.kind is SemanticKind.MEASURE:
            measure = registry.measures.get(owning_ref.path)
            entity_ids = () if measure is None else (measure.entity,)
        else:
            continue
        for binding in body.bindings:
            field_ref = binding.to_ref()
            field_body = sidecar.bodies.get(field_ref)
            field_owner = sidecar.field_owners.get(field_ref)
            if field_ref not in sidecar.catalog_refs or field_body is None or field_owner is None:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.BINDING_TARGET_MISSING,
                        message=(
                            f"Expression body {owning_ref.key!r} binds missing field "
                            f"{field_ref.key!r}."
                        ),
                        refs=(owning_ref.key, field_ref.key),
                        expected=(
                            "a loaded dimension, time_dimension, or measure with a compiled body"
                        ),
                        received="missing catalog object, owner, or expression body",
                        hint=(
                            "Declare the field in the loaded catalog and call it as "
                            "ms.bind(field_ref, entity_alias)."
                        ),
                    )
                )
                continue
            if binding.entity_position >= len(entity_ids):
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.BINDING_ENTITY_MISMATCH,
                        message=(
                            f"Expression body {owning_ref.key!r} binds field "
                            f"{field_ref.key!r} to unavailable entity position "
                            f"{binding.entity_position}."
                        ),
                        refs=(owning_ref.key, field_ref.key),
                        expected=f"an entity position below {len(entity_ids)}",
                        received=str(binding.entity_position),
                        hint="Call the field with its exact direct entity parameter.",
                    )
                )
                continue
            expected_owner = ref_factory.entity(entity_ids[binding.entity_position])
            if field_owner != expected_owner:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.BINDING_ENTITY_MISMATCH,
                        message=(
                            f"Field {field_ref.key!r} does not belong to entity position "
                            f"{binding.entity_position} in {owning_ref.key!r}."
                        ),
                        refs=(owning_ref.key, field_ref.key, expected_owner.key),
                        expected=expected_owner.key,
                        received=field_owner.key,
                        hint="Call the field with the direct parameter for its owning entity.",
                    )
                )
    return errors


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


_PROJECTED_ALIAS_DISPLAY_LIMIT = 8


def _expression_entity_ids(
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None,
) -> frozenset[str]:
    """Return entity ids that own a compiled Table expression body."""
    if sidecar is None:
        return frozenset()
    return frozenset(
        ref.path
        for ref in sidecar.bodies
        if ref.kind is SemanticKind.ENTITY and ref.path in registry.entities
    )


def _validate_projected_source_aliases(
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None,
) -> list[SemanticError]:
    """Validate direct semantic column references against projected aliases.

    Only direct Entity declarations carry the projected-alias contract: their
    output schema is the Source schema. Expression Entities own a transformed
    output schema, so their downstream references are validated against the
    body output when actual physical metadata is bound instead.
    """
    expression_entities = _expression_entity_ids(registry, sidecar)
    missing_by_entity: dict[str, list[dict[str, object]]] = {}

    def record(
        *, object_id: str, entity: EntityIR, field: str, column: str, location: SourceLocation
    ) -> None:
        source = entity.source
        if isinstance(source, TableSourceIR | CsvSourceIR | JsonSourceIR):
            aliases = {output_name for output_name, _source in source.columns}
        elif isinstance(source, ParquetSourceIR) and source.columns is not None:
            aliases = set(source.columns)
        else:
            return
        if not aliases:
            return
        if entity.semantic_id in expression_entities:
            return
        if column in aliases:
            return
        missing_by_entity.setdefault(entity.semantic_id, []).append(
            {
                "object": object_id,
                "field": field,
                "received_column": column,
                "location": {"file": location.file, "line": location.line},
            }
        )

    for primary_entity in registry.entities.values():
        for column in primary_entity.primary_key:
            record(
                object_id=primary_entity.semantic_id,
                entity=primary_entity,
                field="primary_key",
                column=column,
                location=primary_entity.location,
            )
    for dimension in registry.dimensions.values():
        entity = registry.entities.get(dimension.entity)
        if entity is None or dimension.source_column is None:
            continue
        record(
            object_id=dimension.semantic_id,
            entity=entity,
            field="column",
            column=dimension.source_column,
            location=dimension.location,
        )
    if sidecar is not None:
        for measure in registry.measures.values():
            entity = registry.entities.get(measure.entity)
            body = sidecar.bodies.get(ref_factory.measure(measure.semantic_id))
            if entity is None or body is None or body.source_column is None:
                continue
            record(
                object_id=measure.semantic_id,
                entity=entity,
                field="column",
                column=body.source_column,
                location=measure.location,
            )

    errors: list[SemanticError] = []
    for entity_id, missing in missing_by_entity.items():
        entity = registry.entities[entity_id]
        source = entity.source
        if isinstance(source, TableSourceIR | CsvSourceIR | JsonSourceIR):
            aliases = tuple(output_name for output_name, _source in source.columns)
        elif isinstance(source, ParquetSourceIR) and source.columns is not None:
            aliases = source.columns
        else:
            continue
        visible_missing = missing[:_PROJECTED_ALIAS_DISPLAY_LIMIT]
        rendered_missing = ", ".join(
            f"{item['object']}.{item['field']}={item['received_column']!r}"
            for item in visible_missing
        )
        omitted_missing = len(missing) - len(visible_missing)
        if omitted_missing:
            rendered_missing += f", ... (+{omitted_missing} more)"
        refs = tuple(dict.fromkeys((entity_id, *(str(item["object"]) for item in missing))))
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_REF,
                message=(
                    f"Projected entity {entity_id!r} is missing output aliases required by "
                    f"semantic references: {rendered_missing}."
                ),
                refs=refs,
                location=entity.location,
                constraint_id=ConstraintId.REF_SHAPE,
                expected="stable output aliases declared by md.table(columns=...)",
                received=", ".join(str(item["received_column"]) for item in visible_missing),
                hint=(
                    "Add every missing logical output to the entity's columns= projection, "
                    "or change each semantic column= to an exposed alias."
                ),
                details={
                    "entity": entity_id,
                    "missing_references": missing,
                    "omitted_missing_reference_count": omitted_missing,
                    "available_output_aliases": list(aliases),
                },
            )
        )
    return errors


_OUTPUT_COLUMN_DISPLAY_LIMIT = 8


def _render_column_list(columns: Sequence[str]) -> str:
    """Render up to eight columns with an omitted-count suffix."""
    visible = columns[:_OUTPUT_COLUMN_DISPLAY_LIMIT]
    rendered = ", ".join(repr(column) for column in visible)
    omitted = len(columns) - len(visible)
    if omitted > 0:
        rendered += f", ... (+{omitted} more)"
    return rendered


def _expression_entity_output_schema_errors(
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None,
    entity: EntityIR,
    output_columns: tuple[str, ...],
) -> list[SemanticError]:
    """Check declared keys and downstream column refs against the output schema.

    Missing input columns are Entity-definition failures; missing output
    columns referenced by a downstream field are downstream-definition
    failures. Repairs name the bounded actual output columns.
    """
    errors: list[SemanticError] = []
    entity_id = entity.semantic_id
    output_set = set(output_columns)
    missing_keys = tuple(key for key in entity.primary_key if key not in output_set)
    if missing_keys:
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_REF,
                message=(
                    f"Entity {entity_id!r} output is missing declared primary_key "
                    f"component(s) {_render_column_list(missing_keys)}. The output schema "
                    "is authoritative for downstream keys and fields."
                ),
                refs=(entity_id,),
                expected=f"primary_key {_render_column_list(missing_keys)} on the Entity output",
                received=f"output columns: {_render_column_list(output_columns)}",
                hint=(
                    "Expose the declared key column(s) in the Entity body output, or "
                    "update primary_key to columns that identify the transformed grain."
                ),
                location=entity.location,
                constraint_id=ConstraintId.REF_SHAPE,
                details={
                    "entity": entity_id,
                    "missing_references": [
                        {
                            "object": entity_id,
                            "field": "primary_key",
                            "received_column": key,
                        }
                        for key in missing_keys
                    ],
                    "omitted_missing_reference_count": 0,
                    "available_output_columns": list(output_columns),
                },
            )
        )
    downstream_missing: list[dict[str, object]] = []
    for dimension in registry.dimensions.values():
        if dimension.entity != entity_id or dimension.source_column is None:
            continue
        if dimension.source_column not in output_set:
            downstream_missing.append(
                {
                    "object": dimension.semantic_id,
                    "field": "column",
                    "received_column": dimension.source_column,
                    "location": {"file": dimension.location.file, "line": dimension.location.line},
                }
            )
    if sidecar is not None:
        for measure in registry.measures.values():
            body = sidecar.bodies.get(ref_factory.measure(measure.semantic_id))
            if measure.entity != entity_id or body is None or body.source_column is None:
                continue
            if body.source_column not in output_set:
                downstream_missing.append(
                    {
                        "object": measure.semantic_id,
                        "field": "column",
                        "received_column": body.source_column,
                        "location": {
                            "file": measure.location.file,
                            "line": measure.location.line,
                        },
                    }
                )
    if downstream_missing:
        visible = downstream_missing[:_OUTPUT_COLUMN_DISPLAY_LIMIT]
        rendered = ", ".join(
            f"{item['object']}.{item['field']}={item['received_column']!r}" for item in visible
        )
        omitted = len(downstream_missing) - len(visible)
        if omitted > 0:
            rendered += f", ... (+{omitted} more)"
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_REF,
                message=(
                    f"Entity {entity_id!r} output is missing column(s) required by "
                    f"semantic references: {rendered}."
                ),
                refs=(entity_id, *(str(item["object"]) for item in downstream_missing)),
                expected="columns exposed by the Entity body output",
                received=", ".join(str(item["received_column"]) for item in visible),
                hint=(
                    "Expose every missing column in the Entity body output, or change each "
                    "semantic column= to a column the transformed output provides."
                ),
                location=entity.location,
                constraint_id=ConstraintId.REF_SHAPE,
                details={
                    "entity": entity_id,
                    "missing_references": downstream_missing,
                    "omitted_missing_reference_count": omitted,
                    "available_output_columns": list(output_columns),
                },
            )
        )
    return errors


def assembly_validate(
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None = None,
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

    errors.extend(_validate_projected_source_aliases(registry, sidecar))

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
        projected_names = {name for name, _physical in _target_columns(ds_ir)}
        missing_projected_key = bool(projected_names) and any(
            name not in projected_names for name in ds_ir.primary_key
        )
        if (
            (versioning is not None or ds_ir.primary_key)
            and ds_ir.datasource in registry.datasources
            and not missing_projected_key
        ):
            _validate_entity_versioning(errors, ds_id, registry)

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
    # -- Validate period-calendar source-field ownership --------------------
    for calendar_id, calendar in registry.period_calendars.items():
        date_field = registry.dimensions.get(calendar.date)
        if (
            date_field is None
            or not date_field.is_time_dimension
            or date_field.granularity != "day"
        ):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_REF,
                    message=(
                        f"Period calendar {calendar_id!r} date ref {calendar.date!r} must "
                        "resolve to a day-grain time dimension."
                    ),
                    refs=(calendar_id, calendar.date),
                )
            )
            continue
        if isinstance(date_field.parse, (DatetimeParse, TimestampParse, HourPrefixParse)) or (
            isinstance(date_field.parse, StrptimeParse)
            and is_time_bearing_format(date_field.parse.format)
        ):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_REF,
                    message=f"Period calendar {calendar_id!r} date axis declares timestamp meaning.",
                    refs=(calendar_id, calendar.date),
                    expected="civil-date TimeDimension at day grain",
                    received=type(date_field.parse).__name__,
                    hint="Declare a civil-date axis; native untyped axes must prove date values during complete certification.",
                    repair=repair(
                        kind="reauthor",
                        canonical_id="period_calendar",
                        action="Choose a civil-date TimeDimension and reload the calendar.",
                    ),
                )
            )
            continue
        for level, level_ref in calendar.levels:
            field = registry.dimensions.get(level_ref)
            if field is None or field.is_time_dimension or field.entity != date_field.entity:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_REF,
                        message=(
                            f"Period calendar {calendar_id!r} level {level!r} must reference "
                            "a categorical dimension on the calendar date entity."
                        ),
                        refs=(calendar_id, level_ref),
                    )
                )
        for correspondence_name, level, baseline_ref in calendar.correspondences:
            baseline = registry.dimensions.get(baseline_ref)
            if level not in {name for name, _field in calendar.levels}:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_REF,
                        message=(
                            f"Period calendar {calendar_id!r} correspondence {correspondence_name!r} "
                            f"names undeclared level {level!r}."
                        ),
                        refs=(calendar_id, baseline_ref),
                    )
                )
            elif (
                baseline is None
                or baseline.is_time_dimension
                or baseline.entity != date_field.entity
            ):
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_REF,
                        message=(
                            f"Period calendar {calendar_id!r} correspondence {correspondence_name!r} "
                            "must reference a categorical dimension on the calendar date entity."
                        ),
                        refs=(calendar_id, baseline_ref),
                    )
                )

    # -- Validate temporal-set source-field ownership -----------------------
    for set_id, temporal_set in registry.temporal_sets.items():
        occurrence = registry.dimensions.get(temporal_set.occurrence_id)
        start_field = registry.dimensions.get(temporal_set.start)
        end_field = registry.dimensions.get(temporal_set.end)
        category_field = (
            registry.dimensions.get(temporal_set.category)
            if temporal_set.category is not None
            else None
        )
        if (
            occurrence is None
            or occurrence.is_time_dimension
            or occurrence.entity != getattr(start_field, "entity", None)
            or occurrence.entity != getattr(end_field, "entity", None)
            or (temporal_set.category is not None and category_field is None)
            or (
                category_field is not None
                and (category_field.is_time_dimension or category_field.entity != occurrence.entity)
            )
        ):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_REF,
                    message=(
                        f"Temporal set {set_id!r} occurrence_id, start, end, and category "
                        "must be fields on one source entity, with occurrence_id/category categorical."
                    ),
                    refs=tuple(
                        ref
                        for ref in (
                            set_id,
                            temporal_set.occurrence_id,
                            temporal_set.start,
                            temporal_set.end,
                            temporal_set.category,
                        )
                        if isinstance(ref, str)
                    ),
                )
            )

        for label, field in (("start", start_field), ("end", end_field)):
            if field is None or not field.is_time_dimension:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_REF,
                        message=(
                            f"Temporal set {set_id!r} {label} ref must resolve to a time dimension."
                        ),
                        refs=(set_id, getattr(temporal_set, label)),
                    )
                )
        start_encoding = _temporal_set_encoding(start_field)
        end_encoding = _temporal_set_encoding(end_field)
        if (
            start_encoding is not None
            and end_encoding is not None
            and start_encoding != end_encoding
        ):
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_REF,
                    message=(
                        f"Temporal set {set_id!r} start and end time dimensions must "
                        "use the same civil-date or timestamp encoding."
                    ),
                    refs=(set_id, temporal_set.start, temporal_set.end),
                    expected="matching date/date or timestamp/timestamp time encodings",
                    received=f"start={start_encoding}, end={end_encoding}",
                )
            )

    # -- Validate work-schedule source-field ownership ----------------------
    for schedule_id, schedule in registry.work_schedules.items():
        date_field = registry.dimensions.get(schedule.date)
        working_field = registry.dimensions.get(schedule.is_working)
        valid_date = (
            date_field is not None
            and date_field.is_time_dimension
            and date_field.granularity == "day"
            and _temporal_set_encoding(date_field) in {None, "date"}
        )
        valid_working = (
            working_field is not None
            and not working_field.is_time_dimension
            and working_field.entity == getattr(date_field, "entity", None)
        )
        if not valid_date or not valid_working:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_REF,
                    message=(
                        f"Work schedule {schedule_id!r} date must be a day-grain civil-date "
                        "time dimension and is_working must be a categorical field on the same entity."
                    ),
                    refs=(schedule_id, schedule.date, schedule.is_working),
                )
            )

    # -- Validate measure entity refs -----------------------------------------
    errors.extend(_validate_measure_refs(registry))
    errors.extend(_validate_dsl_declarations(registry))

    if sidecar is not None:
        errors.extend(_validate_expression_bindings(registry, sidecar))
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

        if (
            r_ir.from_entity in registry.entities
            and r_ir.to_entity in registry.entities
            and all(
                key.from_key in registry.dimensions and key.to_key in registry.dimensions
                for key in r_ir.keys
            )
        ):
            try:
                normalize_target_relationship(registry, r_id)
            except SemanticError as error:
                errors.append(error)

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

    # -- Warnings -----------------------------------------------------------
    # String ref warnings: datasource names are intentionally strings in the
    # target API, and cross-file refs are common, so skip string-ref warnings.

    # Partition pushdown advisory warnings
    for f_id, f_ir in registry.dimensions.items():
        field_ref = (
            ref_factory.time_dimension(f_id)
            if f_ir.is_time_dimension
            else ref_factory.dimension(f_id)
        )
        body = None if sidecar is None else sidecar.bodies.get(field_ref)
        if _time_dimension_pushdown_advisory(
            f_ir,
            None if body is None else body.callable,
            None if body is None else body._function_ast,
        ):
            warnings.append(
                StructuredWarning(
                    kind=WarningKind.TIME_DIMENSION_PUSHDOWN_ADVISORY.value,
                    message=(
                        f"Time field {f_id!r} casts or parses a partition-like source column. "
                        "For an encoded time axis, declare its raw string/integer column "
                        "with ms.strptime(format) and canonical source values. Qualified "
                        "ordered formats can use bare-column window comparisons without "
                        "partition enumeration or validation queries."
                    ),
                    refs=(f_id,),
                    location=f_ir.location,
                )
            )

    # Warn when the expression casts away from its declared temporal parse result.
    for f_id, f_ir in registry.dimensions.items():
        field_ref = (
            ref_factory.time_dimension(f_id)
            if f_ir.is_time_dimension
            else ref_factory.dimension(f_id)
        )
        body = None if sidecar is None else sidecar.bodies.get(field_ref)
        inferred = _time_dimension_dtype_advisory(
            f_ir,
            None if body is None else body.callable,
            None if body is None else body._function_ast,
        )
        if inferred is not None:
            compatible = sorted(_CAST_TARGET_TO_DECLARED.get(inferred, set()))
            parse = f_ir.parse
            declared_kind: str | None = None
            if parse is None:
                continue  # deferred parse — skip dtype advisory
            elif isinstance(parse, DateParse):
                declared_kind = "date"
            elif isinstance(parse, DatetimeParse):
                declared_kind = "datetime"
            elif isinstance(parse, TimestampParse):
                declared_kind = "timestamp"
            elif isinstance(parse, StrptimeParse):
                declared_kind = "strptime"
            elif isinstance(parse, HourPrefixParse):
                declared_kind = "hour_prefix"
            warnings.append(
                StructuredWarning(
                    kind=WarningKind.TIME_DIMENSION_DTYPE_ADVISORY.value,
                    message=(
                        f"Time field {f_id!r} declares parse kind={declared_kind!r} "
                        f"but body .cast({inferred!r}) produces ibis dtype {inferred!r}. "
                        f"Compatible parse kinds: {', '.join(compatible)}. "
                        "This mismatch causes TypeError at execution."
                    ),
                    refs=(f_id,),
                    location=f_ir.location,
                )
            )

    return errors, warnings
