"""Governed derive_metric_frame escape hatch."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.datasource as md
from marivo.analysis.errors import PromotionFailedError, SemanticKindMismatchError
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


class _Expr:
    def compile(self) -> str:
        return "select created_at as cohort_date, region, amount as value from orders"


def _session_with_fake_backend(tmp_path, monkeypatch, df: pd.DataFrame):
    bootstrap_sales_project(tmp_path)
    session = mv.session.get_or_create(name="derive", use_datasources=True)

    def fake_session_backend(datasource: str):
        assert datasource == "datasource.warehouse"
        return object()

    def fake_execute(expr, *, datasource_name, cache, session_id):
        assert isinstance(expr, _Expr)
        assert datasource_name == "datasource.warehouse"
        assert session_id == session.id
        return SimpleNamespace(
            df=df.copy(),
            row_count=len(df),
            query=SimpleNamespace(
                sql="select created_at as cohort_date, region, amount as value from orders"
            ),
        )

    monkeypatch.setattr(session._connection_runtime, "session_backend", fake_session_backend)
    monkeypatch.setattr("marivo.analysis.derive.execute", fake_execute)
    return session


def test_derive_metric_frame_materializes_metric_frame_with_governed_contract(
    tmp_path, monkeypatch
) -> None:
    session = _session_with_fake_backend(
        tmp_path,
        monkeypatch,
        pd.DataFrame(
            {
                "cohort_date": ["2026-06-18", "2026-06-19"],
                "region": ["US", "CN"],
                "value": [10.0, 12.0],
            }
        ),
    )
    metric = session.catalog.get("metric.sales.revenue")
    region = session.catalog.get("dimension.sales.orders.region")
    order_date = session.catalog.get("time_dimension.sales.orders.order_date")

    frame = session.derive_metric_frame(
        metric=metric,
        query=mv.ibis_query(
            datasource=md.ref("datasource.warehouse"),
            build=lambda db, ctx: _Expr(),
        ),
        columns=mv.metric_columns(
            value="value",
            time=mv.time_column(column="cohort_date", ref=order_date),
            dimensions=[
                mv.dimension_column(column="region", ref=region),
            ],
        ),
        timescope={"start": "2026-06-18", "end": "2026-06-25"},
        grain="day",
        label="revenue_by_region",
    )

    assert isinstance(frame, mv.MetricFrame)
    assert frame.meta.metric_id == "sales.revenue"
    assert frame.meta.semantic_kind == "panel"
    assert frame.meta.semantic_model == "sales"
    assert frame.meta.measure == {"name": "value"}
    assert frame.meta.window == {
        "kind": "absolute",
        "start": "2026-06-18",
        "end": "2026-06-25",
        "grain": "day",
        "time_dimension": "sales.orders.order_date",
    }
    assert frame.meta.axes == {
        "time": {
            "role": "time",
            "column": "cohort_date",
            "ref": "sales.orders.order_date",
            "grain": "day",
            "time_dimension": "sales.orders.order_date",
        },
        "region": {
            "role": "dimension",
            "column": "region",
            "ref": "sales.orders.region",
        },
    }
    assert frame.lineage.steps[-1].intent == "derive_metric_frame"
    assert frame.lineage.steps[-1].params["label"] == "revenue_by_region"
    assert "semantic_kind" not in frame.lineage.steps[-1].params
    assert "semantic_model" not in frame.lineage.steps[-1].params
    assert "version" not in frame.lineage.steps[-1].params


def test_derive_metric_frame_rejects_non_metric_anchor(tmp_path, monkeypatch) -> None:
    session = _session_with_fake_backend(
        tmp_path,
        monkeypatch,
        pd.DataFrame({"value": [10.0]}),
    )

    with pytest.raises(SemanticKindMismatchError):
        session.derive_metric_frame(
            metric=session.catalog.get("dimension.sales.orders.region"),
            query=mv.ibis_query(
                datasource=md.ref("datasource.warehouse"),
                build=lambda db, ctx: _Expr(),
            ),
            columns=mv.metric_columns(value="value"),
            timescope={"start": "2026-06-18", "end": "2026-06-25"},
            grain=None,
        )


def test_derive_metric_frame_rejects_missing_output_columns(tmp_path, monkeypatch) -> None:
    session = _session_with_fake_backend(
        tmp_path,
        monkeypatch,
        pd.DataFrame({"wrong": [10.0]}),
    )

    with pytest.raises(PromotionFailedError) as exc_info:
        session.derive_metric_frame(
            metric=session.catalog.get("metric.sales.revenue"),
            query=mv.ibis_query(
                datasource=md.ref("datasource.warehouse"),
                build=lambda db, ctx: _Expr(),
            ),
            columns=mv.metric_columns(value="value"),
            timescope={"start": "2026-06-18", "end": "2026-06-25"},
            grain=None,
        )

    assert exc_info.value.details["missing"] == ["value"]


def test_derive_helpers_keep_string_columns_and_semantic_refs_separate() -> None:
    dim_ref = make_ref("sales.orders.region", SemanticKind.DIMENSION)
    metric_ref = make_ref("sales.revenue", SemanticKind.METRIC)

    columns = mv.metric_columns(
        value="value",
        dimensions=[mv.dimension_column(column="region", ref=dim_ref)],
    )

    assert columns.value == "value"
    assert columns.dimensions[0].column == "region"
    assert columns.dimensions[0].ref == dim_ref
    spec = mv.ibis_query(datasource=md.ref("datasource.warehouse"), build=lambda db, ctx: _Expr())
    assert spec.datasource == md.ref("datasource.warehouse")
    assert spec.datasource.id == "datasource.warehouse"
    assert metric_ref.id == "sales.revenue"


def test_ibis_query_rejects_string_datasource() -> None:
    with pytest.raises(TypeError, match=r'md\.ref\("datasource\.warehouse"\)'):
        mv.ibis_query(datasource="warehouse", build=lambda db, ctx: _Expr())  # type: ignore[arg-type]

    with pytest.raises(TypeError, match=r'md\.ref\("datasource\.warehouse"\)'):
        mv.IbisQuerySpec(  # type: ignore[arg-type]
            datasource="datasource.warehouse",
            build=lambda db, ctx: _Expr(),
        )


def test_derive_metric_frame_grain_token_consistent_for_non_string_grain(
    tmp_path, monkeypatch
) -> None:
    """Axes grain token must match the window grain token for non-string grains.

    Uses a ``(2, "hour")`` tuple grain (a valid ``GrainInput``) whose normalized
    token is ``"2hour"``.  Before the fix, ``_axis_metadata`` used ``str(grain)``
    on the raw arg, producing ``"(2, 'hour')"`` — diverging from the window's
    serialized ``"2hour"`` token.
    """
    session = _session_with_fake_backend(
        tmp_path,
        monkeypatch,
        pd.DataFrame(
            {
                "cohort_date": ["2026-06-18", "2026-06-19"],
                "region": ["US", "CN"],
                "value": [10.0, 12.0],
            }
        ),
    )
    metric = session.catalog.get("metric.sales.revenue")
    region = session.catalog.get("dimension.sales.orders.region")
    order_date = session.catalog.get("time_dimension.sales.orders.order_date")

    frame = session.derive_metric_frame(
        metric=metric,
        query=mv.ibis_query(
            datasource=md.ref("datasource.warehouse"),
            build=lambda db, ctx: _Expr(),
        ),
        columns=mv.metric_columns(
            value="value",
            time=mv.time_column(column="cohort_date", ref=order_date),
            dimensions=[
                mv.dimension_column(column="region", ref=region),
            ],
        ),
        timescope={"start": "2026-06-18", "end": "2026-06-25"},
        grain=(2, "hour"),
        label="revenue_by_region_hour",
    )

    axes_grain = frame.meta.axes["time"]["grain"]
    window_grain = frame.meta.window["grain"]
    assert axes_grain == window_grain
    assert axes_grain == "2hour"
