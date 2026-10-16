"""R2.3 Query execute preparation, pure planning and scheduling over real DuckDB reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo._temporal import TimeScope
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.graph_plan import (
    QUERY_COMBINE_KERNEL,
    LocalCompute,
    PlannedCheck,
    SourceCheckRoute,
    SourceRead,
    plan_query,
)
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import Binding, CoreRuleError, Fact, Obligation
from marivo.analysis.dsl.temporal import CalendarAxis
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_execution import run_query_plan
from marivo.analysis.materialization.graph_local_execution import run_local
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    open_query_sources,
    read_stage,
)
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.expressions import Dimension, Field, Metric, Predicate

BIG = 9007199254740993

MODEL = """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource("warehouse")
customers = ms.entity(name="customers", datasource=warehouse, source=md.table("customers"), primary_key=["id"])
customer_id = ms.dimension_column(name="id", entity=customers, column="id")
segment = ms.dimension_column(name="segment", entity=customers, column="segment")
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"), primary_key=["id"])
order_customer = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")
region = ms.dimension_column(name="region", entity=orders, column="region")
code = ms.dimension_column(name="code", entity=orders, column="code")
order_time = ms.time_dimension_column(name="ordered_at", entity=orders, column="ordered_at", granularity="day", is_default=True)
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
buyer = ms.relationship(name="buyer", from_entity=orders, to_entity=customers, keys=[ms.join_on(order_customer, customer_id)])
refunds = ms.entity(name="refunds", datasource=warehouse, source=md.table("refunds"), primary_key=["id"])
refund_time = ms.time_dimension_column(name="refunded_at", entity=refunds, column="refunded_at", granularity="day", is_default=True)
refund_amount = ms.measure_column(name="amount", entity=refunds, column="amount", additivity=ms.additive_all(), unit="CNY")
finalized = ms.finalize()
amount_value = finalized.resolve(amount)
refund_value = finalized.resolve(refund_amount)
@ms.metric()
def revenue():
    return amount_value.sum()
@ms.metric()
def refund_total():
    return refund_value.sum()
@ms.metric()
def net():
    return revenue - refund_total
"""


def _seed(path: Path) -> None:
    connection = duckdb.connect(str(path))
    try:
        connection.execute("SET threads = 1")
        connection.execute("CREATE TABLE customers (id BIGINT, segment VARCHAR)")
        connection.execute("INSERT INTO customers VALUES (1, 'retail')")
        connection.execute(
            "CREATE TABLE orders (id BIGINT, customer_id BIGINT, region VARCHAR, "
            "code UBIGINT, ordered_at TIMESTAMPTZ, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES "
            f"(1, 1, 'APAC', 1, '2026-01-05 10:00:00+00', {BIG}), "
            "(2, 1, 'APAC', 1, '2026-01-06 10:00:00+00', NULL), "
            "(3, 1, 'EMEA', 1, '2026-01-07 10:00:00+00', 5), "
            "(4, 1, 'APAC', 1, '2026-02-01 00:00:00+00', 7)"
        )
        connection.execute(
            "CREATE TABLE refunds (id BIGINT, refunded_at TIMESTAMPTZ, amount BIGINT)"
        )
        connection.execute("INSERT INTO refunds VALUES (10, '2026-01-09 00:00:00+00', 3)")
    finally:
        connection.close()


@pytest.fixture
def warehouse_session(
    semantic_project_factory: Callable[..., object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "warehouse.duckdb"
    _seed(database)
    datasources = tmp_path / "models" / "datasources"
    datasources.mkdir(parents=True)
    (datasources / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
    )
    semantic_project_factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n',
            "sales/models.py": MODEL,
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("schedule", report_timezone="UTC")


def _query(
    session: Session,
    name: str,
    *,
    during: TimeScope | CalendarAxis | None = None,
    by: tuple[Field, ...] = (),
    where: Predicate | None = None,
) -> BoundGraph:
    metric = session.catalog.metrics.get(f"sales.{name}")
    assert isinstance(metric, Metric)
    return session.observe({name: metric}, during=during, by=by, where=where)._graph


def _dimension(session: Session, path: str) -> Dimension:
    field = session.catalog.fields.get(path)
    assert isinstance(field, Dimension)
    return field


def _run(prepared: PreparedSources, graph: BoundGraph) -> StageOutput:
    plan = plan_query(graph, prepared.schemas)
    lowered = lower_query(plan, prepared.relations)

    def local(stage: LocalCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        raise AssertionError(f"no local stage expected: {stage}")

    def check(requirement: PlannedCheck, output: StageOutput) -> None:
        raise AssertionError(f"no planned check expected: {requirement}")

    return run_query_plan(
        plan, lambda s: read_stage(prepared, lowered.read(s.stage_id)), local, check
    )


def test_single_root_plan_reads_once_and_publishes_its_source_stage(
    warehouse_session: Session,
) -> None:
    session = warehouse_session
    region = _dimension(session, "sales.orders.region")
    graph = _query(
        session,
        "revenue",
        during=session.time_scope(start="2026-01-01", end="2026-02-01"),
        where=region == "APAC",
    )
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        assert prepared.source.submissions == []
        plan = plan_query(graph, prepared.schemas)
        assert len(plan.stages) == 1 and isinstance(plan.stages[0], SourceRead)
        assert plan.publish == plan.stages[0].stage_id and plan.consumers == ()
        assert plan_query(graph, prepared.schemas) == plan
        output = _run(prepared, graph)
        assert len(prepared.source.submissions) == 1
    slots = [column.slot_id for column in output.columns]
    assert output.keys == (slots[0],)
    table = output.table.sort_values(slots[0]).reset_index(drop=True)
    assert table[slots[0]].tolist() == [1, 2]
    amount = next(c.slot_id for c in output.columns if c.name == "sales.orders.amount")
    assert table[amount].iloc[0] == BIG and pd.isna(table[amount].iloc[1])
    assert str(table[amount].dtype) == "int64[pyarrow]"
    assert session.runs().items == ()


def test_multi_root_plan_combines_locally_without_mutating_shared_reads(
    warehouse_session: Session,
) -> None:
    session = warehouse_session
    graph = _query(session, "net", during=session.time_scope(start="2026-01-01", end="2026-02-01"))
    seen: list[tuple[pd.DataFrame, ...]] = []
    rows = (DataColumn("n.0", "rows", ScalarDataType("int64"), False),)

    def kernel(stage: LocalCompute, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
        seen.append(inputs)
        for frame in inputs:
            frame.iloc[:, 0] = -1
        return pd.DataFrame({"n.0": [sum(len(frame) for frame in inputs)]})

    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        plan = plan_query(graph, prepared.schemas)
        reads = [stage for stage in plan.stages if isinstance(stage, SourceRead)]
        combine = plan.stages[-1]
        assert isinstance(combine, LocalCompute) and combine.kernel == QUERY_COMBINE_KERNEL
        assert {read.source_id for read in reads} == {"sales.orders", "sales.refunds"}
        assert plan.publish == combine.stage_id
        assert all(plan.consumers_of(read.stage_id) == (combine.stage_id,) for read in reads)
        outputs: dict[str, StageOutput] = {}
        lowered = lower_query(plan, prepared.relations)

        def read(stage: SourceRead) -> StageOutput:
            outputs[stage.stage_id] = read_stage(prepared, lowered.read(stage.stage_id))
            return outputs[stage.stage_id]

        published = run_query_plan(
            plan,
            read,
            lambda stage, inputs: run_local(replace(stage, columns=rows), inputs, kernel),
            lambda requirement, output: None,
        )
    assert published.table["n.0"].tolist() == [4]
    assert all((output.table.iloc[:, 0] != -1).all() for output in outputs.values())
    assert len(seen) == 1


def test_lossy_source_type_rejects_before_any_business_read(
    warehouse_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = warehouse_session
    code = _dimension(session, "sales.orders.code")
    graph = _query(session, "revenue", by=(code,))

    def forbidden(self: SourceSession, read: object) -> None:
        raise AssertionError("preparation submitted a business read")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    with (
        pytest.raises(MaterializationError) as caught,
        open_query_sources(session._runtime.connection_service(), graph, session.catalog._state),
    ):
        pass
    assert "sales.orders.code: uint64" in str(caught.value)


def test_execute_prepares_and_plans_then_reports_the_remaining_stage_gap(
    warehouse_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = warehouse_session
    segment = _dimension(session, "sales.customers.segment")
    revenue = session.catalog.metrics.get("sales.revenue")
    assert isinstance(revenue, Metric)

    def forbidden(self: SourceSession, read: object) -> None:
        raise AssertionError("execute submitted a business read")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    query = session.observe({"revenue": revenue})
    with pytest.raises(AnalysisStageUnavailableError, match=r"R2\.4") as caught:
        query.execute()
    assert caught.value.stage == "query_execution" and caught.value.run_ref is None
    assert "prepared R2.3 plan" in str(caught.value)
    import marivo.datasource.backends as backends

    def no_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("route gap opened a datasource")

    monkeypatch.setattr(backends, "_build_backend_from_effective", no_connection)
    with pytest.raises(AnalysisStageUnavailableError, match="relationship path") as path_gap:
        session.observe({"revenue": revenue}, by=(segment,)).execute()
    assert path_gap.value.run_ref is None
    assert session.runs().items == ()


def test_planning_is_pure_over_prepared_schema_facts(warehouse_session: Session) -> None:
    session = warehouse_session
    graph = _query(session, "net")
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        schemas = prepared.schemas
    # The source session is closed; planning consumes only the frozen schema facts.
    plan = plan_query(graph, schemas)
    assert [stage.stage_id for stage in plan.stages][-1] == plan.publish
    reads = [stage for stage in plan.stages if isinstance(stage, SourceRead)]
    assert all(read.period is None for read in reads)
    assert all(
        column.slot_id.startswith(read.stage_id) for read in reads for column in read.columns
    )
    with pytest.raises(CoreRuleError, match="prepared schema facts"):
        plan_query(graph, schemas[:1])


@pytest.mark.parametrize("deadline", ("consume", "publish"))
@pytest.mark.parametrize("fail_check", (False, True))
@pytest.mark.parametrize("same_root", (False, True))
def test_source_check_runs_on_its_exact_output_before_consumption(
    warehouse_session: Session,
    deadline: Literal["consume", "publish"],
    fail_check: bool,
    same_root: bool,
) -> None:
    session = warehouse_session
    graph = _query(session, "net")
    if same_root:
        revenue = session.catalog.metrics.get("sales.revenue")
        assert isinstance(revenue, Metric)
        graph = session.observe({"first": revenue, "second": revenue})._graph
    owner = graph.signature.owner
    obligation = Obligation(
        Fact(
            "finite_numeric",
            Binding(owner.session_id, owner.owner_id, graph.fingerprint, graph.fingerprint),
            "sales.orders.amount",
            "v1",
        ),
        "source.finite_numeric@v1",
        deadline,
    )
    graph = replace(graph, signature=replace(graph.signature, obligations=(obligation,)))
    observation = graph.signature.observation
    assert observation is not None
    occurrence = observation.contributions[1 if same_root else 0].occurrence
    route = SourceCheckRoute(obligation, occurrence)
    events: list[str] = []
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        with pytest.raises(AnalysisStageUnavailableError, match="bound to its source occurrence"):
            plan_query(graph, prepared.schemas)
        with pytest.raises(CoreRuleError, match="exact source contribution occurrence"):
            plan_query(graph, prepared.schemas, checks=(replace(route, occurrence=(999,)),))
        assert prepared.source.submissions == []
        plan = plan_query(graph, prepared.schemas, checks=(route,))
        lowered = lower_query(plan, prepared.relations)

        def read(stage: SourceRead) -> StageOutput:
            events.append(stage.source_id)
            return read_stage(prepared, lowered.read(stage.stage_id))

        def check(requirement: PlannedCheck, output: StageOutput) -> None:
            assert requirement.obligation == obligation
            assert requirement.deadline == deadline
            assert "sales.orders.amount" in {column.name for column in output.columns}
            events.append("check")
            if fail_check:
                raise ValueError("source check rejected")

        def local(stage: LocalCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
            assert events.count("check") == 1
            events.append("local")
            # Only exercise scheduling, without implementing the R2.4 combine kernel.
            return inputs[0]

        if fail_check:
            with pytest.raises(ValueError, match="source check rejected"):
                run_query_plan(plan, read, local, check)
            assert events == (["sales.orders"] * (2 if same_root else 1)) + ["check"]
        else:
            run_query_plan(plan, read, local, check)
            assert events == (
                ["sales.orders", "sales.orders", "check", "local"]
                if same_root
                else ["sales.orders", "check", "sales.refunds", "local"]
            )


@pytest.mark.parametrize("renamed", (False, True))
@pytest.mark.parametrize("kind", ("duration", "timestamp_ns", "timestamp_us"))
def test_parquet_source_metadata_controls_lossless_preparation(
    semantic_project_factory: Callable[..., object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    renamed: bool,
) -> None:
    path = tmp_path / "facts.parquet"
    dtype = pa.duration("ns") if kind == "duration" else pa.timestamp(kind[-2:], "UTC")
    ticks = {
        "duration": 123456789,
        "timestamp_ns": 1767225600000000001,
        "timestamp_us": 1767225600000001,
    }[kind]
    pq.write_table(
        pa.table(
            {
                "id": [1],
                "raw_value": pa.array([ticks], type=dtype),
                "unused": pa.array([1], type=pa.duration("ns")),
            }
        ),
        path,
    )
    entity = (
        f'@ms.entity(datasource=warehouse, source=md.parquet({str(path)!r}), primary_key=["id"])\n'
        'def facts(raw):\n    return raw.select("id", value=raw.raw_value)\n'
        if renamed
        else f'facts = ms.entity(name="facts", datasource=warehouse, source=md.parquet({str(path)!r}), primary_key=["id"])\n'
    )
    column = "value" if renamed else "raw_value"
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n',
            "sales/models.py": (
                'import marivo.datasource as md\nimport marivo.semantic as ms\nwarehouse = ms.ref.datasource("warehouse")\n'
                + entity
                + f'value = ms.measure_column(name="value", entity=facts, column={column!r}, additivity=ms.additive_all())\n'
                "finalized = ms.finalize()\nv = finalized.resolve(value)\n"
                "@ms.metric()\ndef total():\n    return v.min()\n"
            ),
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("metadata", report_timezone="UTC")
    graph = _query(session, "total")
    if kind == "timestamp_us":
        with open_query_sources(
            session._runtime.connection_service(), graph, session.catalog._state
        ) as prepared:
            output = _run(prepared, graph)
        slot = next(c.slot_id for c in output.columns if c.name == "sales.facts.value")
        assert pd.Timestamp(output.table[slot].iloc[0]).value == ticks * 1000
    else:

        def forbidden(self: SourceSession, read: object) -> None:
            raise AssertionError("lossy preparation submitted a business read")

        monkeypatch.setattr(SourceSession, "execute", forbidden)
        expected = AnalysisStageUnavailableError if kind == "duration" else MaterializationError
        with (
            pytest.raises(expected, match=r"Duration|timestamp"),
            open_query_sources(
                session._runtime.connection_service(), graph, session.catalog._state
            ),
        ):
            pytest.fail("lossy source metadata was ignored")
    assert session.runs().items == ()
