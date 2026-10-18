"""Remaining source-original families keep their captured population and fibers."""

import duckdb
import pytest

import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory
from tests.support.source_trace import capture_source


@pytest.mark.parametrize("empty", ["zero", "null"])
def test_dynamic_sample_domain_is_transported_with_actual_leaf_batches(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    empty: str,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" SELECT i::VARCHAR,'A',
            CASE WHEN i%2=0 THEN 'web' ELSE 'store' END,'paid','2026-08-15',1
            FROM range(1000) t(i)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.customer.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    samples = session.members(ms.ref.entity("sales.customer"), where=customer != "D").classify(
        by=(customer,)
    )
    metric = amount.sum(empty=empty).per(samples).mean()
    trace = capture_source(monkeypatch)
    result = session.observe({"value": metric}, by=(channel,)).rollup().execute().to_pandas()
    assert result[("value", "value")].tolist() == [1000 / 3 if empty == "zero" else 1000]
    assert trace.returned_rows == [6]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("selection", ["top", "where", "empty"])
def test_complex_parent_selection_consumes_compact_fibers_once(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" SELECT i::VARCHAR,'A',
            CASE WHEN i%2=0 THEN 'web' ELSE 'store' END,'paid','2026-08-15',
            CASE WHEN i%2=0 THEN 1 ELSE 3 END FROM range(1000) t(i)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    metric = (amount.sum() / amount.count()).per(customer).mean()
    fine = session.observe({"value": metric}, by=(channel,))
    selected = (
        fine.where(fine.coord(channel) == "store")
        if selection == "where"
        else fine.top(0 if selection == "empty" else 1, by=fine["value"])
    )
    trace = capture_source(monkeypatch)
    result = selected.rollup().execute().to_pandas()
    values = result[("value", "value")]
    assert values.isna().all() if selection == "empty" else values.tolist() == [3.0]
    assert trace.returned_rows == [8]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("selected", [False, True])
def test_complex_drill_keeps_samples_and_selected_parent_fibers(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    selected: bool,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" SELECT i::VARCHAR,'A',
            CASE WHEN i%2=0 THEN 'web' ELSE 'store' END,
            CASE WHEN i%2=0 THEN 'paid' ELSE 'cancel' END,'2026-08-15',
            CASE WHEN i%2=0 THEN 1 ELSE 3 END FROM range(1000) t(i)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    fine = session.observe({"value": amount.sum().per(customer).mean()}, by=(status,))
    query = fine.top(1, by=fine["value"]) if selected else fine
    trace = capture_source(monkeypatch)
    result = query.drill(add=(channel,)).execute().to_pandas()
    assert sorted(result[("value", "value")].tolist()) == (
        [1500.0] if selected else [500.0, 1500.0]
    )
    assert trace.returned_rows == [4]
    assert trace.uploaded_key_rows == []


def test_nested_complex_rollups_finish_every_intermediate_formula(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" SELECT i::VARCHAR,'A',
            CASE WHEN i%2=0 THEN 'web' ELSE 'store' END,
            CASE WHEN i%4<2 THEN 'paid' ELSE 'cancel' END,'2026-08-15',1
            FROM range(1000) t(i)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    fine = session.observe({"value": amount.sum().per(customer).mean()}, by=(channel, status))
    trace = capture_source(monkeypatch)
    result = fine.rollup(by=(channel,)).rollup().execute().to_pandas()
    assert result[("value", "value")].tolist() == [1000.0]
    assert trace.returned_rows == [7]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("empty", ["zero", "null"])
def test_dynamic_sample_producer_retains_its_own_period(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    empty: str,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" SELECT i::VARCHAR,
            CASE WHEN i<500 THEN 'A' ELSE 'B' END,'web','paid',
            CASE WHEN i<500 THEN '2026-09-10'::TIMESTAMPTZ ELSE '2026-10-07'::TIMESTAMPTZ END,
            1 FROM range(1000) t(i)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    ordered = session.catalog.fields.get("sales.order.ordered_at")
    producer = session.observe(
        {"n": amount.count(time=ordered)},
        by=(customer,),
        during=session.time_scope(start="2026-09-01", end="2026-10-01"),
    )
    samples = producer.where(producer["n"] > 0).population()
    metric = amount.sum(time=ordered, empty=empty).per(samples).mean()
    fine = session.observe(
        {"value": metric},
        by=(channel,),
        during=session.time_scope(start="2026-10-01", end="2026-11-01"),
    )
    trace = capture_source(monkeypatch)
    result = fine.rollup().execute().to_pandas()[("value", "value")]
    assert result.tolist() == [0.0] if empty == "zero" else result.isna().all()
    assert len(trace.returned_rows) == 1
    assert trace.returned_rows[0] < 10
    assert trace.uploaded_key_rows == []


def test_complex_top_uses_the_exact_completed_parent_score(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        for index, (channel, customer, value) in enumerate(
            (
                ("A", "A", 2**53),
                ("A", "B", 0),
                ("A", "C", 0),
                ("Z", "A", 2**53 + 1),
                ("Z", "B", 0),
                ("Z", "C", 0),
            )
        ):
            connection.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                [str(index), customer, channel, "paid", "2026-10-07", value],
            )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    fine = session.observe({"value": amount.sum().per(customer).mean()}, by=(channel,))
    trace = capture_source(monkeypatch)
    result = fine.top(1, by=fine["value"]).rollup().execute().to_pandas()
    assert result[("value", "value")].tolist() == [(2**53 + 1) / 3]
    assert trace.returned_rows == [12]
    assert trace.uploaded_key_rows == []


def test_complex_top_zero_preserves_the_fine_formula_failure(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.errors import AnalysisError

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
    fine = session.observe({"value": metric}, by=(channel,))
    trace = capture_source(monkeypatch)
    with pytest.raises(AnalysisError, match="zero denominator"):
        fine.top(0, by=fine["value"]).rollup().execute()
    assert trace.returned_rows == [8]


def test_selected_target_rejects_malformed_unselected_parent_key_carrier(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pandas as pd

    from marivo.analysis.compiler.native_formula import NativeOriginalBatchFinish
    from marivo.analysis.materialization import source_original_complex
    from marivo.analysis.materialization.errors import MaterializationError

    case = analysis_dsl_case_factory("j1")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    fine = session.observe({"value": amount.sum().per(customer).mean()}, by=(amount,))
    original_finish = source_original_complex.finish_original_batch

    def malformed(finish: NativeOriginalBatchFinish, frame: pd.DataFrame) -> pd.DataFrame:
        leaf = finish.target.leaves[0]
        auxiliary = finish.selection_keys[0][1]
        transport = next(name for name, original in leaf.columns if original == auxiliary)
        data = frame.copy()
        target_rows = data["__original_batch"] == leaf.tag
        data[transport] = data[transport].astype(object)
        data.loc[target_rows, transport] = 1.5
        return original_finish(finish, data)

    monkeypatch.setattr(source_original_complex, "finish_original_batch", malformed)
    with pytest.raises(MaterializationError, match="complete integer keys"):
        fine.top(1, by=fine["value"]).rollup().execute()


def test_dynamic_sample_domain_transports_distinct_complete_coordinates(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("""INSERT INTO "order" SELECT i::VARCHAR,'A',
            CASE WHEN i%2=0 THEN 'web' ELSE 'store' END,'paid','2026-08-15',1
            FROM range(1000) t(i)""")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    samples = session.members(ms.ref.entity("sales.order")).classify(by=(customer,))
    metric = amount.sum(empty="zero").per(samples).mean()
    trace = capture_source(monkeypatch)
    result = session.observe({"value": metric}, by=(channel,)).rollup().execute().to_pandas()
    assert result[("value", "value")].tolist() == [1000.0]
    assert trace.returned_rows == [4]
    assert trace.uploaded_key_rows == []
