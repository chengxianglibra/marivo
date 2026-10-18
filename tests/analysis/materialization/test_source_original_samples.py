"""Source regrouping preserves internal samples and bound window endpoints."""

import duckdb
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory
from tests.support.source_trace import capture_source


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("empty", ["zero", "null"])
def test_source_rollup_keeps_internal_identity_and_empty_samples(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
    empty: str,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" SELECT i::VARCHAR, 'A',
            CASE WHEN i % 2=0 THEN 'web' ELSE 'store' END, 'paid',
            '2026-08-15'::TIMESTAMPTZ, 1 FROM range(1000) t(i)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    samples = (
        mv.coordinates(by=(customer,), keys=(("A",), ("B",), ("C",))) if explicit else customer
    )
    metric = amount.sum(empty=empty).per(samples).mean()
    trace = capture_source(monkeypatch)
    artifact = session.observe({"mean": metric}, by=(channel,)).rollup().execute()
    frame = artifact.to_pandas()
    expected = 1000 / 3 if explicit and empty == "zero" else 1000
    assert frame[("value", "mean")].tolist() == [expected]
    assert trace.returned_rows == [3]
    assert trace.uploaded_key_rows == []
    assert not any("__original_value" in names for names in trace.returned_columns)
    reads = [dict(item.values) for item in artifact.evidence_digest().items if item.kind == "read"]
    assert [item["returned_rows"] for item in reads] == trace.returned_rows
    assert reads[0]["row_count_scope"] == "physical source submission"


def test_source_rollup_finishes_each_sample_ratio_before_mean(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" VALUES
            ('1','A','web','paid','2026-08-15',0),
            ('2','A','store','paid','2026-08-15',200),
            ('3','B','web','paid','2026-08-15',300)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    metric = (amount.sum() / amount.count()).per(customer).mean()
    trace = capture_source(monkeypatch)
    frame = session.observe({"mean": metric}, by=(channel,)).rollup().execute().to_pandas()
    assert frame[("value", "mean")].tolist() == [200.0]
    assert len(trace.returned_rows) == 1
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("ratio", [False, True])
def test_source_spatial_rollup_preserves_lookback_and_endpoint_occurrences(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    ratio: bool,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" SELECT i::VARCHAR,
            CASE WHEN i % 2=0 THEN 'A' ELSE 'B' END, 'web', 'paid',
            CASE WHEN i<500 THEN '2026-09-20'::TIMESTAMPTZ ELSE '2026-10-07'::TIMESTAMPTZ END,
            1 FROM range(1000) t(i)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    ordered_at = session.catalog.fields.get("sales.order.ordered_at")
    total = amount.sum(time=ordered_at)
    metric = (total / amount.count(time=ordered_at) if ratio else total).window(mv.all_history())
    trace = capture_source(monkeypatch)
    frame = (
        session.observe({"value": metric}, during=calendar, by=(customer,))
        .rollup(by=(calendar,))
        .execute()
        .to_pandas()
    )
    assert frame[("value", "value")].tolist() == ([1.0, 1.0] if ratio else [1000, 1000])
    assert trace.returned_rows == [12 if ratio else 6]
    assert trace.uploaded_key_rows == []


def test_source_sample_rollup_keeps_fine_formula_failure(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" VALUES
            ('1','A','web','paid','2026-08-15',0),
            ('2','A','store','paid','2026-08-15',1)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    metric = amount.count().divide(amount.sum(), zero_denominator="error").per(customer).mean()
    trace = capture_source(monkeypatch)
    with pytest.raises(AnalysisError, match="zero denominator"):
        session.observe({"mean": metric}, by=(channel,)).rollup().execute()
    assert len(trace.returned_rows) == 1


def test_multi_root_read_evidence_keeps_each_contribution_scope(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    amount = session.catalog.fields.get("sales.order_line.line_amount")
    count = session.catalog.metrics.get("sales.order_count")
    metric = amount.sum().where(amount > 0) / count
    trace = capture_source(monkeypatch)
    artifact = session.observe({"ratio": metric}, by=(customer,)).rollup().execute()
    reads = [dict(item.values) for item in artifact.evidence_digest().items if item.kind == "read"]
    assert len(reads) == 2
    assert len({item["source"] for item in reads}) == 2
    assert len({item["physical_stage"] for item in reads}) == 1
    assert {item["returned_rows"] for item in reads} == {trace.returned_rows[0]}
    assert sorted(item["filters"] == "none added" for item in reads) == [False, True]
    assert artifact.to_pandas()[("value", "ratio")].tolist() == [200 / 101]


@pytest.mark.parametrize("reducer", ["mean", "median"])
def test_sample_rollup_uses_original_integer_mean_and_quantile_finish(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    reducer: str,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" VALUES
            ('1','A','web','paid','2026-08-15',-1),
            ('2','A','store','paid','2026-08-15',9007199254740993)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    metric = getattr(amount, reducer)().per(customer).mean()
    trace = capture_source(monkeypatch)
    frame = session.observe({"mean": metric}, by=(channel,)).rollup().execute().to_pandas()
    assert frame[("value", "mean")].tolist() == [4503599627370496.0]
    assert trace.returned_rows == [3]


def test_collated_original_keeps_its_existing_fine_grouping_boundary(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" RENAME TO previous_orders')
        connection.execute("""CREATE TABLE "order" (
            order_id VARCHAR, customer_id VARCHAR, channel VARCHAR COLLATE NOCASE,
            status VARCHAR, ordered_at TIMESTAMPTZ, amount BIGINT)""")
        connection.execute("""INSERT INTO "order" SELECT order_id,customer_id,
            CASE WHEN order_id='j1_september' THEN 'a' ELSE 'A' END,
            status,ordered_at,amount FROM previous_orders""")
        connection.execute("DROP TABLE previous_orders")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    channel = session.catalog.fields.get("sales.order.channel")
    trace = capture_source(monkeypatch)
    fine = session.observe({"total": amount.sum()}, by=(channel,))
    result = fine.where(fine.coord(channel) == "a").rollup().execute().to_pandas()
    assert result[("value", "total")].isna().tolist() == [True]
    assert trace.returned_rows == [5]
