"""R2.3 Query execute preparation, pure planning and scheduling over real DuckDB reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import TimeScope
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.graph_plan import (
    LocalCompute,
    SourceRead,
    plan_query,
)
from marivo.analysis.core.data_types import DataColumn, DecimalDataType, ScalarDataType
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    LiteralFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.dsl.temporal import CalendarAxis
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_execution import run_query_plan
from marivo.analysis.materialization.graph_local_execution import run_local
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    execute_numeric_query,
    open_query_sources,
    read_stage,
)
from marivo.analysis.methods.physical import QUERY_COMBINE_KERNEL
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.errors import SemanticExpressionError, SemanticRuntimeError
from tests.shared_fixtures import (
    catalog_value,
    numeric_literal_node,
    numeric_program_frame,
    terminal_runs,
)

BIG = 9007199254740993

MODEL = """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource("warehouse")
customers = ms.entity(name="customers", datasource=warehouse, source=md.table("customers"), primary_key=["id"])
customer_id = ms.dimension_column(name="id", entity=customers, column="id")
segment = ms.dimension_column(name="segment", entity=customers, column="segment")
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"), primary_key=["id"])
price = ms.measure_column(name="price", entity=orders, column="price", additivity=ms.additive_all())
weight = ms.measure_column(name="weight", entity=orders, column="weight", additivity=ms.additive_all())
order_customer = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")
region = ms.dimension_column(name="region", entity=orders, column="region")
code = ms.dimension_column(name="code", entity=orders, column="code")
order_time = ms.time_dimension_column(name="ordered_at", entity=orders, column="ordered_at", is_default=True)
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
buyer = ms.relationship(name="buyer", from_entity=orders, to_entity=customers, keys=[ms.join_on(order_customer, customer_id)])
refunds = ms.entity(name="refunds", datasource=warehouse, source=md.table("refunds"), primary_key=["id"])
refund_time = ms.time_dimension_column(name="refunded_at", entity=refunds, column="refunded_at", is_default=True)
refund_amount = ms.measure_column(name="amount", entity=refunds, column="amount", additivity=ms.additive_all(), unit="CNY")
amount_value = amount
refund_value = refund_amount
region_value = region
weight_value = weight
@ms.metric()
def masked_overflow():
    return (amount_value.sum() * 4611686018427387904) * 0
@ms.metric()
def tiny_mean():
    return (weight_value * 0 + 1e-308).weighted_mean(weight=weight_value)
@ms.metric()
def revenue():
    return amount_value.sum()
@ms.metric()
def refund_total():
    return refund_value.sum()
@ms.metric()
def net():
    return revenue - refund_total
@ms.metric()
def sampled():
    return revenue.per(region_value).mean()
"""


def _source_graph(frame: mv.AnalysisFrame) -> BoundGraph:
    backing = frame._backing
    assert isinstance(backing, ExpressionFrameBacking)
    assert isinstance(backing.root, (SourceFrameBinding, LiteralFrameBinding))
    return backing.root.graph


def _seed(path: Path) -> None:
    connection = duckdb.connect(str(path))
    try:
        connection.execute("SET threads = 1")
        connection.execute("CREATE TABLE customers (id BIGINT, segment VARCHAR)")
        connection.execute("INSERT INTO customers VALUES (1, 'retail')")
        connection.execute(
            "CREATE TABLE orders (id BIGINT, customer_id BIGINT, region VARCHAR, "
            "code UBIGINT, ordered_at TIMESTAMPTZ, amount BIGINT, weight DOUBLE, price DECIMAL(12,2))"
        )
        connection.execute(
            "INSERT INTO orders VALUES "
            f"(1, 1, 'APAC', 1, '2026-01-05 10:00:00+00', {BIG}, 2, 1.25), "
            "(2, 1, 'APAC', 1, '2026-01-06 10:00:00+00', NULL, 4, 2.50), "
            "(3, 1, 'EMEA', 1, '2026-01-07 10:00:00+00', 5, NULL, 3.75), "
            "(4, 1, 'APAC', 1, '2026-02-01 00:00:00+00', 7, -2, NULL)"
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
    return mv.session.get_or_create("schedule", timezone="UTC")


def _query(
    session: Session,
    name: str,
    *,
    during: TimeScope | CalendarAxis | None = None,
    by: tuple[Deferred, ...] = (),
    where: Deferred | None = None,
) -> BoundGraph:
    metric = catalog_value(session.catalog, f"sales.{name}", kind="metric")
    assert isinstance(metric, (ms.RowExpression, ms.MetricExpression))
    return _source_graph(
        session.observe({name: metric}, by=by if during is None else (during, *by), where=where)
    )


def _dimension(session: Session, path: str) -> ms.Dimension | ms.TimeDimension | ms.Measure:
    field = catalog_value(session.catalog, path, kind="field")
    assert isinstance(field, (ms.Dimension, ms.TimeDimension, ms.Measure))
    return field


def _run(prepared: PreparedSources, graph: BoundGraph) -> StageOutput:
    plan = plan_query(graph, prepared.schemas)
    lowered = lower_query(plan, prepared.relations)

    return execute_numeric_query(prepared, lowered)


def test_single_root_plan_reads_once_and_publishes_its_source_stage(
    warehouse_session: Session,
) -> None:
    session = warehouse_session
    region = _dimension(session, "sales.orders.region")
    time = _dimension(session, "sales.orders.ordered_at")
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    graph = _query(session, "revenue", where=(region == "APAC") & time.within(scope))
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
    assert output.keys == ()
    assert output.table["value.0"].tolist() == [BIG]
    assert output.columns[-1].unit == "CNY"
    assert str(output.table["value.0"].dtype) == "int64[pyarrow]"
    assert terminal_runs(session) == ()


def test_multi_root_plan_combines_locally_without_mutating_shared_reads(
    warehouse_session: Session,
) -> None:
    session = warehouse_session
    graph = _query(session, "net", during=session.time_scope(start="2026-01-01", end="2026-02-01"))
    seen: list[tuple[pd.DataFrame, ...]] = []
    rows = (
        DataColumn("key.0", "interval", ScalarDataType("int64"), False),
        DataColumn("n.0", "rows", ScalarDataType("int64"), False),
    )

    def kernel(stage: LocalCompute, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
        seen.append(inputs)
        for frame in inputs:
            frame.iloc[:, 0] = -1
        return pd.DataFrame({"key.0": [0], "n.0": [sum(len(frame) for frame in inputs)]})

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
            outputs[stage.stage_id] = read_stage(prepared, stage, lowered.read(stage.stage_id))
            return outputs[stage.stage_id]

        published = run_query_plan(
            plan,
            read,
            lambda stage, inputs: run_local(replace(stage, columns=rows), inputs, kernel),
        )
    assert published.table["n.0"].tolist() == [2]
    assert all((output.table.iloc[:, 0] != -1).all() for output in outputs.values())
    assert len(seen) == 1


def test_local_program_has_no_independent_kernel_selector() -> None:
    from dataclasses import fields

    from marivo.analysis.compiler.observation import FormulaProgram

    stage = LocalCompute("local:formula", (), (), (), FormulaProgram((), (), ()))
    assert stage.kernel == QUERY_COMBINE_KERNEL
    assert {field.name for field in fields(stage)}.isdisjoint({"kernel", "result", "fused_steps"})


def test_uint64_prepares_exact_decimal_before_any_business_read(
    warehouse_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = warehouse_session
    code = _dimension(session, "sales.orders.code")
    graph = _query(session, "revenue", by=(code,))

    def forbidden(self: SourceSession, read: object) -> None:
        raise AssertionError("preparation submitted a business read")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        code_field = next(
            field for field in prepared.schemas[0].fields if field.field == "sales.orders.code"
        )
        assert code_field.dtype == DecimalDataType(20, 0)


def test_persist_publishes_prepared_numeric_and_path_routes(warehouse_session: Session) -> None:
    session = warehouse_session
    segment = _dimension(session, "sales.customers.segment")
    revenue = session.catalog.get("metric:sales.revenue")
    assert isinstance(revenue, (ms.RowExpression, ms.MetricExpression))
    first = session.observe({"revenue": revenue}).persist()
    second = session.observe({"revenue": revenue}, by=(segment,)).persist()
    assert first.artifact_id != second.artifact_id
    assert len(terminal_runs(session)) == 2
    assert all(run.lifecycle == "succeeded" for run in terminal_runs(session))


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
    with pytest.raises(CoreRuleError, match="prepared schema facts"):
        plan_query(graph, schemas[:1])


@pytest.mark.parametrize("fail_check", (False, True))
@pytest.mark.parametrize("same_root", (False, True))
def test_real_source_checks_and_strict_formula_keep_occurrence_order(
    warehouse_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fail_check: bool,
    same_root: bool,
) -> None:
    from marivo.analysis.materialization import graph_source_execution as execution
    from marivo.semantic._numeric_lowering import NumericCheck

    session = warehouse_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    strict = amount.sum(nulls="error")
    if not fail_check:
        strict = amount.sum(nulls="error").where(~amount.isnull())
    metrics = (
        {"first": amount.sum(), "second": strict}
        if same_root
        else {
            "first": strict,
            "second": catalog_value(session.catalog, "sales.refunds.amount", kind="field").sum(),
        }
    )
    graph = _source_graph(session.observe(metrics))
    events: list[tuple[str, str]] = []
    completed_reads: dict[str, StageOutput] = {}
    validate = execution._validate_source_checks

    def checked(
        stage: SourceRead,
        frame: pd.DataFrame,
        numeric_checks: tuple[tuple[str, NumericCheck], ...],
    ) -> None:
        events.append(("check-start", stage.stage_id))
        validate(stage, frame, numeric_checks)
        events.append(("check-complete", stage.stage_id))

    def adapt(identifier: str, output: StageOutput) -> StageOutput:
        assert events[-1] == ("check-complete", identifier)
        events.append(("adapt-and-transport", identifier))
        completed_reads[identifier] = output
        return output

    def local(stage: LocalCompute, output: StageOutput) -> None:
        events.append(("local-complete", stage.stage_id))

    monkeypatch.setattr(execution, "_validate_source_checks", checked)
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        plan = plan_query(graph, prepared.schemas)
        reads = tuple(stage for stage in plan.stages if isinstance(stage, SourceRead))
        assert len(reads) == 2 and reads[0].occurrence != reads[1].occurrence
        if same_root:
            assert reads[0].source_id == reads[1].source_id == "sales.orders"
        lowered = lower_query(plan, prepared.relations)
        if fail_check:
            with pytest.raises(SemanticExpressionError, match="non-NA source values") as caught:
                execute_numeric_query(
                    prepared,
                    lowered,
                    observe_local=local,
                    source_outputs={read.stage_id: read.stage_id for read in reads},
                    adapt_source=adapt,
                )
            assert caught.value.expected == "non-NA source values"
            assert caught.value.received == "sum(nulls='error'); coordinates=[]"
            strict_read = reads[1 if same_root else 0]
            flags = tuple(
                name
                for name, check in lowered.read(strict_read.stage_id).numeric_checks
                if check.expected == "non-NA source values"
            )
            assert flags
            # Ordinary formula demand owns these retained flags. Raising them
            # during source adaptation would make conditional branches eager.
            assert any(completed_reads[strict_read.stage_id].table[name].any() for name in flags)
            assert events == [
                event
                for read in reads
                for event in (
                    ("check-start", read.stage_id),
                    ("check-complete", read.stage_id),
                    ("adapt-and-transport", read.stage_id),
                )
            ]
            assert len(prepared.source.submissions) == 2
        else:
            output = execute_numeric_query(
                prepared,
                lowered,
                observe_local=local,
                source_outputs={read.stage_id: read.stage_id for read in reads},
                adapt_source=adapt,
            )
            assert len(output.table) == 1
            assert events == [
                *(
                    event
                    for read in reads
                    for event in (
                        ("check-start", read.stage_id),
                        ("check-complete", read.stage_id),
                        ("adapt-and-transport", read.stage_id),
                    )
                ),
                ("local-complete", plan.publish),
            ]
            assert len(prepared.source.submissions) == 2
    assert terminal_runs(session) == ()


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
                "v = value\n"
                "@ms.metric()\ndef total():\n    return v.min()\n"
            ),
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("metadata", timezone="UTC")
    graph = _query(session, "total")
    if kind == "duration":
        with open_query_sources(
            session._runtime.connection_service(), graph, session.catalog._state
        ) as prepared:
            output = _run(prepared, graph)
        slot = "value.0"
        assert str(output.table[slot].dtype) == "duration[ns][pyarrow]"
        assert pa.array(output.table[slot]).cast(pa.int64()).to_pylist() == [ticks]
    elif kind in ("timestamp_us", "timestamp_ns"):
        # A timestamp is not a quantitative Measure carrier. The shared
        # physical contract now rejects it during source preparation.
        with (
            pytest.raises(SemanticRuntimeError, match="numeric or duration physical type"),
            open_query_sources(
                session._runtime.connection_service(), graph, session.catalog._state
            ) as prepared,
        ):
            plan_query(graph, prepared.schemas)
    else:

        def forbidden(self: SourceSession, read: object) -> None:
            raise AssertionError("lossy preparation submitted a business read")

        monkeypatch.setattr(SourceSession, "execute", forbidden)
        expected = MaterializationError
        with (
            pytest.raises(expected, match=r"Duration|timestamp"),
            open_query_sources(
                session._runtime.connection_service(), graph, session.catalog._state
            ),
        ):
            pytest.fail("lossy source metadata was ignored")
    assert terminal_runs(session) == ()


def _numeric_inputs(session: Session) -> tuple[Deferred, Deferred]:
    connection = duckdb.connect(str(session.catalog.workspace_dir / "warehouse.duckdb"))
    try:
        connection.execute(
            "UPDATE orders SET amount = CASE id WHEN 1 THEN 10 WHEN 2 THEN NULL WHEN 3 THEN 30 ELSE 20 END"
        )
    finally:
        connection.close()
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    weight = catalog_value(session.catalog, "sales.orders.weight", kind="field")
    assert isinstance(amount, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        weight, (ms.RowExpression, ms.MetricExpression)
    )
    return amount, weight


def _compute(
    session: Session,
    metrics: dict[str, Deferred],
    *,
    during: TimeScope | mv.CalendarAxis | None = None,
    by: tuple[Deferred, ...] = (),
    where: Deferred | None = None,
) -> StageOutput:
    graph = _source_graph(
        session.observe(metrics, by=by if during is None else (during, *by), where=where)
    )
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        return _run(prepared, graph)


def test_source_numeric_reducers(warehouse_session: Session) -> None:
    amount, _ = _numeric_inputs(warehouse_session)
    reducers = {
        "sum": amount.sum(),
        "count": amount.count(),
        "count_distinct": amount.count_distinct(),
        "mean": amount.mean(),
        "min": amount.min(),
        "max": amount.max(),
        "median": amount.median(),
        "quantile": amount.quantile(0.25),
    }
    output = _compute(warehouse_session, reducers)
    expected = {
        "sum": 60,
        "count": 3,
        "count_distinct": 3,
        "mean": 20,
        "min": 10,
        "max": 30,
        "median": 20,
        "quantile": 15,
    }
    for index, (reducer, value) in enumerate(expected.items()):
        assert output.table[f"value.{index}"].iloc[0] == pytest.approx(value), reducer
    assert output.keys == ()


def test_empty_all_na_and_strict_policies(warehouse_session: Session) -> None:
    session = warehouse_session
    amount, _ = _numeric_inputs(session)
    region = _dimension(session, "sales.orders.region")
    for predicate, is_empty in ((region == "missing", True), (amount.isnull(), False)):
        output = _compute(
            session,
            {"sum": amount.sum(), "count": amount.count(), "mean": amount.mean()},
            where=predicate,
        )
        assert (
            output.table["value.0"].iloc[0] == 0
            if is_empty
            else pd.isna(output.table["value.0"].iloc[0])
        )
        assert output.table["value.1"].iloc[0] == 0
        assert pd.isna(output.table["value.2"].iloc[0])
    with pytest.raises(SemanticExpressionError, match="non-NA source values"):
        _compute(session, {"sum": amount.sum(nulls="error")})


def test_weighted_mean_pairs_and_zero_policies(warehouse_session: Session) -> None:
    session = warehouse_session
    amount, weight = _numeric_inputs(session)
    january = session.time_scope(start="2026-01-01", end="2026-02-01")
    output = _compute(session, {"weighted": amount.weighted_mean(weight=weight)}, during=january)
    assert output.table["value.0"].iloc[0] == 10
    output = _compute(session, {"weighted": amount.weighted_mean(weight=weight)})
    assert pd.isna(output.table["value.0"].iloc[0])
    with pytest.raises(SemanticExpressionError, match="non-NA value/weight pairs"):
        _compute(session, {"weighted": amount.weighted_mean(weight=weight, nulls="error")})


def test_row_and_metric_formulas_mask_before_division(warehouse_session: Session) -> None:
    session = warehouse_session
    amount, weight = _numeric_inputs(session)
    result = _compute(
        session,
        {
            "row": (amount / weight).mean(),
            "metric": amount.sum() / weight.sum(),
            "net": (amount.sum() - 5) * 2,
            "negative": -amount.sum(),
        },
    )
    assert result.table["value.0"].iloc[0] == -2.5
    assert result.table["value.1"].iloc[0] == 15
    assert result.table["value.2"].iloc[0] == 110
    assert result.table["value.3"].iloc[0] == -60
    zero = amount.sum() - amount.sum()
    output = _compute(session, {"ratio": amount.sum() / zero})
    assert pd.isna(output.table["value.0"].iloc[0])


def test_complete_keys_and_null_classification(warehouse_session: Session) -> None:
    session = warehouse_session
    amount, _ = _numeric_inputs(session)
    region = _dimension(session, "sales.orders.region")
    left = amount.sum().where(region == "APAC")
    right = amount.sum().where(region == "EMEA")
    output = _compute(session, {"difference": left - right}, by=(region,))
    table = output.table.set_index("key.0")
    assert table.loc["APAC", "value.0"] == 30
    assert table.loc["EMEA", "value.0"] == -30
    connection = duckdb.connect(str(session.catalog.workspace_dir / "warehouse.duckdb"))
    try:
        connection.execute("UPDATE orders SET region = NULL WHERE id = 1")
    finally:
        connection.close()
    output = _compute(session, {"sum": amount.sum(), "mean": amount.mean()}, by=(region,))
    assert len(output.table) == 3
    assert output.table.loc[output.table["key.0"].isna(), "value.0"].tolist() == [10]


def test_calendar_fills_only_empty_reductions(warehouse_session: Session) -> None:
    session = warehouse_session
    amount, _ = _numeric_inputs(session)
    calendar = session.time_scope(start="2026-01-05", end="2026-01-09").by(mv.grain("day"))
    output = _compute(session, {"sum": amount.sum(), "mean": amount.mean()}, during=calendar)
    assert output.table["key.0"].tolist() == list(pd.date_range("2026-01-05", periods=4, tz="UTC"))
    assert output.table["value.0"].to_dict() == {0: 10, 1: None, 2: 30, 3: 0}
    assert pd.isna(output.table["value.1"].iloc[1]) and pd.isna(output.table["value.1"].iloc[3])


def test_multiple_roots_reduce_before_combining(warehouse_session: Session) -> None:
    session = warehouse_session
    _numeric_inputs(session)
    net = session.catalog.get("metric:sales.net")
    assert isinstance(net, (ms.RowExpression, ms.MetricExpression))
    output = _compute(session, {"net": net})
    assert output.table["value.0"].tolist() == [57]


def test_semantic_preview_reuses_numeric_kernel(warehouse_session: Session) -> None:
    import marivo.datasource as md

    session = warehouse_session
    _numeric_inputs(session)
    metric = session.catalog.get("metric:sales.net")
    assert isinstance(metric, (ms.RowExpression, ms.MetricExpression))
    scope = tuple(
        (
            session.catalog.get("entity:" + root.ref.path),
            md.unpruned(max_rows=100, timeout_seconds=10),
        )
        for root in consume_metric(metric).roots
    )
    preview = session.catalog.preview(metric, scope=scope)
    assert preview.rows == ({"value": 57},)
    assert (
        preview.kind == "semantic_metric" and preview.sample_policy.method == "pre_aggregate_limit"
    )
    assert terminal_runs(session) == ()


@pytest.mark.parametrize("grouped", (False, True))
def test_strict_row_division_cannot_be_hidden_by_a_predicate(
    warehouse_session: Session, grouped: bool
) -> None:
    session = warehouse_session
    amount, weight = _numeric_inputs(session)
    region = _dimension(session, "sales.orders.region")
    connection = duckdb.connect(str(session.catalog.workspace_dir / "warehouse.duckdb"))
    try:
        connection.execute("UPDATE orders SET weight = 0 WHERE id = 1")
    finally:
        connection.close()
    default = _compute(session, {"row": (amount / weight).mean()})
    assert default.table["value.0"].iloc[0] == -10
    strict = weight.log()
    with pytest.raises(SemanticExpressionError, match="valid numeric function domains"):
        _compute(
            session, {"sum": amount.sum()}, where=strict > 1000, by=(region,) if grouped else ()
        )


def test_native_infinity_and_widened_integer_results_do_not_publish_a_run(
    warehouse_session: Session,
) -> None:
    session = warehouse_session
    amount, weight = _numeric_inputs(session)
    connection = duckdb.connect(str(session.catalog.workspace_dir / "warehouse.duckdb"))
    try:
        connection.execute("UPDATE orders SET weight = 'Infinity'::DOUBLE WHERE id = 1")
    finally:
        connection.close()
    assert _compute(session, {"mean": weight.mean()}).table["value.0"].tolist() == [float("inf")]
    with pytest.raises(SemanticExpressionError, match="overflow"):
        _compute(session, {"wide": amount.sum() * (2**62)})
    assert terminal_runs(session) == ()


def test_decimal_source_and_mixed_local_arithmetic(warehouse_session: Session) -> None:
    from decimal import Decimal

    session = warehouse_session
    amount, _ = _numeric_inputs(session)
    price = catalog_value(session.catalog, "sales.orders.price", kind="field")
    assert isinstance(price, (ms.RowExpression, ms.MetricExpression))
    output = _compute(
        session,
        {
            "sum": price.sum(),
            "mixed": price.sum() + amount.sum() * 0.5,
            "ratio": price.sum() / amount.sum(),
        },
    )
    assert output.table["value.0"].tolist() == [Decimal("7.50")]
    assert str(output.table["value.0"].dtype).startswith("decimal128")
    assert output.table["value.1"].tolist() == [37.5]
    assert output.table["value.2"].iloc[0] == pytest.approx(0.125)


def test_preview_rejects_sample_preparation_before_opening_source(
    warehouse_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.datasource as md
    import marivo.datasource.backends as backends

    session = warehouse_session
    metric = session.catalog.get("metric:sales.sampled")
    assert isinstance(metric, (ms.RowExpression, ms.MetricExpression))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("sample preview opened a datasource")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    with pytest.raises(
        SemanticExpressionError, match="Population/sample/window preview preparation"
    ):
        session.catalog.preview(metric, scope=md.unpruned(max_rows=100, timeout_seconds=10))
    monkeypatch.undo()

    assert _compute(session, {"sample": metric}).table["value.0"].tolist() == [4503599627370502.0]


def test_source_free_numeric_plan_never_reads(warehouse_session: Session) -> None:

    session = warehouse_session
    graph = _source_graph(numeric_program_frame(session, {"constant": numeric_literal_node(7)}))
    plan = plan_query(graph, ())
    assert len(plan.stages) == 1 and isinstance(plan.stages[0], LocalCompute)

    def forbidden(stage: SourceRead) -> StageOutput:
        raise AssertionError("source-free formula submitted a read")

    output = run_query_plan(
        plan,
        forbidden,
        lambda stage, inputs: run_local(stage, inputs),
    )
    assert output.table["value.0"].tolist() == [7]
    assert terminal_runs(session) == ()


def test_entity_count_and_distinct_classification(warehouse_session: Session) -> None:
    from marivo.semantic.catalog import Entity

    session = warehouse_session
    entity = session.catalog.get("entity:sales.orders")
    region = _dimension(session, "sales.orders.region")
    assert isinstance(entity, Entity)
    output = _compute(session, {"rows": entity.count(), "categories": region.count_distinct()})
    assert output.table["value.0"].tolist() == [4]
    assert output.table["value.1"].tolist() == [2]
    assert all(column.unit == "1" for column in output.columns)


def test_local_combination_uses_every_coordinate(warehouse_session: Session) -> None:
    session = warehouse_session
    amount, _ = _numeric_inputs(session)
    region = _dimension(session, "sales.orders.region")
    customer = _dimension(session, "sales.orders.customer_id")
    connection = duckdb.connect(str(session.catalog.workspace_dir / "warehouse.duckdb"))
    try:
        connection.execute("UPDATE orders SET customer_id=2, region='APAC' WHERE id=3")
    finally:
        connection.close()
    output = _compute(
        session,
        {"difference": amount.sum().where(customer == 1) - amount.sum().where(customer == 2)},
        by=(region, customer),
    )
    assert output.keys == ("key.0", "key.1")
    table = output.table.sort_values("key.1")
    assert table["key.0"].tolist() == ["APAC", "APAC"]
    assert table["key.1"].tolist() == [1, 2]
    assert table["value.0"].tolist() == [30, -30]


def test_metric_preview_batch_reuses_scoped_reductions(warehouse_session: Session) -> None:
    import marivo.datasource as md

    session = warehouse_session
    _numeric_inputs(session)
    revenue = session.catalog.get("metric:sales.revenue")
    refund = session.catalog.get("metric:sales.refund_total")
    assert isinstance(revenue, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        refund, (ms.RowExpression, ms.MetricExpression)
    )
    scope = tuple(
        (
            session.catalog.get("entity:" + root.ref.path),
            md.unpruned(max_rows=100, timeout_seconds=10),
        )
        for metric in (revenue, refund)
        for root in consume_metric(metric).roots
    )
    result = session.catalog.preview_many((revenue, refund), scope=scope)
    assert tuple(item.rows for item in result.results) == (({"value": 60},), ({"value": 3},))


def test_weighted_mean_uses_native_overflowing_denominator(warehouse_session: Session) -> None:
    session = warehouse_session
    _, weight = _numeric_inputs(session)
    connection = duckdb.connect(str(session.catalog.workspace_dir / "warehouse.duckdb"))
    try:
        connection.execute("UPDATE orders SET weight = 1e308")
    finally:
        connection.close()
    # The finite numerator divided by DuckDB's infinite denominator is zero.
    assert _compute(session, {"tiny": (weight * 0 + 1e-308).weighted_mean(weight=weight)}).table[
        "value.0"
    ].tolist() == [0.0]
    import marivo.datasource as md

    tiny = session.catalog.get("metric:sales.tiny_mean")
    assert isinstance(tiny, (ms.RowExpression, ms.MetricExpression))
    preview = session.catalog.preview(tiny, scope=md.unpruned(max_rows=100, timeout_seconds=10))
    assert preview.rows == ({"value": 0.0},)
    assert terminal_runs(session) == ()


def test_source_free_constant_keeps_calendar(warehouse_session: Session) -> None:

    session = warehouse_session
    calendar = session.time_scope(start="2026-01-05", end="2026-01-09").by(mv.grain("day"))
    graph = _source_graph(
        numeric_program_frame(session, {"constant": numeric_literal_node(7)}, during=calendar)
    )
    output = run_query_plan(
        plan_query(graph, ()),
        lambda _: pytest.fail("unexpected read"),
        lambda stage, inputs: run_local(stage, inputs),
    )
    assert output.keys == ("key.0",)
    assert output.table["key.0"].tolist() == list(pd.date_range("2026-01-05", periods=4, tz="UTC"))
    assert output.table["value.0"].tolist() == [7, 7, 7, 7]


@pytest.mark.parametrize("invalid", (None, "missing", "duplicate"))
def test_scalar_relationship_preparation(warehouse_session: Session, invalid: str | None) -> None:
    session = warehouse_session
    amount, _ = _numeric_inputs(session)
    segment = _dimension(session, "sales.customers.segment")
    connection = duckdb.connect(str(session.catalog.workspace_dir / "warehouse.duckdb"))
    try:
        if invalid == "missing":
            connection.execute("UPDATE orders SET customer_id = 99 WHERE id = 1")
        elif invalid == "duplicate":
            connection.execute("INSERT INTO customers VALUES (1, 'other')")
    finally:
        connection.close()
    if invalid:
        output = _compute(session, {"sum": amount.sum()}, by=(segment,)).table
        assert {
            None if pd.isna(key) else key: value
            for key, value in output.itertuples(index=False, name=None)
        } == ({"retail": 50, None: 10} if invalid == "missing" else {"other": 60, "retail": 60})
    else:
        output = _compute(session, {"sum": amount.sum()}, by=(segment,), where=segment == "retail")
        assert output.table["key.0"].tolist() == ["retail"]
        assert output.table["value.0"].tolist() == [60]


def test_native_zero_product_can_elide_integer_overflow(warehouse_session: Session) -> None:
    import marivo.datasource as md

    session = warehouse_session
    amount, _ = _numeric_inputs(session)
    # The local Arrow multiply is eager; source SQL may elide its zero product.
    with pytest.raises(SemanticExpressionError, match="overflow"):
        _compute(session, {"v": (amount.sum() * (2**62)) * 0})
    assert _compute(session, {"v": ((amount * (2**62)) * 0).sum()}).table["value.0"].tolist() == [0]
    metric = session.catalog.get("metric:sales.masked_overflow")
    assert isinstance(metric, (ms.RowExpression, ms.MetricExpression))
    preview = session.catalog.preview(metric, scope=md.unpruned(max_rows=100, timeout_seconds=10))
    assert preview.rows[0]["value"] == 0
