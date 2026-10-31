"""Source original formulas return grouped values instead of contribution rows."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.fixture
def original_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "original.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders AS SELECT i AS id, i % 100 AS bucket, i % 2 AS channel, i % 7 AS amount, i % 3 + 1 AS weight FROM range(10000) t(i)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders=ms.entity(name='orders',datasource=ms.ref.datasource('warehouse'),source=md.table('orders'),primary_key=['id'])
bucket=ms.dimension_column(name='bucket',entity=orders,column='bucket')
channel=ms.dimension_column(name='channel',entity=orders,column='channel')
amount=ms.measure_column(name='amount',entity=orders,column='amount',additivity=ms.additive_all())
weight=ms.measure_column(name='weight',entity=orders,column='weight',additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("original", timezone="UTC")


@pytest.mark.parametrize(
    "method", ["sum", "mean", "min", "max", "count_distinct", "median", "quantile"]
)
def test_native_rollup_returns_one_original_result(
    original_session: Session, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    session = original_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    metric = amount.quantile(0.9) if method == "quantile" else getattr(amount, method)()
    trace = capture_source(monkeypatch)
    result = session.observe({"metric": metric}).compute().to_pandas()
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        aggregate = (
            "quantile_cont(amount,0.9)"
            if method == "quantile"
            else "count(DISTINCT amount)"
            if method == "count_distinct"
            else f"{(method if method != 'mean' else 'avg')}(amount)"
        )
        expected = connection.execute(f"SELECT {aggregate} FROM orders").fetchone()
    assert expected is not None
    assert result["metric"].tolist() == pytest.approx([expected[0]])
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []
    assert not any("__original_value" in columns for columns in trace.returned_columns)


def test_native_rollup_keeps_selected_parent_fibers(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = original_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    fine = session.observe({"metric": amount.mean()}, by=(bucket,))
    eligible = fine.where(fine["metric"] >= 3)
    fixed = eligible.top(1, by=eligible["metric"]).compute()
    selected = session.population_from(fixed, on=((fixed.axes[0], bucket),))
    trace = capture_source(monkeypatch)
    result = (
        session.observe({"metric": amount.mean()}, where=mv.in_population(selected))
        .compute()
        .to_pandas()
    )
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        expected = connection.execute(
            "SELECT avg(amount) FROM orders WHERE bucket=(SELECT bucket FROM orders GROUP BY bucket HAVING avg(amount)>=3 ORDER BY avg(amount) DESC,bucket LIMIT 1)"
        ).fetchone()
    assert expected is not None
    assert result["metric"].tolist() == pytest.approx([expected[0]])
    assert trace.returned_rows


def test_native_weighted_mean_and_ratio_recompute_source_formula(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = original_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    weight = catalog_value(session.catalog, "sales.orders.weight", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    for metric, expected in (
        (amount.weighted_mean(weight=weight), 59987 / 19999),
        (amount.sum() / amount.count(), 29994 / 10000),
    ):
        with monkeypatch.context() as scoped:
            trace = capture_source(scoped)
            result = session.observe({"metric": metric}).compute().to_pandas()
            assert result["metric"].tolist() == pytest.approx([expected])
            assert trace.returned_rows == [1]
            assert trace.uploaded_key_rows == []


def test_native_top_drill_returns_only_selected_group_values(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = original_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    channel = catalog_value(session.catalog, "sales.orders.channel", kind="field")
    fine = session.observe({"metric": amount.mean()}, by=(bucket,))
    fixed = fine.top(1, by=fine["metric"]).compute()
    selected = session.population_from(fixed, on=((fixed.axes[0], bucket),))
    trace = capture_source(monkeypatch)
    result = (
        session.observe(
            {"metric": amount.mean()}, by=(bucket, channel), where=mv.in_population(selected)
        )
        .compute()
        .to_pandas()
    )
    assert len(result) == 1
    assert result["metric"].tolist() == pytest.approx([3.04])
    assert trace.returned_rows


@pytest.mark.parametrize("failure", ["null", "overflow", "domain"])
def test_source_original_preserves_parent_numeric_contract_after_empty_selection(
    original_session: Session, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import AnalysisError

    session = original_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        if failure == "null":
            connection.execute("UPDATE orders SET amount=NULL WHERE id=0")
            metric = amount.mean(nulls="error")
        elif failure == "overflow":
            connection.execute(
                "UPDATE orders SET amount=CASE id WHEN 0 THEN 9223372036854775807 WHEN 100 THEN 1 WHEN 1 THEN -9223372036854775808 ELSE 0 END"
            )
            metric = amount.sum()
        else:
            connection.execute("UPDATE orders SET amount=0 WHERE bucket=0")
            metric = amount.sum().log()
    fine = session.observe({"metric": metric}, by=(bucket,))
    trace = capture_source(monkeypatch)
    if failure == "overflow":
        for candidate in (fine, fine.top(0, by=fine["metric"])):
            with pytest.raises(AnalysisError, match="Integer value out of bounds"):
                candidate.compute()
    elif failure == "null":
        with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
            expected = connection.execute(
                "SELECT bucket, AVG(amount) FROM orders GROUP BY bucket ORDER BY bucket"
            ).fetchall()
        rows = session.observe({"metric": amount.mean()}, by=(bucket,)).compute().to_pandas()
        key = rows.columns[0]
        rows = rows.sort_values(key)
        assert rows[key].tolist() == [key for key, _ in expected]
        assert rows["metric"].tolist() == pytest.approx([value for _, value in expected])
        for candidate in (fine, fine.top(0, by=fine["metric"])):
            with pytest.raises(AnalysisError, match="non-NA source values"):
                candidate.compute()
    else:
        with pytest.raises(AnalysisError):
            fine.top(0, by=fine["metric"]).compute()
    assert trace.uploaded_key_rows == []


def test_source_original_empty_selected_drill_has_no_business_rows(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = original_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    channel = catalog_value(session.catalog, "sales.orders.channel", kind="field")
    fine = session.observe({"metric": amount.mean()}, by=(bucket,))
    fixed = fine.top(0, by=fine["metric"]).compute()
    selected = session.population_from(fixed, on=((fixed.axes[0], bucket),))
    result = (
        session.observe(
            {"metric": amount.mean()}, by=(bucket, channel), where=mv.in_population(selected)
        )
        .compute()
        .to_pandas()
    )
    assert result.empty


@pytest.mark.parametrize("method", ["sum", "mean", "weighted_mean"])
def test_float_original_reduction_uses_qualified_native_precision(
    original_session: Session, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    session = original_session
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id>=3")
        connection.execute("ALTER TABLE orders ALTER amount TYPE DOUBLE")
        connection.execute(
            "UPDATE orders SET bucket=id,weight=1,amount=CASE id WHEN 0 THEN 1e16 WHEN 1 THEN 1.0 ELSE -1e16 END"
        )
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    weight = catalog_value(session.catalog, "sales.orders.weight", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    metric = (
        amount.weighted_mean(weight=weight)
        if method == "weighted_mean"
        else getattr(amount, method)()
    )
    trace = capture_source(monkeypatch)
    result = session.observe({"metric": metric}).compute().to_pandas()
    assert result["metric"].tolist() == [0.0]
    assert trace.returned_rows == [1]


@pytest.mark.parametrize(
    "kind,method,expected",
    (
        ("DOUBLE", "sum", 4.0),
        ("DOUBLE", "mean", 2.0),
        ("DOUBLE", "weighted_mean", 2.25),
        ("DOUBLE", "quantile", 1.625),
        ("DECIMAL(18,2)", "sum", Decimal("4.00")),
        ("DECIMAL(18,2)", "mean", 2.0),
        ("DECIMAL(18,2)", "weighted_mean", 2.25),
    ),
)
def test_fractional_original_values_are_not_integer_truncated(
    original_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    method: str,
    expected: float | Decimal,
) -> None:
    session = original_session
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id>=3")
        connection.execute(f"ALTER TABLE orders ALTER amount TYPE {kind}")
        connection.execute(
            "UPDATE orders SET bucket=id,weight=id+1,amount=CASE id WHEN 0 THEN 1.25 WHEN 1 THEN 2.75 ELSE NULL END"
        )
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    weight = catalog_value(session.catalog, "sales.orders.weight", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    metric = (
        amount.weighted_mean(weight=weight)
        if method == "weighted_mean"
        else amount.quantile(0.25)
        if method == "quantile"
        else getattr(amount, method)()
    )
    trace = capture_source(monkeypatch)
    result = session.observe({"metric": metric}).compute().to_pandas()
    assert result["metric"].tolist() == [expected]
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("kind", ("DOUBLE", "DECIMAL(18,2)"))
def test_fractional_weighted_target_masks_cancelled_weight_sum(
    original_session: Session, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:

    session = original_session
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id>=2")
        connection.execute(f"ALTER TABLE orders ALTER amount TYPE {kind}")
        connection.execute(
            "UPDATE orders SET bucket=id,weight=CASE id WHEN 0 THEN 1 ELSE -1 END,amount=CASE id WHEN 0 THEN 1.25 ELSE 2.75 END"
        )
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    weight = catalog_value(session.catalog, "sales.orders.weight", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    metric = amount.weighted_mean(weight=weight)
    trace = capture_source(monkeypatch)
    assert session.observe({"metric": metric}).compute().to_pandas()["metric"].isna().all()
    assert trace.returned_rows == [1]


@pytest.mark.parametrize("method", ["mean", "weighted_mean"])
def test_compact_integer_means_keep_their_native_rounding_boundary(
    original_session: Session, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    session = original_session
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id>=3")
        connection.execute(
            "UPDATE orders SET bucket=id,weight=1,amount=CASE id WHEN 0 THEN 9007199254740993 ELSE 0 END"
        )
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    weight = catalog_value(session.catalog, "sales.orders.weight", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    metric = amount.weighted_mean(weight=weight) if method == "weighted_mean" else amount.mean()
    trace = capture_source(monkeypatch)
    result = session.observe({"metric": metric}).compute().to_pandas()
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        native = connection.execute(
            "SELECT CAST(sum(amount * weight) AS DOUBLE) / CAST(sum(weight) AS DOUBLE) FROM orders"
            if method == "weighted_mean"
            else "SELECT avg(amount) FROM orders"
        ).fetchone()
    assert native is not None
    assert result["metric"].tolist() == [native[0]]
    assert native[0] == (3002399751580330.5 if method == "weighted_mean" else 3002399751580331.0)
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []


def test_source_original_top_ties_keep_null_key_first(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = original_session
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id>=2")
        connection.execute("UPDATE orders SET amount=1,bucket=CASE id WHEN 0 THEN NULL ELSE 1 END")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    fine = session.observe({"metric": amount.sum()}, by=(bucket,))
    trace = capture_source(monkeypatch)
    result = fine.top(1, by=fine["metric"]).compute().to_pandas()
    assert len(result) == 1
    assert result.iloc[:, 0].isna().all()


def test_source_share_finishes_exact_integer_division_on_host(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = original_session
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id>=2")
        connection.execute(
            "UPDATE orders SET bucket=id,amount=CASE id WHEN 0 THEN 9007199254740993 ELSE -9007199254740990 END"
        )
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    metric = amount.sum().where(bucket == 0) / amount.sum()
    result = session.observe({"ratio": metric}).compute().to_pandas()
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        native = connection.execute(
            "SELECT sum(amount) FILTER (WHERE bucket=0) / sum(amount) FROM orders"
        ).fetchone()
    assert native is not None
    assert result["ratio"].tolist() == [native[0]]
    exact = session.values(
        by=(), columns={"n": None, "d": None}, rows=(((), {"n": 2**53 + 1, "d": 3}),)
    )
    divided = exact.select({"ratio": exact["n"] / exact["d"]}).compute()
    assert divided.to_pandas()["ratio"].tolist() == [
        (
            pd.Series([2**53 + 1], dtype="int64[pyarrow]") / pd.Series([3], dtype="int64[pyarrow]")
        ).iloc[0]
    ]


def test_source_original_does_not_deduplicate_or_admit_invalid_identity(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import AnalysisError

    session = original_session
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        connection.execute("UPDATE orders SET id=NULL WHERE id=0")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    trace = capture_source(monkeypatch)
    members = session.members(session.catalog.get("entity:sales.orders"))
    with pytest.raises(AnalysisError, match="non-null complete identity keys"):
        session.observe({"metric": amount.sum()}, where=mv.in_population(members)).compute()
    assert trace.returned_rows


@pytest.mark.parametrize("outer", ["where", "select", "top", "reduce"])
def test_compact_original_prefix_keeps_outer_local_consumer(
    original_session: Session, monkeypatch: pytest.MonkeyPatch, outer: str
) -> None:
    session = original_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    coarse = session.observe({"metric": amount.mean()})
    if outer == "where":
        query = coarse.where(coarse["metric"] > 2)
        expected = 29994 / 10000
    elif outer == "select":
        query = coarse.select({"metric": coarse["metric"] + 1})
        expected = 29994 / 10000 + 1
    elif outer == "top":
        query = coarse.top(1, by=coarse["metric"])
        expected = 29994 / 10000
    else:
        query = coarse.reduce({"metric": coarse["metric"].sum()})
        expected = 29994 / 10000
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert result["metric"].tolist() == [expected]
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []


def test_source_original_retains_native_sum_transport_boundary(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = original_session
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id>=3")
        connection.execute(
            "UPDATE orders SET bucket=CASE id WHEN 2 THEN 1 ELSE 0 END,channel=id,amount=CASE id WHEN 0 THEN 9223372036854775807 WHEN 1 THEN 1 ELSE -9223372036854775808 END"
        )
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    channel = catalog_value(session.catalog, "sales.orders.channel", kind="field")
    fine = session.observe({"metric": amount.sum()}, by=(bucket,))
    trace = capture_source(monkeypatch)
    before = publication_counts(session)
    from marivo.analysis.errors import AnalysisError

    with pytest.raises(AnalysisError, match="Integer value out of bounds"):
        fine.compute()
    assert publication_counts(session) == before


def test_compact_drill_prefix_leaves_top_on_finished_values(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = original_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    channel = catalog_value(session.catalog, "sales.orders.channel", kind="field")
    detail = session.observe({"metric": amount.mean()}, by=(bucket, channel))
    trace = capture_source(monkeypatch)
    result = detail.top(1, by=detail["metric"]).compute().to_pandas()
    assert result["metric"].tolist() == [3.04]
    assert trace.returned_rows == [100]
    assert trace.uploaded_key_rows == []


def test_compact_original_prefix_keeps_compare_completion_local(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = original_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    coarse = session.observe({"metric": amount.mean()})
    baseline = session.values(by=(), columns={"metric": None}, rows=(((), {"metric": 1}),))
    trace = capture_source(monkeypatch)
    result = coarse.compare({"reference": mv.baseline(baseline)}).compute().to_pandas()
    assert result["reference.absolute.metric"].tolist() == [29994 / 10000 - 1]
    assert trace.returned_rows == [1]


def test_nested_float_formula_checks_its_native_denominator(
    original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = original_session
    with duckdb.connect(str(session.project_root / "original.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id>=3")
        connection.execute(
            "UPDATE orders SET bucket=0,amount=CASE id WHEN 0 THEN 9007199254740993 ELSE 0 END"
        )
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="field")
    denominator = amount.sum() / amount.count() - 3002399751580330.5
    metric = amount.count() / denominator
    trace = capture_source(monkeypatch)

    assert session.observe({"metric": metric}).compute().to_pandas()["metric"].isna().all()
