"""Private admitted graph lowering. Expressions and checks never execute here."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
from marivo.analysis.compiler.member_version import select_version
from marivo.analysis.core.graph import MethodNode, Node, SourceLeaf, topology
from marivo.analysis.core.model import (
    Coordinate,
    CoordinateStatePart,
    FactInput,
    Obligation,
    ObservedQuantity,
    OriginalStatePart,
    Part,
    RowStatePart,
    Signature,
    SubjectPart,
    part_role,
    reject,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import (
    AssociationScore,
    BindProject,
    CellDerive,
    GroupObservationTarget,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    OriginalRatio,
    OriginalReduce,
    PartsTransport,
    RowState,
)
from marivo.analysis.methods.builtin import admit
from marivo.analysis.methods.physical import ScalarType
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.datasource.adapters import BoundSource, PhysicalRequirement
from marivo.datasource.ir import ParquetSourceIR, TableSourceIR
from marivo.semantic._expression_binding import (
    CompiledExpressionSidecar,
    evaluate_expression_body,
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
    if isinstance(part, (OriginalStatePart, RowStatePart)):
        return part.components
    if isinstance(part, CoordinateStatePart):
        return ("groups",)
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


LoweredStage: TypeAlias = LoweredRelation | LoweredLocal | ArtifactReadStage
LoweredCheck: TypeAlias = IntegrityCheck | SemanticCheck | CheckRequirement


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
            item.source_ids
            for item in (*self.stages, *self.checks)
            if (isinstance(item, LoweredRelation) and item.expression is expression)
            or (isinstance(item, (IntegrityCheck, SemanticCheck)) and item.violations is expression)
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
                        dt.dtype(part.value_type) if name in ("sum", "numerator_sum") else dt.int64,
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
        if str(table[key.column].type()) not in ("int64", "string"):
            _fail("qualified int64 or string identity columns", str(table[key.column].type()))
    for part in layout.parts:
        if tuple(c.component for c in part.columns) != components(part.part):
            _fail("complete ordered part components", repr(part.columns))
        for component in part.columns:
            role = part_role(part.part)
            if isinstance(part.part, CoordinateStatePart):
                if table[component.column].type() != coordinate_state_type(part.part):
                    _fail("the exact nested contribution coordinate state", component.column)
                continue
            if role == "subject":
                actual = str(table[component.column].type())
                if actual not in ("int64", "string"):
                    _fail("qualified int64 or string Subject identity", actual)
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
                if value_type == "float64" and component.component in ("sum", "weighted_sum")
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
    nulls = reduce(or_, (table[key].isnull() for key in keys))
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
    a = left.expression.select(*(k.column for k in left.layout.keys)).view()
    b = right.expression.select(*(k.column for k in right.layout.keys)).view()
    keys = tuple(k.column for k in left.layout.keys)
    if not keys or keys != tuple(k.column for k in right.layout.keys):
        _fail("matching nonempty complete keys", repr(keys))
    return a.anti_join(b, keys).union(b.anti_join(a, keys), distinct=False)


def _difference(
    stage: SourceMethodStage,
    inputs: tuple[LoweredRelation, LoweredRelation],
    checks: list[LoweredCheck],
) -> tuple[ir.Table, RelationLayout]:
    left, right = inputs
    a, b = left.layout.cell, right.layout.cell
    keys = tuple(item.column for item in left.layout.keys)
    if a is None or b is None or keys != tuple(item.column for item in right.layout.keys):
        _fail("two Cell-valued endpoints with the same complete keys", repr(keys))
    checks.append(
        IntegrityCheck(
            stage.output,
            "equal complete endpoint key sets",
            _pair_violations(left, right),
            _source_ids(left.source_ids, right.source_ids),
        )
    )
    lhs, rhs = left.expression.view(), right.expression.view()
    current = lhs.select(
        *keys,
        current_value=lhs[a.value],
        current_tag=lhs[a.tag],
        current_reason=lhs[a.reason],
    )
    baseline = rhs.select(
        *keys,
        baseline_value=rhs[b.value],
        baseline_tag=rhs[b.tag],
        baseline_reason=rhs[b.reason],
    )
    paired = current.join(baseline, keys).select(
        *(current[key] for key in keys),
        current.current_value,
        current.current_tag,
        current.current_reason,
        baseline.baseline_value,
        baseline.baseline_tag,
        baseline.baseline_reason,
    )
    target = canonical_layout(stage.node.signature, has_value=True)
    output = paired.select(
        *(paired[key] for key in keys),
        value=paired.current_value - paired.baseline_value,
        cell_tag=ibis.literal("defined"),
        cell_reason=ibis.null().cast("string"),
        current_endpoint__value=paired.current_value,
        current_endpoint__cell_tag=paired.current_tag,
        current_endpoint__cell_reason=paired.current_reason,
        baseline_endpoint__value=paired.baseline_value,
        baseline_endpoint__cell_tag=paired.baseline_tag,
        baseline_endpoint__cell_reason=paired.baseline_reason,
        **(
            {f"subject__key_{i}": paired[key] for i, key in enumerate(keys)}
            if any(isinstance(p, SubjectPart) for p in stage.node.signature.parts)
            else {}
        ),
    )
    return output.select(*target.columns), target


def _transport(
    stage: SourceMethodStage, source: LoweredRelation, checks: list[LoweredCheck]
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, PartsTransport)
    table = source.expression
    cell = source.layout.cell
    if params.predicates:
        if cell is None:
            _fail("a bound value for selection", "domain without value")
        for predicate in params.predicates:
            if predicate.unknown == "reject":
                checks.append(
                    IntegrityCheck(
                        stage.output,
                        "a Defined predicate input",
                        table.filter(table[cell.tag] != "defined"),
                        source.source_ids,
                    )
                )
        table = table.filter(reduce(and_, (_predicate(table, cell, p) for p in params.predicates)))
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
    for node in topology(admitted.root):
        if not isinstance(node, MethodNode) or binding.leaf not in node.sources:
            continue
        params = node.parameters
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
        elif isinstance(params, (ObserveMetric, ObserveCount)):
            for coordinate in params.coordinates:
                if coordinate.entity_ref.path == binding.leaf.definition.ref.path:
                    fields.add(coordinate.source_column)
            if params.event.entity_ref.path == binding.leaf.definition.ref.path:
                fields.add(params.event.source_column)
            for relationship in params.path:
                if relationship.from_entity_ref.path == binding.leaf.definition.ref.path:
                    fields.add(relationship.keys[0][0])
            if (
                isinstance(params, ObserveMetric)
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
                for key, item in zip(keys, selected[0].layout.keys, strict=True)
            ),
        )
        .select(
            *(source.expression[key].name(f"member__{i}") for i, key in enumerate(keys)),
            *(first[column].name(f"owner__{column}") for column in first.columns),
        )
    )
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
            else value.cast("int64")
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
    if params.method in ("sum", "mean"):
        cell = source.layout.cell
        if cell is None:
            _fail("Cell values for registered current-row arithmetic", "missing Cell")
        aggregate = table.aggregate(
            state_sum=table[cell.value].sum().fill_null(0),
            state_count=table.count(),
        )
        if params.method == "sum":
            result = aggregate.select(
                value=aggregate.state_sum,
                cell_tag=ibis.literal("defined"),
                cell_reason=ibis.null().cast("string"),
                row_state__sum=aggregate.state_sum,
            )
        else:
            result = aggregate.select(
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
            )
        target = canonical_layout(stage.node.signature, has_value=True)
        return result.select(*target.columns), target
    if params.method == "count_defined":
        cell = source.layout.cell
        if cell is None:
            _fail("Cell tags for defined-count", "missing Cell")
        table = table.filter(table[cell.tag] == "defined")
    result = table.aggregate(value=table.count())
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


def _ratio_finish(table: ir.Table, layout: RelationLayout) -> ir.Table:
    numerator = table.original_state__numerator_sum.cast("int64")
    denominator = table.original_state__denominator_count.cast("int64")
    defined = denominator > 0
    return table.mutate(
        value=ibis.ifelse(
            defined, numerator.cast("float64") / denominator, ibis.null().cast("float64")
        ),
        cell_tag=ibis.ifelse(defined, "defined", "undefined"),
        cell_reason=ibis.ifelse(defined, ibis.null().cast("string"), "zero_denominator"),
        original_state__numerator_sum=numerator,
        original_state__numerator_non_null_count=table.original_state__numerator_non_null_count.cast(
            "int64"
        ),
        original_state__denominator_count=denominator,
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
    fields = {key: a[key] for key in keys}
    fields.update(
        {
            "original_state__numerator_sum": a.original_state__sum,
            "original_state__numerator_non_null_count": a.original_state__non_null_count,
            "original_state__denominator_count": b.original_state__count,
        }
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
            numerator_sum=first.group["sum"],
            numerator_non_null_count=first.group["non_null_count"],
        )
        second = b.select(*keys, group=b.coordinate_state__groups.unnest())
        second = second.select(
            *keys,
            **{name: second.group[name] for name in coordinate.columns},
            denominator_count=second.group["count"],
        )
        paired = first.outer_join(second, (*keys, *coordinate.columns))
        merged = paired.select(
            **{key: first[key].coalesce(second[key]) for key in keys},
            **{name: first[name].coalesce(second[name]) for name in coordinate.columns},
            numerator_sum=first.numerator_sum.fill_null(0),
            numerator_non_null_count=first.numerator_non_null_count.fill_null(0),
            denominator_count=second.denominator_count.fill_null(0),
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
    table = _ratio_finish(base, layout)
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


def _original_sum(
    stage: SourceMethodStage, source: LoweredRelation
) -> tuple[ir.Table, RelationLayout]:
    table = source.expression
    params = stage.node.parameters
    assert isinstance(params, OriginalReduce)
    if params.coordinate is not None:
        coordinate = next(
            p for p in source.node.signature.parts if isinstance(p, CoordinateStatePart)
        )
        exploded = table.select(group=table.coordinate_state__groups.unnest())
        table = exploded.select(
            key_0=exploded.group[coordinate.column_for(params.coordinate)],
            **{f"original_state__{name}": exploded.group[name] for name in coordinate.components},
            coverage__complete=ibis.literal(True),
        )
    grouped = table.group_by("key_0") if params.coordinate is not None else table
    if stage.node.method.name == "state_rollup.ratio":
        reduced = grouped.aggregate(
            **{
                name: table[name].sum().fill_null(0)
                for name in (
                    "original_state__numerator_sum",
                    "original_state__numerator_non_null_count",
                    "original_state__denominator_count",
                )
            }
        )
        target = canonical_layout(stage.node.signature, has_value=True)
        return _ratio_finish(reduced, target), target
    if stage.node.method.name == "state_rollup.count":
        reduced_count = grouped.aggregate(
            original_state__count=table.original_state__count.sum().fill_null(0),
            coverage__complete=table.coverage__complete.all().fill_null(True),
        )
        target_count = canonical_layout(stage.node.signature, has_value=True)
        return reduced_count.mutate(
            value=reduced_count.original_state__count.cast("int64"),
            cell_tag=ibis.literal("defined"),
            cell_reason=ibis.null().cast("string"),
        ).select(*target_count.columns), target_count
    reduced = grouped.aggregate(
        original_state__sum=table.original_state__sum.sum().fill_null(0),
        original_state__non_null_count=table.original_state__non_null_count.sum().fill_null(0),
        coverage__complete=table.coverage__complete.all().fill_null(True),
    )
    support = reduced.original_state__non_null_count
    defined = support > 0 if stage.node.method.name == "state_rollup" else ibis.literal(True)
    value_type = stage.node.value_type
    assert isinstance(value_type, ScalarType)
    target = canonical_layout(stage.node.signature, has_value=True)
    result = reduced.mutate(
        original_state__sum=reduced.original_state__sum.cast(value_type.name),
        value=ibis.ifelse(
            defined,
            reduced.original_state__sum.cast(value_type.name),
            ibis.null().cast(value_type.name),
        ),
        cell_tag=ibis.ifelse(defined, "defined", "null"),
        cell_reason=ibis.ifelse(defined, ibis.null().cast("string"), "empty_contribution"),
    )
    return result.select(*target.columns), target


def _contribution_rows(
    stage: SourceMethodStage,
    params: ObserveMetric | ObserveCount,
    bindings: tuple[SourceBinding, ...],
    relations: tuple[LoweredRelation, ...],
    checks: list[LoweredCheck],
) -> tuple[ir.Table, tuple[str, ...]]:
    by_entity = {binding.leaf.definition.ref.path: binding for binding in bindings}
    root_binding = by_entity[params.contribution.path]
    root = _staged_source(root_binding, relations).view()
    if (
        isinstance(params, ObserveMetric)
        and str(root[params.amount_column].type()) != params.amount_type
    ):
        _fail("the exact contribution amount type", str(root[params.amount_column].type()))
    fields: dict[str, ir.Value] = {
        "amount": root[params.amount_column]
        if isinstance(params, ObserveMetric)
        else ibis.literal(1, type="int64"),
        "next_key": root[params.path[0].keys[0][0]],
    }
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
        destination = _staged_source(binding, relations).view()
        key = relationship.keys[0][1]
        joined = rows.left_join(destination, rows.next_key == destination[key])
        source_ids = _source_ids(source_ids, (binding.leaf.identity,))
        checks.append(
            IntegrityCheck(
                stage.output,
                "complete contribution relationship mapping",
                joined.filter(destination[key].isnull()),
                source_ids,
            )
        )
        selected = {name: rows[name] for name in rows.columns if name != "next_key"}
        if relationship.to_entity_ref.path == params.event.entity_ref.path:
            selected["event_time"] = destination[params.event.source_column]
        for coordinate_index, coordinate in enumerate(params.coordinates):
            if coordinate.entity_ref.path == relationship.to_entity_ref.path:
                selected[
                    "coordinate" if coordinate_index == 0 else f"coordinate_{coordinate_index}"
                ] = destination[coordinate.source_column]
        if index + 1 < len(params.path):
            selected["next_key"] = destination[params.path[index + 1].keys[0][0]]
        else:
            selected["member"] = destination[key]
        rows = joined.select(**selected)
    return rows, source_ids


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
    assert isinstance(params, (ObserveMetric, ObserveCount))
    source, contribution_ids = _contribution_rows(stage, params, bindings, relations, checks)
    event_type = source.event_time.type()
    if (
        len(members.layout.keys) != 1
        or members.layout.keys[0].coordinate.field != params.path[-1].keys[0][1]
        or not isinstance(event_type, dt.Timestamp)
        or event_type.timezone not in (None, "UTC", "Etc/UTC")
        or event_type.scale not in (None, 6)
    ):
        _fail("exact observation key and UTC microsecond timestamp schema", "schema drift")
    start = ibis.literal(datetime.fromisoformat(params.start), type=event_type)
    end = ibis.literal(datetime.fromisoformat(params.end), type=event_type)
    source = source.filter((source.event_time >= start) & (source.event_time < end))
    from_column = "member"
    mapping = members.expression
    key = members.layout.keys[0].column
    source_ids = _source_ids(members.source_ids, contribution_ids)
    coordinate = mapping[key]
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
        coordinate = mapping[cell.value]
    targets = mapping.select(key_0=coordinate).distinct()
    joined = source.inner_join(mapping, source[from_column] == mapping[key])
    values = joined.select(
        key_0=coordinate,
        amount=source.amount,
        **{
            ("coordinate" if i == 0 else f"coordinate_{i}"): source[
                "coordinate" if i == 0 else f"coordinate_{i}"
            ]
            for i in range(len(params.coordinates))
        },
    )
    if isinstance(params, ObserveMetric) and params.amount_type == "float64":
        checks.append(
            IntegrityCheck(
                stage.output,
                "finite contribution amounts",
                values.filter(values.amount.isnan() | values.amount.isinf()),
                source_ids,
            )
        )
    summed = values.group_by("key_0").aggregate(
        state_sum=values.amount.sum(),
        support=values.amount.count(),
    )
    dense = targets.left_join(summed, targets[key] == summed.key_0)
    amount_type = params.amount_type if isinstance(params, ObserveMetric) else "int64"
    total = summed.state_sum.fill_null(0).cast(amount_type)
    support = summed.support.fill_null(0).cast("int64")
    target = canonical_layout(stage.node.signature, has_value=True)
    defined = (
        support > 0
        if isinstance(params, ObserveMetric) and params.metric.empty_rule == "null"
        else ibis.literal(True)
    )
    table = dense.select(
        key_0=targets[key],
        value=ibis.ifelse(defined, total, ibis.null().cast(amount_type)),
        cell_tag=ibis.ifelse(defined, "defined", "null"),
        cell_reason=ibis.ifelse(defined, ibis.null().cast("string"), "empty_contribution"),
        original_state__count=support,
        subject__key_0=targets[key],
        original_state__sum=total,
        original_state__non_null_count=support,
        coverage__complete=ibis.literal(True),
    )
    if params.coordinates:
        part = next(p for p in stage.node.signature.parts if isinstance(p, CoordinateStatePart))
        checks.append(
            IntegrityCheck(
                stage.output,
                "complete string contribution coordinates",
                values.filter(reduce(or_, (values[name].isnull() for name in part.columns))),
                source_ids,
            )
        )
        grouped = values.group_by("key_0", *part.columns).aggregate(
            sum=values.amount.sum().fill_null(0).cast(amount_type),
            non_null_count=values.amount.count().cast("int64"),
            count=values.count().cast("int64"),
        )
        cells = ibis.struct(
            {
                **{name: grouped[name] for name in part.columns},
                **{name: grouped[name] for name in part.components},
            }
        )
        nested = grouped.group_by("key_0").aggregate(
            coordinate_state__groups=cells.collect(
                order_by=[grouped[name] for name in part.columns]
            )
        )
        combined = table.left_join(nested, table.key_0 == nested.key_0)
        table = combined.select(
            *[table[name] for name in table.columns],
            coordinate_state__groups=nested.coordinate_state__groups.fill_null(
                ibis.literal([], type=coordinate_state_type(part))
            ),
        )
    table = table.select(*target.columns)
    actual = joined.aggregate(actual=joined.count())
    selected = source.filter(source[from_column].isin(mapping[key]))
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
        elif check_id == "source.complete_coverage@v1":
            violations = coverage.filter(coverage.actual != coverage.expected)
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
                and immediate[0].signature.quantity is not None
                and immediate[0].signature.quantity.definition_id == fact.subject_id
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
        if isinstance(stage, ArtifactReadStage):
            layout = canonical_layout(
                stage.leaf.signature, has_value=stage.leaf.signature.quantity is not None
            )
            layouts[stage.output] = layout
            stages.append(stage)
            continue
        if isinstance(stage, LocalMethodStage):
            admit(stage.implementation, stage.node.parameters)
            if len(stage.inputs) not in (1, 2):
                _fail("one row input or two Association inputs", repr(stage.inputs))
            if len(stage.inputs) == 2 and not (
                isinstance(stage.node.parameters, AssociationScore)
                or (
                    isinstance(stage.node.parameters, CellDerive)
                    and stage.node.parameters.method == "difference"
                )
            ):
                _fail("a registered two-input local method", repr(stage.inputs))
            output_layout = canonical_layout(
                stage.node.signature,
                has_value=not isinstance(stage.node.parameters, PartsTransport)
                or stage.node.parameters.keep_quantity,
            )
            if isinstance(stage.node.parameters, AssociationScore):
                output_layout = replace(output_layout, extras=("status",))
            stages.append(
                LoweredLocal(stage, tuple(layouts[item] for item in stage.inputs), output_layout)
            )
            layouts[stage.output] = output_layout
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
            raw = select_version(
                bound.source.relation,
                stage.leaf.definition.version,
                stage.leaf.signature.domain.version_selection,
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
            inputs = tuple(results[i] for i in stage.inputs[: len(stage.node.inputs)])
            params = stage.node.parameters
            source_ids = inputs[0].source_ids
            if isinstance(params, PartsTransport):
                table, layout = _transport(stage, inputs[0], checks)
                cell_reasons = inputs[0].cell_reasons
            elif isinstance(params, (ObserveMetric, ObserveCount)):
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
            elif isinstance(params, OriginalReduce):
                table, layout = _original_sum(stage, inputs[0])
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif isinstance(params, RowState):
                table, layout = _count(stage, inputs[0])
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif isinstance(params, AssociationScore) and len(inputs) == 2:
                table, layout = _spearman(stage, (inputs[0], inputs[1]), checks)
                source_ids = _source_ids(*(item.source_ids for item in inputs))
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            elif (
                isinstance(params, CellDerive)
                and params.method == "difference"
                and len(inputs) == 2
            ):
                table, layout = _difference(stage, (inputs[0], inputs[1]), checks)
                source_ids = _source_ids(*(item.source_ids for item in inputs))
                cell_reasons = registry.lookup(stage.node.method).semantics.empty_cell_reasons
            else:
                _fail("a registered source lowerer", str(stage.node.method))
            node = stage.node
        relation = LoweredRelation(stage.output, node, table, layout, source_ids, cell_reasons)
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
                        and isinstance(node.parameters, PartsTransport)
                        and node.parameters.mode == "where"
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
                in ("source.contribution_partition@v1", "source.complete_coverage@v1")
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
        owner = next(r.node for r in results.values() if r.node.identity == requirement.node_id)
        for inputs in _fact_relations(owner, requirement.obligation, tuple(results.values())):
            check_id = requirement.obligation.check_id
            source_ids = _source_ids(*(input.source_ids for input in inputs))
            if check_id == "source.unique_key@v1":
                violations = _key_violations(inputs[0].expression, inputs[0].layout)
                for other in inputs[1:]:
                    violations = violations.union(
                        _key_violations(other.expression, other.layout), distinct=False
                    )
            elif check_id == "source.single_value@v1":
                output = inputs[0]
                violations = _key_violations(output.expression, output.layout)
                source_ids = output.source_ids
            elif check_id == "source.exact_pairing@v1" and len(inputs) == 2:
                violations = _pair_violations(*inputs)
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
                            | value.isnan().fill_null(False)
                            | value.isinf().fill_null(False)
                        )
                    if (
                        isinstance(owner, MethodNode)
                        and isinstance(owner.parameters, RowState)
                        and owner.parameters.method == "mean"
                        and str(value.type()) == "int64"
                    ):
                        invalid = invalid | (value.abs() > 2**53).fill_null(False)
                    selected = current.expression.filter(invalid.fill_null(True)).select(
                        *tuple(k.column for k in current.layout.keys)
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
