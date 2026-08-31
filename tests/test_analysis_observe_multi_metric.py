"""observe with a metric sequence: boundary, fusion, meta, evidence."""

import inspect

import ibis
import pytest

import marivo.analysis as mv
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


WINDOW = mv.time_scope(start="2026-07-01", end="2026-07-04")


def test_boundary_empty_sequence_rejected(sales_session):
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        observe([], time_scope=WINDOW, grain=mv.grain("day"), session=sales_session)
    assert "at least one metric" in str(excinfo.value)


def test_duplicate_entry_and_ref_roots_are_rejected_after_normalization(sales_session):
    catalog = sales_session.catalog
    revenue = catalog.require(ms.ref.metric("sales.revenue"))
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            [revenue, revenue.ref],
            time_scope=WINDOW,
            grain=mv.grain("day"),
            session=sales_session,
        )
    assert exc_info.value._context["duplicate_metric_refs"] == ["metric:sales.revenue"]


def test_boundary_single_element_sequence_equals_scalar_observe(sales_session):
    catalog = sales_session.catalog
    via_list = observe(
        [catalog.require(ms.ref.metric("sales.revenue")).ref],
        time_scope=WINDOW,
        grain=mv.grain("day"),
        session=sales_session,
    )
    via_scalar = observe(
        catalog.require(ms.ref.metric("sales.revenue")).ref,
        time_scope=WINDOW,
        grain=mv.grain("day"),
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
        grain=mv.grain("day"),
    )

    assert frame.metrics == ("sales.revenue", "sales.order_count")
    assert frame.arity == 2


def test_public_session_observe_uses_plural_metrics_keyword(sales_session):
    catalog = sales_session.catalog
    revenue = catalog.require(ms.ref.metric("sales.revenue")).ref
    order_count = catalog.require(ms.ref.metric("sales.order_count")).ref

    parameters = inspect.signature(type(sales_session).observe).parameters
    assert "metrics" in parameters
    assert "metric" not in parameters

    scalar = sales_session.observe(metrics=revenue)
    forest = sales_session.observe(metrics=[revenue, order_count])

    assert scalar.meta.metric_id == "sales.revenue"
    assert forest.metrics == ("sales.revenue", "sales.order_count")
    # The forest (multi-metric) observe path must persist the session report
    # timezone into frame meta just like the scalar path (issue #70 direction
    # ②). Without this pin a refactor that drops report_tz= from
    # _observe_metric_forest would silently re-split summary vs check for every
    # multi-metric frame with no suite signal.
    assert forest.meta.report_tz == sales_session.report_tz_name

    with pytest.raises(TypeError, match="unexpected keyword argument 'metric'"):
        sales_session.observe(metric=revenue)  # type: ignore[call-arg]


def test_public_session_observe_rejects_empty_metrics_sequence(sales_session):
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        sales_session.observe(metrics=[])

    assert exc_info.value._context["argument"] == "metrics"


def test_direct_ref_segmented_observe_executes(sales_session):
    frame = sales_session.observe(
        ms.ref.metric("sales.revenue"),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        grain=mv.grain("day"),
        dimensions=[ms.ref.dimension("sales.orders.region")],
    )
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
        grain=mv.grain("day"),
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
        grain=mv.grain("day"),
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
        grain=mv.grain("day"),
        session=sales_session,
    )
    revenue = observe(
        catalog.require(ms.ref.metric("sales.revenue")).ref,
        time_scope=WINDOW,
        grain=mv.grain("day"),
        session=sales_session,
    )
    count = observe(
        catalog.require(ms.ref.metric("sales.order_count")).ref,
        time_scope=WINDOW,
        grain=mv.grain("day"),
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
            grain=mv.grain("day"),
            session=sales_session,
        )

    error = exc_info.value
    assert error.repair is not None
    assert error.repair.kind == "inspect"
    assert error.repair.snippet is not None
    assert error.repair.candidates == (
        "sales.revenue -> time_dimension:sales.orders.order_date",
        "sales.user_count -> time_dimension:sales.users.signup_date",
    )
    assert error._context["candidate_time_dimensions"] == {
        "sales.revenue": ("sales.orders.order_date",),
        "sales.user_count": ("sales.users.signup_date",),
    }
    assert calls == []
    assert sales_session.runs(limit=100).items == ()
    assert sales_session.graph().artifacts == ()


def test_mixed_axis_forest_repair_outputs_ready_to_copy_split_plan(
    sales_session,
    monkeypatch,
):
    """A mixed-axis forest repair names split-by-time-dimension and renders the plan.

    The ecommerce scenario in issue #106 hits this branch: roots resolve to
    different implicit time dimensions (``sales.orders.order_date`` vs
    ``sales.users.signup_date`` here), and the repair must give a concrete,
    ready-to-copy per-axis split instead of only "combine when one axis is
    valid for the forest".
    """
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
            grain=mv.grain("day"),
            session=sales_session,
        )

    repair = exc_info.value.repair
    assert repair is not None
    assert repair.kind == "inspect"
    assert "Split the metrics into separate observe() calls" in repair.action
    assert repair.snippet is not None
    assert "import marivo.analysis as mv" in repair.snippet
    assert 'time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04")' in repair.snippet
    assert 'grain=mv.grain("day")' in repair.snippet
    assert (
        'time_dimension=session.catalog.time_dimensions.get("sales.orders.order_date")'
        in repair.snippet
    )
    assert (
        'time_dimension=session.catalog.time_dimensions.get("sales.users.signup_date")'
        in repair.snippet
    )
    assert 'session.catalog.metrics.get("sales.revenue")' in repair.snippet
    assert 'session.catalog.metrics.get("sales.user_count")' in repair.snippet
    # The bare-string form is rejected by the strict ref boundary; the snippet
    # must render a typed handle instead (regression guard for issue #106).
    assert 'time_dimension="' not in repair.snippet
    assert calls == []


def test_explicit_axis_incompatible_with_one_root_lists_all_root_candidates(
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
            grain=mv.grain("day"),
            time_dimension=catalog.time_dimensions.get("sales.orders.order_date"),
            session=sales_session,
        )

    error = exc_info.value
    assert error.repair is not None
    assert error.repair.kind == "inspect"
    assert error.repair.snippet is None
    # Every root's candidate axis is listed at once, with the incompatible root
    # marked, instead of only the failing root's candidates.
    assert error.repair.candidates == (
        "sales.revenue -> sales.orders.order_date",
        "sales.user_count -> sales.users.signup_date [incompatible]",
    )
    assert error._context["candidate_time_dimensions"] == {
        "sales.revenue": ("sales.orders.order_date",),
        "sales.user_count": ("sales.users.signup_date",),
    }
    # Message wording is unambiguous (P3-2).
    assert "not a valid candidate for all metric roots" in error.message
    # No shared candidate axis: the repair must point to per-root splitting,
    # not to "omit time_dimension" (which would fail again downstream).
    assert "split the metrics into separate observe() calls" in error.repair.action
    assert "omit time_dimension" not in error.repair.action
    assert calls == []


def test_explicit_axis_conflict_with_shared_candidate_but_ambiguous_rejects_omit(
    sales_session,
    monkeypatch,
):
    """A shared candidate alone does not make "omit time_dimension" executable.

    When one root has multiple candidates (so its implicit selection is ambiguous
    without a preferred/default axis), omitting time_dimension would re-enter the
    ambiguous branch. The repair must not suggest that dead end (issue #87 P3).
    """
    import marivo.analysis.intents._observe_inputs as observe_inputs

    monkeypatch.setattr(
        observe_inputs,
        "_temporal_candidates",
        lambda catalog, metric_inputs: (
            {
                "sales.revenue": (
                    "sales.orders.order_date",
                    "sales.users.signup_date",
                ),
                "sales.user_count": ("sales.users.signup_date",),
            },
            {"sales.revenue": (), "sales.user_count": ()},
        ),
    )
    catalog = sales_session.catalog

    with pytest.raises(TemporalSuitabilityError) as exc_info:
        observe(
            [
                catalog.metrics.get("sales.revenue"),
                catalog.metrics.get("sales.user_count"),
            ],
            time_scope=WINDOW,
            grain=mv.grain("day"),
            time_dimension=catalog.time_dimensions.get("sales.orders.order_date"),
            session=sales_session,
        )

    error = exc_info.value
    assert error.repair is not None
    assert error.repair.candidates == (
        "sales.revenue -> sales.orders.order_date",
        "sales.revenue -> sales.users.signup_date",
        "sales.user_count -> sales.users.signup_date [incompatible]",
    )
    # The roots share signup_date, but revenue's implicit selection is ambiguous
    # (two candidates, no preferred/default), so omit would fail downstream.
    assert "choose an explicit axis valid for the complete metric forest" in error.repair.action
    assert "omit time_dimension" not in error.repair.action
    assert "split the metrics" not in error.repair.action


def test_explicit_axis_conflict_with_convergent_shared_candidate_suggests_omit(
    sales_session,
    monkeypatch,
):
    """When every root's implicit selection converges on the same shared axis,
    repair may suggest omitting time_dimension to auto-select it (issue #87 P3)."""
    import marivo.analysis.intents._observe_inputs as observe_inputs

    monkeypatch.setattr(
        observe_inputs,
        "_temporal_candidates",
        lambda catalog, metric_inputs: (
            {
                "sales.revenue": ("sales.users.signup_date",),
                "sales.user_count": ("sales.users.signup_date",),
            },
            {"sales.revenue": (), "sales.user_count": ()},
        ),
    )
    catalog = sales_session.catalog

    with pytest.raises(TemporalSuitabilityError) as exc_info:
        observe(
            [
                catalog.metrics.get("sales.revenue"),
                catalog.metrics.get("sales.user_count"),
            ],
            time_scope=WINDOW,
            grain=mv.grain("day"),
            time_dimension=catalog.time_dimensions.get("sales.orders.order_date"),
            session=sales_session,
        )

    error = exc_info.value
    assert error.repair is not None
    assert error.repair.candidates == (
        "sales.revenue -> sales.users.signup_date [incompatible]",
        "sales.user_count -> sales.users.signup_date [incompatible]",
    )
    # Both roots select signup_date implicitly, so omit actually converges.
    assert "omit time_dimension to auto-select a shared axis" in error.repair.action
    assert "split the metrics" not in error.repair.action


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
            grain=mv.grain("hour"),
            session=sales_session,
        )

    repair = exc_info.value.repair
    assert repair is not None
    assert repair.kind == "inspect"
    # The repair carries the per-axis split plan, not a grain-retry snippet
    # (kind="inspect" already rules out mechanical partial retry).
    assert repair.snippet is not None
    assert "Split into one observe() call per shared time dimension" in repair.snippet
    assert (
        'time_dimension=session.catalog.time_dimensions.get("sales.orders.order_date")'
        in repair.snippet
    )
    assert (
        'time_dimension=session.catalog.time_dimensions.get("sales.users.signup_date")'
        in repair.snippet
    )
    assert 'time_dimension="' not in repair.snippet
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
        grain=mv.grain("day"),
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
    findings = [f for f in frame.findings().items if f.finding_type == "metric_value"]
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
        grain=mv.grain("day"),
        session=sales_session,
    )

    assert frame.value_columns == ("revenue", "cumulative_revenue")
    assert frame.meta.metric_identities is not None


def test_multi_metric_conflicting_status_time_axes_fail_closed(sales_session, monkeypatch) -> None:
    """When two metric roots prefer different status time axes, multi-metric
    observe must fail closed with a repair instead of silently picking one
    (issue #36)."""
    import importlib

    observe_module = importlib.import_module("marivo.analysis.intents.observe")

    calls = 0

    def fake_preferred(catalog, metric_input):
        nonlocal calls
        calls += 1
        return "sales.orders.order_date" if calls == 1 else "sales.orders.status_at"

    monkeypatch.setattr(
        observe_module, "_preferred_status_time_dimension_for_metric", fake_preferred
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            [
                make_ref("sales.revenue", SemanticKind.METRIC),
                make_ref("sales.order_count", SemanticKind.METRIC),
            ],
            time_scope=WINDOW,
            grain=mv.grain("day"),
            session=sales_session,
        )

    error = exc_info.value
    assert error.location == "observe.time_dimension"
    assert "conflicting" in error.message
    assert error.repair is not None
    assert error.repair.action
    assert "time_dimension" in error.repair.action


def test_conflicting_fold_repairs_distinguish_runtime_and_authored_metrics(
    monkeypatch,
) -> None:
    """The same graph defect must route to its actual authoring owner."""
    import importlib
    from types import SimpleNamespace

    observe_module = importlib.import_module("marivo.analysis.intents.observe")
    revenue = make_ref("sales.revenue", SemanticKind.METRIC)
    inventory = make_ref("sales.inventory", SemanticKind.METRIC)

    class FakeDetails:
        def __init__(self, planned):
            self.planned = planned

    planned = {
        revenue.path: SimpleNamespace(
            semantic_id=revenue.path,
            time_fold="last",
            status_time_dimension="sales.orders.status_at",
            metric_type="simple",
            composition=None,
        ),
        inventory.path: SimpleNamespace(
            semantic_id=inventory.path,
            time_fold="last",
            status_time_dimension="sales.inventory.snapshot_at",
            metric_type="simple",
            composition=None,
        ),
    }
    root = SimpleNamespace(
        semantic_id="sales.authored_conflict",
        time_fold=None,
        status_time_dimension=None,
        metric_type="derived",
        composition=SimpleNamespace(
            components={"revenue": revenue.path, "inventory": inventory.path}
        ),
    )
    catalog = SimpleNamespace(_require_index=lambda: SimpleNamespace(registry=SimpleNamespace()))

    monkeypatch.setattr(observe_module, "SimpleMetricDetails", FakeDetails)
    monkeypatch.setattr(observe_module, "DerivedMetricDetails", FakeDetails)
    monkeypatch.setattr(
        observe_module,
        "normalize_metric_ref_input",
        lambda _catalog, value, *, argument: value,
    )
    monkeypatch.setattr(
        observe_module,
        "_normalize_metric_boundary",
        lambda _catalog, value: value.path,
    )
    monkeypatch.setattr(
        observe_module,
        "_catalog_object",
        lambda _catalog, semantic_id, _kind: SimpleNamespace(
            details=lambda: FakeDetails(planned[semantic_id])
        ),
    )
    monkeypatch.setattr(observe_module, "_planned_metric", lambda details: details.planned)

    runtime = mv.runtime_metric.linear(
        add=[revenue, inventory],
        subtract=[],
        label="runtime conflict",
    )
    with pytest.raises(SemanticKindMismatchError) as runtime_error:
        observe_module._preferred_status_time_dimension_for_metric(catalog, runtime)
    assert runtime_error.value.repair.help_target.surface == "analysis"
    assert runtime_error.value.repair.help_target.canonical_id == "runtime_metric"

    with pytest.raises(SemanticKindMismatchError) as authored_error:
        observe_module._preferred_status_time_dimension_for_metric(
            catalog,
            make_ref("sales.authored_conflict", SemanticKind.METRIC),
            metric_ir=root,
        )
    assert authored_error.value.repair.help_target.surface == "semantic"
    assert authored_error.value.repair.help_target.canonical_id == "objects.metric"
