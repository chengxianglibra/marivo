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
from marivo.analysis.binding.correspondence import ComparisonEndpoint, bind_correspondence
from marivo.analysis.core.data_types import DataColumn, DecimalDataType
from marivo.analysis.errors import AnalysisError
from marivo.analysis.methods.query_comparison import compare_frames
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
) -> None:
    endpoint = ComparisonEndpoint(
        (DataColumn("value.0", "v", DecimalDataType(12, 2), True),), (), (), ()
    )
    binding = bind_correspondence(endpoint, endpoint, operation="compare")
    left = pd.DataFrame({"value.0": [None if current is None else Decimal(current)]})
    right = pd.DataFrame({"value.0": [None if baseline is None else Decimal(baseline)]})
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = True
        frame = compare_frames(binding, left, right).frame
        delta, change = frame["value.0.absolute"].iloc[0], frame["value.0.relative"].iloc[0]
        assert pd.isna(delta) if absolute is None else delta == Decimal(absolute)
        assert pd.isna(change) if relative is None else change == relative
        if current is not None and baseline is not None and Decimal(baseline).is_zero():
            with pytest.raises(AnalysisError, match="nonzero denominator"):
                compare_frames(binding, left, right, zero_denominator="error")
        else:
            compare_frames(binding, left, right, zero_denominator="error")


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
    return mv.session.get_or_create("comparison-regressions", report_timezone="UTC")


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
        current = session.read(mv.identities(entity, keys=((1,),)), {"v": elapsed})
        baseline = current.select({"v": current["v"] / 2})

    comparison = current.compare(baseline)
    for name, expected in (("current", 38), ("baseline", 19), ("absolute", 19)):
        selected = comparison.where(comparison[f"v.{name}"] > timedelta(0))
        projected = selected.select({"duration": selected[f"v.{name}"] + timedelta(0)})
        frame = projected.reduce({"duration": projected["duration"].sum()}).execute().to_pandas()
        assert frame[("value", "duration")].tolist() == [
            pd.Timedelta(expected, unit="us" if endpoint == "literal" else "ns")
        ]
    positive = comparison.where(comparison["v.relative"] > 0)
    assert positive.execute().to_pandas()[("value", "v.relative")].tolist() == [1.0]


def test_numeric_literal_comparison_keeps_known_kind(comparison_session: mv.Session) -> None:
    query = comparison_session.values(by=(), columns={"v": None}, rows=(((), {"v": 2}),))
    comparison = query.compare(query)
    with pytest.raises(AnalysisError, match="matching value kinds"):
        comparison.where(comparison["v.absolute"] > timedelta(0))
    assert comparison.where(comparison["v.current"] > 0).execute().to_pandas()[
        ("value", "v.absolute")
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
        frame = current.compare(baseline).execute().to_pandas()
    assert frame[("value", "v.absolute")].tolist() == [Decimal("19")]
    assert frame[("value", "v.relative")].tolist() == [1.0]


def test_duration_comparison_still_rejects_nonzero_mixed_tick_literals(
    comparison_session: mv.Session,
) -> None:
    elapsed = comparison_session.catalog.fields.get("sales.observations.elapsed")
    query = comparison_session.observe({"v": elapsed.sum()})
    comparison = query.compare(query)
    invalid = comparison.where(comparison["v.absolute"] > timedelta(microseconds=1))
    with pytest.raises(AnalysisError, match="matching exact Duration units"):
        invalid.execute()
