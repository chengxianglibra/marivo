"""Comparison arithmetic and logical kinds survive downstream Query operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, DecimalException, Inexact, Rounded, localcontext
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value


@pytest.mark.parametrize(
    ("current", "baseline", "absolute", "relative"),
    (
        ("38", "19", "19", 1.0),
        ("38", "-19", "57", 3.0),
        ("38", "0", "38", None),
        ("38", "-0", "38", None),
        (None, "0", None, None),
        ("38", None, None, None),
        ("4", "3", "1", 1 / 3),
        ("1E-38", "2E-38", "0E-37", 0.0),
    ),
)
def test_decimal_compare_uses_native_context_and_masks_zero(
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
        return source.select({"v": source["v"] / 0})

    left, right = endpoint(current), endpoint(baseline)
    compared = left.compare(
        {"base": mv.baseline(right, on=tuple(zip(left.axes, right.axes, strict=True)))}
    )
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.rounding = ROUND_DOWN
        try:
            with localcontext(context):
                expected_delta = (
                    None
                    if current is None or baseline is None
                    else Decimal(current) - Decimal(baseline)
                )
                expected_change = (
                    None
                    if expected_delta is None or Decimal(baseline or "0") == 0
                    else float(expected_delta / abs(Decimal(baseline)))
                )
        except DecimalException:
            with pytest.raises(AnalysisError, match="native numeric arithmetic"):
                compared.compute()
        else:
            frame = compared.compute().to_pandas()
            delta, change = frame["base.absolute.v"].iloc[0], frame["base.relative.v"].iloc[0]
            assert pd.isna(delta) if expected_delta is None else delta == expected_delta
            assert pd.isna(change) if expected_change is None else change == expected_change
        assert context.prec == 1
        assert context.rounding == ROUND_DOWN
        assert context.traps[Inexact] and context.traps[Rounded]


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
    return mv.session.get_or_create("comparison-regressions", timezone="UTC")


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
        identifier = catalog_value(session.catalog, "sales.observations.id", kind="field")
        elapsed = catalog_value(session.catalog, "sales.observations.elapsed", kind="field")
        current = session.observe({"v": elapsed.sum()}, where=identifier == 1)
        baseline = session.observe({"v": elapsed.sum()}, where=identifier == 2)
    else:
        entity = session.catalog.get("entity:sales.observations")
        elapsed = catalog_value(
            session.catalog,
            "sales.observations.elapsed_field"
            if endpoint == "read"
            else "sales.observations.elapsed",
            kind="field",
        )
        current = session.read(
            mv.coordinates(by=(mv.identity(entity),), keys=(((1,),),)), {"v": elapsed}
        )
        baseline = current.select({"v": current["v"] / 2})

    comparison = current.compare(
        {"base": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
    )
    for name, expected in (("v", 38), ("base.baseline.v", 19), ("base.absolute.v", 19)):
        selected = comparison.where(comparison[name] > timedelta(0))
        projected = selected.select({"duration": selected[name] + timedelta(0)})
        frame = projected.reduce({"duration": projected["duration"].sum()}).compute().to_pandas()
        assert frame["duration"].tolist() == [
            pd.Timedelta(expected, unit="us" if endpoint == "literal" else "ns")
        ]
    positive = comparison.where(comparison["base.relative.v"] > 0)
    assert positive.compute().to_pandas()["base.relative.v"].tolist() == [1.0]


def test_numeric_literal_comparison_keeps_known_kind(comparison_session: mv.Session) -> None:
    query = comparison_session.values(by=(), columns={"v": None}, rows=(((), {"v": 2}),))
    comparison = query.compare(
        {"base": mv.baseline(query, on=tuple(zip(query.axes, query.axes, strict=True)))}
    )
    with pytest.raises(AnalysisError, match="matching value kinds"):
        comparison.where(comparison["base.absolute.v"] > timedelta(0))
    assert comparison.where(comparison["v"] > 0).compute().to_pandas()[
        "base.absolute.v"
    ].tolist() == [0]


def test_duration_comparison_still_rejects_nonzero_mixed_tick_literals(
    comparison_session: mv.Session,
) -> None:
    elapsed = catalog_value(comparison_session.catalog, "sales.observations.elapsed", kind="field")
    query = comparison_session.observe({"v": elapsed.sum()})
    comparison = query.compare(
        {"base": mv.baseline(query, on=tuple(zip(query.axes, query.axes, strict=True)))}
    )
    invalid = comparison.where(comparison["base.absolute.v"] > timedelta(microseconds=1))
    with pytest.raises(AnalysisError, match=r"matching.*Duration"):
        invalid.compute()
