"""Comparison arithmetic and logical kinds survive downstream Query operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal, Inexact, localcontext
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.semantic.reader import SemanticProject


@pytest.mark.parametrize(
    ("current", "baseline", "absolute", "relative"),
    (
        ("38", "19", "19", 1.0),
        ("38", "-19", "57", 3.0),
        ("38", "0", "38", None),
        ("38", "-0", "38", None),
        (None, "0", None, None),
        ("38", None, None, None),
    ),
)
def test_decimal_compare_ignores_caller_precision_and_keeps_zero_policy(
    current: str | None,
    baseline: str | None,
    absolute: str | None,
    relative: float | None,
    comparison_session: mv.Session,
) -> None:
    session = comparison_session

    def endpoint(value: str | None) -> mv.AnalysisFrame:
        source = session.values(
            by=(), columns={"v": None}, rows=(((), {"v": Decimal(value or "38")}),)
        )
        if value is not None:
            return source
        empty = source.where(source["v"] > Decimal("100"))
        return empty.reduce({"v": empty["v"].sum()})

    left, right = endpoint(current), endpoint(baseline)
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = True
        frame = (
            left.compare(right, on=tuple(zip(left.axes, right.axes, strict=True)))
            .compute()
            .to_pandas()
        )
        delta, change = (
            frame[("value", "absolute.v")].iloc[0],
            frame[("value", "relative.v")].iloc[0],
        )
        assert pd.isna(delta) if absolute is None else delta == Decimal(absolute)
        assert pd.isna(change) if relative is None else change == relative
        if current is not None and baseline is not None and Decimal(baseline).is_zero():
            with pytest.raises(AnalysisError, match="nonzero denominator"):
                left.compare(
                    right,
                    zero_denominator="error",
                    on=tuple(zip(left.axes, right.axes, strict=True)),
                ).compute()
        else:
            left.compare(
                right, zero_denominator="error", on=tuple(zip(left.axes, right.axes, strict=True))
            ).compute()


@pytest.fixture
def comparison_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> mv.Session:
    source = tmp_path / "observations.parquet"
    pq.write_table(
        pa.table({"id": [1, 2], "elapsed": pa.array([38, 19], type=pa.duration("ns"))}),
        source,
    )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\nmd.duckdb(name='warehouse')\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": f"""import marivo.datasource as md
import marivo.semantic as ms
observations = ms.entity(name='observations', datasource=ms.ref.datasource('warehouse'), source=md.parquet({str(source)!r}), primary_key=['id'])
id = ms.dimension_column(name='id', entity=observations, column='id')
elapsed = ms.measure_column(name='elapsed', entity=observations, column='elapsed', additivity=ms.additive_all(), unit='s')
elapsed_field = ms.dimension_column(name='elapsed_field', entity=observations, column='elapsed')
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("comparison-regressions", timezone="UTC")


@pytest.mark.parametrize("endpoint", ("literal", "metric", "read", "read_measure"))
def test_duration_comparison_kinds_allow_filter_projection_and_reduction(
    comparison_session: mv.Session, endpoint: str
) -> None:
    session = comparison_session
    if endpoint == "literal":
        current = session.values(
            by=(), columns={"v": "s"}, rows=(((), {"v": timedelta(microseconds=38)}),)
        )
        baseline = session.values(
            by=(), columns={"v": "s"}, rows=(((), {"v": timedelta(microseconds=19)}),)
        )
    elif endpoint == "metric":
        identifier = session.catalog.fields.get("sales.observations.id")
        elapsed = session.catalog.fields.get("sales.observations.elapsed")
        current = session.observe({"v": elapsed.sum()}, where=identifier == 1)
        baseline = session.observe({"v": elapsed.sum()}, where=identifier == 2)
    else:
        entity = session.catalog.entities.get("sales.observations")
        elapsed = session.catalog.fields.get(
            "sales.observations.elapsed_field"
            if endpoint == "read"
            else "sales.observations.elapsed"
        )
        current = session.read(
            mv.coordinates(by=(mv.identity(entity),), keys=(((1,),),)), {"v": elapsed}
        )
        baseline = current.select({"v": current["v"] / 2})

    comparison = current.compare(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))
    for name, expected in (("left", 38), ("right", 19), ("absolute", 19)):
        selected = comparison.where(comparison[f"{name}.v"] > timedelta(0))
        projected = selected.select({"duration": selected[f"{name}.v"] + timedelta(0)})
        frame = projected.reduce({"duration": projected["duration"].sum()}).compute().to_pandas()
        assert frame[("value", "duration")].tolist() == [
            pd.Timedelta(expected, unit="us" if endpoint == "literal" else "ns")
        ]
    positive = comparison.where(comparison["relative.v"] > 0)
    assert positive.compute().to_pandas()[("value", "relative.v")].tolist() == [1.0]


def test_numeric_literal_comparison_keeps_known_kind(comparison_session: mv.Session) -> None:
    query = comparison_session.values(by=(), columns={"v": None}, rows=(((), {"v": 2}),))
    comparison = query.compare(query, on=tuple(zip(query.axes, query.axes, strict=True)))
    with pytest.raises(AnalysisError, match="matching value kinds"):
        comparison.where(comparison["absolute.v"] > timedelta(0))
    assert comparison.where(comparison["left.v"] > 0).compute().to_pandas()[
        ("value", "absolute.v")
    ].tolist() == [0]


def test_public_decimal_compare_ignores_caller_precision(comparison_session: mv.Session) -> None:
    current = comparison_session.values(
        by=(), columns={"v": None}, rows=(((), {"v": Decimal("38")}),)
    )
    baseline = comparison_session.values(
        by=(), columns={"v": None}, rows=(((), {"v": Decimal("19")}),)
    )
    with localcontext() as context:
        context.prec = 1
        frame = (
            current.compare(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))
            .compute()
            .to_pandas()
        )
    assert frame[("value", "absolute.v")].tolist() == [Decimal("19")]
    assert frame[("value", "relative.v")].tolist() == [1.0]


def test_duration_comparison_still_rejects_nonzero_mixed_tick_literals(
    comparison_session: mv.Session,
) -> None:
    elapsed = comparison_session.catalog.fields.get("sales.observations.elapsed")
    query = comparison_session.observe({"v": elapsed.sum()})
    comparison = query.compare(query, on=tuple(zip(query.axes, query.axes, strict=True)))
    invalid = comparison.where(comparison["absolute.v"] > timedelta(microseconds=1))
    with pytest.raises(AnalysisError, match=r"matching.*Duration"):
        invalid.compute()
