"""Private admitted graph lowering. Expressions and checks never execute here."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from functools import reduce
from operator import and_, or_
from typing import NoReturn, TypeAlias

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.compiler.graph_plan import (
    ArtifactReadStage,
    CheckRequirement,
    GraphPlan,
    LocalMethodStage,
    RouteChoice,
    SourceInputStage,
    SourceMethodStage,
    plan,
)
from marivo.analysis.compiler.member_version import select_version, version_predicate
from marivo.analysis.core.graph import MethodNode, Node, SourceLeaf, topology
from marivo.analysis.core.model import (
    AnchorDomainPart,
    AnchorObservationPart,
    AttributionPart,
    CohortDecisionPart,
    ConditionCellsPart,
    Coordinate,
    CoordinateStatePart,
    CorrespondencePart,
    DisplayPart,
    EndpointPart,
    EntryAxesPart,
    FactInput,
    FindingPolicyPart,
    FitInputsPart,
    FitStatePart,
    FunnelAllocationPart,
    FunnelComparisonPart,
    FunnelPart,
    GridCellsPart,
    HistoryPart,
    HistoryViewPart,
    JourneyPart,
    Obligation,
    ObservedQuantity,
    OccurrencePart,
    OriginalStatePart,
    Part,
    ReferenceStatePart,
    RowStatePart,
    RunCellsPart,
    Signature,
    SubjectMapPart,
    SubjectPart,
    TableFitsPart,
    part_role,
    reject,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import (
    AnchorBind,
    AnchorObserve,
    AnchorRetention,
    AssociationFit,
    AssociationRead,
    AssociationScore,
    AttachCategory,
    AttributionDerive,
    BindProject,
    CellDerive,
    CompleteGroups,
    DeviationFit,
    DisplayRank,
    DisplayTable,
    ForecastFit,
    ForecastRead,
    FunnelAttribute,
    FunnelAxesPrepare,
    FunnelCompare,
    FunnelReduce,
    GroupObservationTarget,
    HistoryAxesPrepare,
    HistoryReplay,
    HistoryView,
    JourneyCompleted,
    JourneyDuration,
    JourneyMatch,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    ObserveWeightedMean,
    OccurrenceCombine,
    OccurrenceFilter,
    OccurrencePrepare,
    OriginalRatio,
    OriginalReduce,
    PartsTransport,
    PreparedObservation,
    ReferenceDerive,
    RetentionBySubject,
    RowState,
    TimeProduct,
    TimeRunRead,
    TimeRuns,
)
from marivo.analysis.core.time_grid import GridVersionSelection
from marivo.analysis.methods.builtin import admit
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    Qualified,
    ScalarType,
    SourceShape,
)
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.datasource.adapters import BoundSource, PhysicalRequirement
from marivo.datasource.ir import ParquetSourceIR, TableSourceIR
from marivo.semantic._expression_binding import (
    CompiledExpressionSidecar,
    evaluate_expression_body,
)
from marivo.semantic.ir import (
    DIRECT_ONLY_AGGREGATES,
    TargetDimensionContract,
    TargetSnapshotVersion,
    TargetValidityVersion,
)


def _fail(expected: str, received: str) -> NoReturn:
    reject(
        expected,
        received,
        "Bind the exact registered input layout and preserve pending checks.",
        "analysis.graph_lowering",
    )


@dataclass(frozen=True, slots=True)
class CoordinateColumn:
    coordinate: Coordinate
    column: str


@dataclass(frozen=True, slots=True)
class CellColumns:
    value: str
    tag: str
    reason: str


@dataclass(frozen=True, slots=True)
class ComponentColumn:
    component: str
    column: str


@dataclass(frozen=True, slots=True)
class PartColumns:
    part: Part
    columns: tuple[ComponentColumn, ...]


def components(part: Part) -> tuple[str, ...]:
    from marivo.analysis.core.model import InstanceRetentionPart, SubjectRetentionPart

    if isinstance(part, (InstanceRetentionPart, SubjectRetentionPart)):
        return ("retained",)
    if isinstance(part, (AnchorDomainPart, AnchorObservationPart)):
        return part.components
    if isinstance(
        part,
        (EntryAxesPart, FindingPolicyPart, FunnelPart, FunnelComparisonPart, FunnelAllocationPart),
    ):
        return ("retained",)
    if isinstance(
        part, (ConditionCellsPart, RunCellsPart, FitInputsPart, FitStatePart, TableFitsPart)
    ):
        return ("retained",)
    if isinstance(part, GridCellsPart):
        return (
            "identity",
            "ordinal",
            "original_start",
            "original_end",
            "start",
            "end",
            "partial",
            "precision",
            "coverage",
        )
    if isinstance(part, SubjectMapPart):
        return tuple(f"key_{i}" for i in range(len(part.subject.subject_key)))
    if isinstance(part, HistoryViewPart):
        return ("retained",)
    if isinstance(part, HistoryPart):
        return ("record",)
    if isinstance(part, JourneyPart):
        return ("assignment",)
    if isinstance(part, OccurrencePart):
        return part.components
    if isinstance(part, AttributionPart):
        from marivo.analysis.methods.attribution import columns

        return columns(part)
    if isinstance(part, EndpointPart):
        return (
            "value",
            "cell_tag",
            "cell_reason",
            *(
                ("state__" + c for c in part.original_state.components)
                if part.original_state
                else ()
            ),
            *(("groups",) if part.coordinate_state else ()),
            *(("complete",) if part.original_state else ()),
        )
    if isinstance(part, DisplayPart):
        return part.components
    if isinstance(part, CorrespondencePart):
        return (
            "current_present",
            "baseline_present",
            "current_error_bound",
            "baseline_error_bound",
            "result_error_bound",
            *(f"current_key_{i}" for i in range(len(part.current_key))),
            *(f"baseline_key_{i}" for i in range(len(part.baseline_key))),
        )
    if isinstance(part, ReferenceStatePart):
        return ("retained",)
    if isinstance(part, (OriginalStatePart, RowStatePart)):
        return part.components
    if isinstance(part, CoordinateStatePart):
        return ("groups",)
    if isinstance(part, CohortDecisionPart):
        return (
            "true_count",
            "unknown_count",
            "false_count",
            "accepted",
            *(("opportunity_count",) if part.opportunity_domain.kind == "journey" else ()),
        )
    if isinstance(part, SubjectPart):
        return tuple(f"key_{i}" for i in range(len(part.subject_key)))
    role = part_role(part)
    if role in ("current_endpoint", "baseline_endpoint"):
        return ("value", "cell_tag", "cell_reason")
    if role == "coverage":
        return ("complete",)
    if role == "statistical_weight":
        return ("weight",)
    if role == "pair_counts":
        return (
            "metric_key_a",
            "metric_key_b",
            "input_observation_count",
            "matched_observation_count",
            "null_pair_count",
            "complete_pair_count",
        )
    return ("reference",)


@dataclass(frozen=True, slots=True)
class RelationLayout:
    keys: tuple[CoordinateColumn, ...]
    cell: CellColumns | None
    parts: tuple[PartColumns, ...] = ()
    extras: tuple[str, ...] = ()

    @property
    def columns(self) -> tuple[str, ...]:
        values = () if self.cell is None else (self.cell.value, self.cell.tag, self.cell.reason)
        return (
            *tuple(k.column for k in self.keys),
            *self.extras,
            *values,
            *(c.column for p in self.parts for c in p.columns),
        )


@dataclass(frozen=True, slots=True)
class SourceBinding:
    """An R4-supplied R1 binding, associated with the exact live leaf identity."""

    leaf: SourceLeaf
    source: BoundSource
    layout: RelationLayout
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = ()
    expression_sidecar: CompiledExpressionSidecar | None = None


@dataclass(frozen=True, slots=True, eq=False)
class LoweredRelation:
    output: str
    node: Node
    expression: ir.Table
    layout: RelationLayout
    source_ids: tuple[str, ...]
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = ()
    part_expressions: tuple[tuple[str, ir.Table], ...] = ()
    part_source_ids: tuple[tuple[str, tuple[str, ...]], ...] = ()
    column_reasons: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...] = ()


@dataclass(frozen=True, slots=True)
class LoweredLocal:
    stage: LocalMethodStage
    input_layouts: tuple[RelationLayout, ...]
    output_layout: RelationLayout


@dataclass(frozen=True, slots=True, eq=False)
class IntegrityCheck:
    """Nonempty violations must reject before the named input is consumed."""

    stage_output: str
    expected: str
    violations: ir.Table
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, eq=False)
class SemanticCheck:
    requirement: CheckRequirement
    violations: ir.Table
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, eq=False)
class TemporalCheck:
    """Governed raw/engine temporal pairs checked against the frozen axis authority."""

    stage_output: str
    violations: ir.Table
    source_ids: tuple[str, ...]
    axis: TargetDimensionContract


LoweredStage: TypeAlias = LoweredRelation | LoweredLocal | ArtifactReadStage
LoweredCheck: TypeAlias = IntegrityCheck | SemanticCheck | TemporalCheck | CheckRequirement


@dataclass(frozen=True, slots=True)
class LoweredPlan:
    admitted: GraphPlan
    stages: tuple[LoweredStage, ...]
    checks: tuple[LoweredCheck, ...]
    source_requirement: PhysicalRequirement
    bindings: tuple[SourceBinding, ...]

    def sources_for(self, expression: ir.Table) -> tuple[SourceBinding, ...]:
        """Resolve an emitted expression's recorded leaf identities, never Ibis equality."""
        matches = tuple(
            next(
                (
                    ids
                    for role, ids in item.part_source_ids
                    if any(
                        name == role and emitted is expression
                        for name, emitted in item.part_expressions
                    )
                ),
                item.source_ids,
            )
            if isinstance(item, LoweredRelation)
            else item.source_ids
            for item in (*self.stages, *self.checks)
            if (
                isinstance(item, LoweredRelation)
                and (
                    item.expression is expression
                    or any(
                        part_expression is expression
                        for _, part_expression in item.part_expressions
                    )
                )
            )
            or (
                isinstance(item, (IntegrityCheck, SemanticCheck, TemporalCheck))
                and item.violations is expression
            )
        )
        if not matches or any(ids != matches[0] for ids in matches):
            reject(
                "an emitted expression with one recorded dependency set",
                "unknown or ambiguous expression",
                "Use an emitted relation or check expression unchanged; re-lower graph changes.",
                "analysis.graph_lowering.sources_for",
            )
        bindings = {b.leaf.identity: b for b in self.bindings}
        return tuple(bindings[identity] for identity in matches[0])

    @property
    def primary_output(self) -> str:
        return self.admitted.primary_output


def _source_ids(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Preserve ordered explicit provenance without merging equal definitions."""
    return tuple(dict.fromkeys(identity for group in groups for identity in group))


def canonical_layout(signature: Signature, *, has_value: bool) -> RelationLayout:
    return RelationLayout(
        tuple(
            CoordinateColumn(key, f"key_{i}") for i, key in enumerate(signature.domain.instance_key)
        ),
        CellColumns("value", "cell_tag", "cell_reason") if has_value else None,
        tuple(
            PartColumns(
                part,
                tuple(
                    ComponentColumn(name, f"{part_role(part)}__{name}") for name in components(part)
                ),
            )
            for part in signature.parts
        ),
    )


def coordinate_state_type(part: CoordinateStatePart) -> dt.Array:
    return dt.Array(
        dt.Struct.from_tuples(
            [
                *((name, dt.string) for name in part.columns),
                *(
                    (
                        name,
                        dt.Decimal(38, (dt.dtype(part.value_type).scale or 0) * 2)
                        if name == "weighted_numerator" and part.value_type.startswith("decimal(")
                        else dt.dtype(
                            "int64" if part.value_type.startswith("interval(") else part.value_type
                        )
                        if "count" not in name
                        else dt.int64,
                    )
                    for name in part.components
                ),
            ]
        )
    )


def _validate_layout(
    table: ir.Table, layout: RelationLayout, signature: Signature, value_type: str
) -> None:
    if tuple(k.coordinate for k in layout.keys) != signature.domain.instance_key:
        _fail("the complete ordered coordinate key", repr(layout.keys))
    if tuple(p.part for p in layout.parts) != signature.parts:
        _fail("each exact retained part and binding", repr(layout.parts))
    if not set(layout.columns) <= set(table.columns):
        _fail("existing physical columns", repr(layout.columns))
    base_columns = (
        *(key.column for key in layout.keys),
        *layout.extras,
        *(() if layout.cell is None else (layout.cell.value, layout.cell.tag, layout.cell.reason)),
    )
    if len(set(base_columns)) != len(base_columns):
        _fail("distinct physical keys, extras and Cell fields", repr(base_columns))
    used = set(base_columns)
    for part in layout.parts:
        for index, component in enumerate(part.columns):
            if component.column in used and not (
                isinstance(part.part, SubjectPart)
                and index < len(part.part.subject_key)
                and any(
                    key.coordinate == part.part.subject_key[index]
                    and key.column == component.column
                    for key in layout.keys
                )
            ):
                _fail("distinct physical part fields or exact Subject key reuse", component.column)
            used.add(component.column)
    for key in layout.keys:
        if not (table[key.column].type().is_int64() or table[key.column].type().is_string()):
            _fail("qualified int64 or string identity columns", str(table[key.column].type()))
    for part in layout.parts:
        if tuple(c.component for c in part.columns) != components(part.part):
            _fail("complete ordered part components", repr(part.columns))
        for component in part.columns:
            role = part_role(part.part)
            if isinstance(part.part, (AttributionPart, EndpointPart)):
                continue
            if isinstance(part.part, DisplayPart):
                if table[component.column].type() != dt.dtype(
                    part.part.types[part.part.components.index(component.component)]
                ):
                    _fail("exact display component type", component.column)
                continue
            if isinstance(part.part, CoordinateStatePart):
                if table[component.column].type() != coordinate_state_type(part.part):
                    _fail("the exact nested contribution coordinate state", component.column)
                continue
            if role == "subject":
                actual = table[component.column].type()
                if not (actual.is_int64() or actual.is_string()):
                    _fail("qualified int64 or string Subject identity", str(actual))
                continue
            dtype = (
                "boolean"
                if role == "coverage"
                else "string"
                if role in ("current_endpoint", "baseline_endpoint")
                and component.component in ("cell_tag", "cell_reason")
                else "string"
                if role == "pair_counts" and component.component in ("metric_key_a", "metric_key_b")
                else "float64"
                if role == "reference_proof"
                or (value_type == "float64" and component.component in ("sum", "weighted_sum"))
                else "int64"
            )
            if str(table[component.column].type()) != dtype:
                _fail(f"qualified {dtype} part component", component.column)
    if signature.quantity is not None and layout.cell is None:
        _fail("Cell columns for a quantity", "missing Cell")
    if layout.cell is not None:
        for column, dtype in (
            (layout.cell.value, value_type),
            (layout.cell.tag, "string"),
            (layout.cell.reason, "string"),
        ):
            if str(table[column].type()) != dtype:
                _fail(f"{dtype} {column}", str(table[column].type()))


def _renamed(table: ir.Table, source: RelationLayout, target: RelationLayout) -> ir.Table:
    return table.select(
        *(table[old].name(new) for old, new in zip(source.columns, target.columns, strict=True))
    )


def _key_violations(
    table: ir.Table, layout: RelationLayout, *, allow_empty: bool = False
) -> ir.Table:
    keys = tuple(k.column for k in layout.keys)
    if not keys:
        counts = table.aggregate(n=table.count())
        return counts.filter(counts.n > 1 if allow_empty else counts.n != 1)
    nulls = reduce(
        or_,
        (
            table[k.column].isnull()
            for k in layout.keys
            if not k.coordinate.field.startswith("attribution:axis:")
        ),
        ibis.literal(False),
    )
    groups = table.group_by(*keys).aggregate(n=table.count())
    duplicates = groups.filter(groups.n > 1).select(*keys)
    return table.filter(nulls).select(*keys).union(duplicates, distinct=False)


def _cell_violations(table: ir.Table, cell: CellColumns) -> ir.Table:
    tag, value, reason = table[cell.tag], table[cell.value], table[cell.reason]
    valid = tag.isin(("defined", "null", "undefined", "unknown")) & ibis.ifelse(
        tag == "defined",
        value.notnull() & reason.isnull(),
        value.isnull() & reason.notnull() & (reason != ""),
    )
    return table.filter(~valid.fill_null(False))


def _predicate(table: ir.Table, cell: CellColumns, predicate: ValuePredicate) -> ir.BooleanValue:
    value = table[cell.value]
    operations = {
        "eq": value == predicate.literal,
        "ne": value != predicate.literal,
        "lt": value < predicate.literal,
        "le": value <= predicate.literal,
        "gt": value > predicate.literal,
        "ge": value >= predicate.literal,
    }
    result: ir.BooleanValue = (
        (table[cell.tag] == "defined") & operations[predicate.operator]
    ).fill_null(False)
    return result


def _pair_violations(left: LoweredRelation, right: LoweredRelation) -> ir.Table:
    keys = tuple(k.column for k in left.layout.keys)
    if keys != tuple(k.column for k in right.layout.keys):
        _fail("matching complete keys", repr(keys))
    if not keys:
        a = left.expression.aggregate(left_count=left.expression.count()).view()
        b = right.expression.aggregate(right_count=right.expression.count()).view()
        counts = a.cross_join(b)
        return counts.filter(
            (counts.left_count != counts.right_count)
            | (counts.left_count > 1)
            | (counts.right_count > 1)
        )
    a = left.expression.select(*keys).view()
    b = right.expression.select(*keys).view()
    predicates = [a[k].identical_to(b[k]) for k in keys]
    return a.anti_join(b, predicates).union(b.anti_join(a, predicates), distinct=False)


def _operand_bound(source: LoweredRelation, table: ir.Table) -> ir.Value:
    zero = ibis.literal(0).cast("float64")
    if source.node.value_type != ScalarType("float64"):
        return zero
    attribution = next(
        (
            p
            for p in source.node.signature.parts
            if isinstance(p, AttributionPart) and p.role == "allocation"
        ),
        None,
    )
    if attribution is not None:
        return table["allocation__" + attribution.view + "_error_bound"]
    if "correspondence__result_error_bound" in table.columns:
        return table.correspondence__result_error_bound
    if "reference_proof__retained" in table.columns:
        return table.reference_proof__retained
    assert source.layout.cell is not None
    rounding = 1e-12 * (1.0 + table[source.layout.cell.value].abs())
    state = next(
        (part for part in source.node.signature.parts if isinstance(part, OriginalStatePart)), None
    )
    if "original_state__absolute_sum" in table.columns:
        error = 1e-12 * (1.0 + table.original_state__absolute_sum)
        if state is not None and state.method_version == "mean@v1":
            return (table.original_state__non_null_count > 0).ifelse(
                error / table.original_state__non_null_count + rounding, zero
            )
        return (table[source.layout.cell.tag] == "defined").ifelse(error, zero)
    if state is not None and state.method_version in ("mean@v1", "weighted_mean@v1", "ratio@v1"):
        numerator = {
            "mean@v1": "sum",
            "weighted_mean@v1": "weighted_numerator",
            "ratio@v1": "numerator_sum",
        }[state.method_version]
        if table["original_state__" + numerator].type().is_integer():
            return rounding.fill_null(zero)
        if state.method_version in ("weighted_mean@v1", "ratio@v1"):
            weighted = state.method_version == "weighted_mean@v1"
            magnitude = "absolute_weighted_numerator" if weighted else "numerator_absolute_sum"
            denominator = "weight_sum" if weighted else "denominator_sum"
            absolute_denominator = "absolute_weight_sum" if weighted else "denominator_absolute_sum"
            error_n = 1e-12 * (1.0 + table["original_state__" + magnitude])
            if weighted:
                error_n += 1e-12 * (
                    table.original_state__absolute_weighted_numerator
                    + table.original_state__non_null_pair_count
                )
            error_d = 1e-12 * (1.0 + table["original_state__" + absolute_denominator])
            bound = (error_n + table[source.layout.cell.value].abs() * error_d) / (
                table["original_state__" + denominator].abs() - error_d
            ) + rounding
            return bound.fill_null(zero)
        _fail("retained operand rounding envelope", "float mean lacks sufficient magnitude state")
    if state is not None and state.method_version == "linear@v1":
        errors = [
            1e-12 * (1.0 + table["original_state__" + name])
            for name in state.components
            if name.endswith("_absolute_sum")
        ]
        if errors:
            return (sum(errors, start=zero) + rounding).fill_null(zero)
    if "row_state__error_bound" in table.columns:
        bound = table.row_state__error_bound
        quantity = source.node.signature.quantity
        if quantity is not None and quantity.method_version == "row.mean@v1":
            bound = bound / table.row_state__count + rounding
        return bound.fill_null(zero)
    if "row_state__sum" in table.columns and table.row_state__sum.type().is_integer():
        return rounding.fill_null(zero)
    if state is not None and state.method_version in ("sum@v1", "sum_zero@v1", "linear@v1"):
        _fail(
            "retained operand rounding envelope", "float aggregate lacks sufficient magnitude state"
        )
    if "row_state__sum" in table.columns:
        _fail("retained operand rounding envelope", "float row statistic lacks error state")
    return zero


def _mapped_period_input(source: LoweredRelation, params: CellDerive) -> LoweredRelation:
    if params.time_index is None:
        return source
    key = source.layout.keys[params.time_index].column
    value = source.expression[key]
    translated = ibis.cases(
        *((value == right, left) for left, right in params.bucket_mapping),
        else_=ibis.null().cast("string"),
    )
    return replace(source, expression=source.expression.mutate(**{key: translated}))


def _difference(
    stage: SourceMethodStage,
    inputs: tuple[LoweredRelation, LoweredRelation],
    checks: list[LoweredCheck],
) -> tuple[ir.Table, RelationLayout]:
    left, right = inputs
    params = stage.node.parameters
    assert isinstance(params, CellDerive)
    a, b = left.layout.cell, right.layout.cell
    keys = tuple(item.column for item in left.layout.keys)
    if a is None or b is None or keys != tuple(item.column for item in right.layout.keys):
        _fail("two Cell-valued endpoints with the same complete keys", repr(keys))
    if params.pairing == "exact":
        checks.append(
            IntegrityCheck(
                stage.output,
                "equal complete endpoint key sets",
                _pair_violations(left, _mapped_period_input(right, params)),
                _source_ids(left.source_ids, right.source_ids),
            )
        )
    if params.pairing == "metric_empty":
        for source in inputs:
            if "coverage__complete" not in source.expression.columns:
                _fail("retained original coverage for metric_empty", "missing coverage")
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "complete original observation coverage",
                    source.expression.filter(~source.expression.coverage__complete),
                    source.source_ids,
                )
            )
    if params.time_index is not None:
        for index, source in enumerate(inputs):
            time_key = source.layout.keys[params.time_index].column
            non_time = tuple(
                item.column for i, item in enumerate(source.layout.keys) if i != params.time_index
            )
            expected = tuple(pair[index] for pair in params.bucket_mapping)
            counts = (
                source.expression.group_by(*non_time) if non_time else source.expression
            ).aggregate(
                bucket_count=source.expression[time_key].nunique(),
                row_count=source.expression.count(),
            )
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "complete original period buckets without renumbering",
                    counts.filter((counts.row_count > 0) & (counts.bucket_count != len(expected))),
                    source.source_ids,
                )
            )
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "original bucket coordinates",
                    source.expression.filter(~source.expression[time_key].isin(expected)),
                    source.source_ids,
                )
            )
    lhs, rhs = left.expression.view(), right.expression.view()
    current = lhs.select(
        **{f"current_key_{i}": lhs[key] for i, key in enumerate(keys)},
        current_value=lhs[a.value],
        current_tag=lhs[a.tag],
        current_reason=lhs[a.reason],
        current_present=ibis.literal(True),
        current_error_bound=_operand_bound(left, lhs),
        **endpoint_fields(stage.node.signature, "current", lhs),
    )
    baseline = rhs.select(
        **{f"baseline_key_{i}": rhs[key] for i, key in enumerate(keys)},
        baseline_value=rhs[b.value],
        baseline_tag=rhs[b.tag],
        baseline_reason=rhs[b.reason],
        baseline_present=ibis.literal(True),
        baseline_error_bound=_operand_bound(right, rhs),
        **endpoint_fields(stage.node.signature, "baseline", rhs),
    )
    translated_keys = {
        i: ibis.cases(
            *(
                (baseline[f"baseline_key_{i}"] == right, left)
                for left, right in params.bucket_mapping
            ),
            else_=ibis.null().cast("string"),
        )
        if i == params.time_index
        else baseline[f"baseline_key_{i}"]
        for i in range(len(keys))
    }
    paired = current.join(
        baseline,
        [current[f"current_key_{i}"] == translated_keys[i] for i in range(len(keys))],
        how="inner" if params.pairing == "exact" else "outer",
    )
    present_a, present_b = (
        paired.current_present.fill_null(False),
        paired.baseline_present.fill_null(False),
    )
    both = present_a & present_b
    first, second = paired.current_value, paired.baseline_value
    tag_a, tag_b = paired.current_tag, paired.baseline_tag
    reason_a, reason_b = paired.current_reason, paired.baseline_reason
    if params.pairing == "metric_empty":
        first = present_a.ifelse(
            first,
            ibis.literal(0).cast(first.type())
            if params.empty_rules[0] == "zero"
            else ibis.null().cast(first.type()),
        )
        second = present_b.ifelse(
            second,
            ibis.literal(0).cast(second.type())
            if params.empty_rules[1] == "zero"
            else ibis.null().cast(second.type()),
        )
        tag_a = present_a.ifelse(
            tag_a,
            "defined"
            if params.empty_rules[0] == "zero"
            else "null"
            if params.empty_rules[0] == "null"
            else "undefined",
        )
        tag_b = present_b.ifelse(
            tag_b,
            "defined"
            if params.empty_rules[1] == "zero"
            else "null"
            if params.empty_rules[1] == "null"
            else "undefined",
        )
        reason_a = present_a.ifelse(
            reason_a,
            ibis.null().cast("string")
            if params.empty_rules[0] == "zero"
            else "empty_contribution"
            if params.empty_rules[0] == "null"
            else "zero_denominator",
        )
        reason_b = present_b.ifelse(
            reason_b,
            ibis.null().cast("string")
            if params.empty_rules[1] == "zero"
            else "empty_contribution"
            if params.empty_rules[1] == "null"
            else "zero_denominator",
        )
    defined = ((tag_a == "defined") & (tag_b == "defined")).fill_null(False)
    value = (
        first - second
        if stage.operation == "ibis"
        else ibis.literal(0).cast(stage.node.value_type.name)
    )
    if stage.operation == "ibis" and first.type().is_integer():
        widened = first.cast("decimal(38,0)") - second.cast("decimal(38,0)")
        in_range = widened.between(
            ibis.literal(-(2**63), type="decimal(38,0)"),
            ibis.literal(2**63 - 1, type="decimal(38,0)"),
        )
        value = in_range.ifelse(widened, ibis.literal(0).cast("decimal(38,0)")).cast("int64")
        checks.append(
            IntegrityCheck(
                stage.output,
                "difference within signed int64 storage",
                paired.filter(defined & ~in_range),
                _source_ids(left.source_ids, right.source_ids),
            )
        )
    tag = ibis.ifelse(
        defined, "defined", ibis.ifelse(~both & (params.pairing == "keep"), "undefined", "null")
    )
    reason = ibis.ifelse(
        defined,
        ibis.null().cast("string"),
        ibis.ifelse(~both & (params.pairing == "keep"), "missing_side", "empty_contribution"),
    )
    if params.pairing == "metric_empty":
        missing_tag = (~present_a).ifelse(tag_a, tag_b)
        missing_reason = (~present_a).ifelse(reason_a, reason_b)
        tag = defined.ifelse("defined", missing_tag)
        reason = defined.ifelse(ibis.null().cast("string"), missing_reason)
    if params.method != "difference" and second.type().is_floating():
        unstable = (
            both & (second != 0) & (second.abs() <= paired.baseline_error_bound.fill_null(0.0))
        )
        checks.append(
            IntegrityCheck(
                stage.output,
                "nonzero denominator interval excludes zero",
                paired.filter(unstable),
                _source_ids(left.source_ids, right.source_ids),
            )
        )
    target = canonical_layout(stage.node.signature, has_value=True)
    identity = {
        key: ibis.coalesce(
            paired[f"current_key_{i}"],
            ibis.cases(
                *(
                    (paired[f"baseline_key_{i}"] == right, left)
                    for left, right in params.bucket_mapping
                ),
                else_=ibis.null().cast("string"),
            )
            if i == params.time_index
            else paired[f"baseline_key_{i}"],
        )
        for i, key in enumerate(keys)
    }
    output = paired.select(
        **identity,
        value=ibis.ifelse(defined, value, ibis.null().cast(value.type())),
        cell_tag=tag,
        cell_reason=reason,
        **{
            name: paired[name]
            for name in paired.columns
            if name.startswith(("current_endpoint__state__", "baseline_endpoint__state__"))
            or name
            in (
                "current_endpoint__groups",
                "baseline_endpoint__groups",
                "current_endpoint__complete",
                "baseline_endpoint__complete",
            )
        },
        current_endpoint__value=first,
        current_endpoint__cell_tag=tag_a,
        current_endpoint__cell_reason=reason_a,
        baseline_endpoint__value=second,
        baseline_endpoint__cell_tag=tag_b,
        baseline_endpoint__cell_reason=reason_b,
        correspondence__current_present=present_a,
        correspondence__baseline_present=present_b,
        correspondence__current_error_bound=paired.current_error_bound.fill_null(0.0),
        correspondence__baseline_error_bound=paired.baseline_error_bound.fill_null(0.0),
        correspondence__result_error_bound=ibis.ifelse(
            defined,
            paired.current_error_bound.fill_null(0.0)
            + paired.baseline_error_bound.fill_null(0.0)
            + 1e-12 * (1.0 + value.abs()),
            0.0,
        )
        if stage.node.value_type == ScalarType("float64") and stage.operation == "ibis"
        else ibis.literal(0).cast("float64"),
        **{
            f"correspondence__{side}_key_{i}": paired[f"{side}_key_{i}"]
            for side in ("current", "baseline")
            for i in range(len(keys))
        },
        **(
            {f"subject__key_{i}": identity[key] for i, key in enumerate(keys)}
            if any(isinstance(p, SubjectPart) for p in stage.node.signature.parts)
            else {}
        ),
    )
    return output.select(*target.columns), target


def _transport(
    stage: SourceMethodStage,
    source: LoweredRelation,
    checks: list[LoweredCheck],
    predicate_sources: tuple[LoweredRelation, ...] = (),
    part_expressions: list[tuple[str, ir.Table]] | None = None,
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, PartsTransport)
    table = source.expression
    cell = source.layout.cell
    source_ids = _source_ids(source.source_ids, *(item.source_ids for item in predicate_sources))
    cohort = params.mode == "cohort"
    cells = [cell]
    dependencies = predicate_sources
    first = None
    if cohort:
        first = predicate_sources[0]
        table = first.expression
        cells.append(first.layout.cell)
        dependencies = predicate_sources[1:]
        keys = tuple(k.column for k in source.layout.keys)
        grid = first.node.signature.domain.time_grid
        expected = source.expression.select(*keys)
        if grid is not None:
            anchor = f"key_{len(keys)}"
            branches = tuple(
                expected.mutate(**{anchor: ibis.literal(item.identity)}) for item in grid.cells
            )
            expected = (
                branches[0].union(*branches[1:], distinct=False)
                if len(branches) > 1
                else branches[0]
            )
        checks.append(
            IntegrityCheck(
                stage.output,
                "complete opportunity keys",
                _pair_violations(replace(first, expression=expected), first),
                source_ids,
            )
        )
    for index, other_input in enumerate(dependencies, 2 if cohort else 1):
        other_cell = other_input.layout.cell
        if other_cell is None:
            _fail("a Cell-valued predicate dependency", "missing Cell")
        receiver = first if cohort else source
        assert receiver is not None
        keys = tuple(k.column for k in receiver.layout.keys)
        checks.append(
            IntegrityCheck(
                stage.output,
                "equal complete predicate keys",
                receiver.expression.select(*keys).anti_join(
                    other_input.expression.select(*keys), keys
                )
                if index in params.inclusion_inputs
                else _pair_violations(receiver, other_input),
                source_ids,
            )
        )
        names = (
            f"__predicate_{index}_value",
            f"__predicate_{index}_tag",
            f"__predicate_{index}_reason",
        )
        other = other_input.expression.select(
            *keys,
            **{
                names[0]: other_input.expression[other_cell.value],
                names[1]: other_input.expression[other_cell.tag],
                names[2]: other_input.expression[other_cell.reason],
            },
        ).view()
        left = table.view()
        table = (
            left.inner_join(other, [left[k].identical_to(other[k]) for k in keys])
            if keys
            else left.cross_join(other)
        ).select(*[left[name] for name in table.columns], *[other[name] for name in names])
        cells.append(CellColumns(*names))

    def leaf_value(predicate: ValuePredicate) -> ir.BooleanValue:
        selected = cells[predicate.input_index]
        assert selected is not None
        left = table[selected.value]
        if predicate.operator == "is_defined":
            return table[selected.tag] == "defined"
        indices = (
            (predicate.input_index,)
            if predicate.right_index is None
            else (predicate.input_index, predicate.right_index)
        )
        for index in indices:
            columns = cells[index]
            assert columns is not None
            invalid = (
                (table[columns.tag] != "defined") & (table[columns.tag] != "unknown")
                if cohort
                else table[columns.tag] != "defined"
            )
            physical = (source, *predicate_sources)[index].node.value_type
            if physical == ScalarType("float64"):
                value = table[columns.value]
                invalid = (
                    invalid
                    | (value.abs() > float.fromhex("0x1.fffffffffffffp+1023"))
                    | (value != value)
                )
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "finite Defined predicate operands",
                    table.filter(invalid),
                    source_ids,
                )
            )
        if predicate.right_index is None:
            right = ibis.literal(predicate.literal)
            right = right.cast(left.type())
        else:
            columns = cells[predicate.right_index]
            assert columns is not None
            right = table[columns.value]
        result = (
            left == right
            if predicate.operator == "eq"
            else left != right
            if predicate.operator == "ne"
            else left < right
            if predicate.operator == "lt"
            else left <= right
            if predicate.operator == "le"
            else left > right
            if predicate.operator == "gt"
            else left >= right
        )
        if cohort:
            unknown = reduce(
                or_,
                (
                    table[column.tag] == "unknown"
                    for i in indices
                    for column in (cells[i],)
                    if column is not None
                ),
            )
            result = unknown.ifelse(ibis.null().cast("boolean"), result)
        return result

    def lower_tree(predicate: ValuePredicate) -> ir.BooleanValue:
        if not predicate.children:
            return leaf_value(predicate)
        children = tuple(lower_tree(child) for child in predicate.children)
        if predicate.operator == "not_":
            return ~children[0]
        return reduce(and_ if predicate.operator == "all_of" else or_, children)

    conditions = tuple(lower_tree(tree) for tree in params.predicates)
    if cohort:
        keys = tuple(k.column for k in source.layout.keys)
        table = table.mutate(__cohort_truth=reduce(and_, conditions)).view()
        truth = table.__cohort_truth
        counted = table.group_by(*keys).aggregate(
            __t=truth.fill_null(False).ifelse(1, 0).cast("int64").sum(),
            __u=truth.isnull().ifelse(1, 0).cast("int64").sum(),
            __f=(~truth).fill_null(False).ifelse(1, 0).cast("int64").sum(),
        )
        left = source.expression.view()
        counted = counted.view()
        merged = left.left_join(counted, keys).select(
            *[left[name] for name in source.expression.columns],
            *[counted[name].fill_null(0).name(name) for name in ("__t", "__u", "__f")],
        )
        t, u, f = merged.__t, merged.__u, merged.__f
        if params.cohort_rule == "any":
            decided, accepted = (t > 0) | (u == 0), t > 0
        elif params.cohort_rule == "at_least":
            accepted = t >= params.cohort_count
            decided = accepted | ((t + u) < params.cohort_count)
        else:
            total = t + u + f
            decided = (f > 0) | (u == 0)
            accepted = (f == 0) & (u == 0)
            if params.cohort_empty == "undefined":
                decided = decided & (total > 0)
            elif params.cohort_empty == "false":
                accepted = accepted & (total > 0)
        checks.append(
            IntegrityCheck(
                stage.output,
                "decidable qualification for every target Subject",
                merged.filter(~decided),
                source_ids,
            )
        )
        complete = merged.mutate(
            cohort_decision__true_count=t,
            cohort_decision__unknown_count=u,
            cohort_decision__false_count=f,
            cohort_decision__accepted=accepted,
        )
        layout = canonical_layout(stage.node.signature, has_value=False)
        assert part_expressions is not None
        part_expressions.append(
            (
                "cohort_decision",
                complete.select(
                    *keys,
                    *(
                        component.column
                        for part in layout.parts
                        if part_role(part.part) == "cohort_decision"
                        for component in part.columns
                    ),
                ),
            )
        )
        return complete.filter(complete.cohort_decision__accepted).select(*layout.columns), layout
    if conditions:
        table = table.filter(reduce(and_, conditions))
    if params.mode == "limit":
        assert params.limit_count is not None
        table = table.order_by(table.ordering__position).limit(params.limit_count)
    if params.attribution_view is not None:
        table = table.mutate(value=table["allocation__" + params.attribution_view])
    if params.display_view == "ranks":
        assert cell is not None
        table = table.mutate(
            value=table.ranks__value,
            cell_tag=table.ranks__cell_tag,
            cell_reason=table.ranks__cell_reason,
        )

    target = canonical_layout(
        stage.node.signature, has_value=cell is not None and params.keep_quantity
    )
    retained = tuple(
        next(p for p in source.layout.parts if part_role(p.part) == role)
        for role in params.retained_roles
    )
    old = RelationLayout(source.layout.keys, cell if target.cell is not None else None, retained)
    return _renamed(table, old, target), target


def _source_fields(admitted: GraphPlan, binding: SourceBinding) -> tuple[str, ...]:
    fields = {key.column for key in binding.layout.keys}
    version = binding.leaf.definition.version
    if isinstance(binding.leaf.signature.domain.version_selection, GridVersionSelection):
        if isinstance(version, TargetSnapshotVersion):
            fields.add(version.source_column)
        elif isinstance(version, TargetValidityVersion):
            fields.update((version.valid_from_column, version.valid_to_column))
    for node in topology(admitted.root):
        if not isinstance(node, MethodNode) or binding.leaf not in node.sources:
            continue
        params = node.parameters
        if isinstance(params, PreparedObservation):
            params = params.observation
        if isinstance(params, BindProject) and params.field_contract is not None:
            if params.field_owner.path == binding.leaf.definition.ref.path:
                if params.expression_bodies:
                    fields.update(binding.source.relation.columns)
                else:
                    fields.add(params.field_contract.source_column)
            for relationship in params.path_contracts:
                if relationship.from_entity_ref.path == binding.leaf.definition.ref.path:
                    fields.update(key[0] for key in relationship.keys)
                if relationship.to_entity_ref.path == binding.leaf.definition.ref.path:
                    fields.update(key[1] for key in relationship.keys)
        elif isinstance(params, (ObserveMetric, ObserveCount, ObserveWeightedMean)):
            if (
                isinstance(params, ObserveMetric)
                and params.contribution.path == binding.leaf.definition.ref.path
            ):
                fields.update(params.distinct_columns)
            if (
                isinstance(params, ObserveWeightedMean)
                and params.contribution.path == binding.leaf.definition.ref.path
            ):
                fields.add(params.weight_column)
            for coordinate in params.coordinates:
                if coordinate.entity_ref.path == binding.leaf.definition.ref.path:
                    fields.add(coordinate.source_column)
            for item in params.filters:
                if item.dimension.entity_ref.path == binding.leaf.definition.ref.path:
                    fields.add(item.dimension.source_column)
            if params.event.entity_ref.path == binding.leaf.definition.ref.path:
                fields.add(params.event.source_column)
            for relationship in params.path:
                if relationship.from_entity_ref.path == binding.leaf.definition.ref.path:
                    fields.update(source for source, _ in relationship.keys)
                if relationship.to_entity_ref.path == binding.leaf.definition.ref.path:
                    fields.update(destination for _, destination in relationship.keys)
            if (
                isinstance(params, (ObserveMetric, ObserveWeightedMean))
                and params.contribution == binding.leaf.definition.ref
            ):
                fields.add(params.amount_column)
    return tuple(column for column in binding.source.relation.columns if column in fields)


def _staged_source(binding: SourceBinding, relations: tuple[LoweredRelation, ...]) -> ir.Table:
    relation = next(item for item in relations if item.node is binding.leaf)
    fields = binding.source.relation.columns
    return relation.expression.select(
        *(
            relation.expression[column].name(fields[int(column.removeprefix("source__"))])
            for column in relation.layout.extras
        )
    )


def _grid_read_filter(table: ir.Table, binding: SourceBinding, source: LoweredRelation) -> ir.Table:
    selected = binding.leaf.signature.domain.version_selection
    if not isinstance(selected, GridVersionSelection):
        return table
    grid = source.node.signature.domain.time_grid
    if (
        grid is None
        or selected.grid_identity != grid.identity
        or tuple(k for k, _ in selected.selections) != tuple(c.identity for c in grid.cells)
    ):
        _fail("the receiver's complete ordered grid selection", selected.grid_identity)
    index = next(i for i, key in enumerate(source.layout.keys) if key.coordinate.role == "anchor")
    version = binding.leaf.definition.version
    if isinstance(version, TargetSnapshotVersion):
        version = replace(version, source_column="owner__" + version.source_column)
    elif isinstance(version, TargetValidityVersion):
        version = replace(
            version,
            valid_from_column="owner__" + version.valid_from_column,
            valid_to_column="owner__" + version.valid_to_column,
        )
    return table.filter(
        ibis.cases(
            *(
                (table[f"member__{index}"] == key, version_predicate(table, version, anchor))
                for key, anchor in selected.selections
            ),
            else_=False,
        )
    )


def _bind(
    stage: SourceMethodStage,
    source: LoweredRelation,
    bindings: tuple[SourceBinding, ...],
    checks: list[LoweredCheck],
    relations: tuple[LoweredRelation, ...],
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, BindProject) and params.field_contract is not None
    selected = tuple(next(b for b in bindings if b.leaf is leaf) for leaf in stage.node.sources)
    keys = tuple(key.column for key in source.layout.keys)
    first = _staged_source(selected[0], relations).view()
    # Anchor the correspondence to the consumer before validating any owner rows.
    scoped = (
        source.expression.select(*keys)
        .join(
            first,
            tuple(
                source.expression[key] == first[item.column]
                for key, item in zip(
                    tuple(k.column for k in source.layout.keys if k.coordinate.role != "anchor"),
                    selected[0].layout.keys,
                    strict=True,
                )
            ),
        )
        .select(
            *(source.expression[key].name(f"member__{i}") for i, key in enumerate(keys)),
            *(first[column].name(f"owner__{column}") for column in first.columns),
        )
    )
    scoped = _grid_read_filter(scoped, selected[0], source)
    for index, relationship in enumerate(params.path_contracts):
        target = _staged_source(selected[index + 1], relations).view()
        joined = scoped.join(
            target,
            tuple(scoped[f"owner__{left}"] == target[right] for left, right in relationship.keys),
        )
        scoped = joined.select(
            *(scoped[f"member__{i}"] for i in range(len(keys))),
            *(target[column].name(f"owner__{column}") for column in target.columns),
        )
        scoped = _grid_read_filter(scoped, selected[index + 1], source)
    if params.expression_bodies:
        sidecar = selected[-1].expression_sidecar
        if sidecar is None:
            _fail("the frozen expression sidecar for this source read", params.ref.path)
        for kind, path, body_hash in params.expression_bodies:
            candidate = next(
                (
                    body
                    for ref, body in sidecar.bodies.items()
                    if ref.kind.value == kind and ref.path == path
                ),
                None,
            )
            if candidate is None or candidate.body_ast_hash != body_hash:
                _fail("the exact frozen expression body and bindings", path)
        owner_alias = scoped.select(
            *(scoped[f"member__{i}"] for i in range(len(keys))),
            *(
                scoped[f"owner__{column}"].name(column)
                for column in selected[-1].source.relation.columns
            ),
        )
        body = next(body for ref, body in sidecar.bodies.items() if ref == params.ref)
        value = evaluate_expression_body(
            catalog_definition_fingerprint=params.expression_bodies[0][2],
            expression_sidecar=sidecar,
            owning_ref=params.ref,
            body=body,
            entity_refs=(params.field_owner,),
            aliases=(owner_alias,),
        )
        # Ibis can infer float64 for integer * decimal literal while DuckDB
        # physically returns DECIMAL; force the declared source boundary type.
        if not isinstance(stage.node.value_type, ScalarType):
            _fail("a computed scalar read type", repr(stage.node.value_type))
        value = (
            value.cast("string").cast("float64")
            if stage.node.value_type.name == "float64"
            else value.cast(stage.node.value_type.name)
        )
        projected = owner_alias.select(
            *(owner_alias[f"member__{i}"].name(key) for i, key in enumerate(keys)),
            __bound_value=value,
        ).view()
    else:
        field = params.field_contract.source_column
        projected = scoped.select(
            *(scoped[f"member__{i}"].name(key) for i, key in enumerate(keys)),
            __bound_value=scoped[f"owner__{field}"],
        ).view()
    source_ids = _source_ids(source.source_ids, tuple(item.leaf.identity for item in selected))
    checks.append(
        IntegrityCheck(
            stage.output,
            "single field value per complete consumed identity",
            _key_violations(projected, RelationLayout(source.layout.keys, None)),
            source_ids,
        )
    )
    checks.append(
        IntegrityCheck(
            stage.output,
            "complete field-owner coverage",
            source.expression.anti_join(projected, keys),
            source_ids,
        )
    )
    joined = source.expression.left_join(projected, keys).select(
        *(
            source.expression[c]
            for c in source.layout.columns
            if c not in ("value", "cell_tag", "cell_reason")
        ),
        value=projected.__bound_value,
        cell_tag=ibis.ifelse(projected.__bound_value.isnull(), "null", "defined"),
        cell_reason=ibis.ifelse(
            projected.__bound_value.isnull(), "source_null", ibis.null().cast("string")
        ),
    )
    target_layout = canonical_layout(stage.node.signature, has_value=True)
    return joined.select(*target_layout.columns), target_layout


def _map(
    stage: SourceMethodStage, inputs: tuple[LoweredRelation, ...], checks: list[LoweredCheck]
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, MapCorrespond)
    source = inputs[0]
    target = canonical_layout(stage.node.signature, has_value=params.mode == "group")
    if params.mode == "group_keys":
        source_keys = source.node.signature.domain.instance_key
        if not params.output_domain.instance_key:
            result = source.expression.aggregate(singleton=source.expression.count()).mutate(
                singleton=ibis.literal(1, type="int64")
            )
            target = replace(target, extras=("singleton",))
        else:
            result = source.expression.select(
                **{
                    f"key_{i}": source.expression[f"key_{source_keys.index(c)}"]
                    for i, c in enumerate(params.output_domain.instance_key)
                }
            ).distinct()
        return result, target
    if params.mode == "group":
        cell = source.layout.cell
        if (
            cell is None
            or len(target.keys) != 1
            or str(source.expression[cell.value].type()) != "string"
        ):
            _fail("one string-valued Group coordinate", repr(source.layout))
        checks.append(
            IntegrityCheck(
                stage.output,
                "Defined non-null Group values",
                source.expression.filter(source.expression[cell.tag] != "defined").select(
                    *(item.column for item in source.layout.keys)
                ),
                source.source_ids,
            )
        )
        grouped = source.expression.select(
            key_0=source.expression[cell.value],
            value=source.expression[cell.value],
            cell_tag=source.expression[cell.tag],
            cell_reason=source.expression[cell.reason],
        ).distinct()
        return grouped.select(*target.columns), target
    if params.mode == "subjects":
        subject = next(p for p in source.layout.parts if isinstance(p.part, SubjectPart))
        assert isinstance(subject.part, SubjectPart)
        projected = source.expression.select(
            *(source.expression[c.column].name(f"key_{i}") for i, c in enumerate(subject.columns))
        )
        if subject.part.injective:
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "injective declared Subject map",
                    _key_violations(projected, RelationLayout(target.keys, None)),
                    source.source_ids,
                )
            )
        else:
            projected = projected.distinct()
        projected = projected.mutate(
            **{
                c.column: projected[f"key_{i}"]
                for p in target.parts
                for i, c in enumerate(p.columns)
            }
        )
        return projected.select(*target.columns), target
    left = source.expression.select(*(k.column for k in source.layout.keys))
    right = inputs[1].expression.select(*(k.column for k in inputs[1].layout.keys))
    if params.mode == "union_keys":
        return left.union(right, distinct=True), target
    checks.append(
        IntegrityCheck(
            stage.output,
            "equal complete key sets",
            _pair_violations(*inputs),
            _source_ids(*(input.source_ids for input in inputs)),
        )
    )
    return left, target


def _count(stage: SourceMethodStage, source: LoweredRelation) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, RowState)
    table = source.expression
    source_keys = source.node.signature.domain.instance_key
    target_keys = stage.node.signature.domain.instance_key
    grouping = {f"key_{i}": table[f"key_{source_keys.index(c)}"] for i, c in enumerate(target_keys)}
    grouped = table.group_by(**grouping) if grouping else table
    if params.merge:
        state = next(p for p in source.node.signature.parts if isinstance(p, RowStatePart))
        aggregate = grouped.aggregate(
            **{
                f"row_state__{name}": table[f"row_state__{name}"].min()
                if name == "min"
                else table[f"row_state__{name}"].max()
                if name == "max"
                else table[f"row_state__{name}"].max().fill_null(0)
                if name == "error_bound" and params.method in ("min", "max")
                else table[f"row_state__{name}"].sum().fill_null(0)
                for name in state.components
            },
            **(
                {"error_magnitude": table.row_state__sum.abs().sum().fill_null(0)}
                if params.retain_error and params.method in ("sum", "mean")
                else {}
            ),
        )
        if params.retain_error and params.method in ("sum", "mean"):
            aggregate = aggregate.mutate(
                row_state__error_bound=aggregate.row_state__error_bound
                + 1e-12 * (1.0 + aggregate.error_magnitude)
            )
        target = canonical_layout(stage.node.signature, has_value=True)
        support = (
            aggregate.row_state__count
            if params.method in ("mean", "min", "max")
            else ibis.literal(1)
        )
        value = (
            aggregate.row_state__sum.cast("float64") / support
            if params.method == "mean"
            else aggregate[f"row_state__{params.method}"]
        )
        result = aggregate.mutate(
            value=ibis.ifelse(support > 0, value, ibis.null().cast(str(value.type()))),
            cell_tag=ibis.ifelse(support > 0, "defined", "undefined"),
            cell_reason=ibis.ifelse(
                support > 0, ibis.null().cast("string"), "empty_" + params.method
            ),
        )
        return result.select(*target.columns), target
    if params.method in ("min", "max"):
        cell = source.layout.cell
        if cell is None:
            _fail("Cell values for current-row extrema", "missing Cell")
        extremum = table[cell.value].min() if params.method == "min" else table[cell.value].max()
        operand_bound = _operand_bound(source, table) if params.retain_error else None
        aggregate = grouped.aggregate(
            extremum=extremum,
            support=table.count(),
            **(
                {
                    "error_bound": operand_bound.max().fill_null(0)
                    if isinstance(operand_bound, ir.Column)
                    else operand_bound
                }
                if params.retain_error
                else {}
            ),
        )
        target = canonical_layout(stage.node.signature, has_value=True)
        result = aggregate.select(
            *tuple(grouping),
            value=aggregate.extremum,
            cell_tag=ibis.ifelse(aggregate.support == 0, "undefined", "defined"),
            cell_reason=ibis.ifelse(
                aggregate.support == 0, "empty_" + params.method, ibis.null().cast("string")
            ),
            **{
                f"row_state__{params.method}": aggregate.extremum,
                "row_state__count": aggregate.support,
                **(
                    {"row_state__error_bound": aggregate.error_bound} if params.retain_error else {}
                ),
            },
        )
        return result.select(*target.columns), target
    if params.method in ("sum", "mean"):
        cell = source.layout.cell
        if cell is None:
            _fail("Cell values for registered current-row arithmetic", "missing Cell")
        aggregate = grouped.aggregate(
            state_sum=table[cell.value].sum().fill_null(0),
            state_count=table.count(),
            **(
                {
                    "error_bound": _operand_bound(source, table).sum().fill_null(0)
                    + 1e-12 * (1.0 + table[cell.value].abs().sum().fill_null(0))
                }
                if params.retain_error
                else {}
            ),
        )
        if params.method == "sum":
            result = aggregate.select(
                *tuple(grouping),
                value=aggregate.state_sum,
                cell_tag=ibis.literal("defined"),
                cell_reason=ibis.null().cast("string"),
                row_state__sum=aggregate.state_sum,
                row_state__count=aggregate.state_count,
                **(
                    {"row_state__error_bound": aggregate.error_bound} if params.retain_error else {}
                ),
            )
        else:
            result = aggregate.select(
                *tuple(grouping),
                value=ibis.ifelse(
                    aggregate.state_count == 0,
                    ibis.null().cast("float64"),
                    aggregate.state_sum.cast("float64") / aggregate.state_count,
                ),
                cell_tag=ibis.ifelse(aggregate.state_count == 0, "undefined", "defined"),
                cell_reason=ibis.ifelse(
                    aggregate.state_count == 0,
                    "empty_mean",
                    ibis.null().cast("string"),
                ),
                row_state__sum=aggregate.state_sum,
                row_state__count=aggregate.state_count,
                **(
                    {"row_state__error_bound": aggregate.error_bound} if params.retain_error else {}
                ),
            )
        target = canonical_layout(stage.node.signature, has_value=True)
        return result.select(*target.columns), target
    if params.method == "count_defined":
        cell = source.layout.cell
        if cell is None:
            _fail("Cell tags for defined-count", "missing Cell")
        value = (table[cell.tag] == "defined").ifelse(1, 0).cast("int64").sum().fill_null(0)
    else:
        value = table.count()
    result = grouped.aggregate(value=value)
    target = canonical_layout(stage.node.signature, has_value=True)
    result = result.mutate(
        cell_tag=ibis.literal("defined"),
        cell_reason=ibis.null().cast("string"),
        **{f"row_state__{params.method}": result.value},
    )
    return result.select(*target.columns), target


def _spearman(
    stage: SourceMethodStage,
    inputs: tuple[LoweredRelation, LoweredRelation],
    checks: list[LoweredCheck],
) -> tuple[ir.Table, RelationLayout]:
    """Lower same-Entity average-rank Spearman and independent pair counts."""
    left, right = inputs
    left_cell, right_cell = left.layout.cell, right.layout.cell
    if left_cell is None or right_cell is None:
        _fail("two Cell-valued Association endpoints", "missing Cell")
    keys = tuple(item.column for item in left.layout.keys)
    if keys != tuple(item.column for item in right.layout.keys):
        _fail("the same complete endpoint key", repr(right.layout.keys))
    lhs = left.expression.view()
    rhs = right.expression.view()
    a = lhs.select(
        *keys,
        va=lhs[left_cell.value],
        taga=lhs[left_cell.tag],
        reasona=lhs[left_cell.reason],
    )
    b = rhs.select(
        *keys,
        vb=rhs[right_cell.value],
        tagb=rhs[right_cell.tag],
        reasonb=rhs[right_cell.reason],
    )
    paired = a.join(b, keys).select(
        *(a[key] for key in keys), a.va, a.taga, a.reasona, b.vb, b.tagb, b.reasonb
    )
    if stage.operation == "prepare":
        layout = RelationLayout(
            left.layout.keys, None, extras=("va", "taga", "reasona", "vb", "tagb", "reasonb")
        )
        return paired.select(*layout.columns), layout
    complete = paired.filter((paired.taga == "defined") & (paired.tagb == "defined"))
    first_a = ibis.rank().over(ibis.window(order_by=complete.va)) + 1
    first_b = ibis.rank().over(ibis.window(order_by=complete.vb)) + 1
    ties_a = complete.count().over(ibis.window(group_by=complete.va))
    ties_b = complete.count().over(ibis.window(group_by=complete.vb))
    ranked = complete.mutate(
        ra=(first_a + (ties_a - 1) / 2).cast("float64"),
        rb=(first_b + (ties_b - 1) / 2).cast("float64"),
    )
    counts = paired.aggregate(
        input_observation_count=paired.count(),
        matched_observation_count=paired.count(),
        null_pair_count=(
            ((paired.taga == "null") | (paired.tagb == "null")).cast("int64").sum().fill_null(0)
        ),
    )
    numbers = ranked.aggregate(
        complete_pair_count=ranked.count(),
        unique_a=ranked.va.nunique(),
        unique_b=ranked.vb.nunique(),
        coefficient=ranked.ra.corr(ranked.rb, how="pop"),
    )
    result = counts.cross_join(numbers)
    status = ibis.ifelse(
        result.complete_pair_count < 2,
        "insufficient_pairs",
        ibis.ifelse(
            (result.unique_a == 1) & (result.unique_b == 1),
            "constant_both",
            ibis.ifelse(
                result.unique_a == 1,
                "constant_a",
                ibis.ifelse(result.unique_b == 1, "constant_b", "valid"),
            ),
        ),
    )
    left_quantity = left.node.signature.quantity
    right_quantity = right.node.signature.quantity
    assert isinstance(left_quantity, ObservedQuantity)
    assert isinstance(right_quantity, ObservedQuantity)
    target = replace(canonical_layout(stage.node.signature, has_value=True), extras=("status",))
    output = result.select(
        status=status,
        value=ibis.ifelse(
            status == "valid",
            ibis.ifelse(
                result.coefficient.abs() >= 1 - 1e-12,
                ibis.ifelse(result.coefficient >= 0, 1.0, -1.0),
                result.coefficient,
            ),
            ibis.null().cast("float64"),
        ),
        cell_tag=ibis.ifelse(status == "valid", "defined", "undefined"),
        cell_reason=ibis.ifelse(status == "valid", ibis.null().cast("string"), status),
        pair_counts__metric_key_a=ibis.literal(str(left_quantity.metric_ref)),
        pair_counts__metric_key_b=ibis.literal(str(right_quantity.metric_ref)),
        pair_counts__input_observation_count=result.input_observation_count,
        pair_counts__matched_observation_count=result.matched_observation_count,
        pair_counts__null_pair_count=result.null_pair_count,
        pair_counts__complete_pair_count=result.complete_pair_count,
    )
    checks.append(
        IntegrityCheck(
            stage.output,
            "finite valid Spearman coefficient in [-1, 1]",
            output.filter(
                (output.status == "valid")
                & (
                    output.value.isnull()
                    | output.value.isnan().fill_null(False)
                    | output.value.isinf().fill_null(False)
                    | (output.value.abs() > 1 + 1e-12)
                )
            ),
            _source_ids(left.source_ids, right.source_ids),
        )
    )
    return output.select(*target.columns), target


def _occurrence_combine(
    stage: SourceMethodStage,
    inputs: tuple[LoweredRelation, ...],
    checks: list[LoweredCheck],
    admitted: GraphPlan,
) -> tuple[ir.Table, RelationLayout]:
    """Combine independently reduced occurrences under ordered signed terms."""
    params = stage.node.parameters
    assert isinstance(params, OccurrenceCombine)
    first = inputs[0]
    keys = tuple(k.column for k in first.layout.keys)
    # Rename each component's own state to its indexed term column so the join
    # cannot silently pair one occurrence's state with another's. Each component
    # contributes whatever state its own method declared: a sum carries
    # sum/non-null-count, a count carries count.
    parts: list[ir.Table] = []
    for index, (_quantity, sign) in enumerate(params.terms):
        source = inputs[index]
        state = next(
            (part for part in source.node.signature.parts if part_role(part) == "original_state"),
            None,
        )
        if not isinstance(state, OriginalStatePart):
            _fail("a retained original state on every combined occurrence", stage.output)
        magnitude, support = _state_magnitude(state)
        selection: dict[str, ir.Value] = {key: source.expression[key] for key in keys}
        selection[_term_sum_name(index, sign)] = source.expression[magnitude]
        selection[_term_support_name(index, sign)] = source.expression[support]
        if "absolute_sum" in state.components:
            selection[_term_sum_name(index, sign).removesuffix("_sum") + "_absolute_sum"] = (
                source.expression.original_state__absolute_sum
            )
        for part in source.node.signature.parts:
            if index == 0 and isinstance(part, SubjectPart):
                selection.update(
                    {
                        column: source.expression[column]
                        for column in _subject_columns(part, source.expression)
                    }
                )
        parts.append(source.expression.select(**selection).view())
    table = parts[0]
    for index, part in enumerate(parts[1:], start=1):
        checks.append(
            IntegrityCheck(
                stage.output,
                "equal combined occurrence keys",
                _pair_violations(first, inputs[index]),
                _source_ids(first.source_ids, inputs[index].source_ids),
            )
        )
        table = table.inner_join(part, keys)
    layout = canonical_layout(stage.node.signature, has_value=True)
    coordinate = next(
        (p for p in stage.node.signature.parts if isinstance(p, CoordinateStatePart)), None
    )
    if coordinate is not None:
        branches: list[ir.Table] = []
        for index, source in enumerate(inputs):
            raw = source.expression
            branch = raw.select(*keys, group=raw.coordinate_state__groups.unnest())
            state = _original_state(source.node.signature)
            magnitude, support = _state_magnitude(state)
            branch = branch.select(
                *keys,
                **{name: branch.group[name] for name in coordinate.columns},
                **{
                    name: branch.group.absolute_sum
                    if name.split("_")[1] == str(index)
                    else ibis.literal(0).cast("float64")
                    for name in coordinate.components
                    if name.endswith("_absolute_sum")
                },
                **{
                    name: branch.group[
                        (magnitude if offset == 0 else support).removeprefix("original_state__")
                    ]
                    if term == index
                    else ibis.literal(0, type=raw[magnitude if offset == 0 else support].type())
                    for term in range(len(inputs))
                    for offset, name in enumerate(coordinate.components[term * 2 : term * 2 + 2])
                },
            )
            branches.append(branch)
        union = branches[0].union(*branches[1:], distinct=False)
        merged = union.group_by(*keys, *coordinate.columns).aggregate(
            **{
                name: union[name].sum().fill_null(0).cast(union[name].type())
                for name in coordinate.components
            }
        )
        cells = ibis.struct(
            {name: merged[name] for name in (*coordinate.columns, *coordinate.components)}
        )
        table = merged.select(
            *keys,
            **{f"key_{len(keys) + i}": merged[name] for i, name in enumerate(coordinate.columns)},
            **{f"original_state__{name}": merged[name] for name in coordinate.components},
            **{f"subject__key_{i}": merged[key] for i, key in enumerate(keys)}
            if any(isinstance(p, SubjectPart) for p in stage.node.signature.parts)
            else {},
            coordinate_state__groups=ibis.array([cells]),
        )
    table = _linear_finish(table, layout, stage.node.signature)
    for requirement in admitted.checks:
        if requirement.node_id == stage.node.identity and requirement.obligation.check_id in (
            "source.contribution_partition@v1",
            "source.complete_coverage@v1",
        ):
            checks.append(
                SemanticCheck(
                    requirement,
                    table.filter(~table.coverage__complete),
                    _source_ids(*(item.source_ids for item in inputs)),
                )
            )
    return table, layout


def _linear_finish(table: ir.Table, layout: RelationLayout, signature: Signature) -> ir.Table:
    state = _original_state(signature)
    total = ibis.literal(0, type="int64")
    defined = ibis.literal(True)
    for index, empty in enumerate(state.empty_rules):
        magnitude, support = state.components[2 * index : 2 * index + 2]
        sign = 1 if magnitude.startswith("plus_") else -1
        component = table[f"original_state__{magnitude}"]
        if component.type().is_integer():
            component = component.cast("decimal(38,0)")
        total = total + component * sign
        defined = defined & (
            (table[f"original_state__{support}"] > 0) | ibis.literal(empty == "zero")
        )
    output_type = table["original_state__" + state.components[0]].type()
    return table.mutate(
        value=ibis.ifelse(defined, total.cast(output_type), ibis.null().cast(output_type)),
        cell_tag=ibis.ifelse(defined, "defined", "null"),
        cell_reason=ibis.ifelse(defined, ibis.null().cast("string"), "empty_contribution"),
        coverage__complete=ibis.literal(True),
    ).select(*layout.columns)


def _subject_columns(part: SubjectPart, table: ir.Table) -> tuple[str, ...]:
    """Return the retained subject-key columns present on one observed relation."""
    return tuple(
        f"subject__key_{index}"
        for index in range(len(part.subject_key))
        if f"subject__key_{index}" in table.columns
    )


def _state_magnitude(state: OriginalStatePart) -> tuple[str, str]:
    """Return one component's additive column and its contributing-row count."""
    if state.components == ("count",):
        return "original_state__count", "original_state__count"
    if state.components in (("sum", "non_null_count"), ("sum", "non_null_count", "absolute_sum")):
        return "original_state__sum", "original_state__non_null_count"
    _fail("a registered additive original state", repr(state.components))


def _term_sum_name(index: int, sign: int) -> str:
    return f"original_state__{'plus' if sign > 0 else 'minus'}_{index}_sum"


def _term_support_name(index: int, sign: int) -> str:
    return f"original_state__{'plus' if sign > 0 else 'minus'}_{index}_non_null_count"


def _original_state(signature: Signature) -> OriginalStatePart:
    return next(part for part in signature.parts if isinstance(part, OriginalStatePart))


def _division_value(
    table: ir.Table,
    numerator: str,
    denominator: str,
    *,
    scale: int | None = None,
    duration: bool = False,
) -> tuple[ir.Table, ir.Value]:
    ntype, dtype = table[numerator].type(), table[denominator].type()
    if duration:
        from marivo.analysis.compiler.numeric_sql import decimal_divide

        table = decimal_divide(table, numerator, denominator, dt.Decimal(38, 0))
        return table, table.numeric_result.cast("int64")
    if isinstance(ntype, dt.Decimal):
        from marivo.analysis.compiler.numeric_sql import decimal_divide

        result_scale = (
            scale
            if scale is not None
            else max(ntype.scale or 0, dtype.scale or 0 if isinstance(dtype, dt.Decimal) else 0, 6)
        )
        table = decimal_divide(table, numerator, denominator, dt.Decimal(38, result_scale))
        return table, table.numeric_result
    backends, _ = table._find_backends()
    if (
        ntype.is_integer()
        and dtype.is_integer()
        and len(backends) == 1
        and backends[0].name == "duckdb"
    ):
        from marivo.analysis.compiler.numeric_sql import integer_divide

        table = integer_divide(table, numerator, denominator)
        return table, table.numeric_result
    return table, table[numerator].cast("float64") / table[denominator]


def _ratio_finish(table: ir.Table, layout: RelationLayout, signature: Signature) -> ir.Table:
    state = _original_state(signature)
    table, result = _division_value(
        table, "original_state__numerator_sum", "original_state__denominator_sum"
    )
    denominator = table.original_state__denominator_sum
    contribution = (
        (table.original_state__numerator_non_null_count > 0)
        | ibis.literal(state.empty_rules[0] == "zero")
    ) & (
        (table.original_state__denominator_non_null_count > 0)
        | ibis.literal(state.empty_rules[1] == "zero")
    )
    defined = contribution & (denominator != 0)
    return table.mutate(
        value=defined.ifelse(result, ibis.null().cast(result.type())),
        cell_tag=ibis.ifelse(defined, "defined", ibis.ifelse(contribution, "undefined", "null")),
        cell_reason=ibis.ifelse(
            defined,
            ibis.null().cast("string"),
            ibis.ifelse(contribution, "zero_denominator", "empty_contribution"),
        ),
        coverage__complete=ibis.literal(True),
    ).select(*layout.columns)


def _original_ratio(
    stage: SourceMethodStage,
    inputs: tuple[LoweredRelation, LoweredRelation],
    checks: list[LoweredCheck],
    admitted: GraphPlan,
) -> tuple[ir.Table, RelationLayout]:
    left, right = inputs
    a, b = left.expression.view(), right.expression.view()
    keys = tuple(k.column for k in left.layout.keys)
    checks.append(
        IntegrityCheck(
            stage.output,
            "equal original component keys",
            _pair_violations(left, right),
            _source_ids(left.source_ids, right.source_ids),
        )
    )
    joined = a.inner_join(b, keys)
    layout = canonical_layout(stage.node.signature, has_value=True)
    first_sum, first_support = _state_magnitude(_original_state(left.node.signature))
    second_sum, second_support = _state_magnitude(_original_state(right.node.signature))
    fields = {key: a[key] for key in keys}
    fields.update(
        {
            "original_state__numerator_sum": a[first_sum],
            "original_state__numerator_non_null_count": a[first_support],
            "original_state__denominator_sum": b[second_sum],
            "original_state__denominator_non_null_count": b[second_support],
        }
    )
    for side, source in (("numerator", a), ("denominator", b)):
        if side + "_absolute_sum" in _original_state(stage.node.signature).components:
            fields["original_state__" + side + "_absolute_sum"] = (
                source.original_state__absolute_sum
            )
    if any(isinstance(part, SubjectPart) for part in stage.node.signature.parts):
        fields.update({f"subject__key_{i}": a[key] for i, key in enumerate(keys)})
    base = joined.select(**fields)
    coordinate = next(
        (p for p in stage.node.signature.parts if isinstance(p, CoordinateStatePart)), None
    )
    if coordinate is not None:
        first = a.select(*keys, group=a.coordinate_state__groups.unnest())
        first = first.select(
            *keys,
            **{name: first.group[name] for name in coordinate.columns},
            **(
                {"numerator_absolute_sum": first.group.absolute_sum}
                if "numerator_absolute_sum" in coordinate.components
                else {}
            ),
            numerator_sum=first.group[first_sum.removeprefix("original_state__")],
            numerator_non_null_count=first.group[first_support.removeprefix("original_state__")],
        )
        second = b.select(*keys, group=b.coordinate_state__groups.unnest())
        second = second.select(
            *keys,
            **{name: second.group[name] for name in coordinate.columns},
            **(
                {"denominator_absolute_sum": second.group.absolute_sum}
                if "denominator_absolute_sum" in coordinate.components
                else {}
            ),
            denominator_sum=second.group[second_sum.removeprefix("original_state__")],
            denominator_non_null_count=second.group[
                second_support.removeprefix("original_state__")
            ],
        )
        paired = first.outer_join(second, (*keys, *coordinate.columns))
        merged = paired.select(
            **{key: first[key].coalesce(second[key]) for key in keys},
            **{name: first[name].coalesce(second[name]) for name in coordinate.columns},
            **(
                {"numerator_absolute_sum": first.numerator_absolute_sum.fill_null(0)}
                if "numerator_absolute_sum" in coordinate.components
                else {}
            ),
            numerator_sum=first.numerator_sum.fill_null(0),
            numerator_non_null_count=first.numerator_non_null_count.fill_null(0),
            **(
                {"denominator_absolute_sum": second.denominator_absolute_sum.fill_null(0)}
                if "denominator_absolute_sum" in coordinate.components
                else {}
            ),
            denominator_sum=second.denominator_sum.fill_null(0),
            denominator_non_null_count=second.denominator_non_null_count.fill_null(0),
        )
        cells = ibis.struct(
            {
                **{name: merged[name] for name in coordinate.columns},
                **{name: merged[name] for name in coordinate.components},
            }
        )
        base = merged.select(
            *keys,
            **{f"key_{len(keys) + i}": merged[name] for i, name in enumerate(coordinate.columns)},
            **{f"original_state__{name}": merged[name] for name in coordinate.components},
            **{f"subject__key_{i}": merged[key] for i, key in enumerate(keys)}
            if any(isinstance(part, SubjectPart) for part in stage.node.signature.parts)
            else {},
            coordinate_state__groups=ibis.array([cells]),
        )
    table = _ratio_finish(base, layout, stage.node.signature)
    for requirement in admitted.checks:
        if requirement.node_id == stage.node.identity and requirement.obligation.check_id in (
            "source.contribution_partition@v1",
            "source.complete_coverage@v1",
        ):
            checks.append(
                SemanticCheck(
                    requirement,
                    table.filter(~table.coverage__complete),
                    _source_ids(left.source_ids, right.source_ids),
                )
            )
    return table, layout


def _weighted_finish(
    table: ir.Table, layout: RelationLayout, *, duration: bool = False
) -> ir.Table:
    weight_type = table.original_state__weight_sum.type()
    scale = max(weight_type.scale or 0, 6) if isinstance(weight_type, dt.Decimal) else None
    table, result = _division_value(
        table,
        "original_state__weighted_numerator",
        "original_state__weight_sum",
        scale=scale,
        duration=duration,
    )
    support = table.original_state__non_null_pair_count
    denominator = table.original_state__weight_sum
    defined = (support > 0) & (denominator != 0)
    return table.mutate(
        value=defined.ifelse(result, ibis.null().cast(result.type())),
        cell_tag=ibis.ifelse(defined, "defined", "null"),
        cell_reason=ibis.ifelse(
            defined,
            ibis.null().cast("string"),
            ibis.ifelse(support == 0, "empty_contribution", "zero_weight_sum"),
        ),
        coverage__complete=ibis.literal(True),
    ).select(*layout.columns)


def _fold_rollup(
    stage: SourceMethodStage, source: LoweredRelation, checks: list[LoweredCheck]
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, OriginalReduce)
    source_keys = source.node.signature.domain.instance_key
    output_keys = tuple(f"key_{i}" for i in range(len(params.coordinates)))
    selected: dict[str, ir.Value] = {}
    for name, coordinate in zip(output_keys, params.coordinates, strict=True):
        index = next(
            i
            for i, c in enumerate(source_keys)
            if c == coordinate or (c.role == coordinate.role == "anchor")
        )
        column = source.expression[f"key_{index}"]
        selected[name] = (
            ibis.cases(*((column == a, b) for a, b in params.time_mapping), else_=column)
            if params.time_mapping and coordinate.role == "anchor"
            else column
        )
    old_time = next(
        (source.expression[f"key_{i}"] for i, c in enumerate(source_keys) if c.role == "anchor"),
        ibis.literal("all"),
    )
    base = source.expression.select(
        **selected,
        period=old_time,
        encoded=source.expression.original_state__samples,
        fold_kind=source.expression.original_state__fold_kind,
    )
    kinds = tuple(
        n.parameters.fold
        for n in topology(source.node)
        if isinstance(n, MethodNode)
        and isinstance(n.parameters, ObserveMetric)
        and n.parameters.fold is not None
    )
    if not kinds or len(set(kinds)) != 1:
        _fail("one bound fold declaration", repr(kinds))
    kind = kinds[0]
    expected = base.group_by(*output_keys, "period").aggregate(expected=base.count())
    exploded = base.select(*output_keys, "period", token=base.encoded.split(";").unnest())
    exploded = exploded.filter(exploded.token != "")
    fields = exploded.token.split("~")
    samples = exploded.select(
        *output_keys,
        "period",
        sample_time=fields[0].cast("timestamp"),
        sample_sum=fields[1]
        .substr(2)
        .cast(
            next(
                (
                    "int64"
                    if n.parameters.amount_type.startswith("interval(")
                    else str(dt.Decimal(38, dt.dtype(n.parameters.amount_type).scale))
                    if n.parameters.amount_type.startswith("decimal(")
                    else n.parameters.amount_type
                )
                for n in topology(source.node)
                if isinstance(n, MethodNode)
                and isinstance(n.parameters, ObserveMetric)
                and n.parameters.fold is not None
            )
        ),
        sample_count=fields[2].cast("int64"),
    )
    merged = samples.group_by(*output_keys, "period", "sample_time").aggregate(
        sample_sum=samples.sample_sum.sum().cast(samples.sample_sum.type()),
        sample_count=samples.sample_count.sum(),
        actual=samples.count(),
    )
    alignment = merged.left_join(expected, [*output_keys, "period"])
    checks.append(
        IntegrityCheck(
            stage.output,
            "aligned pre-fold sampling coordinates",
            alignment.filter(merged.actual != expected.expected),
            source.source_ids,
        )
    )
    combined = merged.drop("period", "actual")
    checks.append(
        IntegrityCheck(
            stage.output,
            "disjoint temporal sampling coordinates",
            combined.group_by(*output_keys, "sample_time")
            .aggregate(n=combined.count())
            .filter(ibis._.n != 1),
            source.source_ids,
        )
    )
    targets = (
        base.select(*output_keys).distinct() if output_keys else base.aggregate(n=base.count())
    )
    table = _fold_samples(
        combined,
        targets,
        output_keys,
        kind,
        duration=isinstance(stage.node.value_type, DurationType),
    )
    layout = canonical_layout(stage.node.signature, has_value=True)
    return _reduction_subjects(table, stage.node.signature).select(*layout.columns), layout


def _original_sum(
    stage: SourceMethodStage, source: LoweredRelation
) -> tuple[ir.Table, RelationLayout]:
    table = source.expression
    params = stage.node.parameters
    assert isinstance(params, OriginalReduce)
    keys = tuple(f"key_{i}" for i in range(len(params.coordinates)))
    if params.coordinates:
        source_keys = source.node.signature.domain.instance_key
        if params.time_mapping:
            target_grid = params.output_domain.time_grid
            assert target_grid is not None
            index = next(i for i, c in enumerate(source_keys) if c.role == "anchor")
            field = f"key_{index}"
            table = table.mutate(
                **{
                    field: ibis.cases(
                        *((table[field] == old, new) for old, new in params.time_mapping),
                        else_=ibis.null().cast("string"),
                    )
                }
            )
            source_keys = tuple(
                replace(c, field="time:" + target_grid.identity) if c.role == "anchor" else c
                for c in source_keys
            )
        if set(params.coordinates) <= set(source_keys):
            state = _original_state(source.node.signature)
            table = table.select(
                **{
                    key: table[f"key_{source_keys.index(c)}"]
                    for key, c in zip(keys, params.coordinates, strict=True)
                },
                **{
                    f"original_state__{name}": table[f"original_state__{name}"]
                    for name in state.components
                },
                coverage__complete=table.coverage__complete,
            )
        else:
            coordinate = next(
                p for p in source.node.signature.parts if isinstance(p, CoordinateStatePart)
            )
            exploded = table.select(
                *(key.column for key in source.layout.keys),
                group=table.coordinate_state__groups.unnest(),
            )
            table = exploded.select(
                **{
                    key: (
                        exploded[f"key_{source_keys.index(c)}"]
                        if c in source_keys
                        else exploded.group[coordinate.columns[coordinate.coordinates.index(c)]]
                    )
                    for key, c in zip(keys, params.coordinates, strict=True)
                },
                **{
                    f"original_state__{name}": exploded.group[name]
                    for name in coordinate.components
                },
                coverage__complete=ibis.literal(True),
            )
    grouped = table.group_by(*keys) if keys else table
    if params.method in ("min", "max"):
        name = "original_state__" + params.method
        values = (table.original_state__non_null_count > 0).ifelse(
            table[name], ibis.null().cast(table[name].type())
        )
        reduced = grouped.aggregate(
            **{name: (values.min() if params.method == "min" else values.max()).fill_null(0)},
            original_state__non_null_count=table.original_state__non_null_count.sum().fill_null(0),
            coverage__complete=table.coverage__complete.all().fill_null(True),
        )
        output_type = stage.node.value_type
        assert isinstance(output_type, (ScalarType, DecimalType, DurationType))
        reduced = reduced.mutate(
            **{
                name: reduced[name].cast(
                    "int64" if isinstance(output_type, DurationType) else output_type.name
                )
            }
        )
        support = reduced.original_state__non_null_count
        result = reduced.mutate(
            value=(support > 0).ifelse(reduced[name], ibis.null().cast(reduced[name].type())),
            cell_tag=(support > 0).ifelse("defined", "null"),
            cell_reason=(support > 0).ifelse(ibis.null().cast("string"), "empty_contribution"),
        )
        target = canonical_layout(
            replace(
                stage.node.signature,
                parts=tuple(
                    p for p in stage.node.signature.parts if not isinstance(p, CoordinateStatePart)
                ),
            ),
            has_value=True,
        )
        return _reduction_subjects(result, stage.node.signature).select(*target.columns), target
    if stage.node.method.name == "state_rollup.mean":
        reduced = grouped.aggregate(
            **{
                f"original_state__{name}": table[f"original_state__{name}"]
                .sum()
                .fill_null(0)
                .cast(table[f"original_state__{name}"].type())
                for name in _original_state(stage.node.signature).components
            }
        )
        target = canonical_layout(
            replace(
                stage.node.signature,
                parts=tuple(
                    p for p in stage.node.signature.parts if not isinstance(p, CoordinateStatePart)
                ),
            ),
            has_value=True,
        )
        return _mean_finish(
            _reduction_subjects(reduced, stage.node.signature),
            target,
            duration=isinstance(stage.node.value_type, DurationType),
        ), target
    if stage.node.method.name in ("state_rollup.weighted_mean", "state_rollup.linear"):
        state = _original_state(stage.node.signature)
        reduced = grouped.aggregate(
            **{
                f"original_state__{name}": table[f"original_state__{name}"]
                .sum()
                .fill_null(0)
                .cast(table[f"original_state__{name}"].type())
                for name in state.components
            }
        )
        target = canonical_layout(
            replace(
                stage.node.signature,
                parts=tuple(
                    p for p in stage.node.signature.parts if not isinstance(p, CoordinateStatePart)
                ),
            ),
            has_value=True,
        )
        return (
            _linear_finish(
                _reduction_subjects(reduced, stage.node.signature), target, stage.node.signature
            )
            if stage.node.method.name == "state_rollup.linear"
            else _weighted_finish(
                _reduction_subjects(reduced, stage.node.signature),
                target,
                duration=isinstance(stage.node.value_type, DurationType),
            )
        ), target
    if stage.node.method.name == "state_rollup.ratio":
        reduced = grouped.aggregate(
            **{
                name: table[name].sum().fill_null(0)
                for name in (
                    "original_state__" + component
                    for component in _original_state(stage.node.signature).components
                )
            }
        )
        target = canonical_layout(
            replace(
                stage.node.signature,
                parts=tuple(
                    p for p in stage.node.signature.parts if not isinstance(p, CoordinateStatePart)
                ),
            ),
            has_value=True,
        )
        return _ratio_finish(
            _reduction_subjects(reduced, stage.node.signature), target, stage.node.signature
        ), target
    if stage.node.method.name == "state_rollup.count":
        reduced_count = grouped.aggregate(
            original_state__count=table.original_state__count.sum().fill_null(0),
            coverage__complete=table.coverage__complete.all().fill_null(True),
        )
        target_count = canonical_layout(
            replace(
                stage.node.signature,
                parts=tuple(
                    p for p in stage.node.signature.parts if not isinstance(p, CoordinateStatePart)
                ),
            ),
            has_value=True,
        )
        return _reduction_subjects(reduced_count, stage.node.signature).mutate(
            value=reduced_count.original_state__count.cast("int64"),
            cell_tag=ibis.literal("defined"),
            cell_reason=ibis.null().cast("string"),
        ).select(*target_count.columns), target_count
    value_type = stage.node.value_type
    assert isinstance(value_type, (ScalarType, DecimalType, DurationType))
    reduced = grouped.aggregate(
        **(
            {
                "original_state__absolute_sum": table.original_state__absolute_sum.sum().fill_null(
                    0.0
                )
            }
            if "absolute_sum" in _original_state(stage.node.signature).components
            else {}
        ),
        original_state__sum=table.original_state__sum.sum().fill_null(
            0.0 if value_type.name == "float64" else 0
        ),
        original_state__non_null_count=table.original_state__non_null_count.sum().fill_null(0),
        coverage__complete=table.coverage__complete.all().fill_null(True),
    )
    support = reduced.original_state__non_null_count
    defined = support > 0 if stage.node.method.name == "state_rollup" else ibis.literal(True)
    value_type = stage.node.value_type
    assert isinstance(value_type, (ScalarType, DecimalType, DurationType))
    target = canonical_layout(
        replace(
            stage.node.signature,
            parts=tuple(
                p for p in stage.node.signature.parts if not isinstance(p, CoordinateStatePart)
            ),
        ),
        has_value=True,
    )
    result = reduced.mutate(
        original_state__sum=reduced.original_state__sum.cast(
            "int64" if isinstance(value_type, DurationType) else value_type.name
        ),
        value=ibis.ifelse(
            defined,
            reduced.original_state__sum.cast(
                "int64" if isinstance(value_type, DurationType) else value_type.name
            ),
            ibis.null().cast("int64" if isinstance(value_type, DurationType) else value_type.name),
        ),
        cell_tag=ibis.ifelse(defined, "defined", "null"),
        cell_reason=ibis.ifelse(defined, ibis.null().cast("string"), "empty_contribution"),
    )
    return _reduction_subjects(result, stage.node.signature).select(*target.columns), target


def _contribution_rows(
    stage: SourceMethodStage,
    params: ObserveMetric | ObserveCount | ObserveWeightedMean,
    bindings: tuple[SourceBinding, ...],
    relations: tuple[LoweredRelation, ...],
    checks: list[LoweredCheck],
    *,
    prepared: bool = False,
    captured_columns: tuple[tuple[str, str], ...] = (),
) -> tuple[ir.Table, tuple[str, ...]]:
    owned_sources = {source.identity for source in stage.node.sources}
    by_entity = {
        binding.leaf.definition.ref.path: binding
        for binding in bindings
        if binding.leaf.identity in owned_sources
    }
    root_binding = by_entity[params.contribution.path]
    root = (
        root_binding.source.relation if prepared else _staged_source(root_binding, relations)
    ).view()
    if (
        isinstance(params, (ObserveMetric, ObserveWeightedMean))
        and root[params.amount_column].type().copy(nullable=True) != dt.dtype(params.amount_type)
        and not (
            params.amount_type.startswith("interval(")
            and root[params.amount_column].type().is_int64()
        )
    ):
        _fail("the exact contribution amount type", str(root[params.amount_column].type()))
    if prepared:
        from marivo.analysis.compiler.domain_preparation import version_checks

        version_checks(
            root,
            root_binding,
            tuple(key.coordinate.field for key in root_binding.layout.keys),
            stage.output,
            (root_binding.leaf.identity,),
            checks,
        )
    root_filters = _owner_predicate(root, params.filters, params.contribution.path)
    if root_filters is not None:
        root = root.filter(root_filters)
    if prepared and root_binding.leaf.definition.version is not None:
        from marivo.analysis.compiler.domain_preparation import _version, capture_time
        from marivo.analysis.compiler.source_time import source_time
        from marivo.analysis.core.domain_captures import fail

        if params.event.entity_ref.path != params.contribution.path:
            fail(
                "physical_qualification",
                "versioned contribution needs its own captured event time",
                stage="lowering",
            )
        instant, _ = source_time(
            capture_time(root[params.event.source_column]),
            params.event,
            boundary_timezone="UTC",
            read_timezone=params.event.timezone,
            engine="duckdb",
        )
        instant = instant.cast("timestamp('UTC')")
        checks.append(
            IntegrityCheck(
                stage.output,
                "r7.input_binding: contribution version at captured time",
                root.filter(~_version(root, root_binding, instant).fill_null(False)),
                (root_binding.leaf.identity,),
            )
        )
    fields: dict[str, ir.Value] = {
        **{name: root[column] for name, column in captured_columns},
        **(
            {
                f"candidate__key_{i}": root[key.coordinate.field]
                for i, key in enumerate(root_binding.layout.keys)
            }
            if prepared
            else {}
        ),
        "amount": root[params.amount_column]
        if isinstance(params, (ObserveMetric, ObserveWeightedMean))
        else ibis.literal(1, type="int64"),
        **(
            {f"next_key_{i}": root[source] for i, (source, _) in enumerate(params.path[0].keys)}
            if params.path
            else {
                f"member_{i}": root[key.coordinate.field]
                for i, key in enumerate(root_binding.layout.keys)
            }
        ),
    }
    if isinstance(params, (ObserveMetric, ObserveWeightedMean)) and params.amount_type.startswith(
        "interval("
    ):
        amount = root[params.amount_column]
        if isinstance(amount.type(), dt.Interval):
            from marivo.analysis.compiler.numeric_sql import duration_ticks, interval_part

            native = root_binding.source.relation
            raw_amount = native[params.amount_column]
            invalid = (
                (interval_part(ibis.literal("year"), raw_amount) != 0)
                | (interval_part(ibis.literal("month"), raw_amount) != 0)
                | (interval_part(ibis.literal("day"), raw_amount) != 0)
            )
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "fixed elapsed Duration without calendar components",
                    native.filter(invalid).select(violation=ibis.literal(1)),
                    (root_binding.leaf.identity,),
                )
            )
            fields["amount"] = duration_ticks(amount)
    if isinstance(params, ObserveMetric) and params.distinct_columns:
        fields["amount"] = ibis.struct({name: root[name] for name in params.distinct_columns})
    if isinstance(params, ObserveWeightedMean):
        if root[params.weight_column].type().copy(nullable=True) != dt.dtype(
            "int64" if params.amount_type.startswith("interval(") else params.amount_type
        ):
            _fail("int64 weight column", str(root[params.weight_column].type()))
        fields["weight"] = root[params.weight_column]
    if params.event.entity_ref.path == params.contribution.path:
        fields["event_time"] = root[params.event.source_column]
    for index, coordinate in enumerate(params.coordinates):
        if coordinate.entity_ref.path == params.contribution.path:
            fields["coordinate" if index == 0 else f"coordinate_{index}"] = root[
                coordinate.source_column
            ]
    rows = root.select(**fields)
    source_ids: tuple[str, ...] = (root_binding.leaf.identity,)
    for index, relationship in enumerate(params.path):
        binding = by_entity[relationship.to_entity_ref.path]
        destination = (
            binding.source.relation if prepared else _staged_source(binding, relations)
        ).view()
        destination_keys = tuple(key for _, key in relationship.keys)
        if prepared:
            from marivo.analysis.compiler.domain_preparation import version_checks

            version_checks(
                destination,
                binding,
                destination_keys,
                stage.output,
                _source_ids(source_ids, (binding.leaf.identity,)),
                checks,
            )
        predicates = [
            rows[f"next_key_{i}"] == destination[key] for i, key in enumerate(destination_keys)
        ]
        if prepared and binding.leaf.definition.version is not None:
            from marivo.analysis.compiler.domain_preparation import _version

            if "event_time" not in rows.columns:
                from marivo.analysis.core.domain_captures import fail

                fail(
                    "physical_qualification",
                    "versioned path before its event-time dependency is not qualified",
                    stage="lowering",
                )
            from marivo.analysis.compiler.domain_preparation import capture_time
            from marivo.analysis.compiler.source_time import source_time

            point, _ = source_time(
                capture_time(rows.event_time),
                params.event,
                boundary_timezone="UTC",
                read_timezone=params.event.timezone,
                engine="duckdb",
            )
            predicates.append(_version(destination, binding, point.cast("timestamp('UTC')")))
        joined = rows.left_join(
            destination,
            predicates,
        )
        source_ids = _source_ids(source_ids, (binding.leaf.identity,))
        checks.append(
            IntegrityCheck(
                stage.output,
                "complete contribution relationship mapping",
                joined.filter(reduce(or_, (destination[key].isnull() for key in destination_keys))),
                source_ids,
            )
        )
        selected = {name: rows[name] for name in rows.columns if not name.startswith("next_key_")}
        if relationship.to_entity_ref.path == params.event.entity_ref.path:
            selected["event_time"] = destination[params.event.source_column]
        for coordinate_index, coordinate in enumerate(params.coordinates):
            if coordinate.entity_ref.path == relationship.to_entity_ref.path:
                selected[
                    "coordinate" if coordinate_index == 0 else f"coordinate_{coordinate_index}"
                ] = destination[coordinate.source_column]
        if index + 1 < len(params.path):
            selected.update(
                {
                    f"next_key_{i}": destination[source]
                    for i, (source, _) in enumerate(params.path[index + 1].keys)
                }
            )
        else:
            selected.update(
                {
                    f"member_{i}": destination[key]
                    for i, key in enumerate(
                        tuple(key.coordinate.field for key in binding.layout.keys)
                        if prepared
                        else destination_keys
                    )
                }
            )
        hop_filters = _owner_predicate(destination, params.filters, relationship.to_entity_ref.path)
        if hop_filters is not None:
            joined = joined.filter(hop_filters)
        rows = joined.select(**selected)
    from marivo.analysis.compiler.domain_preparation import capture_time
    from marivo.analysis.compiler.source_time import source_time

    raw_time = (
        capture_time(rows.event_time)
        if prepared and isinstance(rows.event_time, ir.TimestampValue)
        else rows.event_time
    )
    normalized, _authority = source_time(
        raw_time,
        params.event,
        boundary_timezone="UTC",
        read_timezone=params.event.timezone,
        engine=root_binding.leaf.definition.shape.backend,
    )
    if prepared:
        rows = rows.mutate(__raw_event_time=raw_time)
    else:
        checks.append(
            TemporalCheck(
                stage.output,
                rows.select(raw_time=raw_time, normalized_time=normalized).distinct(),
                source_ids,
                params.event,
            )
        )
    rows = rows.mutate(event_time=normalized)
    return rows, source_ids


def _owner_predicate(
    source: ir.Table,
    filters: tuple[OccurrenceFilter, ...],
    owner_path: str,
) -> ir.BooleanValue | None:
    """Restrict one joined owner's rows to this occurrence's declared slice branch."""
    predicates = [
        _slice_predicate(source[item.dimension.source_column], item.operator, item.value)
        for item in filters
        if item.dimension.entity_ref.path == owner_path
    ]
    if not predicates:
        return None
    combined = predicates[0]
    for predicate in predicates[1:]:
        combined = combined & predicate
    return combined


def _slice_predicate(
    column: ir.Value,
    operator: str,
    value: object,
) -> ir.BooleanValue:
    """Apply the closed canonical slice operator set to one bound source column."""
    if operator == "==":
        return column == value
    if operator == "!=":
        return column != value
    if operator == "in":
        assert isinstance(value, tuple)
        return column.isin(list(value))
    if operator == "between":
        assert isinstance(value, tuple) and len(value) == 2
        return (column >= value[0]) & (column <= value[1])
    if operator == ">":
        return column > value
    if operator == ">=":
        return column >= value
    if operator == "<":
        return column < value
    if operator == "<=":
        return column <= value
    _fail("a closed slice operator", operator)


def _fold_samples(
    samples: ir.Table,
    targets: ir.Table,
    keys: tuple[str, ...],
    kind: str,
    *,
    duration: bool = False,
) -> ir.Table:
    """Fold already spatially aggregated instants and retain every sample."""
    token = (
        samples.sample_time.cast("string")
        + "~"
        + ibis.literal(
            "d:"
            if isinstance(samples.sample_sum.type(), dt.Decimal)
            else "i:"
            if samples.sample_sum.type().is_integer()
            else "f:"
        )
        + samples.sample_sum.cast("string")
        + "~"
        + samples.sample_count.cast("string")
    )
    state = (samples.group_by(*keys) if keys else samples).aggregate(
        original_state__samples=token.group_concat(";").fill_null("")
    )
    defined = samples.filter(samples.sample_count > 0)
    if kind in ("first", "last"):
        ordered = defined.mutate(
            sample_value=defined.sample_sum.first().over(
                ibis.window(
                    group_by=[defined[k] for k in keys],
                    order_by=defined.sample_time if kind == "first" else defined.sample_time.desc(),
                )
            )
        )
        finished = (ordered.group_by(*keys) if keys else ordered).aggregate(
            value=ordered.sample_value.max()
        )
    elif kind == "mean":
        grouped = defined.group_by(*keys) if keys else defined
        finished = grouped.aggregate(
            numeric_sum=defined.sample_sum.sum().cast(defined.sample_sum.type()),
            numeric_count=defined.count(),
        )
        finished, value = _division_value(
            finished, "numeric_sum", "numeric_count", duration=duration
        )
        finished = finished.select(*keys, value=value)
    else:
        finished = (defined.group_by(*keys) if keys else defined).aggregate(
            value=defined.sample_sum.min() if kind == "min" else defined.sample_sum.max()
        )
    if keys:
        table = targets.left_join(state, list(keys)).left_join(finished, list(keys))
        result = table.select(
            **{k: targets[k] for k in keys},
            value=finished.value,
            original_state__samples=state.original_state__samples.fill_null(""),
        )
    else:
        result = state.cross_join(finished)
    return result.mutate(
        original_state__fold_kind=ibis.literal(kind),
        cell_tag=ibis.ifelse(result.value.notnull(), "defined", "null"),
        cell_reason=ibis.ifelse(
            result.value.notnull(), ibis.null().cast("string"), "empty_contribution"
        ),
        coverage__complete=ibis.literal(True),
    )


def _observe(
    stage: SourceMethodStage,
    members: LoweredRelation,
    bindings: tuple[SourceBinding, ...],
    checks: list[LoweredCheck],
    admitted: GraphPlan,
    relations: tuple[LoweredRelation, ...],
) -> tuple[ir.Table, RelationLayout, tuple[str, ...]]:
    from datetime import datetime

    params = stage.node.parameters
    assert isinstance(params, (ObserveMetric, ObserveCount, ObserveWeightedMean))
    source, contribution_ids = _contribution_rows(stage, params, bindings, relations, checks)
    event_type = source.event_time.type()
    if (
        tuple(
            key.coordinate.field for key in members.layout.keys if key.coordinate.role != "anchor"
        )
        != (
            tuple(destination for _, destination in params.path[-1].keys)
            if params.path
            else tuple(
                c.field for c in members.node.signature.domain.instance_key if c.role == "identity"
            )
        )
        or not isinstance(event_type, (dt.Timestamp, dt.Date))
        or (
            isinstance(event_type, dt.Timestamp)
            and (
                event_type.timezone not in (None, "UTC", "Etc/UTC")
                or event_type.scale not in (None, 0, 3, 6, 9)
            )
        )
    ):
        _fail("exact observation key and UTC timestamp precision s/ms/us/ns", "schema drift")

    def bound(value: datetime) -> ir.Scalar:
        from marivo.datasource.timezone import parse_timezone

        grid = members.node.signature.domain.time_grid
        zone = (
            grid.boundary_timezone
            if params.grid_window and grid is not None
            else params.cumulative.boundary_timezone
            if params.cumulative is not None
            else params.window_timezone
        )
        normalized = (
            value.astimezone(parse_timezone(zone)[1]).date()
            if isinstance(event_type, dt.Date)
            else value.replace(tzinfo=None)
        )
        bound_type = (
            dt.Timestamp(timezone=event_type.timezone, scale=max(event_type.scale or 6, 6))
            if isinstance(event_type, dt.Timestamp)
            else event_type
        )
        return ibis.literal(normalized, type=bound_type)

    if params.start is not None and params.end is not None:
        start = bound(datetime.fromisoformat(params.start))
        end = bound(datetime.fromisoformat(params.end))
        source = source.filter((source.event_time >= start) & (source.event_time < end))
    if params.cumulative is not None and params.cumulative.grid_identity is None:
        window = params.cumulative.windows[0]
        condition = source.event_time < bound(datetime.fromisoformat(window.end))
        if window.start is not None:
            condition = condition & (
                source.event_time >= bound(datetime.fromisoformat(window.start))
            )
        source = source.filter(condition)
    mapping = members.expression
    keys = tuple(k.column for k in members.layout.keys)
    source_ids = _source_ids(members.source_ids, contribution_ids)
    target_fields = {key: mapping[key] for key in keys}
    if isinstance(params.target, GroupObservationTarget):
        cell = members.layout.cell
        if cell is None or str(mapping[cell.value].type()) != "string":
            _fail("the exact Group field value", "missing Group projection")
        checks.append(
            IntegrityCheck(
                stage.output,
                "defined member Group coordinates",
                mapping.filter(mapping[cell.tag] != "defined"),
                members.source_ids,
            )
        )
        target_fields = {"key_0": mapping[cell.value]}
    targets = mapping.select(**target_fields).distinct()
    member_keys = tuple(k.column for k in members.layout.keys if k.coordinate.role != "anchor")
    predicates = [source[f"member_{i}"] == mapping[key] for i, key in enumerate(member_keys)]
    grid = members.node.signature.domain.time_grid
    if params.grid_window:
        assert grid is not None
        time_key = next(k.column for k in members.layout.keys if k.coordinate.role == "anchor")
        predicates.append(
            ibis.cases(
                *(
                    (
                        (mapping[time_key] == cell.identity),
                        (source.event_time >= bound(cell.start))
                        & (source.event_time < bound(cell.end)),
                    )
                    for cell in grid.cells
                ),
                else_=False,
            )
        )
    if params.cumulative is not None and params.cumulative.grid_identity is not None:
        assert grid is not None
        time_key = next(k.column for k in members.layout.keys if k.coordinate.role == "anchor")
        conditions: list[tuple[ir.BooleanValue, ir.BooleanValue]] = []
        for window in params.cumulative.windows:
            condition = source.event_time < bound(datetime.fromisoformat(window.end))
            if window.start is not None:
                condition = condition & (
                    source.event_time >= bound(datetime.fromisoformat(window.start))
                )
            conditions.append((mapping[time_key] == window.key, condition))
        predicates.append(ibis.cases(*conditions, else_=False))
    joined = source.inner_join(mapping, predicates)
    target_keys = tuple(target_fields)
    values = joined.select(
        **target_fields,
        amount=source.amount,
        **(
            {"sample_time": source.event_time.cast("timestamp")}
            if isinstance(params, ObserveMetric) and params.fold is not None
            else {}
        ),
        **({"weight": source.weight} if isinstance(params, ObserveWeightedMean) else {}),
        **{
            ("coordinate" if i == 0 else f"coordinate_{i}"): source[
                "coordinate" if i == 0 else f"coordinate_{i}"
            ]
            for i in range(len(params.coordinates))
        },
    )
    if isinstance(params, (ObserveMetric, ObserveWeightedMean)) and params.amount_type == "float64":
        checks.append(
            IntegrityCheck(
                stage.output,
                "finite contribution amounts",
                values.filter(
                    (values.amount.abs() > float.fromhex("0x1.fffffffffffffp+1023"))
                    | (values.amount != values.amount)
                ),
                source_ids,
            )
        )
    target = canonical_layout(stage.node.signature, has_value=True)
    if isinstance(params, ObserveMetric) and params.method in DIRECT_ONLY_AGGREGATES:
        if params.method == "count_distinct":
            statistic = values.amount.nunique()
        elif params.method == "approx_count_distinct":
            statistic = values.amount.approx_nunique()
        elif params.method in ("median", "percentile"):
            statistic = values.amount.quantile(0.5 if params.quantile is None else params.quantile)
        else:
            statistic = values.amount.approx_quantile(
                0.5 if params.quantile is None else params.quantile
            )
        summed = values.group_by(*target_keys).aggregate(statistic=statistic)
        dense = targets.left_join(summed, list(target_keys))
        value = summed.statistic
        if params.method in ("count_distinct", "approx_count_distinct"):
            value = value.fill_null(0).cast("int64")
        elif params.method in ("approx_median", "approx_percentile"):
            value = value.cast("float64")
        table = dense.select(
            **{key: targets[key] for key in target_keys},
            **{f"subject__key_{i}": targets[key] for i, key in enumerate(member_keys)},
            value=value,
            cell_tag=value.notnull().ifelse("defined", "null"),
            cell_reason=value.notnull().ifelse(ibis.null().cast("string"), "empty_contribution"),
            coverage__complete=ibis.literal(True),
        )
    elif isinstance(params, ObserveWeightedMean):
        input_type = dt.dtype(
            "int64" if params.amount_type.startswith("interval(") else params.amount_type
        )
        product_type = (
            str(dt.Decimal(38, (input_type.scale or 0) * 2))
            if isinstance(input_type, dt.Decimal)
            else str(input_type)
        )
        weight_type = (
            str(dt.Decimal(38, input_type.scale))
            if isinstance(input_type, dt.Decimal)
            else str(input_type)
        )
        if params.amount_type.startswith("interval("):
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "nonnegative Duration weights",
                    values.filter(values.weight < 0),
                    source_ids,
                )
            )
        pair = values.amount.notnull() & values.weight.notnull()
        summed = values.group_by(*target_keys).aggregate(
            absolute_weight_sum=pair.ifelse(values.weight.abs(), 0).sum().fill_null(0),
            absolute_weighted_numerator=pair.ifelse((values.amount * values.weight).abs(), 0)
            .sum()
            .fill_null(0)
            if values.amount.type().is_floating()
            else ibis.literal(0).cast("int64"),
            weighted_numerator=pair.ifelse(
                values.amount.cast(weight_type) * values.weight.cast(weight_type), 0
            )
            .cast(product_type)
            .sum()
            .fill_null(0)
            .cast(product_type),
            weight_sum=pair.ifelse(values.weight, 0).sum().fill_null(0).cast(weight_type),
            non_null_pair_count=pair.ifelse(1, 0).sum().fill_null(0).cast("int64"),
            row_count=values.count().cast("int64"),
        )
        dense = targets.left_join(summed, list(target_keys))
        table = dense.select(
            **{key: targets[key] for key in target_keys},
            **{f"subject__key_{i}": targets[key] for i, key in enumerate(member_keys)},
            **{
                f"original_state__{name}": summed[name]
                .fill_null(0)
                .cast(
                    product_type
                    if name in ("weighted_numerator", "absolute_weighted_numerator")
                    else weight_type
                    if name in ("weight_sum", "absolute_weight_sum")
                    else "int64"
                )
                for name in _original_state(stage.node.signature).components
            },
        )
        table = _weighted_finish(
            table,
            replace(
                target,
                parts=tuple(p for p in target.parts if not isinstance(p.part, CoordinateStatePart)),
            ),
            duration=isinstance(stage.node.value_type, DurationType),
        )
    else:
        wide_decimal_sum = (
            isinstance(params, ObserveMetric)
            and params.method in ("sum", "mean")
            and params.amount_type.startswith("decimal(")
            and isinstance(stage.implementation.key.shape, SourceShape)
            and stage.implementation.key.shape.backend == "clickhouse"
        )
        if wide_decimal_sum:
            assert isinstance(params, ObserveMetric)
            amount_dtype = dt.dtype(params.amount_type)
            assert isinstance(amount_dtype, dt.Decimal)
            values = values.mutate(amount=values.amount.cast(dt.Decimal(76, amount_dtype.scale)))
        summed = values.group_by(*target_keys).aggregate(
            absolute_sum=values.amount.abs().sum().fill_null(0.0)
            if values.amount.type().is_floating()
            else ibis.literal(0).cast("float64"),
            state_sum=values.amount.min()
            if isinstance(params, ObserveMetric) and params.method == "min"
            else values.amount.max()
            if isinstance(params, ObserveMetric) and params.method == "max"
            else values.amount.sum(),
            support=values.amount.count(),
            rows=values.count(),
        )
        dense = targets.left_join(summed, list(target_keys))
        amount_type = (
            params.amount_type
            if isinstance(params, ObserveMetric) and not params.amount_type.startswith("interval(")
            else "int64"
        )
        if isinstance(stage.node.value_type, DecimalType) and isinstance(params, ObserveMetric):
            amount_type = (
                str(dt.Decimal(38, dt.dtype(params.amount_type).scale))
                if params.method in ("sum", "mean")
                else params.amount_type
            )
        if wide_decimal_sum:
            output_dtype = dt.dtype(amount_type)
            assert isinstance(output_dtype, dt.Decimal)
            assert output_dtype.precision is not None and output_dtype.scale is not None
            digits = "9" * (output_dtype.precision - output_dtype.scale) or "0"
            if output_dtype.scale:
                digits += "." + "9" * output_dtype.scale
            sum_in_range = summed.state_sum.between(
                ibis.literal(Decimal("-" + digits), type=output_dtype),
                ibis.literal(Decimal(digits), type=output_dtype),
            )
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "Decimal sum within declared precision and scale",
                    summed.filter(~sum_in_range).select(*target_keys),
                    source_ids,
                )
            )
        bounded_sum = (
            sum_in_range.ifelse(summed.state_sum, ibis.null().cast(summed.state_sum.type()))
            if wide_decimal_sum
            else summed.state_sum
        )
        total = bounded_sum.fill_null(0.0 if amount_type == "float64" else 0).cast(amount_type)
        support = summed.support.fill_null(0).cast("int64")
        defined = (
            support > 0
            if isinstance(params, ObserveMetric) and params.metric.empty_rule == "null"
            else ibis.literal(True)
        )
        table = dense.select(
            **{key: targets[key] for key in target_keys},
            value=ibis.ifelse(defined, total, ibis.null().cast(amount_type)),
            cell_tag=ibis.ifelse(defined, "defined", "null"),
            cell_reason=ibis.ifelse(defined, ibis.null().cast("string"), "empty_contribution"),
            original_state__count=support,
            **{f"subject__key_{i}": targets[key] for i, key in enumerate(member_keys)},
            **(
                {"original_state__absolute_sum": summed.absolute_sum.fill_null(0.0)}
                if "absolute_sum" in _original_state(stage.node.signature).components
                else {}
            ),
            original_state__sum=total,
            original_state__min=total,
            original_state__max=total,
            original_state__non_null_count=support,
            original_state__row_count=summed.rows.fill_null(0).cast("int64"),
            coverage__complete=ibis.literal(True),
        )
    if isinstance(params, ObserveMetric) and params.method == "mean":
        table = _mean_finish(
            table,
            replace(
                target,
                parts=tuple(p for p in target.parts if not isinstance(p.part, CoordinateStatePart)),
            ),
            duration=isinstance(stage.node.value_type, DurationType),
        )
    if params.coordinates:
        amount_type = (
            params.amount_type
            if isinstance(params, (ObserveMetric, ObserveWeightedMean))
            else "int64"
        )
        if amount_type.startswith("interval("):
            amount_type = "int64"
        if amount_type.startswith("decimal("):
            amount_type = str(dt.Decimal(38, dt.dtype(amount_type).scale))
        part = next(p for p in stage.node.signature.parts if isinstance(p, CoordinateStatePart))
        checks.append(
            IntegrityCheck(
                stage.output,
                "complete string contribution coordinates",
                values.filter(reduce(or_, (values[name].isnull() for name in part.columns))),
                source_ids,
            )
        )
        if isinstance(params, ObserveWeightedMean):
            pair = values.amount.notnull() & values.weight.notnull()
            grouped = values.group_by(*target_keys, *part.columns).aggregate(
                absolute_weight_sum=pair.ifelse(values.weight.abs(), 0).sum().fill_null(0),
                absolute_weighted_numerator=pair.ifelse((values.amount * values.weight).abs(), 0)
                .sum()
                .fill_null(0)
                if values.amount.type().is_floating()
                else ibis.literal(0).cast("int64"),
                weighted_numerator=pair.ifelse(values.amount * values.weight, 0)
                .sum()
                .fill_null(0)
                .cast(product_type),
                weight_sum=pair.ifelse(values.weight, 0).sum().fill_null(0).cast(weight_type),
                non_null_pair_count=pair.ifelse(1, 0).sum().fill_null(0).cast("int64"),
                row_count=values.count().cast("int64"),
            )
        else:
            grouped = values.group_by(*target_keys, *part.columns).aggregate(
                absolute_sum=values.amount.abs().sum().fill_null(0).cast(amount_type)
                if values.amount.type().is_floating()
                else ibis.literal(0).cast(amount_type),
                sum=values.amount.sum().fill_null(0).cast(amount_type),
                min=values.amount.min().fill_null(0).cast(amount_type),
                max=values.amount.max().fill_null(0).cast(amount_type),
                non_null_count=values.amount.count().cast("int64"),
                count=values.count().cast("int64"),
                row_count=values.count().cast("int64"),
            )
        cells = ibis.struct(
            {
                **{name: grouped[name] for name in part.columns},
                **{name: grouped[name] for name in part.components},
            }
        )
        nested = grouped.group_by(*target_keys).aggregate(
            coordinate_state__groups=cells.collect(
                order_by=[grouped[name] for name in part.columns]
            )
        )
        combined = table.left_join(nested, list(target_keys))
        table = combined.select(
            *[table[name] for name in table.columns],
            coordinate_state__groups=nested.coordinate_state__groups.fill_null(
                ibis.literal([], type=coordinate_state_type(part))
            ),
        )
    if isinstance(params, ObserveMetric) and params.fold is not None:
        sample_type = dt.dtype(
            "int64" if params.amount_type.startswith("interval(") else params.amount_type
        )
        if isinstance(sample_type, dt.Decimal):
            sample_type = dt.Decimal(38, sample_type.scale)
        samples = values.group_by(*target_keys, "sample_time").aggregate(
            sample_sum=values.amount.sum().cast(sample_type).fill_null(0),
            sample_count=values.amount.count(),
        )
        table = _fold_samples(
            samples,
            targets,
            target_keys,
            params.fold,
            duration=isinstance(stage.node.value_type, DurationType),
        )
        table = _reduction_subjects(table, stage.node.signature)
    table = table.select(*target.columns)
    if grid is not None:
        time_key = next(k.column for k in members.layout.keys if k.coordinate.role == "anchor")
        actual = joined.group_by(mapping[time_key].name("time_key")).aggregate(
            actual=joined.count()
        )
        unique_mapping = mapping.select(*member_keys, time_key).distinct()
        expected_predicates: list[ir.BooleanValue] = []
        for predicate in predicates:
            rewritten = predicate.op().replace({mapping.op(): unique_mapping.op()}).to_expr()
            assert isinstance(rewritten, ir.BooleanValue)
            expected_predicates.append(rewritten)
        selected = source.inner_join(unique_mapping, expected_predicates)
        expected = selected.group_by(unique_mapping[time_key].name("time_key")).aggregate(
            expected=selected.count()
        )
        partition = expected.left_join(actual, "time_key").select(
            expected=expected.expected, actual=actual.actual.fill_null(0)
        )
    else:
        actual = joined.aggregate(actual=joined.count())
        selected = source.semi_join(mapping, predicates)
        expected = selected.aggregate(expected=selected.count())
        partition = actual.cross_join(expected)
    actual_coverage = table.aggregate(actual=table.count())
    expected_coverage = targets.aggregate(expected=targets.count())
    coverage = actual_coverage.cross_join(expected_coverage)
    for requirement in admitted.checks:
        if requirement.node_id != stage.node.identity:
            continue
        check_id = requirement.obligation.check_id
        if check_id == "source.contribution_partition@v1":
            violations = partition.filter(partition.actual != partition.expected)
        elif check_id in ("source.complete_coverage@v1", "source.calendar_members@v1"):
            violations = coverage.filter(coverage.actual != coverage.expected)
        elif check_id == "source.calendar_contributions@v1":
            violations = joined.filter(source.event_time.isnull()).select(**target_fields)
        else:
            continue
        checks.append(SemanticCheck(requirement, violations, source_ids))
    return table, target, source_ids


def _fact_relations(
    node: Node, obligation: Obligation, relations: tuple[LoweredRelation, ...]
) -> tuple[tuple[LoweredRelation, ...], ...]:
    """Keep each originating check's ordered inputs separate across shared paths."""
    by_identity = {r.node.identity: r for r in relations}
    fact = obligation.fact
    if isinstance(node, MethodNode):
        if (
            isinstance(node.parameters, BindProject)
            and obligation.check_id == "source.single_value@v1"
            and fact in node.derivation.pre
        ):
            return ((by_identity[node.identity],),)
        immediate = tuple(edge.node for edge in node.inputs)
        if fact in node.derivation.pre and (
            fact.inputs
            == tuple(FactInput(n.signature.domain, n.signature.quantity) for n in immediate)
            or (
                not fact.inputs
                and len(immediate) == 1
                and (
                    immediate[0].signature.quantity.definition_id
                    if immediate[0].signature.quantity is not None
                    else immediate[0].signature.domain.definition_id
                )
                == fact.subject_id
            )
        ):
            return (tuple(by_identity[n.identity] for n in immediate),)
        inherited = tuple(
            dict.fromkeys(
                group
                for n in immediate
                if obligation in n.signature.obligations
                for group in _fact_relations(n, obligation, relations)
            )
        )
        if inherited:
            return inherited
    elif isinstance(node, SourceLeaf) and obligation in node.signature.obligations:
        return ((by_identity[node.identity],),)
    _fail("an obligation bound to its originating graph inputs", fact.subject_id)


def lower(
    admitted: GraphPlan, *, bindings: tuple[SourceBinding, ...], registry: MethodRegistry = REGISTRY
) -> LoweredPlan:
    """Lower a revalidated plan without I/O, SQL, route retries or fulfilled proofs."""
    routes = tuple(RouteChoice(p.node_id, p.key.route) for p in admitted.physical_requirements)
    if plan(admitted.root, routes=routes, registry=registry) != admitted:
        _fail("an unchanged admitted plan", "altered stages or obligations")
    expected = admitted.classification.sources
    if len(bindings) != len(expected) or {id(b.leaf) for b in bindings} != {
        id(s) for s in expected
    }:
        _fail(
            "exactly one binding for each reachable source leaf",
            "missing, duplicate or extra binding",
        )
    if bindings and len({id(b.source._owner) for b in bindings}) != 1:
        _fail("one R1 SourceSession owner", "foreign bindings")
    results: dict[str, LoweredRelation] = {}
    stages: list[LoweredStage] = []
    checks: list[LoweredCheck] = []
    layouts: dict[str, RelationLayout] = {}
    for stage in admitted.stages:
        part_expressions: list[tuple[str, ir.Table]] = []
        part_source_ids: tuple[tuple[str, tuple[str, ...]], ...] = ()
        if isinstance(stage, ArtifactReadStage):
            layout = canonical_layout(
                stage.leaf.signature, has_value=stage.leaf.signature.quantity is not None
            )
            layouts[stage.output] = layout
            stages.append(stage)
            continue
        if isinstance(stage, LocalMethodStage):
            admit(stage.implementation, stage.node.parameters)
            if len(stage.inputs) not in (1, 2) and not isinstance(
                stage.node.parameters,
                (
                    AssociationFit,
                    AssociationRead,
                    ForecastFit,
                    ForecastRead,
                    TimeRuns,
                    TimeRunRead,
                    DeviationFit,
                    AttributionDerive,
                    PartsTransport,
                    ReferenceDerive,
                    DisplayRank,
                    DisplayTable,
                ),
            ):
                _fail("registered row, Association, or predicate inputs", repr(stage.inputs))
            if len(stage.inputs) == 2 and not (
                isinstance(
                    stage.node.parameters,
                    (
                        AssociationScore,
                        AssociationFit,
                        AssociationRead,
                        ForecastFit,
                        ForecastRead,
                        TimeRuns,
                        TimeRunRead,
                        DeviationFit,
                        AttachCategory,
                        CompleteGroups,
                        AnchorRetention,
                        RetentionBySubject,
                        AnchorBind,
                        AnchorObserve,
                        PreparedObservation,
                        HistoryView,
                        FunnelReduce,
                        FunnelCompare,
                        FunnelAttribute,
                    ),
                )
                or (
                    isinstance(stage.node.parameters, PartsTransport)
                    and stage.node.parameters.external_predicate
                )
                or (
                    isinstance(
                        stage.node.parameters,
                        (
                            HistoryReplay,
                            AttributionDerive,
                            CellDerive,
                            ReferenceDerive,
                            DisplayRank,
                            DisplayTable,
                        ),
                    )
                )
            ):
                _fail("a registered two-input local method", repr(stage.inputs))
            output_layout = canonical_layout(
                stage.node.signature,
                has_value=not isinstance(stage.node.parameters, PartsTransport)
                or stage.node.parameters.keep_quantity,
            )
            if (
                isinstance(stage.node.parameters, CompleteGroups)
                and stage.node.signature.quantity is None
            ):
                output_layout = canonical_layout(stage.node.signature, has_value=False)
            if isinstance(
                stage.node.parameters,
                (
                    AnchorBind,
                    HistoryReplay,
                    OccurrencePrepare,
                    JourneyMatch,
                    JourneyDuration,
                    JourneyCompleted,
                ),
            ):
                output_layout = canonical_layout(stage.node.signature, has_value=False)
            if (
                isinstance(stage.node.parameters, HistoryView)
                and stage.node.signature.quantity is None
            ):
                output_layout = canonical_layout(stage.node.signature, has_value=False)
            if (
                isinstance(stage.node.parameters, MapCorrespond)
                and stage.node.parameters.mode == "subjects"
            ):
                output_layout = canonical_layout(stage.node.signature, has_value=False)
            if isinstance(stage.node.parameters, DisplayTable):
                output_layout = replace(
                    canonical_layout(stage.node.signature, has_value=False),
                    extras=tuple(
                        f"column_{i}__{f}"
                        for i in range(len(stage.node.parameters.labels))
                        for f in ("value", "cell_tag", "cell_reason")
                    ),
                )
            if isinstance(stage.node.parameters, AssociationScore):
                output_layout = replace(output_layout, extras=("status",))
            stages.append(
                LoweredLocal(stage, tuple(layouts[item] for item in stage.inputs), output_layout)
            )
            layouts[stage.output] = output_layout
            if (
                isinstance(
                    stage.node.parameters,
                    (AttributionDerive, CellDerive, ReferenceDerive, DisplayRank, DisplayTable),
                )
                and len(stage.inputs) == 1
                and stage.inputs[0] in results
            ):
                predecessor = results[stage.inputs[0]]
                results[stage.output] = replace(predecessor, output=stage.output)
            continue
        if isinstance(stage, SourceInputStage):
            bound = next(b for b in bindings if b.leaf is stage.leaf)
            if bound.source.facts.source_identity != stage.leaf.identity:
                _fail("R1 binding for this exact leaf identity", bound.source.facts.source_identity)
            shape = stage.leaf.definition.shape
            if not (
                (shape.form == "table" and isinstance(bound.source.source, TableSourceIR))
                or (shape.form == "parquet" and isinstance(bound.source.source, ParquetSourceIR))
            ):
                _fail("the declared physical source form", type(bound.source.source).__name__)
            if not bound.source.relation.schema().to_pyarrow().equals(bound.source.facts.schema):
                _fail("unchanged R1 physical schema", "schema mismatch")
            if not isinstance(stage.leaf.value_type, ScalarType):
                _fail("qualified scalar source value", repr(stage.leaf.value_type))
            _validate_layout(
                bound.source.relation,
                bound.layout,
                stage.leaf.signature,
                stage.leaf.value_type.name,
            )
            layout = canonical_layout(stage.leaf.signature, has_value=bound.layout.cell is not None)
            captured_dependency = any(
                isinstance(node, MethodNode)
                and isinstance(
                    node.parameters,
                    (
                        OccurrencePrepare,
                        PreparedObservation,
                        AnchorObserve,
                        FunnelAxesPrepare,
                        HistoryAxesPrepare,
                    ),
                )
                and stage.leaf in node.sources
                for node in topology(admitted.root)
            )
            population_input = any(
                isinstance(node, MethodNode)
                and any(edge.node is stage.leaf for edge in node.inputs)
                for node in topology(admitted.root)
            )
            raw = (
                bound.source.relation
                if captured_dependency
                and not population_input
                and stage.leaf.signature.domain.version_selection is None
                else select_version(
                    bound.source.relation,
                    stage.leaf.definition.version,
                    stage.leaf.signature.domain.version_selection,
                )
            )
            raw_fields = _source_fields(admitted, bound)
            extras = tuple(f"source__{raw.columns.index(column)}" for column in raw_fields)
            selected_columns = tuple(
                raw[old].name(new)
                for old, new in zip(bound.layout.columns, layout.columns, strict=True)
            )
            layout = replace(layout, extras=extras)
            table = (
                raw.select(
                    *selected_columns,
                    *(
                        raw[column].name(alias)
                        for column, alias in zip(raw_fields, extras, strict=True)
                    ),
                )
                .select(*layout.columns)
                .view()
            )
            node: Node = stage.leaf
            source_ids: tuple[str, ...] = (stage.leaf.identity,)
            cell_reasons = bound.cell_reasons
        else:
            admit(stage.implementation, stage.node.parameters)
            if stage.operation not in ("ibis", "prepare"):
                _fail("a qualified preparation consumer", stage.operation)
            params = stage.node.parameters
            inputs = tuple(
                results[i]
                for i in stage.inputs[
                    : 1
                    if isinstance(params, PreparedObservation)
                    or (isinstance(params, AnchorObserve) and stage.operation == "prepare")
                    else len(stage.node.inputs)
                ]
            )
            source_ids = inputs[0].source_ids
            if isinstance(params, AnchorBind):
                from marivo.analysis.compiler.anchors import bind as lower_anchor_bind

                table, layout, source_ids = lower_anchor_bind(stage, inputs[0])
                cell_reasons = ()
            elif isinstance(params, AnchorRetention):
                from marivo.analysis.compiler.retention import lower as lower_retention

                table, layout, source_ids = lower_retention(stage, inputs, checks)
                cell_reasons = (("unknown", ("insufficient_followup",)),)
            elif isinstance(params, AnchorObserve):
                from marivo.analysis.compiler.anchors import observe as lower_anchor_observe

                table, layout, source_ids = lower_anchor_observe(
                    stage, inputs, bindings, tuple(results.values()), checks
                )
                cell_reasons = (
                    ("null", ("empty_contribution",)),
                    ("undefined", ("zero_denominator",)),
                )
            elif isinstance(params, HistoryAxesPrepare):
                from marivo.analysis.compiler.history_axes import lower_axes as lower_history_axes

                table, layout, source_ids = lower_history_axes(stage, inputs[0], bindings, checks)
                cell_reasons = ()
            elif isinstance(params, FunnelAxesPrepare):
                from marivo.analysis.compiler.funnel_axes import lower_axes

                table, layout, source_ids = lower_axes(stage, inputs[0], bindings, checks)
                cell_reasons = ()
            elif isinstance(params, OccurrencePrepare):
                from marivo.analysis.compiler.domain_preparation import lower_occurrences

                table, layout, source_ids = lower_occurrences(stage, inputs[0], bindings, checks)
                cell_reasons = ()
            elif isinstance(params, PreparedObservation):
                from marivo.analysis.compiler.domain_preparation import lower_candidates

                table, layout, source_ids = lower_candidates(
                    stage, inputs[0], bindings, tuple(results.values()), checks
                )
                cell_reasons = ()
            elif isinstance(params, AttributionDerive):
                from marivo.analysis.compiler.graph_attribution import (
                    prepare as prepare_attribution,
                )

                table, layout = prepare_attribution(stage, inputs, checks, part_expressions)
                source_ids = _source_ids(*(item.source_ids for item in inputs))
                cell_reasons = ()
            elif isinstance(params, (DisplayRank, DisplayTable)):
                from marivo.analysis.compiler.graph_display import prepare

                table, layout = prepare(stage, inputs, checks)
                part_expressions.extend(
                    (role, expression)
                    for role, expression in inputs[0].part_expressions
                    if isinstance(params, DisplayRank)
                    and role
                    not in {
                        part.role
                        for part in inputs[0].node.signature.parts
                        if isinstance(part, DisplayPart)
                    }
                )
                source_ids = _source_ids(*(item.source_ids for item in inputs))
                cell_reasons = inputs[0].cell_reasons
            elif isinstance(params, ReferenceDerive):
                table, layout = _reference(stage, inputs, part_expressions)
                part_source_ids = tuple(
                    (
                        part.role,
                        inputs[
                            1
                            if part.role in ("fixed_reference", "strata")
                            else 2
                            if part.role == "reference_proof" and params.kind == "share"
                            else 0
                        ].source_ids,
                    )
                    for part in stage.node.signature.parts
                    if isinstance(part, ReferenceStatePart)
                )
                source_ids = _source_ids(*(item.source_ids for item in inputs))
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif isinstance(params, PartsTransport):
                part_source_ids = inputs[0].part_source_ids
                part_expressions.extend(
                    (role, expression)
                    for role, expression in inputs[0].part_expressions
                    if role
                    in (
                        "fixed_reference",
                        "reference_proof",
                        "strata",
                        "stratum_values",
                        "basis",
                        "allocation",
                        "reconciliation",
                        "current_endpoint",
                        "baseline_endpoint",
                        "ranking_domain",
                        "partitions",
                        "ordering",
                    )
                )
                for part in inputs[0].layout.parts:
                    if isinstance(part.part, DisplayPart) and part.part.independent:
                        role = part.part.role
                        if not any(name == role for name, _ in part_expressions):
                            part_expressions.append(
                                (
                                    role,
                                    inputs[0].expression.select(
                                        *(key.column for key in inputs[0].layout.keys),
                                        *(component.column for component in part.columns),
                                    ),
                                )
                            )
                table, layout = _transport(stage, inputs[0], checks, inputs[1:], part_expressions)
                source_ids = _source_ids(*(item.source_ids for item in inputs))
                cell_reasons = () if params.mode == "cohort" else inputs[0].cell_reasons
            elif isinstance(params, (ObserveMetric, ObserveCount, ObserveWeightedMean)):
                table, layout, source_ids = _observe(
                    stage, inputs[0], bindings, checks, admitted, tuple(results.values())
                )
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif isinstance(params, BindProject):
                table, layout = _bind(stage, inputs[0], bindings, checks, tuple(results.values()))
                source_ids = _source_ids(
                    source_ids, tuple(leaf.identity for leaf in stage.node.sources)
                )
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif isinstance(params, MapCorrespond):
                table, layout = _map(stage, inputs, checks)
                cell_reasons = ()
                if params.mode == "union_keys":
                    source_ids = _source_ids(*(input.source_ids for input in inputs))
            elif isinstance(params, OriginalRatio) and len(inputs) == 2:
                table, layout = _original_ratio(stage, (inputs[0], inputs[1]), checks, admitted)
                source_ids = _source_ids(*(item.source_ids for item in inputs))
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif isinstance(params, OccurrenceCombine) and len(inputs) >= 2:
                table, layout = _occurrence_combine(stage, inputs, checks, admitted)
                source_ids = _source_ids(*(item.source_ids for item in inputs))
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif isinstance(params, TimeProduct):
                table, layout = _time_product(stage, inputs[0])
                cell_reasons = ()
            elif isinstance(params, CompleteGroups):
                table, layout = _complete_groups(stage, inputs, checks)
                cell_reasons = inputs[0].cell_reasons
                source_ids = _source_ids(*(item.source_ids for item in inputs))
            elif isinstance(params, AttachCategory):
                table, layout = _attach_category(stage, inputs, checks)
                cell_reasons = inputs[0].cell_reasons
                source_ids = _source_ids(*(item.source_ids for item in inputs))
            elif isinstance(params, OriginalReduce):
                table, layout = (
                    _fold_rollup(stage, inputs[0], checks)
                    if params.method == "fold"
                    else _original_sum(stage, inputs[0])
                )
                from marivo.analysis.compiler.graph_attribution import retain_partition

                table, layout = retain_partition(stage, inputs[0], table, layout)
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif isinstance(params, RowState):
                table, layout = _count(stage, inputs[0])
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif isinstance(params, AssociationScore) and len(inputs) == 2:
                table, layout = _spearman(stage, (inputs[0], inputs[1]), checks)
                source_ids = _source_ids(*(item.source_ids for item in inputs))
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif isinstance(params, CellDerive) and len(inputs) == 2:
                table, layout = _difference(stage, (inputs[0], inputs[1]), checks)
                source_ids = _source_ids(*(item.source_ids for item in inputs))
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            else:
                _fail("a registered source lowerer", str(stage.node.method))
            node = stage.node
        relation = LoweredRelation(
            stage.output,
            node,
            table,
            layout,
            source_ids,
            cell_reasons,
            tuple(part_expressions),
            part_source_ids,
            tuple(item.cell_reasons for item in inputs)
            if isinstance(node, MethodNode) and isinstance(node.parameters, DisplayTable)
            else (),
        )
        results[stage.output] = relation
        layouts[stage.output] = layout
        stages.append(relation)
        auxiliary = isinstance(node, SourceLeaf) and not any(
            isinstance(candidate, MethodNode)
            and any(edge.node is node for edge in candidate.inputs)
            for candidate in topology(admitted.root)
        )
        if not auxiliary:
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "unique non-null complete identity",
                    _key_violations(
                        table,
                        layout,
                        allow_empty=isinstance(node, MethodNode)
                        and (
                            isinstance(node.parameters, (CellDerive, DisplayRank, DisplayTable))
                            or (
                                isinstance(node.parameters, PartsTransport)
                                and (
                                    node.parameters.mode in ("where", "limit")
                                    or node.parameters.display_view is not None
                                )
                            )
                        )
                        and not layout.keys,
                    ),
                    source_ids,
                )
            )
        if layout.cell is not None:
            checks.append(
                IntegrityCheck(
                    stage.output,
                    "valid four-state Cell encoding",
                    _cell_violations(table, layout.cell),
                    source_ids,
                )
            )
    for requirement in admitted.checks:
        existing = next(
            (
                check
                for check in checks
                if isinstance(check, SemanticCheck)
                and requirement.obligation.check_id
                in (
                    "source.contribution_partition@v1",
                    "source.complete_coverage@v1",
                    "source.calendar_members@v1",
                    "source.calendar_contributions@v1",
                )
                and check.requirement.obligation.fact == requirement.obligation.fact
                and check.requirement.obligation.check_id == requirement.obligation.check_id
            ),
            None,
        )
        if existing is not None:
            if existing.requirement != requirement:
                checks.append(replace(existing, requirement=requirement))
            continue
        if admitted.classification.kind == "artifact":
            checks.append(requirement)
            continue
        owner = next(
            node for node in topology(admitted.root) if node.identity == requirement.node_id
        )
        if isinstance(owner, MethodNode) and (
            isinstance(owner.parameters, PreparedObservation)
            or (
                isinstance(owner.parameters, (OriginalReduce, CellDerive, AttributionDerive))
                and any(
                    isinstance(stage, LocalMethodStage)
                    and stage.node.identity == owner.identity
                    and isinstance(stage.implementation.qualification, Qualified)
                    and stage.implementation.qualification.implementation_id.startswith("r93.c09.")
                    for stage in admitted.stages
                )
            )
            or (
                isinstance(owner.parameters, RowState)
                and isinstance(owner.inputs[0].node, MethodNode)
                and isinstance(owner.inputs[0].node.parameters, PreparedObservation)
            )
            or (
                isinstance(owner.parameters, RowState)
                and owner.inputs[0].node.signature.domain.kind in ("journey", "interval")
                and requirement.obligation.check_id == "source.finite_numeric@v1"
            )
        ):
            checks.append(requirement)
            continue
        for inputs in _fact_relations(owner, requirement.obligation, tuple(results.values())):
            check_id = requirement.obligation.check_id
            source_ids = _source_ids(*(input.source_ids for input in inputs))
            if check_id == "source.unique_key@v1":
                violations = _key_violations(
                    inputs[0].expression,
                    inputs[0].layout,
                    allow_empty=isinstance(owner, MethodNode)
                    and isinstance(owner.parameters, CellDerive),
                )
                for other in inputs[1:]:
                    violations = violations.union(
                        _key_violations(
                            other.expression,
                            other.layout,
                            allow_empty=isinstance(owner, MethodNode)
                            and isinstance(owner.parameters, CellDerive),
                        ),
                        distinct=False,
                    )
            elif check_id == "source.single_value@v1":
                output = inputs[0]
                violations = _key_violations(output.expression, output.layout)
                source_ids = output.source_ids
            elif check_id == "source.exact_pairing@v1" and len(inputs) >= 2:
                pairing_owner = next(
                    (
                        relation.node
                        for relation in results.values()
                        if isinstance(relation.node, MethodNode)
                        and isinstance(relation.node.parameters, CellDerive)
                        and requirement.obligation.fact in relation.node.derivation.pre
                        and tuple(edge.node.identity for edge in relation.node.inputs)
                        == tuple(item.node.identity for item in inputs)
                    ),
                    None,
                )
                paired_right = (
                    _mapped_period_input(inputs[1], pairing_owner.parameters)
                    if pairing_owner is not None
                    and isinstance(pairing_owner.parameters, CellDerive)
                    else inputs[1]
                )
                violations = _pair_violations(inputs[0], paired_right)
                for other in inputs[2:]:
                    violations = violations.union(
                        _pair_violations(inputs[0], other), distinct=False
                    )
            elif check_id == "source.cell_policy@v1" and inputs[0].layout.cell is not None:
                violations = _cell_violations(inputs[0].expression, inputs[0].layout.cell)
                source_ids = inputs[0].source_ids
            elif check_id == "source.group_mapping@v1" and inputs[0].layout.cell is not None:
                group_cell = inputs[0].layout.cell
                violations = (
                    inputs[0]
                    .expression.filter(inputs[0].expression[group_cell.tag] != "defined")
                    .select(*(item.column for item in inputs[0].layout.keys))
                )
                source_ids = inputs[0].source_ids
            elif check_id == "source.finite_numeric@v1" and inputs[0].layout.cell is not None:
                numeric_inputs = (
                    inputs
                    if isinstance(owner, MethodNode)
                    and isinstance(owner.parameters, (AssociationScore, CellDerive))
                    else inputs[:1]
                )
                violations = None
                for current in numeric_inputs:
                    cell = current.layout.cell
                    assert cell is not None
                    if (
                        isinstance(owner, MethodNode)
                        and isinstance(owner.parameters, CellDerive)
                        and owner.parameters.pairing == "keep"
                    ):
                        other = (
                            numeric_inputs[1] if current is numeric_inputs[0] else numeric_inputs[0]
                        )
                        if current is numeric_inputs[1]:
                            current = _mapped_period_input(current, owner.parameters)
                        else:
                            other = _mapped_period_input(other, owner.parameters)
                        current_keys = tuple(k.column for k in current.layout.keys)
                        matching = (
                            current.expression.semi_join(
                                other.expression.view(), list(current_keys)
                            )
                            if current_keys
                            else current.expression.cross_join(other.expression.view()).select(
                                current.expression
                            )
                        )
                        current = replace(current, expression=matching)
                    value = current.expression[cell.value]
                    tag = current.expression[cell.tag]
                    if isinstance(owner, MethodNode) and isinstance(
                        owner.parameters, AssociationScore
                    ):
                        invalid = ~(
                            (tag == "defined")
                            | (
                                (tag == "null")
                                & current.expression[cell.reason].isin(
                                    ("source_null", "empty_contribution")
                                )
                            )
                        )
                    else:
                        invalid = tag != "defined"
                    if str(value.type()) == "float64":
                        invalid = (
                            invalid
                            | (value.abs() > float.fromhex("0x1.fffffffffffffp+1023")).fill_null(
                                False
                            )
                            | (value != value).fill_null(False)
                        )
                    if (
                        isinstance(owner, MethodNode)
                        and isinstance(owner.parameters, RowState)
                        and owner.parameters.method == "mean"
                        and str(value.type()) == "int64"
                    ):
                        invalid = invalid | (value.abs() > 2**53).fill_null(False)
                    selected = current.expression.filter(invalid.fill_null(True)).select(
                        *(tuple(k.column for k in current.layout.keys) or (cell.tag,))
                    )
                    violations = (
                        selected
                        if violations is None
                        else violations.union(selected, distinct=False)
                    )
                assert violations is not None
                source_ids = _source_ids(*(item.source_ids for item in numeric_inputs))
            else:
                _fail("an implemented checker for this bound obligation", check_id)
            checks.append(SemanticCheck(requirement, violations, source_ids))
    return LoweredPlan(
        admitted,
        tuple(stages),
        tuple(checks),
        PhysicalRequirement(
            "analysis.r34",
            1,
            frozenset(
                {"scan", "filter", "project", "group", "count", "join", "union", "window", "sort"}
            )
            if any(
                isinstance(stage, SourceMethodStage)
                and isinstance(stage.node.parameters, AssociationScore)
                and stage.operation == "ibis"
                for stage in admitted.stages
            )
            else frozenset({"scan", "filter", "project", "group", "count", "join", "union"}),
        ),
        bindings,
    )


def _attach_category(
    stage: SourceMethodStage, inputs: tuple[LoweredRelation, ...], checks: list[LoweredCheck]
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, AttachCategory)
    source, category = inputs
    cell = category.layout.cell
    if cell is None:
        _fail("a Cell-valued category", "missing classification")
    left, right = source.expression.view(), category.expression.view()
    columns = (
        tuple(f"subject__key_{i}" for i in range(len(category.layout.keys)))
        if params.subject_mapping
        else tuple(k.column for k in source.layout.keys)
    )
    predicates = [
        left[column] == right[key.column]
        for column, key in zip(columns, category.layout.keys, strict=True)
    ]
    selected = right.semi_join(left, predicates)
    source_ids = _source_ids(source.source_ids, category.source_ids)
    checks.extend(
        (
            IntegrityCheck(
                stage.output,
                "complete classification mapping",
                left.anti_join(right, predicates),
                source_ids,
            ),
            IntegrityCheck(
                stage.output,
                "one classification per complete key",
                _key_violations(selected, category.layout),
                source_ids,
            ),
            IntegrityCheck(
                stage.output,
                "Defined non-null classifications",
                selected.filter((selected[cell.tag] != "defined") | selected[cell.value].isnull()),
                source_ids,
            ),
        )
    )
    target = canonical_layout(stage.node.signature, has_value=source.layout.cell is not None)
    result = left.inner_join(right, predicates).select(
        *[left[name] for name in source.layout.columns],
        **{f"key_{len(source.layout.keys)}": right[cell.value]},
    )
    return result.select(*target.columns), target


def _complete_groups(
    stage: SourceMethodStage, inputs: tuple[LoweredRelation, ...], checks: list[LoweredCheck]
) -> tuple[ir.Table, RelationLayout]:
    from marivo.analysis.methods.state_validation import empty_reduction_cell

    source, target = inputs
    keys = tuple(k.column for k in source.layout.keys)
    if not keys:
        checks.append(
            IntegrityCheck(
                stage.output,
                "exact Singleton target",
                target.expression.aggregate(rows=target.expression.count()).filter(
                    lambda t: t.rows != 1
                ),
                target.source_ids,
            )
        )
        return source.expression, source.layout
    left, right = source.expression.view(), target.expression.select(*keys).view()
    source_ids = _source_ids(source.source_ids, target.source_ids)
    checks.append(
        IntegrityCheck(
            stage.output,
            "all consumed groups in the explicit target",
            left.anti_join(right, keys),
            source_ids,
        )
    )
    checks.append(
        IntegrityCheck(
            stage.output,
            "unique complete explicit targets",
            _key_violations(right, RelationLayout(target.layout.keys, None)),
            target.source_ids,
        )
    )
    if source.layout.cell is None:
        return right.select(*keys), canonical_layout(stage.node.signature, has_value=False)
    value, tag, reason = empty_reduction_cell(source.node.signature)
    marked = left.mutate(__present=ibis.literal(True))
    joined = right.left_join(marked, keys)
    missing = marked.__present.isnull()
    fields: dict[str, ir.Value] = {key: right[key] for key in keys}
    subject_fields = {
        f"subject__key_{i}": keys[source.node.signature.domain.instance_key.index(coordinate)]
        for part in source.node.signature.parts
        if isinstance(part, SubjectPart)
        for i, coordinate in enumerate(part.subject_key)
    }
    for name in source.layout.columns:
        if name in keys:
            continue
        if name in subject_fields:
            fields[name] = ibis.ifelse(missing, right[subject_fields[name]], marked[name])
            continue
        empty: int | bool | str | None = (
            value
            if name == "value"
            else tag
            if name == "cell_tag"
            else reason
            if name == "cell_reason"
            else True
            if name == "coverage__complete"
            else None
            if name in ("row_state__min", "row_state__max")
            else 0
        )
        fields[name] = ibis.ifelse(
            missing, ibis.literal(empty).cast(str(marked[name].type())), marked[name]
        )
    layout = canonical_layout(stage.node.signature, has_value=True)
    return joined.select(**fields).select(*layout.columns), layout


def _mean_finish(table: ir.Table, layout: RelationLayout, *, duration: bool = False) -> ir.Table:
    table, result = _division_value(
        table, "original_state__sum", "original_state__non_null_count", duration=duration
    )
    support = table.original_state__non_null_count
    return table.mutate(
        value=(support > 0).ifelse(result, ibis.null().cast(result.type())),
        cell_tag=ibis.ifelse(support > 0, "defined", "null"),
        cell_reason=ibis.ifelse(support > 0, ibis.null().cast("string"), "empty_contribution"),
        coverage__complete=ibis.literal(True),
    ).select(*layout.columns)


def _reduction_subjects(table: ir.Table, signature: Signature) -> ir.Table:
    fields = {
        f"subject__key_{i}": table[f"key_{signature.domain.instance_key.index(c)}"]
        for part in signature.parts
        if isinstance(part, SubjectPart)
        for i, c in enumerate(part.subject_key)
    }
    return table.mutate(**fields) if fields else table


def _time_product(
    stage: SourceMethodStage, source: LoweredRelation
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, TimeProduct)
    grid = params.output_domain.time_grid
    assert grid is not None
    table = source.expression
    key = f"key_{len(source.layout.keys)}"
    # Literal finite coordinates stay in the admitted Ibis expression; no source
    # rows are collected by construction or a second executor.
    branches = tuple(table.mutate(**{key: ibis.literal(cell.identity)}) for cell in grid.cells)
    expanded = (
        branches[0].union(*branches[1:], distinct=False) if len(branches) > 1 else branches[0]
    )
    layout = canonical_layout(stage.node.signature, has_value=False)
    return expanded.select(*layout.columns), layout


def _reference(
    stage: SourceMethodStage, inputs: tuple[LoweredRelation, ...], parts: list[tuple[str, ir.Table]]
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, ReferenceDerive)
    layout = canonical_layout(stage.node.signature, has_value=True)
    for declaration in stage.node.signature.parts:
        assert isinstance(declaration, ReferenceStatePart)
        index = (
            1
            if declaration.role in ("fixed_reference", "strata")
            else 2
            if declaration.role == "reference_proof" and params.kind == "share"
            else 0
        )
        source = inputs[index]
        names = tuple(key.column for key in source.layout.keys)
        columns = (
            names
            if source.layout.cell is None or declaration.role == "strata"
            else (
                *names,
                source.layout.cell.value,
                source.layout.cell.tag,
                source.layout.cell.reason,
            )
        )
        if declaration.role == "reference_proof" and params.share_state is not None:
            columns = (
                *columns,
                *("original_state__" + name for name in params.share_state.components),
            )
        parts.append(
            (
                declaration.role,
                source.expression.select(
                    *columns, error_bound=_operand_bound(source, source.expression)
                )
                if source.layout.cell is not None and declaration.role != "strata"
                else source.expression.select(*columns),
            )
        )
    source = inputs[0].expression
    table = (
        source.select(*(key.column for key in inputs[0].layout.keys))
        if params.kind == "share" and inputs[0].layout.keys
        else source.aggregate(_reference_count=source.count())
    )
    for index, dependency in enumerate(inputs[1:], 1):
        table = table.cross_join(
            dependency.expression.aggregate(
                **{f"_reference_dependency_{index}": dependency.expression.count()}
            )
        )
    dtype = (
        str(stage.node.value_type.name)
        if isinstance(stage.node.value_type, ScalarType)
        else f"decimal({stage.node.value_type.precision},{stage.node.value_type.scale})"
        if isinstance(stage.node.value_type, DecimalType)
        else "float64"
    )
    table = table.mutate(
        value=ibis.literal(0).cast(dtype),
        cell_tag=ibis.literal("defined"),
        cell_reason=ibis.null().cast("string"),
        **{
            column.column: ibis.literal(0).cast(
                "float64" if part_role(part.part) == "reference_proof" else "int64"
            )
            for part in layout.parts
            for column in part.columns
        },
    )
    return table.select(*layout.columns), layout


def endpoint_fields(signature: Signature, side: str, table: ir.Table) -> dict[str, ir.Value]:
    part = next(p for p in signature.parts if isinstance(p, EndpointPart) and p.side == side)
    fields = (
        {
            f"{side}_endpoint__state__{c}": table["original_state__" + c]
            for c in part.original_state.components
        }
        if part.original_state
        else {}
    )
    if part.original_state:
        fields[f"{side}_endpoint__complete"] = table.coverage__complete
    if part.coordinate_state:
        fields[f"{side}_endpoint__groups"] = table[
            "allocation_state__groups"
            if part.coordinate_state.attribution_only
            else "coordinate_state__groups"
        ]
    return fields
