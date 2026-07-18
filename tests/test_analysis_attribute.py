"""session.attribute public attribution operator."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    AttributionMaterializationError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _now() -> datetime:
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _delta(
    session: mv.Session,
    df: pd.DataFrame,
    *,
    semantic_kind: str = "segmented",
    additivity: str | None = "additive",
) -> DeltaFrame:
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref="frame_delta",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_compare",
        created_at=_now(),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="compare",
                    job_ref="job_compare",
                    inputs=["frame_current", "frame_baseline"],
                    params_digest="sha256:compare",
                )
            ]
        ),
        metric_id="sales.revenue",
        source_current_ref="frame_current",
        source_baseline_ref="frame_baseline",
        alignment={
            "kind": "window_bucket",
            "axes": {
                "region": {
                    "role": "dimension",
                    "column": "region",
                    "ref": "sales.orders.region",
                },
                "platform": {
                    "role": "dimension",
                    "column": "platform",
                    "ref": "sales.orders.platform",
                },
            },
        },
        semantic_kind=semantic_kind,  # type: ignore[arg-type]
        semantic_model="sales",
        additivity=additivity,  # type: ignore[arg-type]
    )
    return DeltaFrame(_df=df, meta=meta)


def test_attribute_single_axis_returns_attribution_frame_with_public_lineage() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "CN", "US"],
                "delta": [10.0, -2.0, 4.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )

    assert isinstance(out, AttributionFrame)
    assert out.meta.kind == "attribution_frame"
    assert out.lineage.steps[-1].intent == "attribute"
    assert out.meta.method == "ordered_hierarchy_sum"
    assert out.meta.params["axes"] == ["sales.orders.region"]
    assert out.meta.driver_field == "path"
    assert out.to_pandas()[["driver", "contribution"]].to_dict("records") == [
        {"driver": "US", "contribution": 14.0},
        {"driver": "CN", "contribution": -2.0},
    ]


def test_attribute_nested_axes_returns_flattened_hierarchy_rows() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "US", "CN", "CN"],
                "platform": ["ios", "android", "ios", "android"],
                "delta": [6.0, 4.0, -3.0, 1.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[
            make_ref("sales.orders.region", SemanticKind.DIMENSION),
            make_ref("sales.orders.platform", SemanticKind.DIMENSION),
        ],
        mode="hierarchy",
    )

    df = out.to_pandas()
    assert out.meta.method == "ordered_hierarchy_sum"
    assert out.meta.driver_field == "path"
    assert {"region", "platform", "value_effect", "mix_effect", "residual"}.issubset(df.columns)
    assert df.loc[df["level"] == 2, "contribution"].sum() == pytest.approx(8.0)
    assert df.loc[df["level"] == 1, "platform"].isna().all()


def test_attribute_requires_explicit_axes() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"region": ["US"], "delta": [10.0]}))

    with pytest.raises(SemanticKindMismatchError, match="attribute requires at least one axis"):
        session.attribute(frame, axes=[])


def test_attribute_present_axes_delegates_to_decompose_without_materialization() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "CN", "US"],
                "delta": [10.0, -2.0, 4.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )

    assert isinstance(out, AttributionFrame)
    assert out.lineage.steps[-1].intent == "attribute"
    assert out.meta.params["materialization_status"] == "not_required"
    assert out.meta.params["source_ref"] == "frame_delta"
    assert out.meta.params["axes"] == ["sales.orders.region"]
    assert "mode" not in out.meta.params


def test_attribute_single_axis_ignores_mode_parameter() -> None:
    """A single-axis attribution has no joint/hierarchy distinction, so ``mode``
    is meaningless and must be ignored rather than rejected — letting callers
    pass a fixed ``mode`` without branching on axis count (see issue #23).
    """
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"region": ["US", "CN", "US"], "delta": [10.0, -2.0, 4.0]}),
    )

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
        mode="joint",
    )

    assert isinstance(out, AttributionFrame)
    assert out.meta.params["axes"] == ["sales.orders.region"]
    # mode is not applicable to a single axis, so it is dropped from params.
    assert "mode" not in out.meta.params
    assert out.attribution_mode is None


def test_attribute_rejects_duplicate_axes() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"region": ["US"], "delta": [10.0]}))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.attribute(
            frame,
            axes=[
                make_ref("sales.orders.region", SemanticKind.DIMENSION),
                make_ref("sales.orders.region", SemanticKind.DIMENSION),
            ],
        )

    assert exc_info.value._context["reason"] == "duplicate_axes"


def test_attribute_missing_axis_materializes_expanded_delta(semantic_project_factory) -> None:
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "warehouse = md.ref('datasource.warehouse')\n"
                "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day')\n"
                "def created_at(orders):\n"
                "    return orders.created_at.cast('date')\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    import ibis

    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (id INTEGER, created_at DATE, region VARCHAR, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 'US', 100.0),"
        "(2, DATE '2026-07-02', 'CN', 20.0),"
        "(3, DATE '2025-07-01', 'US', 70.0),"
        "(4, DATE '2025-07-02', 'CN', 30.0)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: con})
    revenue = session.catalog.get("metric.sales.revenue")
    region = session.catalog.get("dimension.sales.orders.region").ref
    cur = session.observe(
        revenue,
        time_scope={"start": "2026-07-01", "end": "2026-08-01"},
    )
    base = session.observe(
        revenue,
        time_scope={"start": "2025-07-01", "end": "2025-08-01"},
    )
    delta = session.compare(cur, base)

    out = session.attribute(delta, axes=[region])

    assert isinstance(out, AttributionFrame)
    assert out.meta.params["materialization_status"] == "expanded"
    assert out.meta.params["original_delta_ref"] == delta.ref
    assert out.meta.params["missing_axes"] == ["sales.orders.region"]
    assert out.meta.params["expanded_delta_ref"]
    assert out.to_pandas()[["driver", "contribution"]].to_dict("records") == [
        {"driver": "US", "contribution": 30.0},
        {"driver": "CN", "contribution": -10.0},
    ]
    assert [job.intent for job in session.jobs()].count("observe") == 4
    assert [job.intent for job in session.jobs()].count("compare") == 2


def test_attribute_validates_original_delta_before_axis_materialization(
    semantic_project_factory,
) -> None:
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "warehouse = md.ref('datasource.warehouse')\n"
                "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day')\n"
                "def created_at(orders):\n"
                "    return orders.created_at.cast('date')\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    import ibis

    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (id INTEGER, created_at DATE, region VARCHAR, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 'US', 100.0),"
        "(2, DATE '2025-07-01', 'US', 70.0)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: con})
    revenue = session.catalog.get("metric.sales.revenue")
    region = session.catalog.get("dimension.sales.orders.region").ref
    current = session.observe(
        revenue,
        time_scope={"start": "2026-07-01", "end": "2026-08-01"},
    )
    baseline = session.observe(
        revenue,
        time_scope={"start": "2025-07-01", "end": "2025-08-01"},
    )
    delta = session.compare(current, baseline)
    delta.meta = delta.meta.model_copy(update={"additivity": None})

    with pytest.raises(mv.errors.AttributionAdditivityError) as exc_info:
        session.attribute(delta, axes=[region])

    assert exc_info.value._context["reason"] == "missing_additivity_metadata"
    assert [job.intent for job in session.jobs()].count("observe") == 2
    assert [job.intent for job in session.jobs()].count("compare") == 1


def test_attribute_lowers_tier1_mean_to_exact_non_null_components(
    semantic_project_factory,
) -> None:
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "orders = ms.entity("
                "name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=md.table('orders'))\n"
                "created_at = ms.time_dimension_column("
                "name='created_at', entity=orders, column='created_at', "
                "granularity='day', is_default=True)\n"
                "region = ms.dimension_column("
                "name='region', entity=orders, column='region')\n"
                "amount = ms.measure_column("
                "name='amount', entity=orders, column='amount', additivity='additive')\n"
                "avg_amount = ms.aggregate("
                "name='avg_amount', measure=amount, agg='mean')\n"
            ),
        }
    )
    import ibis

    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (created_at DATE, region VARCHAR, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-07-01', 'US', 100.0),"
        "(DATE '2026-07-02', 'US', 200.0),"
        "(DATE '2026-07-03', 'CN', 10.0),"
        "(DATE '2026-07-04', 'US', NULL),"
        "(DATE '2025-07-01', 'US', 100.0),"
        "(DATE '2025-07-02', 'CN', 10.0),"
        "(DATE '2025-07-03', 'CN', 20.0),"
        "(DATE '2025-07-04', 'US', NULL)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: con})
    avg_amount = session.catalog.get("metric.sales.avg_amount")
    region = session.catalog.get("dimension.sales.orders.region").ref
    cur = session.observe(
        avg_amount,
        time_scope={"start": "2026-07-01", "end": "2026-08-01"},
    )
    base = session.observe(
        avg_amount,
        time_scope={"start": "2025-07-01", "end": "2025-08-01"},
    )
    delta = session.compare(cur, base)

    assert cur.meta.additivity == "non_additive"
    assert cur.meta.aggregation == "mean"
    assert cur.meta.status_time_dimension is None
    assert cur.meta.composition is not None
    assert cur.meta.composition["kind"] == "weighted_average"
    assert cur.meta.composition["lowered_from"] == "mean"
    assert cur.meta.composition["denominator_semantics"] == "count_non_null"
    assert cur.components().to_pandas()["__mean_count_non_null"].iloc[0] == 3
    assert delta.meta.additivity == "non_additive"
    assert delta.meta.aggregation == "mean"
    assert delta.meta.status_time_dimension is None
    loaded_delta = session.get_frame(delta.ref)
    assert isinstance(loaded_delta, DeltaFrame)
    assert loaded_delta.meta.additivity == "non_additive"
    assert loaded_delta.meta.aggregation == "mean"
    assert delta.to_pandas().iloc[0]["delta"] == pytest.approx(60.0)
    attribution = session.attribute(delta, axes=[region])

    assert attribution.meta.method == "weighted_mix"
    assert attribution.to_pandas()["contribution"].sum() == pytest.approx(60.0)


def test_attribute_missing_axis_without_replayable_sources_fails_closed() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"delta": [10.0]}))

    with pytest.raises(AttributionMaterializationError) as exc_info:
        session.attribute(frame, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)])

    assert exc_info.value._context["delta_ref"] == "frame_delta"
    assert exc_info.value._context["missing_axes"] == ["sales.orders.region"]
    assert exc_info.value._context["recoverability_status"] in {
        "source_frame_missing",
        "observe_params_missing",
    }
