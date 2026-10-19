"""Explicit source leaves, fixed Frame operations and typed Population constraints."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import DslCaseFactory
from tests.support.source_trace import capture_source


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


def _attributes(session: Session) -> mv.AnalysisFrame:
    return session.read(
        session.members(ms.ref.entity("sales.orders")),
        {
            "amount": session.catalog.fields.get("sales.orders.amount"),
            "region": session.catalog.fields.get("sales.orders.region"),
        },
    )


def _identities(session: Session, frame: mv.AnalysisFrame) -> mv.Population:
    return session.population_from(
        frame, on=((frame.axes[0], session.catalog.entities.get("sales.orders")),)
    )


def _regions(session: Session, frame: mv.AnalysisFrame) -> mv.Population:
    return session.population_from(
        frame, on=((frame.axes[0], session.catalog.fields.get("sales.orders.region")),)
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
    source = _attributes(collated_source_query_session)
    result = (
        (
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
        .compute()
        .to_pandas()
    )
    expected = {
        "eq": ["a"],
        "ne": ["B", "Z", "é"],
        "lt": ["B", "Z"],
        "top_string": ["é", "a"],
        "top_tie": ["B", "Z"],
    }
    assert result.iloc[:, 0].tolist() == expected[operation]


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
    collated_source_query_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = collated_source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    grouped = session.observe({"count": amount.count()}, by=(region,))
    fixed = grouped.top(1, by=grouped["count"]).compute()
    assert fixed.to_pandas()["value", "count"].tolist() == [2]
    selected = _regions(session, fixed)
    key = fixed.to_pandas().iloc[0, 0]
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        expected = db.execute(
            "SELECT count(amount) FROM orders WHERE region IS NOT DISTINCT FROM ?", [key]
        ).fetchone()
    assert expected is not None
    trace = capture_source(monkeypatch)
    result = session.observe({"count": amount.count()}, where=mv.in_population(selected)).compute()
    assert result.to_pandas()["value", "count"].tolist() == [expected[0]]
    assert trace.uploaded_key_rows == [1]


@pytest.mark.parametrize("operation", ("read", "where", "select", "top", "sum", "rows"))
def test_terminal_relation_returns_only_selected_rows(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    source = _attributes(source_query_session)
    query = (
        source.where(source["amount"] >= 20)
        if operation == "where"
        else source.select({"kept": source["region"]})
        if operation == "select"
        else source.top(2, by=source["amount"])
        if operation == "top"
        else source.reduce({"total": source["amount"].sum()})
        if operation == "sum"
        else source.reduce({"rows": mv.row_count()})
        if operation == "rows"
        else source
    )
    trace = capture_source(monkeypatch)
    rows = query.compute().to_pandas()
    assert (
        len(rows) == {"read": 4, "where": 2, "select": 4, "top": 2, "sum": 1, "rows": 1}[operation]
    )
    if operation in ("where", "top"):
        assert rows.iloc[:, 0].tolist() == [2, 3]
        assert rows["value", "amount"].tolist() == [30, 20]
    elif operation == "sum":
        assert rows["value", "total"].tolist() == [60]
    elif operation == "rows":
        assert rows["value", "rows"].tolist() == [4]
    else:
        assert rows.iloc[:, 0].tolist() == [1, 2, 3, 4]
        name = "kept" if operation == "select" else "region"
        assert rows["value", name].iloc[:3].tolist() == ["A", "A", "B"]
        assert pd.isna(rows["value", name].iloc[3])
        if operation == "read":
            assert rows["value", "amount"].iloc[:3].tolist() == [10, 30, 20]
            assert pd.isna(rows["value", "amount"].iloc[3])
    assert trace.returned_rows == [4]
    assert trace.uploaded_key_rows == []
    assert source_query_session.runs().items == ()


def test_top_and_where_keep_call_order(source_query_session: Session) -> None:
    source = _attributes(source_query_session)
    top = source.top(2, by=source["amount"])
    filtered = source.where(source["amount"] < 30)
    assert top.where(top["amount"] < 30).compute().to_pandas().iloc[:, 0].tolist() == [3]
    assert filtered.top(2, by=filtered["amount"]).compute().to_pandas().iloc[:, 0].tolist() == [
        3,
        1,
    ]


def test_fixed_top_identity_bridge_reads_explicit_source(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    source = _attributes(session)
    fixed = source.top(1, by=source["amount"]).compute()
    keys = _identities(session, fixed)
    members = session.members(ms.ref.entity("sales.orders"), where=mv.in_population(keys))
    trace = capture_source(monkeypatch)
    rows = (
        session.observe(
            {"total": session.catalog.fields.get("sales.orders.amount").sum()}, members=members
        )
        .compute()
        .to_pandas()
    )
    assert fixed.to_pandas().iloc[:, 0].tolist() == [2]
    assert rows["value", "total"].tolist() == [30]
    assert trace.uploaded_key_rows == [1]


def test_empty_selection_retains_full_identity_checks(
    source_query_session: Session, tmp_path: Path
) -> None:
    source = _attributes(source_query_session)
    filtered = source.where(source["amount"] > 100)
    query = filtered.top(0, by=filtered["amount"])
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        db.execute("INSERT INTO orders VALUES (1,'different',99,0)")
    with pytest.raises(MaterializationError, match="exactly one attribute row"):
        query.compute()
    assert source_query_session.runs().items == ()


def test_cell_sum_overflow_is_checked_before_empty_filter(
    source_query_session: Session, tmp_path: Path
) -> None:
    source = _attributes(source_query_session)
    total = source.reduce({"total": source["amount"].sum()})
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        db.execute("UPDATE orders SET amount=9223372036854775807 WHERE id IN (1,2)")
    with pytest.raises(AnalysisError, match=r"overflow|current-row statistic"):
        total.where(total["total"] < 0).compute()


def test_fixed_aggregate_axis_bridge_reads_explicit_source(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    producer = session.observe(
        {"total": amount.sum()}, by=(session.catalog.fields.get("sales.orders.region"),)
    )
    fixed = producer.where(producer["total"] >= 30).compute()
    trace = capture_source(monkeypatch)
    result = session.observe(
        {"total": amount.sum()}, where=mv.in_population(_regions(session, fixed))
    ).compute()
    assert fixed.to_pandas().iloc[:, 0].tolist() == ["A"]
    assert result.to_pandas()["value", "total"].tolist() == [40]
    assert trace.uploaded_key_rows == [1]


@pytest.mark.parametrize("selected_region", ("A", "missing"))
def test_shared_frame_key_constraint_keeps_complement_masks(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, selected_region: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    fixed = source.where(source["region"] == selected_region).compute()
    condition = mv.in_population(_identities(session, fixed))
    amount = session.catalog.fields.get("sales.orders.amount")
    trace = capture_source(monkeypatch)
    result = (
        session.observe(
            {
                "selected": amount.sum(empty="zero").where(condition),
                "other": amount.sum(empty="zero").where(~condition),
            }
        )
        .compute()
        .to_pandas()
    )
    assert result["value", "selected"].tolist() == [40 if selected_region == "A" else 0]
    assert result["value", "other"].tolist() == [20 if selected_region == "A" else 60]
    assert trace.uploaded_key_rows == [2 if selected_region == "A" else 0]


@pytest.mark.parametrize("method", ("sum", "mean"))
def test_aggregate_selection_retains_parent_strict_null_check(
    source_query_session: Session, method: str
) -> None:
    session = source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    metric = amount.sum(nulls="error") if method == "sum" else amount.mean(nulls="error")
    producer = session.observe(
        {"value": metric}, by=(session.catalog.fields.get("sales.orders.region"),)
    )
    chosen = producer.where(producer["value"] > 1000)
    query = session.observe(
        {"total": amount.sum()}, where=mv.in_population(_regions(session, chosen))
    )
    with pytest.raises(MaterializationError, match="non-NA"):
        query.compute()
    assert session.runs().items == ()


def test_aggregate_top_population_keeps_original_parent_ranking(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    producer = session.observe(
        {"value": amount.sum()}, by=(session.catalog.fields.get("sales.orders.region"),)
    )
    fixed = producer.top(1, by=producer["value"]).compute()
    trace = capture_source(monkeypatch)
    rows = (
        session.observe({"total": amount.sum()}, where=mv.in_population(_regions(session, fixed)))
        .compute()
        .to_pandas()
    )
    assert fixed.to_pandas().iloc[:, 0].tolist() == ["A"]
    assert rows["value", "total"].tolist() == [40]
    assert trace.uploaded_key_rows == [1]


def test_top_ties_use_complete_identity_and_missing_scores_last(
    source_query_session: Session, tmp_path: Path
) -> None:
    source = _attributes(source_query_session)
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        db.execute("UPDATE orders SET amount=20 WHERE id IN (1,2,3)")
    assert source.top(4, by=source["amount"]).compute().to_pandas().iloc[:, 0].tolist() == [
        1,
        2,
        3,
        4,
    ]


def test_cell_sum_empty_input_stays_null(source_query_session: Session) -> None:
    source = _attributes(source_query_session)
    selected = source.where(source["amount"] > 100)
    rows = selected.reduce({"sum": selected["amount"].sum()}).compute().to_pandas()
    assert len(rows) == 1
    assert rows["value", "sum"].isna().all()


@pytest.mark.parametrize("operation", ("align", "compare"))
def test_shared_source_frames_keep_complete_endpoint_presence(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    source = _attributes(source_query_session)
    left = source.where((source["amount"] <= 20) | source["amount"].is_null())
    left = left.select({"value": left["amount"]})
    right = source.where((source["amount"] >= 20) | source["amount"].is_null())
    right = right.select({"value": right["amount"]})
    on = ((left.axes[0], right.axes[0]),)
    paired = (
        left.align(right, on=on, how="outer")
        if operation == "align"
        else left.compare(right, on=on, how="outer")
    )
    rows = paired.compute().to_pandas()
    assert rows.iloc[:, 0].tolist() == [1, 3, 4, 2]
    assert rows["presence", "left_present"].tolist() == [True, True, True, False]
    assert rows["presence", "right_present"].tolist() == [False, True, True, True]
    assert rows["value", "left.value"].dropna().tolist() == [10, 20]


def test_same_source_exact_alignment_failure_survives_top_zero(
    source_query_session: Session,
) -> None:
    source = _attributes(source_query_session)
    left = source.where(source["amount"] <= 20)
    right = source.where(source["amount"] >= 20)
    paired = left.align(right, on=((left.axes[0], right.axes[0]),))
    with pytest.raises(AnalysisError, match=r"complete|exact"):
        paired.top(0, by=paired["left.amount"]).compute()


def test_source_compare_keeps_large_integer_and_na_zero_policy(
    source_query_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        db.execute("UPDATE orders SET amount=9007199254740993, baseline=9007199254740992")
    source = session.read(
        session.members(ms.ref.entity("sales.orders")),
        {
            "amount": session.catalog.fields.get("sales.orders.amount"),
            "baseline": session.catalog.fields.get("sales.orders.baseline"),
        },
    )
    current = source.select({"value": source["amount"]})
    baseline = source.select({"value": source["baseline"]})
    rows = (
        current.compare(baseline, on=((current.axes[0], baseline.axes[0]),)).compute().to_pandas()
    )
    assert rows["value", "left.value"].tolist() == [9007199254740993] * 4
    assert rows["value", "absolute.value"].tolist() == [1] * 4
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        db.execute("UPDATE orders SET amount=NULL, baseline=0 WHERE id=4")
    nullable = source.where(source["amount"].is_null())
    zero = nullable.select({"value": nullable["baseline"]})
    nullable = nullable.select({"value": nullable["amount"]})
    rows = (
        nullable.compare(zero, on=((nullable.axes[0], zero.axes[0]),), zero_denominator="error")
        .compute()
        .to_pandas()
    )
    assert rows["value", "absolute.value"].isna().all()


def _sets(session: Session, operation: str) -> mv.Population:
    source = _attributes(session)
    left = _identities(session, source.where(source["amount"] >= 20).compute())
    right = _identities(session, source.where(source["amount"] <= 20).compute())
    return (
        left.union(right)
        if operation == "union"
        else left.intersection(right)
        if operation == "intersection"
        else left.difference(right)
    )


@pytest.mark.parametrize("operation", ("union", "intersection", "difference"))
def test_population_set_algebra_keeps_complete_frame_keys(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = source_query_session
    members = session.members(
        ms.ref.entity("sales.orders"), where=mv.in_population(_sets(session, operation))
    )
    rows = (
        session.observe(
            {"total": session.catalog.fields.get("sales.orders.amount").sum()}, members=members
        )
        .compute()
        .to_pandas()
    )
    assert rows["value", "total"].tolist() == [
        {"union": 60, "intersection": 20, "difference": 30}[operation]
    ]


@pytest.mark.parametrize("empty", ("zero", "null"))
def test_grouped_shared_population_consumers_keep_independent_grids(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, empty: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    fixed = source.where(source["amount"] >= 25).compute()
    condition = mv.in_population(_identities(session, fixed))
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    rows = (
        session.observe(
            {
                "selected": amount.sum(empty=empty).where(condition).where(region == "A"),
                "other": amount.sum(empty=empty).where(~condition).where(region == "B"),
                "nullable": amount.sum(empty="null").where(~condition),
            },
            by=(region,),
        )
        .compute()
        .to_pandas()
    )
    assert rows.iloc[:, 0].tolist()[:2] == ["A", "B"]
    assert rows["value", "selected"].iloc[0] == 30
    assert rows["value", "other"].iloc[1] == 20
    assert rows["value", "nullable"].iloc[:2].tolist() == [10, 20]
    assert pd.isna(rows["value", "nullable"].iloc[2])
    for name, index in (("selected", 1), ("other", 0)):
        value = rows["value", name].iloc[index]
        assert value == 0 if empty == "zero" else pd.isna(value)


@pytest.mark.parametrize("operation", ("union", "intersection", "difference"))
def test_population_set_terminal_uses_complete_frame_keys(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    rows = source_query_session.frame(_sets(source_query_session, operation)).compute().to_pandas()
    assert (
        sorted(rows.iloc[:, 0].tolist())
        == {"union": [1, 2, 3], "intersection": [3], "difference": [2]}[operation]
    )


@pytest.mark.parametrize("operation", ("product", "project", "filter"))
def test_frame_key_product_projection_and_filter_preserve_domain(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    selected = _identities(session, source.where(source["amount"] >= 20).compute())
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
    rows = session.frame(population).compute().to_pandas()
    assert len(rows) == (3 if operation == "project" else 6)
    if operation != "project":
        assert rows.iloc[:, 0].tolist() == [2, 2, 2, 3, 3, 3]
    assert rows.iloc[:, -1].isna().sum() == (1 if operation == "project" else 2)


def test_public_frame_bridge_evidence_keeps_producer_and_consumer_scopes(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = analysis_dsl_case_factory("j1").session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    status = session.catalog.fields.get("sales.order.status")
    ordered = session.catalog.fields.get("sales.order.ordered_at")
    producer = session.observe(
        {"count": amount.count(time=ordered).where(status == "paid")},
        by=(customer,),
        during=session.time_scope(start="2026-09-01", end="2026-10-01"),
    )
    chosen = producer.where(producer["count"] > 0)
    selected = session.population_from(chosen, on=((chosen.axes[0], customer),))
    query = session.observe(
        {"count": amount.count(time=ordered)},
        where=mv.in_population(selected),
        during=session.time_scope(start="2026-10-01", end="2026-11-01"),
    )
    trace = capture_source(monkeypatch)
    saved = query.persist()
    reads = [
        dict(item.values) for item in saved.artifact.evidence_digest().items if item.kind == "read"
    ]
    assert len(reads) == 2
    assert len({(read["period_start"], read["period_end"]) for read in reads}) == 2
    starts = set()
    for read in reads:
        start, end = read["period_start"], read["period_end"]
        assert isinstance(start, datetime) and isinstance(end, datetime)
        starts.add((start.month, end.month))
        assert isinstance(read["returned_rows"], int)
        assert read["returned_rows"] >= 0
        assert read["returned_rows"] in trace.returned_rows
        if start.month == 9:
            assert "paid" in str(read["filters"])
    assert starts == {(9, 10), (10, 11)}
    assert len(session.runs().items) == 1
