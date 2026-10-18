"""Source relation lowering keeps finite Query operations beside their inputs."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.graph_plan import plan_query
from marivo.analysis.compiler.optimization import optimize_query
from marivo.analysis.dsl.query import Query
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import (
    execute_numeric_query,
    open_query_sources,
)
from marivo.analysis.materialization.query_key_fusion import fuse_query_keys, fuse_query_terminal
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import DslCaseFactory
from tests.support.source_trace import SourceTrace, capture_source


@pytest.fixture
def source_query_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "query.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, region VARCHAR, amount BIGINT, baseline BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,'A',10,0),(2,'A',30,0),(3,'B',20,0),(4,NULL,NULL,0)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id'])
region = ms.dimension_column(name='region', entity=orders, column='region')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all(), unit='CNY')
baseline = ms.measure_column(name='baseline', entity=orders, column='baseline', additivity=ms.additive_all(), unit='CNY')
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("source-query", report_timezone="UTC")


def _execute(session: Session, query: Query, *, fuse: bool) -> StageOutput:
    with open_query_sources(
        session._runtime.connection_service(), query._graph, session.catalog._state
    ) as prepared:
        plan = optimize_query(plan_query(query._graph, prepared.schemas, context=prepared.context))
        lowered = lower_query(plan, prepared.relations)
        if fuse:
            lowered = fuse_query_terminal(prepared, fuse_query_keys(prepared, lowered))
        return execute_numeric_query(prepared, lowered)


def _attributes(session: Session) -> Query:
    return session.read(
        session.members(ms.ref.entity("sales.orders")),
        {
            "amount": session.catalog.fields.get("sales.orders.amount"),
            "region": session.catalog.fields.get("sales.orders.region"),
        },
    )


@pytest.fixture
def collated_source_query_session(source_query_session: Session, tmp_path: Path) -> Session:
    with duckdb.connect(str(tmp_path / "query.duckdb")) as connection:
        connection.execute("DROP TABLE orders")
        connection.execute(
            "CREATE TABLE orders(id VARCHAR COLLATE NOCASE, region VARCHAR COLLATE NOCASE, amount BIGINT)"
        )
        connection.executemany(
            "INSERT INTO orders VALUES (?, ?, ?)",
            (("a", "a", 1), ("B", "A", 1), ("é", "é", 1), ("Z", "Z", 1), ("c", None, 1)),
        )
    return source_query_session


@pytest.mark.parametrize("operation", ("eq", "ne", "lt", "top_string", "top_tie"))
def test_collated_strings_keep_host_comparison_and_order(
    collated_source_query_session: Session, operation: str
) -> None:
    session = collated_source_query_session
    source = _attributes(session)
    query = (
        source.where(source["region"] == "a")
        if operation == "eq"
        else source.where(source["region"] != "a")
        if operation == "ne"
        else source.where(source["region"] < "a")
        if operation == "lt"
        else source.top(2, by=source["region"])
        if operation == "top_string"
        else source.top(2, by=source["amount"])
    )
    baseline = _execute(session, query, fuse=False)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    if operation == "eq":
        assert output.table["key.0"].tolist() == ["a"]


@pytest.mark.parametrize("default_collation", ("", "nocase"))
def test_collated_string_membership_uses_exact_utf8_bytes(
    tmp_path: Path, default_collation: str
) -> None:
    import ibis

    from marivo.analysis.compiler.source_predicates import parameter_string_comparisons
    from marivo.analysis.materialization.query_key_fusion import _distinct
    from marivo.analysis.materialization.query_parameters import _replace_parameter

    database = tmp_path / "membership.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE labels(label VARCHAR COLLATE NOCASE)")
        connection.executemany("INSERT INTO labels VALUES (?)", (("a",), ("A",), ("é",)))
    backend = ibis.duckdb.connect(str(database))
    try:
        backend.raw_sql(f"SET default_collation = '{default_collation}'")
        labels = backend.table("labels")
        distinct = backend.execute(_distinct(labels, "duckdb"))
        assert sorted(distinct["label"].tolist()) == ["A", "a", "é"]
        parameter = ibis.table({"key": "string"}, name="selected")
        query = labels.semi_join(parameter, labels.label.identical_to(parameter.key))
        query = parameter_string_comparisons(query, "selected", "duckdb")
        selected = ibis.memtable({"key": ["a", "é"]})
        result = backend.execute(_replace_parameter(query, "selected", selected))
        assert sorted(result["label"].tolist()) == ["a", "é"]
    finally:
        backend.disconnect()


def test_native_collated_group_membership_keeps_every_original_contribution(
    collated_source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = collated_source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    grouped = session.observe({"count": amount.count()}, by=(region,))
    selected = grouped.top(1, by=grouped["count"]).population()
    query = session.observe({"count": amount.count()}, where=mv.in_population(selected))
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert output.table["value.0"].tolist() == [2]
    assert trace.returned_rows == [1]


@pytest.mark.parametrize("operation", ("read", "where", "select", "top", "sum", "rows"))
def test_terminal_relation_returns_only_selected_rows(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from marivo.analysis.dsl.column import row_count

    session = source_query_session
    source = _attributes(session)
    query = (
        source.where(source["amount"] >= 20)
        if operation == "where"
        else source.select({"kept": source["region"]})
        if operation == "select"
        else source.top(2, by=source["amount"])
        if operation == "top"
        else source.reduce({"total": source["amount"].sum()})
        if operation == "sum"
        else source.reduce({"rows": row_count()})
        if operation == "rows"
        else source
    )
    baseline = _execute(session, query, fuse=False)
    trace: SourceTrace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert len(trace.native_sql) == 1
    assert (
        len(output.table)
        == {"read": 4, "where": 2, "select": 4, "top": 2, "sum": 1, "rows": 1}[operation]
    )


def test_top_and_where_keep_call_order(source_query_session: Session) -> None:
    session = source_query_session
    source = _attributes(session)
    top = source.top(2, by=source["amount"])
    filtered = source.where(source["amount"] < 30)
    for query in (top.where(top["amount"] < 30), filtered.top(2, by=filtered["amount"])):
        baseline = _execute(session, query, fuse=False)
        output = _execute(session, query, fuse=True)
        pd.testing.assert_frame_equal(output.table, baseline.table)


def test_top_population_stays_in_source(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    source = _attributes(session)
    members = source.top(1, by=source["amount"]).population()
    amount = session.catalog.fields.get("sales.orders.amount")
    query = session.observe({"total": amount.sum()}, members=members)
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert len(trace.native_sql) == 1
    assert output.table["value.0"].tolist() == [30]


def test_empty_selection_retains_full_identity_checks(
    source_query_session: Session, tmp_path: Path
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError

    session = source_query_session
    source = _attributes(session)
    filtered = source.where(source["amount"] > 100)
    query = filtered.top(0, by=filtered["amount"])
    with duckdb.connect(str(tmp_path / "query.duckdb")) as connection:
        connection.execute("INSERT INTO orders VALUES (1,'different',99,0)")
    for fuse in (False, True):
        with pytest.raises(MaterializationError, match="exactly one attribute row"):
            _execute(session, query, fuse=fuse)


def test_cell_sum_overflow_is_checked_before_empty_filter(
    source_query_session: Session, tmp_path: Path
) -> None:
    from marivo.semantic.errors import SemanticExpressionError

    session = source_query_session
    source = _attributes(session)
    total = source.reduce({"total": source["amount"].sum()})
    query = total.where(total["total"] < 0)
    with duckdb.connect(str(tmp_path / "query.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=9223372036854775807 WHERE id IN (1,2)")
    for fuse in (False, True):
        with pytest.raises(SemanticExpressionError, match="finite value statistic"):
            _execute(session, query, fuse=fuse)


def test_aggregate_population_stays_in_source(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    producer = session.observe({"total": amount.sum()}, by=(region,))
    selected = producer.where(producer["total"] >= 30).population()
    query = session.observe({"total": amount.sum()}, where=mv.in_population(selected))
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("selected_region", ("A", "missing"))
def test_shared_population_has_one_source_statement(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, selected_region: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    selected = source.where(source["region"] == selected_region).population()
    condition = mv.in_population(selected)
    amount = session.catalog.fields.get("sales.orders.amount")
    query = session.observe(
        {
            "selected": amount.sum(empty="zero").where(condition),
            "other": amount.sum(empty="zero").where(~condition),
        }
    )
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("method", ("sum", "mean"))
def test_aggregate_selection_retains_parent_strict_null_check(
    source_query_session: Session, method: str
) -> None:
    from marivo.semantic.errors import SemanticExpressionError

    session = source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    metric = amount.sum(nulls="error") if method == "sum" else amount.mean(nulls="error")
    producer = session.observe({"value": metric}, by=(region,))
    chosen = producer.where(producer["value"] > 1000).population()
    query = session.observe({"total": amount.sum()}, where=mv.in_population(chosen))
    for fuse in (False, True):
        with pytest.raises(SemanticExpressionError, match="non-NA"):
            _execute(session, query, fuse=fuse)


def test_aggregate_top_population_keeps_original_parent_ranking(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    producer = session.observe({"value": amount.sum()}, by=(region,))
    chosen = producer.top(1, by=producer["value"]).population()
    query = session.observe({"total": amount.sum()}, where=mv.in_population(chosen))
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert output.table["value.0"].tolist() == [40]
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []


def test_top_ties_use_complete_identity_and_missing_scores_last(
    source_query_session: Session, tmp_path: Path
) -> None:
    session = source_query_session
    source = _attributes(session)
    with duckdb.connect(str(tmp_path / "query.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=20 WHERE id IN (1,2,3)")
    query = source.top(4, by=source["amount"])
    baseline = _execute(session, query, fuse=False)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert output.table["key.0"].tolist() == [1, 2, 3, 4]


def test_cell_sum_empty_input_stays_null(source_query_session: Session) -> None:
    session = source_query_session
    source = _attributes(session)
    selected = source.where(source["amount"] > 100)
    query = selected.reduce({"sum": selected["amount"].sum()})
    baseline = _execute(session, query, fuse=False)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert output.table["value.0"].isna().all()


@pytest.mark.parametrize("operation", ("align", "compare"))
def test_same_source_relation_joins_complete_endpoints_once(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    left = source.where((source["amount"] <= 20) | source["amount"].is_null())
    left = left.select({"value": left["amount"]})
    right = source.where((source["amount"] >= 20) | source["amount"].is_null())
    right = right.select({"value": right["amount"]})
    query = (
        left.align(right, how="outer") if operation == "align" else left.compare(right, how="outer")
    )
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert trace.returned_rows == [4]
    assert trace.uploaded_key_rows == []


def test_same_source_exact_alignment_failure_survives_top_zero(
    source_query_session: Session,
) -> None:
    from marivo.analysis.errors import AnalysisError

    source = _attributes(source_query_session)
    left = source.where(source["amount"] <= 20)
    right = source.where(source["amount"] >= 20)
    paired = left.align(right)
    query = paired.top(0, by=paired["left.amount"])
    for fuse in (False, True):
        with pytest.raises(AnalysisError, match="equal complete endpoint key images"):
            _execute(source_query_session, query, fuse=fuse)


def test_source_broadcast_preserves_exact_coarse_values_and_coverage(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import AnalysisError

    session = source_query_session
    source = _attributes(session)
    total = source.reduce({"total": source["amount"].sum()})
    query = source.select({"reference": mv.broadcast(total["total"], to=source)})
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert trace.returned_rows == [4]
    assert trace.uploaded_key_rows == []
    missing = total.where(total["total"] < 0)
    invalid = source.select({"reference": mv.broadcast(missing["total"], to=source)})
    invalid = invalid.top(0, by=invalid["reference"])
    for fuse in (False, True):
        with pytest.raises(AnalysisError, match="exactly one zero-axis reference key"):
            _execute(session, invalid, fuse=fuse)


def test_source_compare_keeps_large_integer_and_na_zero_policy(
    source_query_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    with duckdb.connect(str(tmp_path / "query.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=9007199254740993, baseline=9007199254740992")
    source = session.read(
        session.members(ms.ref.entity("sales.orders")),
        {
            "amount": session.catalog.fields.get("sales.orders.amount"),
            "baseline": session.catalog.fields.get("sales.orders.baseline"),
        },
    )
    current = source.select({"value": source["amount"]})
    baseline_query = source.select({"value": source["baseline"]})
    query = current.compare(baseline_query)
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert output.table["value.0.current"].tolist() == [9007199254740993] * 4
    assert trace.returned_rows == [4]
    with duckdb.connect(str(tmp_path / "query.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=NULL, baseline=0 WHERE id=4")
    nullable = source.where(source["amount"].is_null())
    zero = nullable.select({"value": nullable["baseline"]})
    nullable = nullable.select({"value": nullable["amount"]})
    null_compare = nullable.compare(zero, zero_denominator="error")
    pd.testing.assert_frame_equal(
        _execute(session, null_compare, fuse=True).table,
        _execute(session, null_compare, fuse=False).table,
    )


@pytest.mark.parametrize("operation", ("union", "intersection", "difference"))
def test_population_set_algebra_keeps_query_keys_in_source(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    left = source.where(source["amount"] >= 20).population()
    right = source.where(source["amount"] <= 20).population()
    members = (
        left.union(right)
        if operation == "union"
        else left.intersection(right)
        if operation == "intersection"
        else left.difference(right)
    )
    amount = session.catalog.fields.get("sales.orders.amount")
    query = session.observe({"total": amount.sum()}, members=members)
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("empty", ("zero", "null"))
def test_grouped_shared_population_consumers_keep_independent_grids(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, empty: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    selected = source.where(source["amount"] >= 25).population()
    condition = mv.in_population(selected)
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    query = session.observe(
        {
            "selected": amount.sum(empty=empty).where(condition).where(region == "A"),
            "other": amount.sum(empty=empty).where(~condition).where(region == "B"),
            "nullable": amount.sum(empty="null").where(~condition),
        },
        by=(region,),
    )
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert trace.returned_rows == [3]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("operation", ("union", "intersection", "difference"))
def test_population_set_terminal_is_one_source_relation(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    left = source.where(source["amount"] >= 20).population()
    right = source.where(source["amount"] <= 20).population()
    members = (
        left.union(right)
        if operation == "union"
        else left.intersection(right)
        if operation == "intersection"
        else left.difference(right)
    )
    query = session.query(members)
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert trace.returned_rows == [{"union": 3, "intersection": 1, "difference": 1}[operation]]


@pytest.mark.parametrize("operation", ("product", "project", "filter"))
def test_population_product_projection_and_filter_stay_in_source(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    selected = source.where(source["amount"] >= 20).population()
    region = session.catalog.fields.get("sales.orders.region")
    classifications = session.members(ms.ref.entity("sales.orders")).classify(by=(region,))
    product = mv.product(selected, classifications)
    population = (
        product.project(by=(region,))
        if operation == "project"
        else product.where(mv.in_population(selected))
        if operation == "filter"
        else product
    )
    query = session.query(population)
    baseline = _execute(session, query, fuse=False)
    trace = capture_source(monkeypatch)
    output = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(output.table, baseline.table)
    assert trace.returned_rows == [3 if operation == "project" else 6]


def test_public_fused_read_evidence_keeps_producer_and_consumer_scopes(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    status = session.catalog.fields.get("sales.order.status")
    ordered = session.catalog.fields.get("sales.order.ordered_at")
    producer = session.observe(
        {"count": amount.count(time=ordered).where(status == "paid")},
        by=(customer,),
        during=session.time_scope(start="2026-09-01", end="2026-10-01"),
    )
    selected = producer.where(producer["count"] > 0).population()
    query = session.observe(
        {"count": amount.count(time=ordered)},
        where=mv.in_population(selected),
        during=session.time_scope(start="2026-10-01", end="2026-11-01"),
    )
    trace = capture_source(monkeypatch)
    artifact = query.execute()
    reads = [dict(item.values) for item in artifact.evidence_digest().items if item.kind == "read"]
    assert len(trace.returned_rows) == 1
    assert len(reads) == 2
    assert len({read["contribution"] for read in reads}) == 2
    assert len({read["physical_stage"] for read in reads}) == 1
    starts = set()
    for read in reads:
        start, end = read["period_start"], read["period_end"]
        assert isinstance(start, datetime) and isinstance(end, datetime)
        starts.add((start.month, end.month))
        assert read["row_count_scope"] == "physical source submission"
        assert read["returned_rows"] == trace.returned_rows[0]
        if start.month == 9:
            assert "paid" in str(read["filters"])
    assert starts == {(9, 10), (10, 11)}
