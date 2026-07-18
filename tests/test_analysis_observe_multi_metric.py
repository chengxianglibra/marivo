"""observe with a metric sequence: boundary, fusion, meta, evidence."""

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.evidence.identity import make_artifact_id
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
from tests.shared_fixtures import (
    bootstrap_multi_metric_sales_project,
    seed_multi_metric_tables,
)


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    yield


@pytest.fixture
def sales_session(tmp_path):
    bootstrap_multi_metric_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    seed_multi_metric_tables(con)
    return session_attach.get_or_create(name="multi_metric", backends={"warehouse": lambda: con})


WINDOW = {"start": "2026-07-01", "end": "2026-07-04"}


def test_boundary_empty_sequence_rejected(sales_session):
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        observe([], time_scope=WINDOW, grain="day", session=sales_session)
    assert "at least one metric" in str(excinfo.value)


def test_boundary_duplicate_metrics_rejected(sales_session):
    catalog = sales_session.catalog
    revenue = catalog.get("metric.sales.revenue")
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        observe(
            [revenue, revenue],
            time_scope=WINDOW,
            grain="day",
            session=sales_session,
        )
    assert "sales.revenue" in str(excinfo.value)


def test_boundary_single_element_sequence_equals_scalar_observe(sales_session):
    catalog = sales_session.catalog
    via_list = observe(
        [catalog.get("metric.sales.revenue")],
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    via_scalar = observe(
        catalog.get("metric.sales.revenue"),
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    assert via_list.meta.metric_id == "sales.revenue"
    assert via_list.meta.artifact_id == via_scalar.meta.artifact_id


# --- Task 5: fused planning, execution, join ---


def test_same_entity_metrics_fuse_into_one_query(sales_session, monkeypatch):
    import marivo.analysis.intents.observe_multi as om

    calls: list[int] = []
    real_execute = om.execute

    def counting_execute(*args, **kwargs):
        calls.append(1)
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(om, "execute", counting_execute)
    catalog = sales_session.catalog
    frame = observe(
        [catalog.get("metric.sales.revenue"), catalog.get("metric.sales.order_count")],
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    assert len(calls) == 1
    assert frame.metrics == ("sales.revenue", "sales.order_count")
    assert list(frame.columns) == ["bucket_start", "revenue", "order_count"]


def test_value_columns_exposes_metric_value_columns_regardless_of_arity(sales_session):
    """value_columns exposes the metric-named columns exported by to_pandas()."""
    catalog = sales_session.catalog
    multi = observe(
        [catalog.get("metric.sales.revenue"), catalog.get("metric.sales.order_count")],
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    assert multi.value_columns == ("revenue", "order_count")
    # The exposed names match the DataFrame value columns exactly.
    multi_df = multi.to_pandas()
    assert set(multi.value_columns) <= set(multi_df.columns)


def test_fused_values_match_single_observes(sales_session):
    catalog = sales_session.catalog
    fused = observe(
        [catalog.get("metric.sales.revenue"), catalog.get("metric.sales.order_count")],
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    revenue = observe(
        catalog.get("metric.sales.revenue"),
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    count = observe(
        catalog.get("metric.sales.order_count"),
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    fused_df = fused.to_pandas().set_index("bucket_start")
    assert (
        fused_df["revenue"].tolist()
        == revenue.to_pandas().set_index("bucket_start")["revenue"].tolist()
    )
    assert (
        fused_df["order_count"].tolist()
        == count.to_pandas().set_index("bucket_start")["order_count"].tolist()
    )


def test_cross_entity_metrics_join_on_time_axis(sales_session, monkeypatch):
    import marivo.analysis.intents.observe_multi as om

    calls: list[int] = []
    real_execute = om.execute

    def counting_execute(*args, **kwargs):
        calls.append(1)
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(om, "execute", counting_execute)
    catalog = sales_session.catalog
    frame = observe(
        [catalog.get("metric.sales.revenue"), catalog.get("metric.sales.user_count")],
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    assert len(calls) == 2
    df = frame.to_pandas()
    # users has a signup on 2026-07-03 where orders has no rows: outer join keeps it.
    assert len(df) == 3
    assert df["revenue"].isna().sum() == 1


def test_segmented_multi_metric(sales_session):
    catalog = sales_session.catalog
    frame = observe(
        [catalog.get("metric.sales.revenue"), catalog.get("metric.sales.order_count")],
        time_scope=WINDOW,
        dimensions=[catalog.get("dimension.sales.orders.region").ref],
        session=sales_session,
    )
    assert frame.meta.semantic_kind == "segmented"
    assert set(frame.columns) == {"region", "revenue", "order_count"}


def test_scalar_multi_metric(sales_session):
    catalog = sales_session.catalog
    frame = observe(
        [catalog.get("metric.sales.revenue"), catalog.get("metric.sales.order_count")],
        time_scope=WINDOW,
        session=sales_session,
    )
    assert frame.meta.semantic_kind == "scalar"
    assert frame.shape == (1, 2)


# --- Task 6: meta, params, cache, evidence ---


def _fused_frame(sales_session):
    catalog = sales_session.catalog
    return observe(
        [catalog.get("metric.sales.revenue"), catalog.get("metric.sales.order_count")],
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )


def test_meta_measures_ordered_and_scalars_none(sales_session):
    frame = _fused_frame(sales_session)
    assert frame.meta.metric_id is None
    assert [m["metric_id"] for m in frame.meta.measures] == [
        "sales.revenue",
        "sales.order_count",
    ]
    assert [m["column"] for m in frame.meta.measures] == ["revenue", "order_count"]
    assert [m["additivity"] for m in frame.meta.measures] == ["additive", "additive"]
    assert [m["aggregation"] for m in frame.meta.measures] == [None, None]
    assert [m["status_time_dimension"] for m in frame.meta.measures] == [None, None]
    assert frame.meta.semantic_model == "sales"


def test_params_record_metric_list_and_fusion(sales_session):
    frame = _fused_frame(sales_session)
    params = frame.meta.lineage.steps[0].params
    assert params["metrics"] == ["sales.revenue", "sales.order_count"]
    assert params["fusion"] == [["sales.revenue", "sales.order_count"]]
    assert params["metric_semantics"] == {
        "sales.order_count": {
            "additivity": "additive",
            "aggregation": None,
            "status_time_dimension": None,
        },
        "sales.revenue": {
            "additivity": "additive",
            "aggregation": None,
            "status_time_dimension": None,
        },
    }
    legacy_params = dict(params)
    legacy_params.pop("metric_semantics")
    assert frame.ref != make_artifact_id(
        step_type="observe",
        normalized_inputs=[],
        normalized_params=legacy_params,
        semantic_anchors={
            "metrics": ["sales.revenue", "sales.order_count"],
            "models": ["sales"],
        },
    )


def test_repeat_call_hits_frame_cache(sales_session):
    first = _fused_frame(sales_session)
    second = _fused_frame(sales_session)
    assert first.meta.artifact_id == second.meta.artifact_id


def test_evidence_findings_per_metric(sales_session):
    frame = _fused_frame(sales_session)
    findings = [
        f
        for f in sales_session.evidence.findings(artifact_ref=frame.meta.artifact_id)
        if f.finding_type == "metric_value"
    ]
    subjects = {f.subject.metric for f in findings}
    assert subjects == {"sales.revenue", "sales.order_count"}


# --- Task 5: multi-metric cumulative rejection ---


def test_multi_metric_observe_rejects_cumulative_metric(sales_session):
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            [
                make_ref("sales.revenue", SemanticKind.METRIC),
                make_ref("sales.cumulative_revenue", SemanticKind.METRIC),
            ],
            time_scope=WINDOW,
            grain="day",
            session=sales_session,
        )

    assert "cumulative" in str(exc_info.value)
    assert "single metric" in str(exc_info.value)
