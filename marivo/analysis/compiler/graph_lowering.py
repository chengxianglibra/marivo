"""Private admitted graph lowering. Expressions and checks never execute here."""

from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from operator import and_, or_
from typing import NoReturn, TypeAlias

import ibis
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
from marivo.analysis.core.graph import MethodNode, Node, SourceLeaf
from marivo.analysis.core.model import (
    Coordinate,
    FactInput,
    Obligation,
    OriginalStatePart,
    Part,
    RowStatePart,
    Signature,
    SubjectPart,
    part_role,
    reject,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import BindProject, MapCorrespond, PartsTransport, RowState
from marivo.analysis.methods.builtin import admit
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.datasource.adapters import BoundSource, PhysicalRequirement
from marivo.datasource.ir import ParquetSourceIR, TableSourceIR


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
    if isinstance(part, SubjectPart):
        return tuple(f"key_{i}" for i in range(len(part.subject_key)))
    role = part_role(part)
    if role in ("current_endpoint", "baseline_endpoint"):
        return ("value", "cell_tag", "cell_reason")
    if role == "coverage":
        return ("complete",)
    if role == "statistical_weight":
        return ("weight",)
    return ("reference",)


@dataclass(frozen=True, slots=True)
class RelationLayout:
    keys: tuple[CoordinateColumn, ...]
    cell: CellColumns | None
    parts: tuple[PartColumns, ...] = ()

    @property
    def columns(self) -> tuple[str, ...]:
        values = () if self.cell is None else (self.cell.value, self.cell.tag, self.cell.reason)
        return (
            *tuple(k.column for k in self.keys),
            *values,
            *(c.column for p in self.parts for c in p.columns),
        )


@dataclass(frozen=True, slots=True)
class SourceBinding:
    """An R4-supplied R1 binding, associated with the exact live leaf identity."""

    leaf: SourceLeaf
    source: BoundSource
    layout: RelationLayout


@dataclass(frozen=True, slots=True, eq=False)
class LoweredRelation:
    output: str
    node: Node
    expression: ir.Table
    layout: RelationLayout
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LoweredLocal:
    stage: LocalMethodStage
    input_layout: RelationLayout
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
LoweredCheck: TypeAlias = IntegrityCheck | SemanticCheck


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


def _validate_layout(table: ir.Table, layout: RelationLayout, signature: Signature) -> None:
    if tuple(k.coordinate for k in layout.keys) != signature.domain.instance_key:
        _fail("the complete ordered coordinate key", repr(layout.keys))
    if tuple(p.part for p in layout.parts) != signature.parts:
        _fail("each exact retained part and binding", repr(layout.parts))
    if len(set(layout.columns)) != len(layout.columns) or not set(layout.columns) <= set(
        table.columns
    ):
        _fail("distinct existing physical columns", repr(layout.columns))
    for key in layout.keys:
        if str(table[key.column].type()) != "int64":
            _fail("qualified int64 identity columns", str(table[key.column].type()))
    for part in layout.parts:
        if tuple(c.component for c in part.columns) != components(part.part):
            _fail("complete ordered part components", repr(part.columns))
        for component in part.columns:
            dtype = "boolean" if part_role(part.part) == "coverage" else "int64"
            if str(table[component.column].type()) != dtype:
                _fail(f"qualified {dtype} part component", component.column)
    if signature.quantity is not None and layout.cell is None:
        _fail("Cell columns for a quantity", "missing Cell")
    if layout.cell is not None:
        for column, dtype in (
            (layout.cell.value, "int64"),
            (layout.cell.tag, "string"),
            (layout.cell.reason, "string"),
        ):
            if str(table[column].type()) != dtype:
                _fail(f"{dtype} {column}", str(table[column].type()))


def _renamed(table: ir.Table, source: RelationLayout, target: RelationLayout) -> ir.Table:
    return table.select(
        *(table[old].name(new) for old, new in zip(source.columns, target.columns, strict=True))
    )


def _key_violations(table: ir.Table, layout: RelationLayout) -> ir.Table:
    keys = tuple(k.column for k in layout.keys)
    if not keys:
        counts = table.aggregate(n=table.count())
        return counts.filter(counts.n != 1)
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
        "eq": value == predicate.value,
        "ne": value != predicate.value,
        "lt": value < predicate.value,
        "le": value <= predicate.value,
        "gt": value > predicate.value,
        "ge": value >= predicate.value,
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


def _bind(
    stage: SourceMethodStage,
    source: LoweredRelation,
    bindings: tuple[SourceBinding, ...],
    checks: list[LoweredCheck],
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, BindProject) and params.field_contract is not None
    owner = next(b for b in bindings if b.leaf is stage.node.sources[0])
    if owner.leaf.signature.domain != source.node.signature.domain:
        _fail("direct projection on the exact owner domain", owner.leaf.identity)
    field = params.field_contract.source_column
    raw = owner.source.relation
    if field not in raw.columns or str(raw[field].type()) != "int64":
        _fail("the normalized int64 field column", field)
    keys = tuple(k.column for k in source.layout.keys)
    projected = raw.select(
        *(raw[k.column].name(f"key_{i}") for i, k in enumerate(owner.layout.keys)),
        __bound_value=raw[field],
    ).view()
    # Restrict to the actual subject input, not the full owner relation.
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
    checks.append(
        IntegrityCheck(
            stage.output,
            "complete field-owner coverage",
            source.expression.anti_join(projected, keys),
            _source_ids(source.source_ids, (owner.leaf.identity,)),
        )
    )
    target = canonical_layout(stage.node.signature, has_value=True)
    return joined.select(*target.columns), target


def _map(
    stage: SourceMethodStage, inputs: tuple[LoweredRelation, ...], checks: list[LoweredCheck]
) -> tuple[ir.Table, RelationLayout]:
    params = stage.node.parameters
    assert isinstance(params, MapCorrespond)
    source = inputs[0]
    target = canonical_layout(stage.node.signature, has_value=False)
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


def _fact_relations(
    node: Node, obligation: Obligation, relations: tuple[LoweredRelation, ...]
) -> tuple[tuple[LoweredRelation, ...], ...]:
    """Keep each originating check's ordered inputs separate across shared paths."""
    by_identity = {r.node.identity: r for r in relations}
    fact = obligation.fact
    if isinstance(node, MethodNode):
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
            if len(stage.inputs) != 1:
                _fail("one local count input", repr(stage.inputs))
            output_layout = canonical_layout(stage.node.signature, has_value=True)
            stages.append(LoweredLocal(stage, layouts[stage.inputs[0]], output_layout))
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
            _validate_layout(bound.source.relation, bound.layout, stage.leaf.signature)
            layout = canonical_layout(stage.leaf.signature, has_value=bound.layout.cell is not None)
            table = _renamed(bound.source.relation, bound.layout, layout)
            node: Node = stage.leaf
            source_ids: tuple[str, ...] = (stage.leaf.identity,)
        else:
            admit(stage.implementation, stage.node.parameters)
            if stage.operation != "ibis":
                _fail("a qualified preparation consumer", stage.operation)
            inputs = tuple(results[i] for i in stage.inputs[: len(stage.node.inputs)])
            params = stage.node.parameters
            source_ids = inputs[0].source_ids
            if isinstance(params, PartsTransport):
                table, layout = _transport(stage, inputs[0], checks)
            elif isinstance(params, BindProject):
                table, layout = _bind(stage, inputs[0], bindings, checks)
                source_ids = _source_ids(source_ids, (stage.node.sources[0].identity,))
            elif isinstance(params, MapCorrespond):
                table, layout = _map(stage, inputs, checks)
                if params.mode == "union_keys":
                    source_ids = _source_ids(*(input.source_ids for input in inputs))
            elif isinstance(params, RowState):
                table, layout = _count(stage, inputs[0])
            else:
                _fail("a registered source lowerer", str(stage.node.method))
            node = stage.node
        relation = LoweredRelation(stage.output, node, table, layout, source_ids)
        results[stage.output] = relation
        layouts[stage.output] = layout
        stages.append(relation)
        checks.append(
            IntegrityCheck(
                stage.output,
                "unique non-null complete identity",
                _key_violations(table, layout),
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
            elif check_id == "source.exact_pairing@v1" and len(inputs) == 2:
                violations = _pair_violations(*inputs)
            elif check_id == "source.cell_policy@v1" and inputs[0].layout.cell is not None:
                violations = _cell_violations(inputs[0].expression, inputs[0].layout.cell)
                source_ids = inputs[0].source_ids
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
            frozenset({"scan", "filter", "project", "group", "count", "join", "union"}),
        ),
        bindings,
    )
