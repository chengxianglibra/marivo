"""Independent numerical and complete-calendar regression oracles for review R1."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import terminal_store
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject


@pytest.fixture
def core_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "core.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, region VARCHAR, day DATE, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,'A','2026-01-30',10),(2,'A','2026-01-31',30),"
            "(3,'B','2026-01-30',20),(4,'A','2026-02-01',60),(5,'B','2026-02-01',40)"
        )
    duration_file = tmp_path / "durations.parquet"
    pq.write_table(
        pa.table({"id": [1, 2, 3], "elapsed": pa.array([3, None, 0], type=pa.duration("ns"))}),
        duration_file,
    )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\n"
            + "ms.domain(name='sales', owner='Core Review', default=True)\n",
            "sales/model.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + "orders=ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id'])\n"
            + "region=ms.dimension_column(name='region', entity=orders, column='region')\n"
            + "day=ms.time_dimension_column(name='day', entity=orders, column='day', granularity='day')\n"
            + "amount=ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
            + f"durations=ms.entity(name='durations', datasource=ms.ref.datasource('warehouse'), source=md.parquet({str(duration_file)!r}), primary_key=['id'])\n"
            + "elapsed=ms.measure_column(name='elapsed', entity=durations, column='elapsed', additivity=ms.additive_all(), unit='s')\n",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("round1-core", timezone="UTC")


@pytest.mark.parametrize("small_first", (None, True, False))
def test_nullable_integer_axis_preserves_explicit_source_and_reduction(
    core_session: Session, tmp_path: Path, small_first: bool | None
) -> None:
    with duckdb.connect(str(tmp_path / "core.duckdb")) as connection:
        connection.execute("ALTER TABLE orders ADD COLUMN code BIGINT")
        connection.execute(
            "UPDATE orders SET code=CASE WHEN id=2 THEN NULL WHEN region='A' THEN 9007199254740993 ELSE 2 END"
        )
    model = tmp_path / "models/semantic/sales/model.py"
    model.write_text(
        model.read_text() + "code=ms.dimension_column(name='code',entity=orders,column='code')\n"
    )
    amount = core_session.catalog.fields.get("sales.orders.amount")
    region = core_session.catalog.fields.get("sales.orders.region")
    code = core_session.catalog.fields.get("sales.orders.code")
    metrics = {"total": amount.sum()}
    if small_first is not None:
        small = {"small": amount.sum(empty="zero").where(amount < 20)}
        metrics = {**small, **metrics} if small_first else {**metrics, **small}

    direct = core_session.observe(metrics, by=(region, code)).compute().to_pandas()
    drilled = core_session.observe(metrics, by=(region, code))
    artifact = drilled.persist()
    actual = artifact.to_pandas()
    assert actual.equals(direct)
    assert actual[("coordinate", "axis.0")].tolist() == ["A", "A", "B"]
    assert actual[("coordinate", "axis.1")].dropna().tolist() == [9007199254740993, 2]
    assert actual[("coordinate", "axis.1")].isna().tolist() == [False, True, False]
    assert actual[("value", "total")].tolist() == [70, 30, 60]
    if small_first is not None:
        assert actual[("value", "small")].tolist() == [10, 0, 0]
    assert str(actual[("coordinate", "axis.1")].dtype) == "int64[pyarrow]"
    rolled = core_session.observe(metrics, by=(region,)).compute().to_pandas()
    assert rolled[("value", "total")].tolist() == [100, 60]
    assert artifact.to_pandas().equals(
        core_session.persisted_frame(artifact.artifact.id).to_pandas()
    )


def test_source_refuses_unsafe_integer_key_promotion_before_finish(
    core_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from tests.analysis.materialization.publication_fixtures import publication_counts

    with duckdb.connect(str(tmp_path / "core.duckdb")) as connection:
        connection.execute("ALTER TABLE orders ADD COLUMN code BIGINT")
        connection.execute("UPDATE orders SET code=9007199254740993 WHERE id=1")
        connection.execute("UPDATE orders SET code=2 WHERE id=3")
    model = tmp_path / "models/semantic/sales/model.py"
    model.write_text(
        model.read_text() + "code=ms.dimension_column(name='code',entity=orders,column='code')\n"
    )
    amount = core_session.catalog.fields.get("sales.orders.amount")
    region = core_session.catalog.fields.get("sales.orders.region")
    code = core_session.catalog.fields.get("sales.orders.code")
    query = core_session.observe(
        {"small": amount.sum().where(amount < 20), "large": amount.sum().where(amount >= 20)},
        by=(region, code),
    )

    original_execute = SourceSession.execute

    def floating_nullable_columns(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        frame = original_execute(owner, read)
        # Other providers can still promote nullable integer columns to float64.
        for name in frame:
            if pd.api.types.is_integer_dtype(frame[name].dtype) and frame[name].isna().any():
                frame[name] = frame[name].astype("float64")
        return frame

    monkeypatch.setattr(SourceSession, "execute", floating_nullable_columns)
    before = publication_counts(core_session)
    with pytest.raises(MaterializationError, match=r"complete integer keys.*carrier"):
        query.compute()
    assert publication_counts(core_session) == before


@pytest.mark.parametrize("precision", (28, 7))
def test_decimal38_negation_preserves_every_digit_under_caller_context(
    core_session: Session,
    precision: int,
) -> None:
    source = Decimal("12345678901234567890123456789012345678")
    with localcontext() as context:
        context.prec = precision
        query = core_session.values(by=(), columns={"x": None}, rows=(((), {"x": source}),))
        result = (
            query.select({"negative": -query["x"], "original": query["x"]}).compute().to_pandas()
        )
    assert result[("value", "negative")].tolist() == [source.copy_negate()]
    assert result[("value", "original")].tolist() == [source]
    assert str(result[("value", "negative")].dtype) == "decimal128(38, 0)[pyarrow]"


def test_column_na_over_zero_propagates_but_nonmissing_zero_divisions_fail(
    core_session: Session,
) -> None:
    base = core_session.values(by=(), columns={"zero": None}, rows=(((), {"zero": 0}),))
    nullable = base.select({"x": base["zero"] / base["zero"], "zero": base["zero"]})
    result = (
        nullable.select({"v": nullable["x"].divide(nullable["zero"], zero_denominator="error")})
        .compute()
        .to_pandas()
    )
    assert len(result) == 1 and result[("value", "v")].isna().all()
    for numerator in (0, 1):
        query = core_session.values(by=(), columns={"x": None}, rows=(((), {"x": numerator}),))
        with pytest.raises(AnalysisError) as error:
            query.select({"v": query["x"].divide(0, zero_denominator="error")}).compute()
        assert isinstance(error.value.__cause__, SemanticExpressionError)
        assert error.value.__cause__.expected == "a nonzero denominator"


def test_literal_duration_sum_and_mean_keep_declared_microseconds(core_session: Session) -> None:
    query = core_session.values(
        by=(), columns={"x": None}, rows=(((), {"x": timedelta(microseconds=3)}),)
    )
    result = (
        query.reduce({"sum": query["x"].sum(), "mean": query["x"].mean()}).compute().to_pandas()
    )
    for name in ("sum", "mean"):
        assert result[("value", name)].tolist() == [timedelta(microseconds=3)]
        assert pa.array(result[("value", name)]).type == pa.duration("us")


@pytest.mark.parametrize("ticks", ((0, 1), (1, 2), (-1, -2), (2**53 + 1, 2**53 + 3)))
def test_duration_statistics_accumulate_exact_nanoseconds_and_finish_half_even(
    core_session: Session,
    ticks: tuple[int, ...],
) -> None:
    path = core_session.catalog.workspace_dir / "durations.parquet"
    pq.write_table(
        pa.table(
            {"id": list(range(len(ticks))), "elapsed": pa.array(ticks, type=pa.duration("ns"))}
        ),
        path,
    )
    population = core_session.members(
        core_session.catalog.require(ms.ref.entity("sales.durations"))
    )
    elapsed = core_session.catalog.fields.get("sales.durations.elapsed")
    query = core_session.read(population, {"elapsed": elapsed})
    result = (
        query.reduce({"sum": query["elapsed"].sum(), "mean": query["elapsed"].mean()})
        .compute()
        .to_pandas()
    )
    expected = {"sum": sum(ticks), "mean": round(Fraction(sum(ticks), len(ticks)))}
    for name, value in expected.items():
        assert pa.array(result[("value", name)]).type == pa.duration("ns")
        assert pa.array(result[("value", name)]).cast(pa.int64()).to_pylist() == [value]


def test_duration_empty_and_na_statistics_and_checked_middle_sum(core_session: Session) -> None:
    population = core_session.members(
        core_session.catalog.require(ms.ref.entity("sales.durations"))
    )
    elapsed = core_session.catalog.fields.get("sales.durations.elapsed")
    query = core_session.read(population, {"elapsed": elapsed})
    for selected in (query.where(query["elapsed"].is_null()), query.top(0, by=query["elapsed"])):
        result = (
            selected.reduce({"sum": selected["elapsed"].sum(), "mean": selected["elapsed"].mean()})
            .compute()
            .to_pandas()
        )
        assert pa.array(result[("value", "sum")]).cast(pa.int64()).to_pylist() == [None]
        assert result[("value", "mean")].isna().all()
    path = core_session.catalog.workspace_dir / "durations.parquet"
    pq.write_table(
        pa.table({"id": [1, 2], "elapsed": pa.array([2**63 - 1, 1], type=pa.duration("ns"))}), path
    )
    mean = query.reduce({"mean": query["elapsed"].mean()}).compute().to_pandas()
    assert pa.array(mean[("value", "mean")]).cast(pa.int64()).to_pylist() == [2**62]
    middle = query.reduce({"sum": query["elapsed"].sum()})
    with pytest.raises(AnalysisError) as error:
        middle.select({"cancelled": middle["sum"] - middle["sum"]}).compute()
    assert isinstance(error.value.__cause__, SemanticExpressionError)
    assert error.value.__cause__.received is not None
    assert "Duration finish tick overflow" in error.value.__cause__.received


def test_grouped_duration_mean_keeps_large_ticks_beside_na(core_session: Session) -> None:
    ticks = 2**53 + 1
    path = core_session.catalog.workspace_dir / "durations.parquet"
    pq.write_table(
        pa.table({"id": [1, 2], "elapsed": pa.array([ticks, None], type=pa.duration("ns"))}),
        path,
    )
    population = core_session.members(
        core_session.catalog.require(ms.ref.entity("sales.durations"))
    )
    elapsed = core_session.catalog.fields.get("sales.durations.elapsed")
    query = core_session.read(population, {"elapsed": elapsed})
    result = (
        query.reduce(
            {"sum": query["elapsed"].sum(), "mean": query["elapsed"].mean()},
            by=(query.axes[0],),
        )
        .compute()
        .to_pandas()
    )
    assert pa.array(result[("value", "sum")]).cast(pa.int64()).to_pylist() == [ticks, None]
    assert pa.array(result[("value", "mean")]).cast(pa.int64()).to_pylist() == [ticks, None]


def test_explicit_source_monthly_values(core_session: Session) -> None:
    amount = core_session.catalog.fields.get("sales.orders.amount")
    day = core_session.catalog.fields.get("sales.orders.day")
    months = core_session.time_scope(start="2026-01-01", end="2026-03-01").by(mv.grain("month"))
    current = core_session.observe({"sales": amount.sum(time=day)}, by=(months,)).compute()
    assert current.to_pandas()[("value", "sales")].tolist() == [60, 100]
    with duckdb.connect(str(core_session.project_root / "core.duckdb")) as connection:
        connection.execute(
            "INSERT INTO orders SELECT id+10, region, day-INTERVAL '1 year', amount*2 FROM orders"
        )
    previous = core_session.time_scope(start="2025-01-01", end="2025-03-01").by(mv.grain("month"))
    baseline = core_session.observe({"sales": amount.sum(time=day)}, by=(previous,)).compute()
    assert baseline.to_pandas()[("value", "sales")].tolist() == [120, 200]


@pytest.mark.parametrize("operation", ("add", "sub", "mul", "div"))
def test_duration_binary_expression_keeps_type_before_sum_and_mean(
    core_session: Session,
    operation: str,
) -> None:
    query = core_session.values(
        by=(), columns={"d": "s"}, rows=(((), {"d": timedelta(microseconds=3)}),)
    )
    if operation == "add":
        expression, expected = query["d"] + query["d"], 6
    elif operation == "sub":
        expression, expected = query["d"] - query["d"], 0
    elif operation == "mul":
        expression, expected = query["d"] * 2, 6
    else:
        expression, expected = query["d"] / 3, 1
    result = (
        query.reduce({"sum": expression.sum(), "mean": expression.mean()}).compute().to_pandas()
    )
    for name in ("sum", "mean"):
        assert pa.array(result[("value", name)]).type == pa.duration("us")
        assert pa.array(result[("value", name)]).cast(pa.int64()).to_pylist() == [expected]


def test_duration_nested_unary_and_current_frame_arithmetic_retains_ticks(
    core_session: Session,
) -> None:
    region = core_session.catalog.fields.get("sales.orders.region")
    query = core_session.values(
        by=(region,),
        columns={"d": "s"},
        rows=tuple(
            ((key,), {"d": timedelta(microseconds=ticks)})
            for key, ticks in (("A", 3), ("B", 6), ("C", 0))
        ),
    )
    expression = -(query["d"] + timedelta(microseconds=3)) / 3
    selected = query.select({"d": expression})
    frame = selected.compute().to_pandas()
    assert pa.array(frame[("value", "d")]).cast(pa.int64()).to_pylist() == [-2, -3, -1]
    reduced = (
        selected.reduce({"sum": selected["d"].sum(), "mean": selected["d"].mean()})
        .compute()
        .to_pandas()
    )
    assert pa.array(reduced[("value", "sum")]).cast(pa.int64()).to_pylist() == [-6]
    assert pa.array(reduced[("value", "mean")]).cast(pa.int64()).to_pylist() == [-2]
    ratio = (
        query.reduce({"ratio": (query["d"] / timedelta(microseconds=3)).mean()})
        .compute()
        .to_pandas()
    )
    assert ratio[("value", "ratio")].tolist() == [1.0]
    assert pa.array(ratio[("value", "ratio")]).type == pa.float64()
    reverse = query.select({"d": 2 * query["d"]}).compute().to_pandas()
    assert pa.array(reverse[("value", "d")]).cast(pa.int64()).to_pylist() == [6, 12, 0]


@pytest.mark.parametrize(
    ("values", "unit", "ticks"),
    (
        ((timedelta(0), timedelta(seconds=3), timedelta(0)), "s", [0, 3, 0]),
        ((timedelta(0), timedelta(milliseconds=3), timedelta(0)), "us", [0, 3000, 0]),
        ((timedelta(0), timedelta(0), timedelta(0)), "us", [0, 0, 0]),
    ),
)
def test_zero_duration_literal_retains_nonzero_unit_and_refuses_missing_values(
    core_session: Session,
    values: tuple[timedelta, ...],
    unit: str,
    ticks: list[int | None],
) -> None:
    region = core_session.catalog.fields.get("sales.orders.region")
    query = core_session.values(
        by=(region,),
        columns={"d": "s"},
        rows=tuple(((key,), {"d": value}) for key, value in zip("ABC", values, strict=True)),
    )
    result = query.compute().to_pandas()
    physical = pa.array(result[("value", "d")])
    assert str(physical.type) == f"duration[{unit}]"
    assert physical.cast(pa.int64()).to_pylist() == ticks
    with pytest.raises(AnalysisError, match="non-null literal values"):
        core_session.values(by=(), columns={"d": "s"}, rows=(((), {"d": None}),))


def test_duration_binary_exact_ns_finish_na_and_overflow_barriers(core_session: Session) -> None:
    tick = 2**53 + 1
    path = core_session.catalog.workspace_dir / "durations.parquet"
    pq.write_table(
        pa.table({"id": [1, 2, 3], "elapsed": pa.array([tick, None, 3], type=pa.duration("ns"))}),
        path,
    )
    represented_tick = tick
    population = core_session.members(
        core_session.catalog.require(ms.ref.entity("sales.durations"))
    )
    elapsed = core_session.catalog.fields.get("sales.durations.elapsed")
    query = core_session.read(population, {"d": elapsed})
    expression = query["d"] / 2
    selected = query.select(
        {"half": expression, "twice": query["d"] * 2, "decimal_half": query["d"] * Decimal("0.5")}
    )
    result = selected.compute().to_pandas()
    halves = [round(Fraction(represented_tick, 2)), None, round(Fraction(3, 2))]
    assert pa.array(result[("value", "half")]).cast(pa.int64()).to_pylist() == halves
    assert pa.array(result[("value", "decimal_half")]).cast(pa.int64()).to_pylist() == halves
    assert pa.array(result[("value", "twice")]).cast(pa.int64()).to_pylist() == [
        represented_tick * 2,
        None,
        6,
    ]
    # A Duration-valued binary node finishes in its existing unit before its parent.
    nested = query.select({"d": (query["d"] / 2) * 2}).compute().to_pandas()
    assert pa.array(nested[("value", "d")]).cast(pa.int64()).to_pylist() == [
        2 * round(Fraction(represented_tick, 2)),
        None,
        4,
    ]
    missing = query.select({"d": query["d"] / 0})
    finished = (
        missing.reduce({"sum": missing["d"].divide(0, zero_denominator="error").sum()})
        .compute()
        .to_pandas()
    )
    assert pa.array(finished[("value", "sum")]).cast(pa.int64()).to_pylist() == [None]
    with pytest.raises(AnalysisError) as zero:
        query.reduce({"d": query["d"].divide(0, zero_denominator="error").sum()}).compute()
    assert isinstance(zero.value.__cause__, SemanticExpressionError)
    assert zero.value.__cause__.expected == "a nonzero denominator"
    pq.write_table(
        pa.table({"id": [1], "elapsed": pa.array([2**62], type=pa.duration("ns"))}), path
    )
    with pytest.raises(AnalysisError) as overflow:
        query.reduce({"d": ((query["d"] * 2) / 2).sum()}).compute()
    assert isinstance(overflow.value.__cause__, SemanticExpressionError)
    assert overflow.value.__cause__.expected == "exact signed int64 Duration arithmetic"


def test_duration_binary_rejects_mixed_tick_units_before_source_or_run(
    core_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    population = core_session.members(
        core_session.catalog.require(ms.ref.entity("sales.durations"))
    )
    source = core_session.read(
        population, {"d": core_session.catalog.fields.get("sales.durations.elapsed")}
    )
    invalid = source.reduce({"d": (source["d"] + timedelta(microseconds=3)).sum()})
    before = core_session.runs().items

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Incompatible Duration units crossed preparation")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    monkeypatch.setattr(terminal_store, "admit", forbidden)
    with pytest.raises(AnalysisError, match="compatible exact Duration units") as caught:
        invalid.compute()
    assert (
        caught.value.received == "(DurationDataType(unit='ns'), DurationDataType(unit='us'), 'add')"
    )
    assert caught.value.repair is not None
    assert core_session.runs().items == before
