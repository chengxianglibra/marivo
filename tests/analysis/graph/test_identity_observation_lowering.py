"""Complete-key observation rewrites preserve membership, Cells and original state."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Literal

import ibis
import pyarrow as pa
import pytest
import sqlglot
from sqlglot import expressions as sge

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler import graph_lowering
from marivo.analysis.compiler.graph_lowering import (
    ComponentColumn,
    CoordinateColumn,
    LoweredPlan,
    LoweredRelation,
    PartColumns,
    RelationLayout,
    SourceBinding,
    lower,
)
from marivo.analysis.compiler.graph_plan import RouteChoice, plan
from marivo.analysis.core.graph import (
    Edge,
    MethodNode,
    Node,
    SourceDefinition,
    SourceLeaf,
    method_node,
    topology,
)
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainSignature,
    ObservedQuantity,
    SubjectPart,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import (
    AttachCategory,
    BindProject,
    DirectMetricDefinition,
    EntityObservationTarget,
    GroupObservationTarget,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    OccurrenceFilter,
    PartsTransport,
    TimeProduct,
    entity_members,
)
from marivo.analysis.core.time_grid import bind_grid
from marivo.analysis.methods.physical import ScalarType, SourceShape, TimeShape
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession, provider_for
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation, TableSourceIR
from marivo.refs import RefPayloadV1, ref
from marivo.semantic.ir import TargetDimensionContract, TargetEntityContract
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CanonicalSliceEntryV1,
    MetricExpressionGraphV1,
    MetricGraphNodeRecordV1,
)
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.shared_fixtures import DslCaseFactory

WIDE = 2**53 + 17
ENTITY = ref.entity("sales.events")


@contextmanager
def _source(
    *, empty: bool = False, compound: bool = True
) -> Iterator[tuple[SourceSession, SourceLeaf, SourceBinding]]:
    rows = pa.table(
        {
            "tenant": pa.array(["a", "a", "b", "a", "a"], type=pa.string()),
            "query_id": pa.array([WIDE, 2, WIDE if compound else WIDE + 1, 3, 4], type=pa.int64()),
            "cluster": pa.array(["bi", "bi", "bi", "other", "bi"], type=pa.string()),
            "amount": pa.array([10, None, 7, 20, 99], type=pa.int64()),
            "point": pa.array(
                ["20260902", "20260903", "20260904", "20260904", "20260830"], type=pa.string()
            ),
        }
    )
    backend = ibis.duckdb.connect()
    backend.create_table("events", rows.slice(0, 0) if empty else rows)
    datasource = DatasourceIR(
        "db", "db", "duckdb", {}, {}, AiContextIR(), "db", DatasourceSourceLocation("source.py", 1)
    )
    with SourceSession(provider_for("duckdb"), datasource, backend) as session:
        physical = TableSourceIR("events")
        keys = ("tenant", "query_id") if compound else ("query_id",)
        contract = TargetEntityContract(
            RefPayloadV1.from_ref(ENTITY),
            RefPayloadV1.from_ref(ref.datasource("db")),
            "events-v1",
            physical,
            keys,
            tuple((key, "string" if key == "tenant" else "int64") for key in keys),
            keys,
            tuple((name, str(dtype)) for name, dtype in backend.table("events").schema().items()),
            None,
            (),
        )
        signature = entity_members(contract, ENTITY, Binding("session", "owner", "events", "all"))
        leaf = SourceLeaf(
            SourceDefinition(
                ENTITY,
                "events-v1",
                ref.datasource("db"),
                SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC")),
            ),
            signature,
            ScalarType("string" if compound else "int64"),
        )
        subject = next(part for part in signature.parts if isinstance(part, SubjectPart))
        layout = RelationLayout(
            tuple(CoordinateColumn(key, key.field) for key in signature.domain.instance_key),
            None,
            (
                PartColumns(
                    subject,
                    tuple(
                        ComponentColumn(f"key_{i}", key.field)
                        for i, key in enumerate(signature.domain.instance_key)
                    ),
                ),
            ),
        )
        yield (
            session,
            leaf,
            SourceBinding(leaf, session.bind(physical, source_identity=leaf.identity), layout),
        )


def _members(leaf: SourceLeaf) -> MethodNode:
    return method_node(
        (Edge("subject", leaf),),
        PartsTransport("view", leaf.signature.domain, ("subject",), False),
        value_type=leaf.value_type,
    )


def _cluster() -> TargetDimensionContract:
    return TargetDimensionContract(
        RefPayloadV1.from_ref(ref.dimension("sales.events.cluster")),
        RefPayloadV1.from_ref(ENTITY),
        "cluster",
        "string",
        False,
        False,
        None,
        False,
        None,
    )


def _read_cluster(members: Node) -> MethodNode:
    field = _cluster()
    return method_node(
        (Edge("subject", members),),
        BindProject(ref.dimension(field.ref.path), ENTITY, field, None, (), ()),
        value_type=ScalarType("string"),
        sources=tuple(node for node in topology(members) if isinstance(node, SourceLeaf)),
    )


def _observation(
    members: Node,
    leaf: SourceLeaf,
    *,
    method: Literal["sum", "mean", "count"] = "sum",
    empty: Literal["null", "zero"] = "null",
    grouped: bool = False,
    grid_window: bool = False,
) -> MethodNode:
    metric = ref.metric("sales.total")
    graph = MetricExpressionGraphV1(
        "metric-expression/v1",
        ("aggregate",),
        (
            MetricGraphNodeRecordV1(
                "aggregate",
                AggregateNodeV1(
                    "aggregate",
                    RefPayloadV1.from_ref(
                        ENTITY if method == "count" else ref.measure("sales.events.amount")
                    ),
                    "amount-v1",
                    method,
                    None,
                    filter=(CanonicalSliceEntryV1(_cluster().ref, ("bi",)),),
                ),
            ),
        ),
        (),
    )
    definition = DirectMetricDefinition(
        metric,
        graph,
        "aggregate",
        "metric-v1",
        "sales-v1",
        ENTITY,
        ref.time_dimension("sales.events.point"),
        None,
        empty,
        (),
    )
    quantity = ObservedQuantity(
        "observed",
        metric,
        "metric-v1",
        None,
        "window",
        "contributions",
        "strict",
        "sum_zero@v1" if method == "sum" and empty == "zero" else f"{method}@v1",
    )
    group_keys = (Coordinate(ENTITY, _cluster().ref.path, "group"),)
    if grouped:
        assert isinstance(members, MethodNode)
        category = members
        source = category.inputs[0].node
        domain = source.signature.domain
        members = method_node(
            (Edge("subject", source), Edge("subject", category)),
            AttachCategory(
                group_keys[0],
                replace(
                    domain,
                    instance_key=(*domain.instance_key, *group_keys),
                    target_key=(*domain.target_key, *group_keys),
                ),
                False,
            ),
            value_type=source.value_type,
        )
    target = (
        GroupObservationTarget(
            DomainSignature(
                members.signature.domain.binding, "group", group_keys, group_keys, "cluster-groups"
            ),
            group_keys,
        )
        if grouped
        else EntityObservationTarget(members.signature.domain)
    )
    start = None if grid_window else "2026-09-01T00:00:00+00:00"
    end = None if grid_window else "2026-09-08T00:00:00+00:00"
    filters = (OccurrenceFilter(_cluster(), "in", ("bi",)),)
    parameters: ObserveCount | ObserveMetric
    if method == "count":
        parameters = ObserveCount(
            definition,
            target,
            quantity,
            ENTITY,
            (),
            axis("%Y%m%d"),
            start,
            end,
            filters=filters,
            grid_window=grid_window,
        )
    else:
        parameters = ObserveMetric(
            definition,
            target,
            quantity,
            ENTITY,
            (),
            axis("%Y%m%d"),
            start,
            end,
            "amount",
            "int64",
            filters=filters,
            grid_window=grid_window,
            method=method,
        )
    return method_node(
        (Edge("subject", members),),
        parameters,
        value_type=ScalarType("float64" if method == "mean" else "int64"),
        sources=(leaf,),
    )


def _lower(root: MethodNode, bindings: tuple[SourceBinding, ...]) -> LoweredPlan:
    admitted = plan(
        root,
        routes=tuple(
            RouteChoice(node.identity, "ibis")
            for node in topology(root)
            if isinstance(node, MethodNode)
        ),
    )
    return lower(admitted, bindings=bindings)


def _primary(lowered: LoweredPlan) -> LoweredRelation:
    return next(
        stage
        for stage in lowered.stages
        if isinstance(stage, LoweredRelation) and stage.output == lowered.primary_output
    )


@pytest.mark.parametrize(
    "method,empty", [("sum", "null"), ("sum", "zero"), ("mean", "null"), ("count", "zero")]
)
def test_identity_observation_preserves_complete_members_and_original_state(
    monkeypatch: pytest.MonkeyPatch,
    method: Literal["sum", "mean", "count"],
    empty: Literal["null", "zero"],
) -> None:
    with _source() as (_, leaf, binding):
        root = _observation(_members(leaf), leaf, method=method, empty=empty)
        lowered = _lower(root, (binding,))
        primary = _primary(lowered)
        sql = ibis.to_sql(primary.expression, dialect="trino")
        ast = sqlglot.parse_one(sql, read="trino")
        assert not list(ast.find_all(sge.Distinct))
        assert all(join.side == "LEFT" for join in ast.find_all(sge.Join))
        assert len([table for table in ast.find_all(sge.Table) if table.name == "events"]) == 2
        actual = primary.expression.to_pyarrow()
        rows = {(row["key_0"], row["key_1"]): row for row in actual.to_pylist()}
        assert set(rows) == {("a", WIDE), ("a", 2), ("b", WIDE), ("a", 3), ("a", 4)}
        assert rows["a", WIDE]["value"] == (1 if method == "count" else 10)
        assert rows["b", WIDE]["value"] == (1 if method == "count" else 7)
        for key in (("a", 2), ("a", 3), ("a", 4)):
            expected = (
                1 if method == "count" and key == ("a", 2) else 0 if empty == "zero" else None
            )
            assert rows[key]["value"] == expected
            assert rows[key]["cell_tag"] == ("defined" if expected is not None else "null")
            assert rows[key]["cell_reason"] == (
                None if expected is not None else "empty_contribution"
            )
        if method != "count":
            if "original_state__row_count" in rows["a", 2]:
                assert rows["a", 2]["original_state__row_count"] == 1
            assert rows["a", 2]["original_state__non_null_count"] == 0
            assert rows["a", 3]["original_state__non_null_count"] == 0
        with monkeypatch.context() as baseline:
            baseline.setattr(graph_lowering, "_identity_observation", lambda *args: False)
            previous = _primary(_lower(root, (binding,))).expression.to_pyarrow()
        sort = [("key_0", "ascending"), ("key_1", "ascending")]
        assert actual.sort_by(sort).equals(previous.sort_by(sort))
        assert lowered.admitted.root is root
        assert primary.source_ids == (leaf.identity,)


def test_empty_identity_observation_keeps_an_empty_member_domain() -> None:
    with _source(empty=True) as (_, leaf, binding):
        result = _primary(
            _lower(_observation(_members(leaf), leaf), (binding,))
        ).expression.to_pyarrow()
        assert result.num_rows == 0
        assert result.schema.field("key_1").type == pa.int64()


def test_selected_members_keep_the_contribution_join() -> None:
    with _source() as (_, leaf, binding):
        read = _read_cluster(_members(leaf))
        selected = method_node(
            (Edge("subject", read),),
            PartsTransport(
                "where",
                read.signature.domain,
                ("subject",),
                False,
                (ValuePredicate(read.signature.domain.binding, "eq", "other"),),
            ),
            value_type=read.value_type,
        )
        members = method_node(
            (Edge("subject", selected),),
            MapCorrespond("subjects", leaf.signature.domain),
            value_type=leaf.value_type,
        )
        primary = _primary(_lower(_observation(members, leaf), (binding,)))
        sql = ibis.to_sql(primary.expression, dialect="trino")
        assert "INNER JOIN" in sql
        assert "SELECT DISTINCT" not in sql
        rows = primary.expression.to_pyarrow().to_pylist()
        assert [(row["key_0"], row["key_1"], row["value"]) for row in rows] == [("a", 3, None)]


def test_group_projection_still_deduplicates_and_maps_members() -> None:
    with _source(compound=False) as (_, leaf, binding):
        read = _read_cluster(_members(leaf))
        primary = _primary(_lower(_observation(read, leaf, grouped=True), (binding,)))
        sql = ibis.to_sql(primary.expression, dialect="trino")
        assert "SELECT DISTINCT" in sql
        assert "INNER JOIN" in sql
        rows = primary.expression.to_pyarrow().to_pylist()
        assert {row["key_0"]: row["value"] for row in rows} == {"bi": 17, "other": None}


def test_equal_entity_refs_do_not_authorize_identity_join_elimination() -> None:
    with _source() as (session, leaf, binding):
        other = replace(leaf, identity="other-source")
        other_binding = replace(
            binding,
            leaf=other,
            source=session.bind(TableSourceIR("events"), source_identity=other.identity),
        )
        primary = _primary(_lower(_observation(_members(leaf), other), (binding, other_binding)))
        assert "INNER JOIN" in ibis.to_sql(primary.expression, dialect="trino")
        assert set(primary.source_ids) == {leaf.identity, other.identity}


def test_missing_source_uniqueness_keeps_the_general_mapping() -> None:
    with _source() as (_, leaf, binding):
        undeclared = replace(leaf, signature=replace(leaf.signature, evidence=()))
        primary = _primary(
            _lower(_observation(undeclared, undeclared), (replace(binding, leaf=undeclared),))
        )
        sql = ibis.to_sql(primary.expression, dialect="trino")
        assert "SELECT DISTINCT" in sql
        assert "INNER JOIN" in sql


def test_time_product_keeps_each_cell_mapping() -> None:
    with _source() as (_, leaf, binding):
        grid = bind_grid(
            mv.time_scope(start="2026-09-01", end="2026-09-08"),
            mv.grain("day"),
            report_timezone="UTC",
        )
        domain = replace(
            leaf.signature.domain,
            instance_key=(
                *leaf.signature.domain.instance_key,
                Coordinate(ENTITY, "time:" + grid.identity, "anchor"),
            ),
            time_grid=grid,
        )
        members = method_node(
            (Edge("subject", _members(leaf)),),
            TimeProduct(domain, "time_product"),
            value_type=leaf.value_type,
        )
        primary = _primary(_lower(_observation(members, leaf, grid_window=True), (binding,)))
        sql = ibis.to_sql(primary.expression, dialect="trino")
        assert "INNER JOIN" in sql
        result = primary.expression.to_pyarrow()
        assert result.num_rows == 35
        assert sorted(
            row["value"] for row in result.to_pylist() if row["cell_tag"] == "defined"
        ) == [7, 10]


@pytest.mark.runtime
def test_public_identity_observation_executes_and_retains_outside_window_members(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    reads: list[str] = []
    original = SourceSession.batches

    def traced(self: SourceSession, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        if read.purpose == "analysis.graph.stage":
            reads.append(read.sql)
        return original(self, read, chunk_size=chunk_size)

    monkeypatch.setattr(SourceSession, "batches", traced)
    members = case.session.members(ms.ref.entity(f"{case.names.domain}.{case.names.order}"))
    observed = members.observe(
        ms.ref.metric(f"{case.names.domain}.{case.names.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(ms.ref.entity(f"{case.names.domain}.{case.names.order}"),),
    )
    result = observed.execute()
    assert result._dataset is not None
    exchange = result._dataset.verified()
    assert {row["key_0"]: row["value"] for row in exchange.primary.to_pylist()} == {
        "j1_a": 450,
        "j1_b": 150,
        "j1_c": 400,
        "j1_july": None,
        "j1_september": None,
    }
    assert {part.role for part in exchange.parts} == {"subject", "original_state", "coverage"}
    assert all(part.table.num_rows == 5 for part in exchange.parts)
    assert len(reads) == 1
    assert "SELECT DISTINCT" not in reads[0]
    assert "INNER JOIN" not in reads[0]
    assert observed.rollup().execute().to_pandas().iloc[0]["value"] == 1000
    assert case.session._runtime.store.resources(case.session.id) == ()
