"""Result expressions consume the same checked source/pandas stage input."""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo
import marivo.analysis as mv
from marivo.analysis.dsl.column import row_count
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.semantic.expressions import Entity
from marivo.semantic.reader import SemanticProject


def _population(
    session: Session, frame: mv.AnalysisFrame, *targets: Deferred | Entity
) -> mv.Population:
    return session.population_from(frame, on=tuple(zip(frame.axes, targets, strict=True)))


@pytest.fixture
def result_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "results.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, region VARCHAR, amount BIGINT, channel VARCHAR)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,'A',10,'Web'),(2,'A',30,'Store'),(3,'B',20,'Web'),(4,'C',NULL,'Web')"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id'])
region = ms.dimension_column(name='region', entity=orders, column='region')
channel = ms.dimension_column(name='channel', entity=orders, column='channel')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all(), unit='CNY')
other_orders = ms.entity(name='other_orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id'])
other_channel = ms.dimension_column(name='channel', entity=other_orders, column='channel')
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("results", timezone="UTC")


@pytest.mark.parametrize("mapping", (None, False, 0, {}))
@pytest.mark.parametrize("entry", ("read", "coordinate_values"))
def test_constructor_mapping_refusal_precedes_source_io(
    result_session: Session, monkeypatch: pytest.MonkeyPatch, mapping: object, entry: str
) -> None:
    from marivo.datasource.adapters import SourceSession

    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    people = session.members(session.catalog.entities.get("sales.orders"))
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append("source")
        raise AssertionError("malformed mapping attempted source I/O")

    for name in ("__enter__", "bind", "compile", "execute"):
        monkeypatch.setattr(SourceSession, name, forbidden)
    before = session.runs().items
    with pytest.raises(AnalysisError) as failure:
        if entry == "read":
            session.read(people, mapping)  # type: ignore[arg-type]  # Deliberately malformed public mapping.
        else:
            session.values(by=(region,), columns=mapping, rows=((("A",), {"value": 1}),))  # type: ignore[arg-type]  # Deliberately malformed public mapping.
    error = failure.value
    assert error.expected and error.received and error.repair is not None
    if entry == "read":
        assert error.expected == "a nonempty named Field mapping"
        assert "session.read" in error.repair.action
    else:
        assert error.expected == "named columns and finite typed literal rows"
        assert "literal row" in error.repair.action
    assert error.run_ref is None and calls == []
    assert session.runs().items == before == ()
    assert session._runtime.store.resources(session.id) == ()


def _assert_compute_repair(error: AnalysisError) -> None:
    from marivo._help.render import help as public_help

    assert marivo.help is public_help
    assert error.expected and error.received and error.repair is not None
    assert error.repair.action
    target = error.repair.help_target
    assert target.surface == "analysis" and target.canonical_id == "analysis_frame.compute"
    public_help(f"{target.surface}.{target.canonical_id}")


@pytest.mark.parametrize("failure_kind", ("cleanup", "numeric_carrier"))
def test_actual_source_failure_compute_help_resolves_without_run(
    result_session: Session, monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = result_session
    source = session.observe({"amount": session.catalog.fields.get("sales.orders.amount").sum()})
    close = SourceSession.close
    execute = SourceSession.execute

    def fail_close(owner: SourceSession) -> None:
        close(owner)
        raise RuntimeError("injected source cleanup failure")

    def incompatible(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        rows = execute(owner, read)
        numeric = rows.select_dtypes(include="number").columns
        assert len(numeric)
        rows[numeric[0]] = "incompatible numeric carrier"
        return rows

    if failure_kind == "cleanup":
        monkeypatch.setattr(SourceSession, "close", fail_close)
    else:
        monkeypatch.setattr(SourceSession, "execute", incompatible)
    with pytest.raises(MaterializationError) as failure:
        source.compute()
    error = failure.value
    _assert_compute_repair(error)
    assert error.expected is not None
    if failure_kind == "cleanup":
        assert error.stage == "frame_compute" and isinstance(error.__cause__, RuntimeError)
        assert "source cleanup" in error.expected
    else:
        assert error.expected == "value.0: a native numeric column"
        assert error.received == "string"
    assert error.run_ref is None
    assert session.runs().items == ()
    assert session._runtime.store.resources(session.id) == ()


def test_select_where_top_and_independent_statistics(result_session: Session) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    q = session.observe({"sum": amount.sum(empty="zero"), "mean": amount.mean()}, by=(region,))
    assert q.select({"net": q["sum"] - 10}).compute().to_pandas()[("value", "net")].tolist() == [
        30,
        10,
        -10,
    ]
    selected = q.where(q["sum"] >= 20)
    result = selected.top(1, by=selected["sum"]).compute().to_pandas()
    assert result.iloc[:, 0].tolist() == ["A"]
    assert result[("value", "sum")].tolist() == [40]
    statistics = (
        q.reduce({"sum": q["sum"].sum(), "mean": q["mean"].mean(), "rows": row_count()})
        .compute()
        .to_pandas()
    )
    assert statistics[("value", "sum")].tolist() == [60]
    assert statistics[("value", "mean")].tolist() == [20.0]
    assert statistics[("value", "rows")].tolist() == [3]


def test_explicit_source_reads_selected_fibers_and_original_mean(result_session: Session) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    amount = session.catalog.fields.get("sales.orders.amount")
    fine = session.observe({"mean": amount.mean()}, by=(region,))
    eligible = fine.where(fine["mean"] == 20)
    selected = eligible.top(1, by=eligible["mean"])
    chosen = _population(session, selected, region)
    drilled = session.observe(
        {"mean": amount.mean()}, by=(region, channel), where=mv.in_population(chosen)
    )
    frame = drilled.compute().to_pandas()
    assert frame.iloc[:, 0].tolist() == ["A", "A"]
    assert frame.iloc[:, 1].tolist() == ["Store", "Web"]
    assert frame[("value", "mean")].tolist() == [30.0, 10.0]
    assert session.observe(
        {"mean": amount.mean()}, by=(region,), where=mv.in_population(chosen)
    ).compute().to_pandas()[("value", "mean")].tolist() == [20.0]


def test_explicit_selected_members_keep_complete_identity(result_session: Session) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    fine = session.observe(
        {
            "people": session.members(session.catalog.entities.get("sales.orders"))
            .classify(by=(mv.identity(session.catalog.entities.get("sales.orders")), region))
            .count()
        },
        by=(region,),
    )
    selected = fine.where(fine["people"] == 2)
    chosen = _population(session, selected, region)
    domain = session.members(
        session.catalog.entities.get("sales.orders"), where=mv.in_population(chosen)
    )
    domain = domain.classify(by=(domain.axes[0], region, channel))
    drilled = session.observe({"people": domain.count()}, by=(region, channel))
    assert drilled.compute().to_pandas()[("value", "people")].tolist() == [1, 1]
    assert session.observe({"people": domain.count()}, by=(region,)).compute().to_pandas()[
        ("value", "people")
    ].tolist() == [2]


def test_explicit_source_read_preserves_snapshot_row_identity(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "versions.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT,snapshot_day DATE,region VARCHAR,channel VARCHAR,amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES(1,'2026-01-01','East','Web',10),(1,'2026-01-02','East','Store',30),(2,'2026-01-01','West','Other',300)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders=ms.entity(name='orders',datasource=ms.ref.datasource('warehouse'),source=md.table('orders'),primary_key=['id'],versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.orders.snapshot_day'),grain='day',timezone='UTC'))
snapshot_day=ms.time_dimension_column(name='snapshot_day',entity=orders,column='snapshot_day',granularity='day')
region=ms.dimension_column(name='region',entity=orders,column='region')
channel=ms.dimension_column(name='channel',entity=orders,column='channel')
amount=ms.measure_column(name='amount',entity=orders,column='amount',additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("versions", timezone="UTC")
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    entity = session.catalog.entities.get("sales.orders")
    at = session.instant("2026-01-01")
    existing = session.members(entity, at=at)
    requested = mv.coordinates(by=(mv.identity(entity, at=at),), keys=(((1,),), ((999,),)))
    assert session.frame(existing.intersection(requested)).compute().to_pandas().iloc[
        :, 0
    ].tolist() == [1]
    fine = session.observe({"total": amount.sum()}, by=(region,))
    selected = fine.where(fine["total"] < 100)
    chosen = _population(session, selected, region)
    drilled = session.observe(
        {"total": amount.sum()}, by=(region, channel), where=mv.in_population(chosen)
    )
    frame = drilled.compute().to_pandas()
    assert frame.iloc[:, 1].tolist() == ["Store", "Web"]
    assert frame[("value", "total")].tolist() == [30, 10]
    assert session.observe(
        {"total": amount.sum()}, where=mv.in_population(chosen)
    ).compute().to_pandas()[("value", "total")].tolist() == [40]


def test_result_division_policy_and_exact_domain_binding(result_session: Session) -> None:
    session = result_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    q = session.observe({"sum": amount.sum()}, by=(region,))
    divided = q.select({"ratio": q["sum"] / 0}).compute().to_pandas()
    assert divided[("value", "ratio")].isna().all()
    with pytest.raises(AnalysisError) as failure:
        q.select({"ratio": q["sum"].divide(0, zero_denominator="error")}).compute()
    _assert_compute_repair(failure.value)
    assert failure.value.run_ref is None and session.runs().items == ()
    other = session.observe({"sum": amount.sum()}, by=(region,))
    # Independent captures require explicit Frame correspondence before composition.
    with pytest.raises(AnalysisError):
        q.select({"same": other["sum"]})
    assert q.select({"same": q["sum"]}).compute().to_pandas()[("value", "same")].to_dict() == {
        0: 40,
        1: 20,
        2: None,
    }
    with pytest.raises(AnalysisError):
        q.select({"bad": q.where(q["sum"] > 0)["sum"]})


def test_public_compare_retains_presence_and_projection_on_cold_read(
    result_session: Session,
) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    current = session.observe({"sales": amount.sum()}, by=(region,))
    baseline = session.values(
        by=(region,),
        columns={"sales": "CNY"},
        rows=((("A",), {"sales": -20}), (("B",), {"sales": 0}), (("D",), {"sales": 10})),
    )
    comparison = current.compare(baseline, on=((current.axes[0], baseline.axes[0]),), how="outer")
    artifact = comparison.persist()
    frame = session.persisted_frame(artifact.artifact.id).to_pandas()
    assert frame[("value", "absolute.sales")].iloc[:2].tolist() == [60, 20]
    assert frame[("value", "relative.sales")].iloc[0] == 3.0
    assert pd.isna(frame[("value", "relative.sales")].iloc[1])
    assert frame[("presence", "left_present")].tolist() == [True, True, True, False]
    assert frame[("presence", "right_present")].tolist() == [True, True, False, True]
    assert comparison.axes[0].role == "field"
    selected = comparison.select({"delta": comparison["absolute.sales"]})
    assert selected.compute().to_pandas()[("presence", "left_present")].tolist() == [
        True,
        True,
        True,
        False,
    ]


def test_empty_reduction_keeps_scalar_and_checks(result_session: Session) -> None:
    session = result_session
    amount = session.catalog.fields.get("sales.orders.amount")
    q = session.observe(
        {"sum": amount.sum()}, by=(session.catalog.fields.get("sales.orders.region"),)
    )
    empty = q.top(0, by=q["sum"])
    result = (
        empty.reduce({"sum": empty["sum"].sum(), "mean": empty["sum"].mean(), "rows": row_count()})
        .compute()
        .to_pandas()
    )
    assert result[("value", "sum")].isna().all()
    assert result[("value", "mean")].isna().all()
    assert result[("value", "rows")].tolist() == [0]


def test_literal_values_and_saved_top_order(result_session: Session) -> None:
    from marivo.analysis.core.frame_data import StoredFrameBacking

    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    q = session.values(
        by=(region,),
        columns={"budget": "CNY"},
        rows=((("A",), {"budget": 10}), (("B",), {"budget": 30}), (("C",), {"budget": 20})),
    )
    ranked = q.top(3, by=q["budget"])
    selected = ranked.where(ranked["budget"] > 10)
    saved = selected.persist()
    assert saved.to_pandas().iloc[:, 0].tolist() == ["B", "C"]
    assert isinstance(saved._backing, StoredFrameBacking)
    preview = session._runtime.local.preview(saved._backing, 1)
    assert preview.rows.iloc[:, 0].tolist() == ["B"]
    reopened = session.persisted_frame(saved.artifact.id)
    assert isinstance(reopened._backing, StoredFrameBacking)
    assert session._runtime.local.preview(reopened._backing, 2).rows.iloc[:, 0].tolist() == [
        "B",
        "C",
    ]
    scalar = session.values(by=(), columns={"budget": "CNY"}, rows=(((), {"budget": 100}),))
    assert scalar.compute().to_pandas()[("value", "budget")].tolist() == [100]


def test_values_bind_real_key_types_and_preserve_exact_constants(
    result_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime
    from decimal import Decimal

    from marivo.datasource.adapters import SourceSession

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("literal Query must not read business values")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    for field, key in ((region, 1), (amount, True)):
        query = session.values(
            by=(field,), columns={"budget": "CNY"}, rows=(((key,), {"budget": 1}),)
        )
        with pytest.raises(AnalysisError, match="coordinate"):
            query.compute()
    nullable = (
        session.values(by=(region,), columns={"budget": "CNY"}, rows=(((None,), {"budget": 1}),))
        .compute()
        .to_pandas()
    )
    assert pd.isna(nullable.iloc[0, 0])
    large = (
        session.values(by=(), columns={"budget": "CNY"}, rows=(((), {"budget": 2**70}),))
        .compute()
        .to_pandas()
    )
    assert large[("value", "budget")].tolist() == [Decimal(2**70)]
    mixed = (
        session.values(
            by=(region,),
            columns={"budget": "CNY"},
            rows=((("A",), {"budget": Decimal("1.5")}), (("B",), {"budget": 2.0})),
        )
        .compute()
        .to_pandas()
    )
    assert mixed[("value", "budget")].tolist() == [1.5, 2.0]
    for invalid in (float("nan"), datetime(2026, 1, 1)):
        with pytest.raises(AnalysisError):
            session.values(by=(), columns={"value": None}, rows=(((), {"value": invalid}),))


def test_frame_population_uses_selected_keys_and_preserves_checks(result_session: Session) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    q = session.observe({"sum": amount.sum()}, by=(region,))
    chosen = q.where(q["sum"] > 20)
    fixed = mv.coordinates(by=(region,), keys=(("B",), (None,)))
    domain = _population(session, chosen, region).union(fixed)
    result = session.frame(domain).compute().to_pandas()
    assert [None if pd.isna(value) else value for value in result.iloc[:, 0]] == ["A", None, "B"]
    strict = q.select({"bad": q["sum"].divide(0, zero_denominator="error")})
    with pytest.raises(AnalysisError):
        session.frame(_population(session, strict, region)).compute()


def test_explicit_members_and_member_count(result_session: Session) -> None:

    session = result_session
    orders = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    requested = mv.coordinates(by=(mv.identity(orders),), keys=(((1,),), ((3,),), ((999,),)))
    result = (
        session.observe(
            {
                "total": amount.sum(),
                "people": requested.count(),
                "per_person": amount.sum() / requested.count(),
            },
            where=mv.in_population(requested),
        )
        .compute()
        .to_pandas()
    )
    assert result[("value", "total")].tolist() == [30]
    assert result[("value", "people")].tolist() == [3]
    assert result[("value", "per_person")].tolist() == [10.0]
    complete = session.observe({"total": amount.sum()}, by=requested).compute().to_pandas()
    assert complete.iloc[:, 0].tolist() == [1, 3, 999]
    assert complete[("value", "total")].to_dict() == {0: 10, 1: 20, 2: None}
    assert isinstance(requested.axes[0], mv.Grouping)
    assert requested.axes[0].kind == "identity"
    assert requested.classify(by=(requested.axes[0],)).axes == requested.axes
    assert session.frame(requested.project(by=(requested.axes[0],))).compute().to_pandas().iloc[
        :, 0
    ].tolist() == [1, 3, 999]
    with pytest.raises(AnalysisError):
        session.observe({"total": amount.sum()}, by=(mv.identity(()),))
    regions = mv.coordinates(
        by=(session.catalog.fields.get("sales.orders.region"),), keys=(("A",),)
    )
    assert session.observe({"regions": regions.count()}).compute().to_pandas()[
        ("value", "regions")
    ].tolist() == [1]
    empty = mv.coordinates(by=(mv.identity(orders),), keys=())
    result = (
        session.observe(
            {"people": empty.count(), "sum": amount.sum()}, where=mv.in_population(empty)
        )
        .compute()
        .to_pandas()
    )
    assert result[("value", "people")].tolist() == [0]
    assert result[("value", "sum")].isna().all()


def test_population_count_counts_full_tuples_and_empty_cells(result_session: Session) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    domain = mv.coordinates(
        by=(region, channel),
        keys=(("A", "Web"), ("A", "Store"), (None, "Web"), ("A", "Web")),
    )
    scalar = session.observe({"tuples": domain.count()}).compute().to_pandas()
    assert scalar[("value", "tuples")].tolist() == [3]
    groups = mv.coordinates(by=(region,), keys=(("A",), (None,), ("missing",)))
    grouped = session.observe({"tuples": domain.count()}, by=groups).compute().to_pandas()
    assert [
        (None if pd.isna(key) else key, count)
        for key, count in grouped.itertuples(index=False, name=None)
    ] == [
        (None, 1),
        ("A", 2),
        ("missing", 0),
    ]
    empty = mv.coordinates(by=(), keys=())
    unit = mv.coordinates(by=(), keys=((),))
    scalars = session.observe({"empty": empty.count(), "unit": unit.count()}).compute().to_pandas()
    assert scalars[("value", "empty")].tolist() == [0]
    assert scalars[("value", "unit")].tolist() == [1]


def test_frame_keys_drive_a_later_typed_source_read(result_session: Session) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    q = session.observe({"sum": amount.sum()}, by=(region,))
    chosen = _population(session, q.where(q["sum"] > 20), region)
    follow = session.observe({"selected": amount.sum().where(mv.in_population(chosen))})
    assert follow.compute().to_pandas()[("value", "selected")].tolist() == [40]


def test_dependent_source_uses_complete_null_safe_key_relations(
    result_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = result_session
    with duckdb.connect(str(session.project_root / "results.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=7,channel=NULL WHERE id=4")
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    amount = session.catalog.fields.get("sales.orders.amount")
    chosen = _population(
        session,
        session.values(
            by=(region, channel),
            columns={"flag": "1"},
            rows=(
                (("A", "Store"), {"flag": 1}),
                (("B", "Web"), {"flag": 1}),
                (("C", None), {"flag": 1}),
            ),
        ),
        region,
        channel,
    )
    statements: list[str] = []
    original = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append(read.sql)
        return original(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    result = (
        session.observe({"total": amount.sum()}, where=mv.in_population(chosen))
        .compute()
        .to_pandas()
    )
    assert result[("value", "total")].tolist() == [57]
    assert len(statements) == 1
    assert "UNION" not in statements[0]
    assert "JOIN" in statements[0]
    assert "'Store'" not in statements[0]
    assert "IS NOT DISTINCT FROM" in statements[-1]

    selected = session.members(
        session.catalog.entities.get("sales.orders"), where=mv.in_population(chosen)
    )
    assert session.frame(selected).compute().to_pandas().iloc[:, 0].tolist() == [2, 3, 4]
    remainder = session.members(
        session.catalog.entities.get("sales.orders"), where=~mv.in_population(chosen)
    )
    assert session.frame(remainder).compute().to_pandas().iloc[:, 0].tolist() == [1]


def test_frame_population_transport_keeps_consumer_expression_bounded(
    result_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis.expr.operations as ops

    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    producer = session.observe({"total": amount.sum()}, by=(region,))
    selected = _population(session, producer.where(producer["total"] > 0), region)
    consumer = session.observe({"total": amount.sum()}, where=mv.in_population(selected))
    statements: list[tuple[str, int]] = []
    original = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append((read.sql, len(read.expression.op().find(ops.Node))))
        return original(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    sizes = []
    for count in (1_000, 10_000):
        with duckdb.connect(str(session.project_root / "results.duckdb")) as connection:
            connection.execute("DELETE FROM orders")
            connection.execute(
                "INSERT INTO orders SELECT i, 'region_' || i, 1, 'Web' FROM range(?) AS rows(i)",
                [count],
            )
        statements.clear()
        result = consumer.compute().to_pandas()
        assert result[("value", "total")].tolist() == [count]
        assert len(statements) == 2
        sql, nodes = statements[-1]
        assert "region_0" not in sql
        assert "UNION" not in sql
        assert "_marivo_keys_" in sql
        sizes.append((len(sql), nodes))
    assert abs(sizes[1][0] - sizes[0][0]) <= 128
    assert sizes[1][1] == sizes[0][1]


def test_source_read_frame_bridge_uses_bounded_key_transport(
    result_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis.expr.operations as ops

    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    members = session.members(session.catalog.entities.get("sales.orders"))
    attributes = session.read(members, {"region": region})
    selected = _population(
        session,
        attributes.where(attributes["region"] == "selected"),
        session.catalog.entities.get("sales.orders"),
    )
    consumer = session.observe({"total": amount.sum()}, where=mv.in_population(selected))
    statements: list[tuple[str, int]] = []
    original = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append((read.sql, len(read.expression.op().find(ops.Node))))
        return original(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    sizes = []
    for count in (1_000, 10_000, 100_000):
        with duckdb.connect(str(session.project_root / "results.duckdb")) as connection:
            connection.execute("DELETE FROM orders")
            connection.execute(
                "INSERT INTO orders SELECT 9007199254740993 + i, "
                "CASE WHEN i % 2 = 0 THEN 'selected' ELSE 'other' END, 1, 'Web' "
                "FROM range(?) AS rows(i)",
                [count],
            )
        statements.clear()
        result = consumer.compute().to_pandas()
        assert result[("value", "total")].tolist() == [count // 2]
        assert len(statements) == 2
        sql, nodes = statements[-1]
        assert "JOIN" in sql
        assert "'selected'" not in sql
        assert "_marivo_keys_" in sql
        sizes.append((len(sql), nodes))
    assert max(length for length, _ in sizes) - min(length for length, _ in sizes) <= 128
    assert len({nodes for _, nodes in sizes}) == 1


def test_source_read_frame_bridge_preserves_complete_keys_and_complements(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    large = 2**53 + 1
    database = tmp_path / "accounts.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE accounts(tenant VARCHAR,id BIGINT,region VARCHAR,amount BIGINT)"
        )
        connection.executemany(
            "INSERT INTO accounts VALUES (?,?,?,?)",
            [
                ("t1", large, "east", 10),
                ("t2", large + 1, "east", 20),
                ("t1", large + 1, "west", 100),
                ("t2", large, None, 200),
            ],
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
accounts = ms.entity(name='accounts',datasource=ms.ref.datasource('warehouse'),source=md.table('accounts'),primary_key=['tenant','id'])
region = ms.dimension_column(name='region',entity=accounts,column='region')
amount = ms.measure_column(name='amount',entity=accounts,column='amount',additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("source-keys", timezone="UTC")
    members = session.members(session.catalog.entities.get("sales.accounts"))
    region = session.catalog.fields.get("sales.accounts.region")
    amount = session.catalog.fields.get("sales.accounts.amount")
    attributes = session.read(members, {"region": region})
    selected = _population(
        session,
        attributes.where(attributes["region"] == "east"),
        session.catalog.entities.get("sales.accounts"),
    )
    missing = _population(
        session,
        attributes.where(attributes["region"].is_null()),
        session.catalog.entities.get("sales.accounts"),
    )
    empty = _population(
        session,
        attributes.where(attributes["region"] == "absent"),
        session.catalog.entities.get("sales.accounts"),
    )
    statements: list[str] = []
    original = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append(read.sql)
        return original(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    for condition, expected in (
        (mv.in_population(selected), 30),
        (~mv.in_population(selected), 300),
        (mv.in_population(missing), 200),
        (mv.in_population(empty), 0),
    ):
        statements.clear()
        result = (
            session.observe({"total": amount.sum(empty="zero")}, where=condition)
            .compute()
            .to_pandas()
        )
        assert result[("value", "total")].tolist() == [expected]
        assert len(statements) == 2
        assert "IS NOT DISTINCT FROM" in statements[-1]


@pytest.mark.parametrize(
    "selected_region,corruption,expected",
    (
        ("A", "null", "non-null complete identity keys"),
        ("absent", "null", "non-null complete identity keys"),
        ("A", "duplicate", "exactly one attribute row for every requested identity"),
    ),
)
def test_source_read_frame_bridge_checks_source_contracts_before_selection(
    result_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    selected_region: str,
    corruption: str,
    expected: str,
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = result_session
    with duckdb.connect(str(session.project_root / "results.duckdb")) as connection:
        connection.execute(
            "UPDATE orders SET id=NULL WHERE id=4"
            if corruption == "null"
            else "INSERT INTO orders SELECT * FROM orders WHERE id=4"
        )
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    members = session.members(session.catalog.entities.get("sales.orders"))
    attributes = session.read(members, {"region": region})
    selected = _population(
        session,
        attributes.where(attributes["region"] == selected_region),
        session.catalog.entities.get("sales.orders"),
    )
    statements: list[str] = []
    original = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append(read.sql)
        return original(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    with pytest.raises(AnalysisError, match=expected):
        session.observe({"total": amount.sum()}, where=mv.in_population(selected)).compute()
    assert len(statements) == 1


def test_top_selected_frame_bridge_keeps_source_checks(
    result_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = result_session
    amount = session.catalog.fields.get("sales.orders.amount")
    members = session.members(session.catalog.entities.get("sales.orders"))
    attributes = session.read(members, {"amount": amount})
    selected = _population(
        session,
        attributes.top(1, by=attributes["amount"]),
        session.catalog.entities.get("sales.orders"),
    )
    statements: list[str] = []
    original = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append(read.sql)
        return original(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    result = (
        session.observe({"total": amount.sum()}, where=mv.in_population(selected))
        .compute()
        .to_pandas()
    )
    assert result[("value", "total")].tolist() == [30]
    assert len(statements) == 2
    assert "_marivo_keys_" in statements[-1]


def test_float_nan_attribute_selection_keeps_pandas_missing_value_semantics(
    result_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = result_session
    with duckdb.connect(str(session.project_root / "results.duckdb")) as connection:
        connection.execute("ALTER TABLE orders ALTER COLUMN amount TYPE DOUBLE")
        connection.execute("UPDATE orders SET amount='NaN'::DOUBLE WHERE id=4")
    entity = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    attributes = session.read(
        session.members(session.catalog.entities.get("sales.orders")), {"amount": amount}
    )
    selected = _population(
        session,
        attributes.where(attributes["amount"].is_null()),
        session.catalog.entities.get("sales.orders"),
    )
    statements: list[str] = []
    original = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append(read.sql)
        return original(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    result = (
        session.observe({"rows": entity.count()}, where=mv.in_population(selected))
        .compute()
        .to_pandas()
    )
    assert result[("value", "rows")].tolist() == [1]
    assert len(statements) == 2
    assert "_marivo_keys_" in statements[-1]


def test_submicrosecond_timestamp_selection_keeps_exact_pandas_literal(
    result_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = result_session
    with duckdb.connect(str(session.project_root / "results.duckdb")) as connection:
        connection.execute("ALTER TABLE orders ADD COLUMN happened TIMESTAMPTZ")
        connection.execute("UPDATE orders SET happened=TIMESTAMPTZ '2026-10-18 01:02:03.123456+00'")
    model = session.project_root / "models/semantic/sales/model.py"
    model.write_text(
        model.read_text()
        + "\nhappened = ms.time_dimension_column(name='happened', entity=orders, "
        + "column='happened', granularity='second')\n"
    )
    happened = session.catalog.fields.get("sales.orders.happened")
    amount = session.catalog.fields.get("sales.orders.amount")
    attributes = session.read(
        session.members(session.catalog.entities.get("sales.orders")), {"happened": happened}
    )
    selected = attributes.where(
        attributes["happened"] < pd.Timestamp("2026-10-18T01:02:03.123456999Z")
    )
    assert len(selected.compute().to_pandas()) == 4
    statements: list[str] = []
    execute = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append(read.sql)
        return execute(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    result = (
        session.observe(
            {"total": amount.sum(empty="zero")},
            where=mv.in_population(
                _population(session, selected, session.catalog.entities.get("sales.orders"))
            ),
        )
        .compute()
        .to_pandas()
    )
    assert result[("value", "total")].tolist() == [60]
    assert len(statements) == 2
    assert "_marivo_keys_" in statements[-1]


def test_shared_frame_selection_transports_once_for_checked_source_consumers(
    result_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis.expr.types as ir
    import pyarrow as pa

    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    members = session.members(session.catalog.entities.get("sales.orders"))
    attributes = session.read(members, {"region": region})
    selected = _population(
        session,
        attributes.where(attributes["region"] == "A"),
        session.catalog.entities.get("sales.orders"),
    )
    condition = mv.in_population(selected)
    consumer = session.observe(
        {
            "selected": amount.sum(empty="zero").where(condition),
            "other": amount.sum(empty="zero").where(~condition),
        }
    )
    statements: list[str] = []
    uploads: list[int] = []
    execute = SourceSession.execute
    bind = SourceSession._bind_key_relation

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append(read.sql)
        return execute(owner, read)

    def upload(owner: SourceSession, data: pa.Table) -> ir.Table:
        uploads.append(data.num_rows)
        return bind(owner, data)

    monkeypatch.setattr(SourceSession, "execute", record)
    monkeypatch.setattr(SourceSession, "_bind_key_relation", upload)
    result = consumer.compute().to_pandas()
    assert result[("value", "selected")].tolist() == [40]
    assert result[("value", "other")].tolist() == [20]
    assert uploads == [2]
    assert len(statements) == 3
    assert "_marivo_keys_" in statements[-1]


def test_session_read_preserves_identity_and_feeds_a_new_source(result_session: Session) -> None:
    session = result_session
    orders = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    people = mv.coordinates(by=(mv.identity(orders),), keys=(((1,),), ((2,),)))
    q = session.read(people, {"amount": amount, "region": region})
    result = q.compute().to_pandas()
    assert result.iloc[:, 0].tolist() == [1, 2]
    assert result[("value", "region")].tolist() == ["A", "A"]
    selected = _population(
        session, q.where(q["amount"] > 10), session.catalog.entities.get("sales.orders")
    )
    assert session.observe(
        {"total": amount.sum()}, where=mv.in_population(selected)
    ).compute().to_pandas()[("value", "total")].tolist() == [30]
    missing = session.read(
        mv.coordinates(by=(mv.identity(orders),), keys=(((999,),),)), {"region": region}
    )
    with pytest.raises(AnalysisError):
        missing.compute()


def test_explicit_source_aggregation_and_current_row_statistics(result_session: Session) -> None:
    session = result_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    fine = session.observe(
        {"sales": amount.sum(), "aov": amount.mean(), "shifted": amount.sum() + 10}, by=(region,)
    )
    chosen = fine.where(fine["sales"] > 0)
    domain = _population(session, chosen, region)
    original = (
        session.observe(
            {"sales": amount.sum(), "aov": amount.mean(), "shifted": amount.sum() + 10},
            where=mv.in_population(domain),
        )
        .compute()
        .to_pandas()
    )
    assert original[("value", "sales")].tolist() == [60]
    assert original[("value", "aov")].tolist() == [20.0]
    assert original[("value", "shifted")].tolist() == [70]
    rows = (
        chosen.reduce({"sales": chosen["sales"].sum(), "shifted": chosen["shifted"].sum()})
        .compute()
        .to_pandas()
    )
    assert rows[("value", "sales")].tolist() == [60]
    assert rows[("value", "shifted")].tolist() == [80]
    fixed = fine.compute()
    denominator = int(
        fixed.reduce({"total": fixed["sales"].sum()})
        .compute()
        .to_pandas()[("value", "total")]
        .iloc[0]
    )
    shares = fixed.select({"sales": fixed["sales"], "share": fixed["sales"] / denominator})
    normalized = shares.compute().to_pandas()
    assert normalized[("value", "share")].dropna().tolist() == pytest.approx([2 / 3, 1 / 3])
    assert normalized[("value", "share")].isna().tolist() == [False, False, True]
    assert shares.where(shares["sales"] > 20).compute().to_pandas()[
        ("value", "share")
    ].tolist() == pytest.approx([2 / 3])
    zeroed = session.observe({"sales": amount.sum(empty="zero")}, by=(region,)).compute()
    assert zeroed.select({"ratio": zeroed["sales"] / denominator}).compute().to_pandas()[
        ("value", "ratio")
    ].tolist() == pytest.approx([2 / 3, 1 / 3, 0])
    assert session.observe(
        {"sales": amount.sum()}, by=(region,), where=mv.in_population(domain)
    ).compute().to_pandas()[("value", "sales")].tolist() == [40, 20]


def test_two_datasource_composite_parameters_compare_and_terminal_read(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    databases = tuple(tmp_path / (name + ".duckdb") for name in ("crm", "billing"))
    for database, amounts in zip(databases, ((150, 50), (100, 25)), strict=True):
        with duckdb.connect(str(database)) as connection:
            connection.execute("CREATE TABLE accounts(tenant VARCHAR,id BIGINT,amount BIGINT)")
            connection.executemany(
                "INSERT INTO accounts VALUES (?,?,?)",
                [("t1", 9007199254740993, amounts[0]), ("t2", 9007199254740993, amounts[1])],
            )
    semantic_project_factory(
        {
            **{
                f"datasources/{name}.py": "import marivo.datasource as md\n"
                + f"md.duckdb(name={name!r},path={str(database)!r})\n"
                for name, database in zip(("crm", "billing"), databases, strict=True)
            },
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
crm = ms.entity(name='crm',datasource=ms.ref.datasource('crm'),source=md.table('accounts'),primary_key=['tenant','id'])
billing = ms.entity(name='billing',datasource=ms.ref.datasource('billing'),source=md.table('accounts'),primary_key=['tenant','id'])
crm_tenant = ms.dimension_column(name='tenant',entity=crm,column='tenant')
crm_id = ms.dimension_column(name='id',entity=crm,column='id')
billing_tenant = ms.dimension_column(name='tenant',entity=billing,column='tenant')
billing_id = ms.dimension_column(name='id',entity=billing,column='id')
crm_amount = ms.measure_column(name='amount',entity=crm,column='amount',unit='CNY',additivity=ms.additive_all())
billing_amount = ms.measure_column(name='amount',entity=billing,column='amount',unit='CNY',additivity=ms.additive_all())
account_match = ms.relationship(name='account_match',from_entity=crm,to_entity=billing,keys=[ms.join_on(crm_tenant,billing_tenant),ms.join_on(crm_id,billing_id)])
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("two-sources", timezone="UTC")
    left_members = session.members(session.catalog.entities.get("sales.crm"))
    right_members = session.members(session.catalog.entities.get("sales.billing"))
    left_amount = session.catalog.fields.get("sales.crm.amount")
    right_amount = session.catalog.fields.get("sales.billing.amount")
    left = session.observe({"sales": left_amount.sum()}, by=left_members)
    right = session.observe({"sales": right_amount.sum()}, by=right_members)
    profiles = session.read(right_members, {"sales": right_amount})
    chosen = _population(
        session,
        profiles.where(profiles["sales"] > 50),
        session.catalog.entities.get("sales.billing"),
    )
    filtered = session.observe({"sales": left_amount.sum()}, where=mv.in_population(chosen))
    statements: list[str] = []
    original = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append(read.sql)
        return original(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    assert filtered.compute().to_pandas()[("value", "sales")].tolist() == [150]
    assert len(statements) == 2
    assert "_marivo_keys_" in statements[-1]
    comparison = left.compare(
        right,
        on=((left.axes[0], right.axes[0]),),
    )
    artifact = comparison.persist()
    frame = artifact.to_pandas()
    assert frame[("value", "absolute.sales")].tolist() == [50, 25]
    assert frame[("presence", "left_present")].tolist() == [True, True]

    def unavailable(*args: object, **kwargs: object) -> None:
        raise AssertionError("terminal reads must not reopen sources")

    monkeypatch.setattr(SourceSession, "__enter__", unavailable)
    assert session.persisted_frame(artifact.artifact.id).to_pandas().equals(frame)


def test_explicit_daily_and_monthly_source_observations(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "share-calendar.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE orders(id BIGINT,region VARCHAR,day DATE,amount BIGINT)")
        connection.execute(
            "INSERT INTO orders VALUES (1,'A','2026-01-30',10),(2,'A','2026-01-31',30),(3,'B','2026-01-30',20),(4,'A','2026-02-01',60),(5,'B','2026-02-01',40)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders',datasource=ms.ref.datasource('warehouse'),source=md.table('orders'),primary_key=['id'])
region = ms.dimension_column(name='region',entity=orders,column='region')
day = ms.time_dimension_column(name='day',entity=orders,column='day',granularity='day')
amount = ms.measure_column(name='amount',entity=orders,column='amount',additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("calendar-share", timezone="UTC")
    amount = session.catalog.fields.get("sales.orders.amount")
    day = session.catalog.fields.get("sales.orders.day")
    region = session.catalog.fields.get("sales.orders.region")
    grid = session.time_scope(start="2026-01-30", end="2026-02-03").by(mv.grain("day"))
    daily = session.observe({"sales": amount.sum(time=day, empty="zero")}, by=(grid, region))
    assert daily.compute().to_pandas()[("value", "sales")].tolist() == [10, 30, 60, 0, 20, 0, 40, 0]
    monthly = session.time_scope(start="2026-01-30", end="2026-02-03").by(mv.grain("month"))
    coarse = session.observe({"sales": amount.sum(time=day, empty="zero")}, by=(monthly,))
    assert coarse.compute().to_pandas()[("value", "sales")].tolist() == [60, 100]


@pytest.mark.parametrize("kind", ["integer", "float", "decimal", "duration"])
def test_column_sum_distinguishes_missing_samples_from_valid_zero(
    result_session: Session, kind: str
) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    raw = (1, 10, 10, 5)
    values: tuple[int | float | Decimal | timedelta, ...] = (
        tuple(timedelta(seconds=value) for value in raw)
        if kind == "duration"
        else tuple(Decimal(value) for value in raw)
        if kind == "decimal"
        else tuple(float(value) for value in raw)
        if kind == "float"
        else raw
    )
    base = session.values(
        by=(region,),
        columns={"value": "s" if kind == "duration" else None, "divisor": None, "group": None},
        rows=tuple(
            ((key,), {"value": value, "divisor": divisor, "group": group})
            for key, value, divisor, group in zip(
                "ABCD", values, (0, -1, 1, 1), (0, 1, 1, 2), strict=True
            )
        ),
    )
    for selected, expected, count in (
        (base.where(base["group"] < 0), None, 0),
        (base.where(base["group"] == 0), None, 1),
        (base, values[3], 4),
        (base.where(base["group"] == 1), values[0] * 0, 2),
        (base.where(base["group"] > 0), values[3], 3),
    ):
        query = selected.select(
            {"value": selected["value"] if count == 0 else selected["value"] / selected["divisor"]}
        )
        artifact = query.reduce({"sum": query["value"].sum(), "rows": row_count()}).persist()
        rows = artifact.to_pandas()
        actual = rows[("value", "sum")].iloc[0]
        assert pd.isna(actual) if expected is None else actual == expected
        assert rows[("value", "rows")].tolist() == [count]
        assert session.persisted_frame(artifact.artifact.id).to_pandas().equals(rows)


def test_nullable_source_sum_preserves_large_integer_through_local_consumers(
    result_session: Session,
) -> None:
    session = result_session
    large = 2**53 + 1
    with duckdb.connect(str(session.project_root / "results.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=? WHERE id=1", [large])
        connection.execute("UPDATE orders SET amount=0 WHERE id=2")
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    total = amount.sum()
    query = session.observe({"total": total, "shifted": total + 10}, by=(region,))
    artifact = query.persist()
    frame = artifact.to_pandas()
    assert frame[("value", "total")].to_dict() == {0: large, 1: 20, 2: None}
    assert frame[("value", "shifted")].to_dict() == {0: large + 10, 1: 30, 2: None}
    assert pd.api.types.is_integer_dtype(frame[("value", "total")].dtype)
    pd.testing.assert_frame_equal(session.persisted_frame(artifact.artifact.id).to_pandas(), frame)
    for reduced in (
        query.reduce({"total": query["total"].sum()}),
        session.observe({"total": total.per(region).sum()}),
    ):
        assert reduced.compute().to_pandas()[("value", "total")].tolist() == [large + 20]


def test_explicit_domains_keep_joint_keys_and_empty_unit(result_session: Session) -> None:
    session = result_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    domain = mv.coordinates(by=(region, channel), keys=(("A", "Web"), ("B", "Store")))
    rows = session.observe({"mean": amount.mean()}, by=domain).compute().to_pandas()
    assert list(zip(rows.iloc[:, 0], rows.iloc[:, 1], strict=True)) == [
        ("A", "Web"),
        ("B", "Store"),
    ]
    assert rows[("value", "mean")].iloc[0] == 10
    assert pd.isna(rows[("value", "mean")].iloc[1])
    unit = mv.coordinates(by=(), keys=((),))
    empty = mv.coordinates(by=(), keys=())
    assert len(session.observe({"total": amount.sum()}, by=unit).compute().to_pandas()) == 1
    assert len(session.observe({"total": amount.sum()}, by=empty).compute().to_pandas()) == 0


def test_population_counts_are_independent_of_fact_conditions(result_session: Session) -> None:
    session = result_session
    entity = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    p = mv.coordinates(by=(mv.identity(entity),), keys=(((1,),), ((3,),), ((999,),)))
    q = mv.coordinates(by=(mv.identity(entity),), keys=(((2,),),))
    rows = (
        session.observe(
            {
                "p": p.count(),
                "q": q.count(),
                "sum": amount.sum(),
                "ratio": amount.sum() / p.count(),
            },
            where=amount > 15,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "p")].tolist() == [3]
    assert rows[("value", "q")].tolist() == [1]
    assert rows[("value", "sum")].tolist() == [50]
    assert rows[("value", "ratio")].tolist() == [50 / 3]
    with pytest.raises(AnalysisError, match="fact contributions"):
        session.observe({"p": p.count()}, where=amount > 15)


def test_count_domains_follow_their_own_internal_samples(result_session: Session) -> None:
    session = result_session
    entity = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    selected = mv.coordinates(by=(mv.identity(entity),), keys=(((1,),), ((3,),)))
    selected = selected.classify(by=(selected.axes[0], region))
    rows = (
        session.observe({"count": selected.count(), "mean": amount.sum().per(region).mean()})
        .compute()
        .to_pandas()
    )
    assert rows[("value", "count")].tolist() == [2]
    assert rows[("value", "mean")].tolist() == [30.0]
    rows = (
        session.observe(
            {"count": selected.count(), "sampled_count": selected.count().per(region).sum()}
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "count")].tolist() == [2]
    assert rows[("value", "sampled_count")].tolist() == [2]
    grouped = (
        session.observe({"count": selected.count().per(region).sum()}, by=(region,))
        .compute()
        .to_pandas()
    )
    assert grouped[("value", "count")].tolist() == [1, 1]
    requested = mv.coordinates(by=(region,), keys=(("A",), ("C",)))
    rows = session.observe({"count": selected.count().per(requested).sum()}).compute().to_pandas()
    assert rows[("value", "count")].tolist() == [1]
