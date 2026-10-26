"""Relationship Populations keep checks at source without downloading ancestors."""

import json
import os
from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_population_membership import _session
from tests.datasource.source_cases import source_params
from tests.support.source_trace import SourceTrace, capture_source


def _save_trace(
    name: str,
    size: int,
    trace: SourceTrace,
    result: pd.DataFrame | None = None,
    error: MaterializationError | None = None,
) -> None:
    directory = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
    if directory:
        Path(directory, name + ".json").write_text(
            json.dumps(
                {
                    "provider": "duckdb",
                    "version": duckdb.__version__,
                    "size": size,
                    "returned_rows": trace.returned_rows,
                    "returned_columns": trace.returned_columns,
                    "returned_arrow_bytes": trace.returned_arrow_bytes,
                    "returned_pandas_bytes": trace.returned_pandas_bytes,
                    "uploaded_key_rows": trace.uploaded_key_rows,
                    "uploaded_key_bytes": trace.uploaded_key_bytes,
                    "native_sql": trace.native_sql,
                    "result": {
                        "columns": list(result.columns),
                        "dtypes": [str(dtype) for dtype in result.dtypes],
                        "rows": list(result.itertuples(index=False, name=None)),
                    }
                    if result is not None
                    else None,
                    "error": {
                        "stage": error.stage,
                        "expected": error.expected,
                        "received": error.received,
                        "repair": error.repair,
                    }
                    if error is not None
                    else None,
                },
                indent=2,
                default=str,
            )
        )


def _case(
    factory: Callable[..., SemanticProject],
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    size: int = 1000,
) -> Session:
    session = _session(factory, root, monkeypatch)
    with duckdb.connect(str(root / "population.duckdb")) as db:
        db.execute("DELETE FROM orders")
        db.execute("DELETE FROM links")
        db.execute("INSERT INTO orders SELECT i,i%10,1 FROM range(?) t(i)", [size])
        db.execute("INSERT INTO links SELECT i,i,i%2+1 FROM range(?) t(i)", [size])
        db.execute("INSERT INTO links SELECT i+?,i,i%2+1 FROM range(?) t(i)", [size, size])
    return session


@pytest.mark.parametrize("drop_axis", (False, True))
def test_population_product_only_deduplicates_when_projection_drops_an_axis(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drop_axis: bool,
) -> None:
    import ibis
    import ibis.expr.operations as ops

    from marivo.analysis.materialization.population_preparation import prepare_population

    session = _case(semantic_project_factory, tmp_path, monkeypatch, size=4)
    orders = session.members(session.catalog.entities.get("sales.orders"))
    tags = session.members(session.catalog.entities.get("sales.tags"))
    product = mv.product(orders, tags)
    selected = product.project(by=(tags.axes[0],) if drop_axis else (tags.axes[0], orders.axes[0]))
    prepared = prepare_population(
        selected._definition,
        {
            "sales.orders": ibis.table({"id": "int64"}, name="orders"),
            "sales.tags": ibis.table({"id": "int64"}, name="tags"),
        },
        session.catalog._state,
    )
    assert bool(prepared.table.op().find(ops.Distinct)) == drop_axis

    trace = capture_source(monkeypatch)
    rows = session.frame(selected).compute().to_pandas()
    expected = (
        {(1,), (2,), (3,)}
        if drop_axis
        else {(tag, order) for tag in (1, 2, 3) for order in range(4)}
    )
    assert set(rows.itertuples(index=False, name=None)) == expected
    assert len(rows) == len(expected)
    assert trace.native_sql
    assert any("SELECT DISTINCT" in sql.upper() for sql in trace.native_sql) == drop_axis


@pytest.mark.parametrize(
    "operation", ("image", "image_count", "literal_preimage", "query_preimage", "empty_query")
)
def test_relationship_population_only_returns_consumed_domain_or_value(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch)
    orders = session.members(session.catalog.entities.get("sales.orders"))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    image = orders.image(via=path)
    if operation == "image":
        query = session.frame(image)
    elif operation == "image_count":
        query = session.observe(
            {
                "v": session.catalog.fields.get("sales.tags.id")
                .count()
                .where(mv.in_population(image))
            }
        )
    else:
        if operation == "literal_preimage":
            targets = mv.coordinates(
                by=(mv.identity(session.catalog.entities.get("sales.tags")),), keys=(((1,),),)
            )
        else:
            tags = session.members(session.catalog.entities.get("sales.tags"))
            fields = session.read(tags, {"label": session.catalog.fields.get("sales.tags.label")})
            chosen = fields.where(
                fields["label"] == ("missing" if operation == "empty_query" else "A")
            )
            targets = session.population_from(
                chosen, on=((chosen.axes[0], session.catalog.entities.get("sales.tags")),)
            )
        selected = orders.preimage(targets, via=path)
        query = session.observe(
            {"v": session.catalog.metrics.get("sales.revenue")}, where=mv.in_population(selected)
        )
    trace = capture_source(monkeypatch)
    frame = query.compute().to_pandas()
    if operation != "image":
        assert frame["v"].tolist() == [
            2 if operation == "image_count" else 0 if operation == "empty_query" else 500
        ]
    if operation == "image":
        assert frame.iloc[:, 0].tolist() == [1, 2]
    if operation in ("image", "image_count", "literal_preimage"):
        assert trace.returned_rows == [2 if operation == "image" else 1]
        assert trace.uploaded_key_rows == []
    else:
        assert trace.uploaded_key_rows == [0 if operation == "empty_query" else 1]


def test_relationship_population_identity_failure_survives_empty_top(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch)
    orders = session.members(session.catalog.entities.get("sales.orders"))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    with duckdb.connect(str(tmp_path / "population.duckdb")) as db:
        db.execute("INSERT INTO orders VALUES (NULL,0,1)")
    selected = orders.image(via=path)
    metric = session.catalog.fields.get("sales.tags.id").count().where(mv.in_population(selected))
    fine = session.observe({"v": metric})
    query = fine.top(0, by=fine["v"])
    trace = capture_source(monkeypatch)
    with pytest.raises(MaterializationError, match="non-null complete identity keys"):
        query.compute()
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("size", (100, 10_000))
@pytest.mark.parametrize("with_sum", (False, True))
def test_relationship_member_count_returns_completed_count(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    with_sum: bool,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch, size=size)
    orders = session.members(session.catalog.entities.get("sales.orders"))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    targets = mv.coordinates(
        by=(mv.identity(session.catalog.entities.get("sales.tags")),), keys=(((1,),),)
    )
    selected = orders.preimage(targets, via=path)
    metrics = {"n": selected.count()}
    if with_sum:
        metrics["v"] = session.catalog.metrics.get("sales.revenue")
    query = session.observe(
        metrics,
        where=mv.in_population(selected) if with_sum else None,
    )
    trace = capture_source(monkeypatch, measure_cost=True)
    frame = query.compute().to_pandas()
    assert frame["n"].tolist() == [size // 2]
    if with_sum:
        assert frame["v"].tolist() == [size // 2]
    assert sum(trace.returned_rows) <= 2
    assert trace.uploaded_key_rows == []
    assert sum(trace.returned_arrow_bytes) < 200
    _save_trace(f"count-{size}-{with_sum}", size, trace, frame)


def test_relationship_image_missing_source_uses_empty_set(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch)
    missing = mv.coordinates(
        by=(mv.identity(session.catalog.entities.get("sales.orders")),), keys=(((9999,),),)
    )
    selected = missing.image(
        via=(
            session.catalog.relationships.get("sales.order_links"),
            session.catalog.relationships.get("sales.link_tags"),
        )
    )
    assert session.frame(selected).compute().to_pandas().empty


@pytest.mark.parametrize("empty", (False, True))
def test_source_member_count_zero_axes_preserves_empty_and_unit(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    empty: bool,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch)
    if empty:
        with duckdb.connect(str(tmp_path / "population.duckdb")) as db:
            db.execute("DELETE FROM orders")
    domain = session.members(session.catalog.entities.get("sales.orders")).project(by=())
    trace = capture_source(monkeypatch)
    result = session.observe({"n": domain.count()}).compute().to_pandas()
    assert result["n"].tolist() == [0 if empty else 1]
    assert sum(trace.returned_rows) <= 2
    assert trace.uploaded_key_rows == []


def test_source_member_counts_keep_complete_tuples_and_null_categories(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch, size=100)
    with duckdb.connect(str(tmp_path / "population.duckdb")) as db:
        db.execute("UPDATE orders SET customer=NULL WHERE customer=0")
    entity = session.catalog.entities.get("sales.orders")
    customer = session.catalog.fields.get("sales.orders.customer")
    selected = session.members(entity).classify(by=(mv.identity(entity), customer))
    categories = selected.project(by=(customer,))
    trace = capture_source(monkeypatch)
    grouped = session.observe({"n": selected.count()}, by=(customer,)).compute().to_pandas()
    assert grouped["n"].tolist() == [10] * 10
    assert grouped.iloc[:, 0].isna().sum() == 1
    assert session.observe({"n": categories.count()}).compute().to_pandas()["n"].tolist() == [10]
    assert sum(trace.returned_rows) <= 11
    assert trace.uploaded_key_rows == []


def test_source_member_count_projection_preserves_identity_check(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch)
    with duckdb.connect(str(tmp_path / "population.duckdb")) as db:
        db.execute("INSERT INTO orders VALUES (NULL,0,1)")
    domain = session.members(session.catalog.entities.get("sales.orders")).project(by=())
    trace = capture_source(monkeypatch)
    with pytest.raises(MaterializationError, match="non-null complete identity keys"):
        session.observe({"n": domain.count()}).compute()
    assert sum(trace.returned_rows) <= 2


@pytest.mark.parametrize("size", (100, 10_000))
def test_source_output_coverage_returns_bounded_actual_missing_keys(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch, size=size)
    identifier = session.catalog.fields.get("sales.orders.id")
    output = mv.coordinates(by=(identifier,), keys=((0,),))
    query = session.observe({"v": session.catalog.metrics.get("sales.revenue")}, by=output)
    trace = capture_source(monkeypatch, measure_cost=True)
    with pytest.raises(MaterializationError, match="missing output coordinates") as error:
        query.top(0, by=query["v"]).compute()
    assert error.value.stage == "population_coverage"
    assert "'key.0': 1" in error.value.received
    assert "'key.0': 6" in error.value.received
    assert "'key.0': 0" not in error.value.received
    assert trace.returned_rows == [6]
    assert trace.uploaded_key_rows == []
    _save_trace(f"coverage-failure-{size}", size, trace, error=error.value)


def test_member_read_preserves_missing_requested_identity_as_null(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch)
    domain = mv.coordinates(
        by=(mv.identity(session.catalog.entities.get("sales.tags")),), keys=(((1,),), ((999,),))
    )
    rows = (
        session.read(domain, {"label": session.catalog.fields.get("sales.tags.label")})
        .compute()
        .to_pandas()
    )
    assert rows.iloc[:, 0].tolist() == [1, 999]
    assert rows["label"].iloc[0] == "A"
    assert rows["label"].isna().tolist() == [False, True]


@pytest.mark.parametrize("dialect", ("duckdb", "postgres", "mysql", "trino", "clickhouse"))
def test_source_check_batches_compile_as_one_summary_per_scope(dialect: str) -> None:
    import ibis
    import ibis.expr.operations as ops

    from marivo.analysis.compiler.graph_lowering import LoweredRead
    from marivo.analysis.compiler.graph_plan import ProjectionRead, QueryPlan, SourceRead
    from marivo.analysis.core.data_types import DataColumn, ScalarDataType
    from marivo.analysis.materialization.query_key_fusion import (
        NativeQueryRelation,
        _attach_relation_checks,
    )
    from marivo.semantic._numeric_lowering import NumericCheck

    table = ibis.table(
        {"key.0": "int64", "a": "float64", "b": "float64", "bad_a": "boolean", "bad_b": "boolean"},
        name="facts",
    )
    columns = (DataColumn("key.0", "id", ScalarDataType("int64"), False),)
    stage = SourceRead(
        "read",
        "facts",
        (),
        columns,
        ("key.0",),
        ProjectionRead((("key.0", "key.0"),)),
        (),
        (),
        None,
    )
    source = LoweredRead(
        "read",
        table,
        tuple(
            (name, NumericCheck(table[name], "finite values", name)) for name in ("bad_a", "bad_b")
        ),
    )
    native = NativeQueryRelation(
        table,
        frozenset({"read"}),
        (source,),
        tuple(NumericCheck(table[name] < 0, "nonnegative values", name) for name in ("a", "b")),
    )
    expression, _, checks = _attach_relation_checks(
        table.limit(0), native, QueryPlan((stage,), (), "read"), provider=dialect
    )
    summaries = tuple(
        node
        for node in expression.op().find(ops.Aggregate)
        if any(name.startswith("__query_") for name in node.metrics)
    )
    assert len(summaries) == 2
    assert all(
        len(summary.metrics) == (4 if dialect in ("duckdb", "trino", "clickhouse") else 2)
        for summary in summaries
    )
    assert len(checks) == 4
    assert all(f"{alias}.witness.0" in expression.columns for alias, _ in checks)
    sql = str(ibis.to_sql(expression, dialect=dialect))
    assert "facts" in sql
    assert ("LIMIT 1" in sql) is (dialect not in ("duckdb", "trino", "clickhouse"))


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
def test_native_member_count_preserves_exact_string_groups_and_distinct_tuples(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    from datetime import date
    from decimal import Decimal

    from tests.analysis.numeric.test_remote_numeric_query import DATA, _project
    from tests.datasource.source_cases import SourceData, source_case

    labels = ("A", "a", "Á", "A ", "a ", "", None, "A")
    rows = [
        {
            "id": index,
            "tenant": "t",
            "category": label,
            "amount": Decimal("1"),
            "big": 1,
            "weight": 1,
            "day": date(2026, 7, 1),
        }
        for index, label in enumerate(labels)
    ]
    data = SourceData(
        DATA.columns,
        ",".join(
            f"({index},'t',{'NULL' if label is None else repr(label)},1,1,1,DATE '2026-07-01')"
            for index, label in enumerate(labels)
        ),
        DATA.clickhouse_columns,
        rows,
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("exact-native-count", timezone="UTC")
        entity = session.catalog.entities.get("sales.facts")
        category = session.catalog.fields.get("sales.facts.category")
        members = session.members(entity)
        classified = members.classify(by=(mv.identity(entity), category))
        categories = classified.project(by=(category,))
        trace = capture_source(monkeypatch)
        grouped = session.observe({"n": classified.count()}, by=(category,)).compute().to_pandas()
        assert {
            None if pd.isna(key) else key: value
            for key, value in grouped.itertuples(index=False, name=None)
        } == {"A": 2, "a": 1, "Á": 1, "A ": 1, "a ": 1, "": 1, None: 1}
        assert session.observe({"n": members.count()}).compute().to_pandas()["n"].tolist() == [8]
        assert session.observe({"n": categories.count()}).compute().to_pandas()["n"].tolist() == [7]
        assert sum(trace.returned_rows) == 9
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
def test_remote_relationship_domains_and_explicit_frame_targets(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    from contextlib import ExitStack

    from marivo.datasource.ir import TableSourceIR
    from tests.analysis.materialization.test_population_membership import _MODEL
    from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
    from tests.datasource.source_cases import SourceData, source_case

    orders: list[dict[str, object]] = [
        {"id": i, "customer": i % 10, "amount": 1} for i in range(1000)
    ]
    links: list[dict[str, object]] = [
        {"id": i, "order_id": i % 1000, "tag_id": i % 2 + 1} for i in range(2000)
    ]
    tags = [
        {"id": i, "label": label, "channel": "web"} for i, label in enumerate(("A", "B", "C"), 1)
    ]
    definitions = (
        (
            "orders",
            SourceData(
                "id BIGINT NOT NULL, customer BIGINT, amount BIGINT NOT NULL",
                ",".join(f"({row['id']},{row['customer']},1)" for row in orders),
                "id Int64, customer Int64, amount Int64",
                orders,
            ),
        ),
        (
            "links",
            SourceData(
                "id BIGINT NOT NULL, order_id BIGINT NOT NULL, tag_id BIGINT NOT NULL",
                ",".join(f"({row['id']},{row['order_id']},{row['tag_id']})" for row in links),
                "id Int64, order_id Int64, tag_id Int64",
                links,
            ),
        ),
        (
            "tags",
            SourceData(
                "id BIGINT NOT NULL, label VARCHAR(16), channel VARCHAR(16)",
                "(1,'A','web'),(2,'B','web'),(3,'C','web')",
                "id Int64, label String, channel String",
                tags,
            ),
        ),
    )
    monkeypatch.chdir(tmp_path)
    with ExitStack() as stack:
        model = _MODEL
        first = None
        for name, data in definitions:
            case = stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, data))
            first = case if first is None else first
            assert isinstance(case.source, TableSourceIR)
            model = model.replace(
                f'md.table("{name}")',
                f"md.table({case.source.table!r},database={case.source.database!r})",
            )
        assert first is not None
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, first, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
                "sales/objects.py": model,
            }
        )
        session = mv.session.get_or_create("remote-population-fusion", timezone="UTC")
        source = session.members(session.catalog.entities.get("sales.orders"))
        path = (
            session.catalog.relationships.get("sales.order_links"),
            session.catalog.relationships.get("sales.link_tags"),
        )
        image = source.image(via=path)
        one_tag = mv.coordinates(
            by=(mv.identity(session.catalog.entities.get("sales.tags")),), keys=(((1,),),)
        )
        counted = source.preimage(one_tag, via=path)
        with monkeypatch.context() as count_patch:
            count_trace = capture_source(count_patch, measure_cost=True)
            for metrics in (
                {"n": counted.count()},
                {"n": counted.count(), "v": session.catalog.metrics.get("sales.revenue")},
            ):
                result = (
                    session.observe(
                        metrics, where=mv.in_population(counted) if "v" in metrics else None
                    )
                    .compute()
                    .to_pandas()
                )
                assert result["n"].tolist() == [500]
                if "v" in metrics:
                    assert result["v"].tolist() == [500]
            assert sum(count_trace.returned_rows) <= 3
            assert count_trace.uploaded_key_rows == []
        trace = capture_source(monkeypatch)
        assert len(session.frame(image).compute().to_pandas()) == 2
        count = (
            session.observe(
                {
                    "v": session.catalog.fields.get("sales.tags.id")
                    .count()
                    .where(mv.in_population(image))
                }
            )
            .compute()
            .to_pandas()
        )
        assert count["v"].tolist() == [2]
        fields = session.read(
            session.members(session.catalog.entities.get("sales.tags")),
            {"label": session.catalog.fields.get("sales.tags.label")},
        )
        for label, expected in (("A", 500), ("missing", 0)):
            chosen = fields.where(fields["label"] == label)
            targets = session.population_from(
                chosen, on=((chosen.axes[0], session.catalog.entities.get("sales.tags")),)
            )
            selected = source.preimage(targets, via=path)
            frame = (
                session.observe(
                    {"v": session.catalog.metrics.get("sales.revenue")},
                    where=mv.in_population(selected),
                )
                .compute()
                .to_pandas()
            )
            assert frame["v"].tolist() == [expected]
        if backend != "postgres":
            assert trace.uploaded_key_rows == [1, 0], {
                "rows": trace.returned_rows,
                "sql": trace.native_sql,
            }
        category = session.catalog.fields.get("sales.tags.label")
        allocation = mv.membership(category, via=(path,), unmatched="exclude")
        incomplete = mv.coordinates(by=(category,), keys=(("A",),))
        empty = mv.coordinates(by=(session.catalog.fields.get("sales.orders.id"),), keys=())
        required = session.observe(
            {"v": session.catalog.metrics.get("sales.revenue").per(empty).mean()},
            by=(incomplete, allocation),
            where=mv.in_population(source.preimage(image, via=path)),
        )
        with pytest.raises(MaterializationError, match="missing output coordinates") as error:
            required.top(0, by=required["v"]).compute()
        assert error.value.stage == "population_coverage"
        assert "B" in error.value.received
        if backend != "postgres":
            assert trace.uploaded_key_rows == [1, 0], {
                "rows": trace.returned_rows,
                "sql": trace.native_sql,
            }


def test_output_coverage_survives_source_population_fusion_and_empty_top(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch)
    revenue = session.catalog.metrics.get("sales.revenue")
    orders = session.members(session.catalog.entities.get("sales.orders"))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    # The independent relationship image is source-fused into the fact predicate.
    selected = orders.image(via=path)
    label = session.catalog.fields.get("sales.tags.label")
    allocation = mv.membership(label, via=(path,), unmatched="exclude")
    incomplete = mv.coordinates(by=(label,), keys=(("A",),))
    empty = mv.coordinates(by=(session.catalog.fields.get("sales.orders.id"),), keys=())
    query = session.observe(
        {"v": revenue.per(empty).mean()},
        by=(incomplete, allocation),
        where=mv.in_population(orders.preimage(selected, via=path)),
    )
    trace = capture_source(monkeypatch)
    with pytest.raises(MaterializationError, match="missing output coordinates") as error:
        query.top(0, by=query["v"]).compute()
    assert error.value.stage == "population_coverage"
    assert "B" in error.value.received
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("dialect", ("duckdb", "trino", "clickhouse"))
def test_tuple_check_summary_candidate_compiles(dialect: str) -> None:
    import ibis

    from marivo.analysis.materialization.query_key_fusion import _check_summary

    table = ibis.table({"key.0": "int64", "key.1": "string", "bad": "boolean"}, name="facts")
    summary = _check_summary(
        table, (("bad", table.bad, ("key.0", "key.1")),), combined=True, provider=dialect
    )
    sql = str(ibis.to_sql(summary, dialect=dialect))
    assert ("LIMIT 1" in sql) is (dialect == "duckdb")
    assert summary.columns == ("bad", "bad.witness.0", "bad.witness.1")


@pytest.mark.parametrize("collation_scope", ("column", "connection"))
def test_check_witness_preserves_duckdb_collation_with_empty_output(collation_scope: str) -> None:
    import ibis

    from marivo.analysis.compiler.graph_lowering import LoweredRead
    from marivo.analysis.compiler.graph_plan import NativeRead, QueryPlan, SourceRead
    from marivo.analysis.core.data_types import DataColumn, ScalarDataType
    from marivo.analysis.materialization.query_key_fusion import (
        NativeQueryRelation,
        _attach_relation_checks,
    )
    from marivo.semantic._numeric_lowering import NumericCheck

    connection = ibis.duckdb.connect(threads=1)
    try:
        collation = " COLLATE NOCASE" if collation_scope == "column" else ""
        if collation_scope == "connection":
            connection.raw_sql("SET default_collation='nocase'")
        connection.raw_sql(
            f'CREATE TABLE facts ("key.0" VARCHAR{collation}, "key.1" BIGINT, bad BOOLEAN)'
        )
        connection.raw_sql(
            "INSERT INTO facts VALUES ('a',2,true),('a',1,true),('B',0,true),(NULL,0,true),('0',0,false)"
        )
        table = connection.table("facts")
        keys = ("key.0", "key.1")
        columns = (
            DataColumn(keys[0], "label", ScalarDataType("string"), True),
            DataColumn(keys[1], "id", ScalarDataType("int64"), False),
        )
        stage = SourceRead("read", "facts", (), columns, keys, NativeRead(()), (), (), None)
        check = NumericCheck(table.bad, "valid inputs", "invalid input")
        native = NativeQueryRelation(
            table,
            frozenset({"read"}),
            (LoweredRead("read", table, (("bad", check),)),),
            (check,),
        )
        expression, _, checks = _attach_relation_checks(
            table.limit(0), native, QueryPlan((stage,), (), "read"), provider="duckdb"
        )
        result = expression.execute()
        assert len(result) == 1
        assert pd.isna(result["__numeric_present"].iloc[0])
        assert len(checks) == 2
        for alias, _ in checks:
            assert result[alias].iloc[0]
            assert (
                result[f"{alias}.witness.0"].iloc[0],
                result[f"{alias}.witness.1"].iloc[0],
            ) == ("a", 1)
    finally:
        connection.disconnect()


@pytest.mark.parametrize("key_type", ("BIGINT", "VARCHAR"))
@pytest.mark.parametrize("selection", ("mixed", "all", "none", "empty"))
def test_combined_check_summary_preserves_real_null_safe_failure_tuple(
    selection: str, key_type: str
) -> None:
    import ibis

    from marivo.analysis.materialization.query_key_fusion import _check_summary

    connection = ibis.duckdb.connect(threads=1)
    try:
        connection.raw_sql(
            f'CREATE TABLE witness_probe ("key.0" BIGINT, "key.1" {key_type}, a BOOLEAN, b BOOLEAN)'
        )
        connection.raw_sql(
            "INSERT INTO witness_probe VALUES (2,'1',true,false),(1,'9',true,false),(NULL,'2',true,true),(NULL,NULL,true,true),(0,'8',false,false)"
        )
        table = connection.table("witness_probe")
        if selection == "empty":
            table = table.filter(ibis.literal(False))
        if selection in ("all", "none"):
            table = table.mutate(
                a=ibis.literal(selection == "all"), b=ibis.literal(selection == "all")
            )
        flags = (table.a, table.b)
        specs = tuple(
            (name, flag, ("key.0", "key.1")) for name, flag in zip(("a", "b"), flags, strict=True)
        )
        combined = _check_summary(table, specs, combined=True, provider="duckdb")
        original = _check_summary(table, specs, combined=False)
        actual = combined.execute()
        pd.testing.assert_frame_equal(actual, original.execute())
        row = actual.iloc[0]
        if selection == "mixed":
            assert (row["a.witness.0"], row["a.witness.1"]) == (
                1,
                9 if key_type == "BIGINT" else "9",
            )
            assert pd.isna(row["b.witness.0"])
            assert row["b.witness.1"] == (2 if key_type == "BIGINT" else "2")
        elif selection == "all":
            assert (row["a.witness.0"], row["a.witness.1"]) == (
                0,
                8 if key_type == "BIGINT" else "8",
            )
        else:
            assert not row["a"] and not row["b"]
            assert pd.isna(row["a.witness.0"]) and pd.isna(row["a.witness.1"])
        plan = connection.raw_sql(
            "EXPLAIN " + str(ibis.to_sql(combined, dialect="duckdb"))
        ).fetchone()[1]
        if key_type == "BIGINT":
            if selection != "empty":
                assert plan.count("SEQ_SCAN") == 1
            assert "TOP_N" not in plan and "ORDER_BY" not in plan
    finally:
        connection.disconnect()


@pytest.mark.parametrize(
    "backend,profile", source_params([("trino", "iceberg"), ("clickhouse", "mergetree")])
)
def test_remote_tuple_check_summary_preserves_nulls_and_real_rows(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ibis

    from marivo.analysis.materialization.query_key_fusion import _check_summary
    from marivo.datasource.ir import TableSourceIR
    from tests.datasource.environment import clickhouse_analysis, trino_analysis
    from tests.datasource.source_cases import SourceData, source_case

    data = SourceData(
        "k0 BIGINT, k1 VARCHAR, a BOOLEAN, b BOOLEAN",
        "(2,'a',true,false),(1,'z',true,false),(NULL,'b',true,true),(NULL,NULL,true,true),(0,'x',false,false)",
        "k0 Nullable(Int64), k1 Nullable(String), a Bool, b Bool",
        [
            dict(zip(("k0", "k1", "a", "b"), row, strict=True))
            for row in (
                (2, "a", True, False),
                (1, "z", True, False),
                (None, "b", True, True),
                (None, None, True, True),
                (0, "x", False, False),
            )
        ],
    )

    def native_rows(sql: str) -> list[tuple[object, ...]]:
        if backend == "trino":
            with trino_analysis.connection() as connection:
                cursor = connection.cursor()
                try:
                    return [tuple(row) for row in cursor.execute(sql).fetchall()]
                finally:
                    cursor.close()
        with clickhouse_analysis.connection() as client:
            return [tuple(row) for row in client.query(sql).result_rows]

    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        for selection in ("mixed", "all", "none", "empty", "nonnullable_none", "nonnullable_empty"):
            table = ibis.table(
                {"k0": "int64", "k1": "string", "a": "boolean", "b": "boolean"},
                name=case.source.table,
            )
            if selection.startswith("nonnullable"):
                table = table.mutate(k0=table.k0.fill_null(-1), k1=table.k1.fill_null(""))
            if selection.endswith("empty"):
                table = table.filter(ibis.literal(False))
            if selection in ("all", "none", "nonnullable_none"):
                table = table.mutate(
                    a=ibis.literal(selection == "all"), b=ibis.literal(selection == "all")
                )
            summary = _check_summary(
                table,
                tuple((name, table[name], ("k0", "k1")) for name in ("a", "b")),
                combined=True,
                provider=backend,
            )
            rows = native_rows(str(ibis.to_sql(summary, dialect=backend)))
            expected = (
                (True, True, 1, "z", None, "b")
                if selection == "mixed"
                else (True, True, 0, "x", 0, "x")
                if selection == "all"
                else (False, False, None, None, None, None)
            )
            assert rows == [expected]
