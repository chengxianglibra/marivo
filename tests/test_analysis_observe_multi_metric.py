"""observe with a metric sequence: boundary, fusion, meta, evidence."""

import ibis
import pytest

import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis.errors import SemanticKindMismatchError, TemporalSuitabilityError
from marivo.analysis.evidence.identity import make_artifact_id
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.metric_graph import CatalogMetricSubjectV1
from tests.ref_helpers import make_ref
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


def test_duplicate_entry_and_ref_roots_are_rejected_after_normalization(sales_session):
    catalog = sales_session.catalog
    revenue = catalog.require(ms.ref.metric("sales.revenue"))
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            [revenue, revenue.ref],
            time_scope=WINDOW,
            grain="day",
            session=sales_session,
        )
    assert exc_info.value._context["duplicate_metric_refs"] == ["metric:sales.revenue"]


def test_boundary_single_element_sequence_equals_scalar_observe(sales_session):
    catalog = sales_session.catalog
    via_list = observe(
        [catalog.require(ms.ref.metric("sales.revenue")).ref],
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    via_scalar = observe(
        catalog.require(ms.ref.metric("sales.revenue")).ref,
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    assert via_list.meta.metric_id == "sales.revenue"
    assert via_list.meta.artifact_id == via_scalar.meta.artifact_id


def test_public_session_observe_accepts_non_empty_metric_sequence(sales_session):
    catalog = sales_session.catalog
    frame = sales_session.observe(
        (
            catalog.require(ms.ref.metric("sales.revenue")),
            catalog.require(ms.ref.metric("sales.order_count")).ref,
        ),
        time_scope=WINDOW,
        grain="day",
    )

    assert frame.metrics == ("sales.revenue", "sales.order_count")
    assert frame.arity == 2


def test_registered_direct_ref_segmented_example_executes(sales_session):
    from marivo.analysis._capabilities.registry import REGISTRY

    example = REGISTRY.by_id("observe").additional_examples[0]
    namespace = {"session": sales_session, "ms": ms}
    exec(compile(example.code, "<observe-help-example>", "exec"), namespace)
    frame = namespace["frame"]
    assert frame.meta.semantic_kind == "panel"
    assert frame.meta.metric_id == "sales.revenue"


# --- Task 5: fused planning, execution, join ---


def test_same_entity_metrics_fuse_into_one_query(sales_session, monkeypatch):
    import marivo.analysis.intents._metric_graph_execute as graph_execute

    calls: list[int] = []
    real_execute = graph_execute.execute

    def counting_execute(*args, **kwargs):
        calls.append(1)
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(graph_execute, "execute", counting_execute)
    catalog = sales_session.catalog
    frame = observe(
        [
            catalog.require(ms.ref.metric("sales.revenue")).ref,
            catalog.require(ms.ref.metric("sales.order_count")).ref,
        ],
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
        [
            catalog.require(ms.ref.metric("sales.revenue")).ref,
            catalog.require(ms.ref.metric("sales.order_count")).ref,
        ],
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
        [
            catalog.require(ms.ref.metric("sales.revenue")).ref,
            catalog.require(ms.ref.metric("sales.order_count")).ref,
        ],
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    revenue = observe(
        catalog.require(ms.ref.metric("sales.revenue")).ref,
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )
    count = observe(
        catalog.require(ms.ref.metric("sales.order_count")).ref,
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


def test_cross_entity_metrics_with_different_time_axes_fail_before_execution(
    sales_session,
    monkeypatch,
):
    calls: list[int] = []

    def unexpected_query_capture() -> None:
        calls.append(1)
        raise AssertionError("temporal preflight must fail before query capture")

    monkeypatch.setattr(
        sales_session._connection_runtime,
        "begin_query_capture",
        unexpected_query_capture,
    )
    catalog = sales_session.catalog

    with pytest.raises(TemporalSuitabilityError) as exc_info:
        observe(
            [
                catalog.metrics.get("sales.revenue"),
                catalog.metrics.get("sales.user_count"),
            ],
            time_scope=WINDOW,
            grain="day",
            session=sales_session,
        )

    error = exc_info.value
    assert error.repair is not None
    assert error.repair.kind == "inspect"
    assert error.repair.snippet is None
    assert error.repair.candidates == (
        "sales.revenue -> time_dimension:sales.orders.order_date",
        "sales.user_count -> time_dimension:sales.users.signup_date",
    )
    assert error._context["candidate_time_dimensions"] == {
        "sales.revenue": ("sales.orders.order_date",),
        "sales.user_count": ("sales.users.signup_date",),
    }
    assert calls == []
    assert sales_session.jobs() == []
    assert sales_session.frame_summaries().items == ()


def test_cross_entity_subday_grain_reports_axis_conflict_without_partial_retry(
    sales_session,
    monkeypatch,
):
    calls: list[int] = []

    def unexpected_query_capture() -> None:
        calls.append(1)
        raise AssertionError("temporal preflight must fail before query capture")

    monkeypatch.setattr(
        sales_session._connection_runtime,
        "begin_query_capture",
        unexpected_query_capture,
    )
    catalog = sales_session.catalog

    with pytest.raises(TemporalSuitabilityError) as exc_info:
        observe(
            [
                catalog.metrics.get("sales.revenue"),
                catalog.metrics.get("sales.user_count"),
            ],
            time_scope=WINDOW,
            grain="hour",
            session=sales_session,
        )

    repair = exc_info.value.repair
    assert repair is not None
    assert repair.kind == "inspect"
    assert repair.snippet is None
    assert repair.candidates == (
        "sales.revenue -> time_dimension:sales.orders.order_date",
        "sales.user_count -> time_dimension:sales.users.signup_date",
    )
    assert calls == []


def test_segmented_multi_metric(sales_session):
    catalog = sales_session.catalog
    frame = observe(
        [
            catalog.require(ms.ref.metric("sales.revenue")).ref,
            catalog.require(ms.ref.metric("sales.order_count")).ref,
        ],
        time_scope=WINDOW,
        dimensions=[catalog.require(ms.ref.dimension("sales.orders.region")).ref],
        session=sales_session,
    )
    assert frame.meta.semantic_kind == "segmented"
    assert set(frame.columns) == {"region", "revenue", "order_count"}


def test_scalar_multi_metric(sales_session):
    catalog = sales_session.catalog
    frame = observe(
        [
            catalog.require(ms.ref.metric("sales.revenue")).ref,
            catalog.require(ms.ref.metric("sales.order_count")).ref,
        ],
        time_scope=WINDOW,
        session=sales_session,
    )
    assert frame.meta.semantic_kind == "scalar"
    assert frame.shape == (1, 2)


# --- Task 6: meta, params, cache, evidence ---


def _fused_frame(sales_session):
    catalog = sales_session.catalog
    return observe(
        [
            catalog.require(ms.ref.metric("sales.revenue")).ref,
            catalog.require(ms.ref.metric("sales.order_count")).ref,
        ],
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
    assert [identity["metric_ref"]["path"] for identity in params["metric_identities"]] == [
        "sales.revenue",
        "sales.order_count",
    ]
    assert len(params["metric_graph"]["roots"]) == 2
    assert len(params["lineage_metadata"]["physical_leaves"]) == 2
    assert params["semantic_dependency_digest"]["digest"]
    legacy_params = dict(params)
    legacy_params.pop("semantic_dependency_digest")
    assert frame.ref != make_artifact_id(
        step_type="observe",
        normalized_inputs=[],
        normalized_params=legacy_params,
        semantic_anchors={
            "metric_identities": params["metric_identities"],
            "model": "sales",
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
    assert all(isinstance(f.subject.typed_metric_subject, CatalogMetricSubjectV1) for f in findings)


# --- Unified graph supports cumulative roots in an ordered forest ---


def test_multi_metric_observe_accepts_cumulative_metric(sales_session):
    frame = observe(
        [
            make_ref("sales.revenue", SemanticKind.METRIC),
            make_ref("sales.cumulative_revenue", SemanticKind.METRIC),
        ],
        time_scope=WINDOW,
        grain="day",
        session=sales_session,
    )

    assert frame.value_columns == ("revenue", "cumulative_revenue")
    assert frame.meta.metric_identities is not None
