"""Remaining source-original families keep their captured population and fibers."""

import duckdb
import pytest

import marivo.analysis as mv
from tests.shared_fixtures import DslCaseFactory
from tests.support.source_trace import capture_source


def test_dynamic_sample_domain_is_transported_with_actual_leaf_batches(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" SELECT i::VARCHAR,'A',\n            CASE WHEN i%2=0 THEN 'web' ELSE 'store' END,'paid','2026-08-15',1\n            FROM range(1000) t(i)"
        )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.customer.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    samples = session.members(
        session.catalog.entities.get("sales.customer"), where=customer != "D"
    ).classify(by=(customer,))
    metric = amount.sum().per(samples).mean()
    trace = capture_source(monkeypatch)
    result = session.observe({"value": metric}).compute().to_pandas()
    assert result["value", "value"].tolist() == [1000 / 3]


@pytest.mark.parametrize("selection", ["top", "where", "empty"])
def test_complex_parent_selection_consumes_compact_fibers_once(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" SELECT i::VARCHAR,'A',CASE WHEN i%2=0 THEN 'web' ELSE 'store' END,'paid','2026-08-15',CASE WHEN i%2=0 THEN 1 ELSE 3 END FROM range(1000) t(i)"
        )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    metric = (amount.sum() / amount.count()).per(customer).mean()
    fine = session.observe({"value": metric}, by=(channel,))
    chosen = (
        fine.where(fine.coord(fine.axes[0]) == "store")
        if selection == "where"
        else fine.top(0 if selection == "empty" else 1, by=fine["value"])
    )
    fixed = chosen.compute()
    keys = session.population_from(fixed, on=((fixed.axes[0], channel),))
    result = session.observe({"value": metric}, where=mv.in_population(keys)).compute().to_pandas()
    values = result["value", "value"]
    assert values.isna().all() if selection == "empty" else values.tolist() == [3.0]


@pytest.mark.parametrize("selected", [False, True])
def test_complex_drill_keeps_samples_and_selected_parent_fibers(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, selected: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" SELECT i::VARCHAR,'A',CASE WHEN i%2=0 THEN 'web' ELSE 'store' END,CASE WHEN i%2=0 THEN 'paid' ELSE 'cancel' END,'2026-08-15',CASE WHEN i%2=0 THEN 1 ELSE 3 END FROM range(1000) t(i)"
        )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    metric = amount.sum().per(customer).mean()
    if selected:
        fine = session.observe({"value": metric}, by=(status,))
        fixed = fine.top(1, by=fine["value"]).compute()
        keys = session.population_from(fixed, on=((fixed.axes[0], status),))
        query = session.observe(
            {"value": metric}, by=(status, channel), where=mv.in_population(keys)
        )
    else:
        query = session.observe({"value": metric}, by=(status, channel))
    result = query.compute().to_pandas()
    assert sorted(result["value", "value"].tolist()) == ([1500.0] if selected else [500.0, 1500.0])


def test_nested_complex_rollups_finish_every_intermediate_formula(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" SELECT i::VARCHAR,'A',\n            CASE WHEN i%2=0 THEN 'web' ELSE 'store' END,\n            CASE WHEN i%4<2 THEN 'paid' ELSE 'cancel' END,'2026-08-15',1\n            FROM range(1000) t(i)"
        )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    fine = session.observe({"value": amount.sum().per(customer).mean()}, by=(channel, status))
    trace = capture_source(monkeypatch)
    result = session.observe({"value": amount.sum().per(customer).mean()}).compute().to_pandas()
    assert result["value", "value"].tolist() == [1000.0]


def test_dynamic_sample_producer_retains_its_own_period(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" SELECT i::VARCHAR,\n            CASE WHEN i<500 THEN 'A' ELSE 'B' END,'web','paid',\n            CASE WHEN i<500 THEN '2026-09-10'::TIMESTAMPTZ ELSE '2026-10-07'::TIMESTAMPTZ END,\n            1 FROM range(1000) t(i)"
        )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    ordered = session.catalog.fields.get("sales.order.ordered_at")
    producer = session.observe(
        {"n": amount.count(time=ordered)},
        by=(session.time_scope(start="2026-09-01", end="2026-10-01"), customer),
    )
    fixed = producer.where(producer["n"] > 0).compute()
    samples = session.population_from(fixed, on=((fixed.axes[1], customer),))
    metric = amount.sum(time=ordered).per(samples).mean()
    fine = session.observe(
        {"value": metric}, by=(session.time_scope(start="2026-10-01", end="2026-11-01"), channel)
    )
    trace = capture_source(monkeypatch)
    result = (
        session.observe(
            {"value": metric}, by=(session.time_scope(start="2026-10-01", end="2026-11-01"),)
        )
        .compute()
        .to_pandas()["value", "value"]
    )
    assert result.tolist() == [0.0]


def test_complex_top_uses_the_exact_completed_parent_score(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        for index, (seed_channel, seed_customer, value) in enumerate(
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
                [str(index), seed_customer, seed_channel, "paid", "2026-10-07", value],
            )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    fine = session.observe({"value": amount.sum().per(customer).mean()}, by=(channel,))
    trace = capture_source(monkeypatch)
    result = fine.top(1, by=fine["value"]).compute().to_pandas()
    assert result["value", "value"].tolist() == [(2**53 + 1) / 3]
    assert result.iloc[:, 0].tolist() == ["Z"]


def test_complex_top_zero_preserves_the_fine_formula_failure(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" VALUES\n            ('1','A','web','paid','2026-08-15',0),\n            ('2','A','store','paid','2026-08-15',1)"
        )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    metric = amount.count().divide(amount.sum(), zero_denominator="error").per(customer).mean()
    fine = session.observe({"value": metric}, by=(channel,))
    trace = capture_source(monkeypatch)
    with pytest.raises(AnalysisError, match="zero denominator"):
        fine.top(0, by=fine["value"]).compute()


def test_dynamic_sample_domain_transports_distinct_complete_coordinates(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            "INSERT INTO \"order\" SELECT i::VARCHAR,'A',\n            CASE WHEN i%2=0 THEN 'web' ELSE 'store' END,'paid','2026-08-15',1\n            FROM range(1000) t(i)"
        )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    samples = session.members(session.catalog.entities.get("sales.order")).classify(by=(customer,))
    metric = amount.sum().per(samples).mean()
    trace = capture_source(monkeypatch)
    result = session.observe({"value": metric}).compute().to_pandas()
    assert result["value", "value"].tolist() == [1000.0]
