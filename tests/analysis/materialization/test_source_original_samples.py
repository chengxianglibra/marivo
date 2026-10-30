"""Source regrouping preserves internal samples and bound window endpoints."""

from datetime import datetime, timezone

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pytest

import marivo.analysis as mv
from tests.shared_fixtures import DslCaseFactory, catalog_value
from tests.support.source_trace import capture_source


@pytest.mark.parametrize("explicit", [False, True])
def test_source_rollup_keeps_internal_identity_and_empty_samples(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" SELECT i::VARCHAR, 'A',\n            CASE WHEN i % 2=0 THEN 'web' ELSE 'store' END, 'paid',\n            '2026-08-15'::TIMESTAMPTZ, 1 FROM range(1000) t(i)"
        )
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    samples = (
        mv.coordinates(by=(customer,), keys=(("A",), ("B",), ("C",))) if explicit else customer
    )
    metric = amount.sum().per(samples).mean()
    trace = capture_source(monkeypatch)
    artifact = session.observe({"mean": metric}).persist()
    frame = artifact.to_pandas()
    expected = 1000 / 3 if explicit else 1000
    assert frame["mean"].tolist() == [expected]
    assert not any("__original_value" in names for names in trace.returned_columns)
    completed = datetime.now(timezone.utc)
    reads = artifact.source_reads
    assert [read.returned_rows for read in reads] == trace.returned_rows
    assert len(reads) == len(trace.native_sql)
    assert {scope.source for read in reads for scope in read.scopes} == {"sales.order"}
    assert all(
        start <= read.completed_at <= end
        for read, start, end in zip(
            reads, trace.started_at, (*trace.started_at[1:], completed), strict=True
        )
    )


def test_source_rollup_finishes_each_sample_ratio_before_mean(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" VALUES\n            ('1','A','web','paid','2026-08-15',0),\n            ('2','A','store','paid','2026-08-15',200),\n            ('3','B','web','paid','2026-08-15',300)"
        )
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    metric = (amount.sum() / amount.count()).per(customer).mean()
    trace = capture_source(monkeypatch)
    frame = session.observe({"mean": metric}).compute().to_pandas()
    assert frame["mean"].tolist() == [200.0]


@pytest.mark.parametrize("ratio", [False, True])
def test_source_spatial_rollup_preserves_lookback_and_endpoint_occurrences(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, ratio: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" SELECT i::VARCHAR,\n            CASE WHEN i % 2=0 THEN 'A' ELSE 'B' END, 'web', 'paid',\n            CASE WHEN i<500 THEN '2026-09-20'::TIMESTAMPTZ ELSE '2026-10-07'::TIMESTAMPTZ END,\n            1 FROM range(1000) t(i)"
        )
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    ordered_at = catalog_value(session.catalog, "sales.order.ordered_at", kind="field")
    total = amount.sum(time=ordered_at)
    metric = (total / amount.count(time=ordered_at) if ratio else total).window(mv.all_history())
    trace = capture_source(monkeypatch)
    frame = session.observe({"value": metric}, by=(calendar,)).compute().to_pandas()
    assert frame["value"].tolist() == ([1.0, 1.0] if ratio else [1000, 1000])


def test_source_sample_rollup_keeps_fine_formula_failure(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" VALUES\n            ('1','A','web','paid','2026-08-15',0),\n            ('2','A','store','paid','2026-08-15',1)"
        )
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    metric = (amount.count() / amount.sum()).per(customer).mean()
    trace = capture_source(monkeypatch)
    result = session.observe({"mean": metric}, by=(channel,)).compute().to_pandas()
    assert result["mean"].isna().any()


def test_multi_root_read_evidence_keeps_each_contribution_scope(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    session = case.session
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    amount = catalog_value(session.catalog, "sales.order_line.line_amount", kind="field")
    count = session.catalog.get("metric:sales.order_count")
    metric = amount.sum().where(amount > 0) / count
    trace = capture_source(monkeypatch)
    artifact = session.observe({"ratio": metric}).persist()
    reads = artifact.source_reads
    assert len(reads) == 1
    assert {scope.source for scope in reads[0].scopes} == {"sales.order", "sales.order_line"}
    assert reads[0].returned_rows == 1
    assert trace.returned_rows == [1]
    assert trace.started_at[0] <= reads[0].completed_at <= datetime.now(timezone.utc)
    assert artifact.to_pandas()["ratio"].tolist() == [200 / 101]


@pytest.mark.parametrize("reducer", ["mean", "median"])
def test_sample_rollup_uses_original_integer_mean_and_quantile_finish(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, reducer: str
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" VALUES\n            ('1','A','web','paid','2026-08-15',-1),\n            ('2','A','store','paid','2026-08-15',9007199254740993)"
        )
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    metric = getattr(amount, reducer)().per(customer).mean()
    with duckdb.connect(str(case.database_path)) as connection:
        native = connection.execute('SELECT avg(amount),median(amount) FROM "order"').fetchone()
    assert native is not None
    assert native == (4503599627370496.0, 4503599627370495.5)
    trace = capture_source(monkeypatch)
    frame = session.observe({"mean": metric}).compute().to_pandas()
    assert frame["mean"].tolist() == [native[0 if reducer == "mean" else 1]]
    fixed = session.values(
        by=(channel,),
        columns={"amount": None},
        rows=((("web",), {"amount": -1}), (("store",), {"amount": 2**53 + 1})),
    ).compute()
    # Local mean follows Arrow independently of the source AVG/median algorithm.
    midpoint = pc.mean(pa.array([-1, 2**53 + 1], type=pa.int64())).as_py()
    reduced = fixed.reduce({"finish": fixed["amount"].mean()}).compute().to_pandas()
    assert reduced["finish"].tolist() == [midpoint]


def test_collated_original_keeps_its_existing_fine_grouping_boundary(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" RENAME TO previous_orders')
        connection.execute(
            'CREATE TABLE "order" (\n            order_id VARCHAR, customer_id VARCHAR, channel VARCHAR COLLATE NOCASE,\n            status VARCHAR, ordered_at TIMESTAMPTZ, amount BIGINT)'
        )
        connection.execute(
            "INSERT INTO \"order\" SELECT order_id,customer_id,\n            CASE WHEN order_id='j1_september' THEN 'a' ELSE 'A' END,\n            status,ordered_at,amount FROM previous_orders"
        )
        connection.execute("DROP TABLE previous_orders")
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    trace = capture_source(monkeypatch)
    fine = session.observe({"total": amount.sum()}, by=(channel,))
    fixed = fine.where(fine.coord(fine.axes[0]) == "a").compute()
    assert fixed.to_pandas().empty
    selected = session.population_from(fixed, on=((fixed.axes[0], channel),))
    result = (
        session.observe({"total": amount.sum()}, where=mv.in_population(selected))
        .compute()
        .to_pandas()
    )
    assert result["total"].tolist() == [0]


@pytest.mark.parametrize("unknown", [False, True])
def test_empty_actual_read_retains_zero_or_unknown_original_count(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, unknown: bool
) -> None:
    from dataclasses import replace

    from marivo.analysis.compiler.graph_lowering import LoweredRead
    from marivo.analysis.compiler.graph_plan import SourceRead
    from marivo.analysis.materialization import graph_source_execution
    from marivo.analysis.materialization.graph_exchange import StageOutput

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
    amount = catalog_value(case.session.catalog, "sales.order.amount", kind="field")
    customer = catalog_value(case.session.catalog, "sales.order.customer_id", kind="field")
    original = graph_source_execution.read_stage

    def without_count(
        prepared: graph_source_execution.PreparedSources,
        stage: SourceRead,
        request: LoweredRead,
        *,
        value_keys: tuple[str, ...] = (),
    ) -> StageOutput:
        result = original(prepared, stage, request, value_keys=value_keys)
        return replace(result, source_rows=None)

    if unknown:
        monkeypatch.setattr(graph_source_execution, "read_stage", without_count)
    trace = capture_source(monkeypatch)
    saved = case.session.observe({"total": amount.sum()}, by=(customer,)).persist()
    assert len(saved.source_reads) == len(trace.native_sql) == 1
    assert saved.source_reads[0].returned_rows == (None if unknown else trace.returned_rows[0])
    assert trace.returned_rows == [0]
    assert saved.to_pandas().empty
    assert saved.compute().persist().source_reads == saved.source_reads
