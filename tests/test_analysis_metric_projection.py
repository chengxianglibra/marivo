"""MetricFrame arity accessors, gate, and projection."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.refs import ref as ref_factory
from tests.shared_fixtures import (
    make_test_metric_meta_contract,
    make_test_multi_metric_contract,
)


def _lineage() -> Lineage:
    return Lineage(
        steps=[
            LineageStep(
                intent="observe",
                job_ref="job_test",
                inputs=[],
                params_digest="sha256:0",
                analysis_purpose=None,
                params={},
            )
        ]
    )


def _meta_kwargs() -> dict:
    return {
        "kind": "metric_frame",
        "ref": "frame_test",
        "session_id": "sess_test",
        "project_root": "/tmp/proj",
        "produced_by_job": "job_test",
        "analysis_purpose": None,
        "created_at": datetime.now(UTC),
        "row_count": 2,
        "byte_size": 0,
        "lineage": _lineage(),
        "axes": {"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        "window": None,
        "where": {},
        "semantic_kind": "time_series",
        "semantic_model": "sales",
    }


def make_single_frame() -> MetricFrame:
    meta = MetricFrameMeta(
        **make_test_metric_meta_contract("sales.revenue"),
        metric_id="sales.revenue",
        measure={"name": "revenue"},
        unit="usd",
        **_meta_kwargs(),
    )
    df = pd.DataFrame(
        {"bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02"]), "value": [1.0, 2.0]}
    )
    return MetricFrame(_df=df, meta=meta)


def make_multi_frame() -> MetricFrame:
    meta = MetricFrameMeta(
        **make_test_multi_metric_contract("sales.revenue", "sales.order_count"),
        metric_id=None,
        measure={},
        measures=[
            {
                "metric_id": "sales.revenue",
                "name": "revenue",
                "column": "revenue",
                "unit": "usd",
                "additivity": "additive",
                "reaggregatable": True,
            },
            {
                "metric_id": "sales.order_count",
                "name": "order_count",
                "column": "order_count",
                "unit": None,
                "additivity": "additive",
                "reaggregatable": True,
            },
        ],
        **_meta_kwargs(),
    )
    df = pd.DataFrame(
        {
            "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02"]),
            "revenue": [10.0, 50.0],
            "order_count": [1, 2],
        }
    )
    return MetricFrame(_df=df, meta=meta)


def test_single_frame_metrics_and_arity():
    frame = make_single_frame()
    assert frame.metrics == ("sales.revenue",)
    assert frame.arity == 1


def test_single_frame_measures_meta_derived_from_scalars():
    frame = make_single_frame()
    entries = frame.measures_meta()
    assert entries == [
        {
            "metric_id": "sales.revenue",
            "name": "revenue",
            "column": "value",
            "unit": "usd",
            "additivity": None,
            "aggregation": None,
            "status_time_dimension": None,
            "reaggregatable": True,
            "cumulative": None,
        }
    ]


def test_multi_frame_metrics_and_arity():
    frame = make_multi_frame()
    assert frame.metrics == ("sales.revenue", "sales.order_count")
    assert frame.arity == 2


def test_multi_frame_repr_reports_metric_count():
    frame = make_multi_frame()
    text = repr(frame)
    assert "metrics=2" in text
    assert ".show()" in text


def test_single_frame_repr_unchanged():
    frame = make_single_frame()
    assert "metric=sales.revenue" in repr(frame)


def test_legacy_meta_without_measures_field_loads():
    # Legacy persisted frames have metric_id set and no measures key.
    meta = MetricFrameMeta(
        **make_test_metric_meta_contract("sales.revenue"),
        metric_id="sales.revenue",
        measure={"name": "revenue"},
        **_meta_kwargs(),
    )
    assert meta.measures is None


# ---------------------------------------------------------------------------
# Arity gate: require_single_metric + MetricArityError
# ---------------------------------------------------------------------------


def test_require_single_metric_passes_arity_1():
    from marivo.analysis.intents._validate import require_single_metric

    require_single_metric(make_single_frame(), intent="compare")


def test_require_single_metric_raises_teaching_error():
    from marivo.analysis.errors import MetricArityError
    from marivo.analysis.intents._validate import require_single_metric

    with pytest.raises(MetricArityError) as excinfo:
        require_single_metric(make_multi_frame(), intent="compare")
    err = excinfo.value
    assert err._context["intent"] == "compare"
    assert err._context["got_arity"] == 2
    assert err._context["metrics"] == ["sales.revenue", "sales.order_count"]
    assert 'frame.metric("sales.revenue")' in str(err)


# ---------------------------------------------------------------------------
# Task 7: frame.metric(id) projection — committed select_metric step.
# ---------------------------------------------------------------------------
# These tests require the DuckDB session; the _chdir and sales_session fixtures
# are duplicated locally (matching tests/test_analysis_observe_multi_metric.py).

import ibis  # noqa: E402

import marivo.analysis.session as session_attach  # noqa: E402
from marivo.analysis.intents.observe import observe  # noqa: E402
from tests.shared_fixtures import (  # noqa: E402
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


_PROJECTION_WINDOW = {"start": "2026-07-01", "end": "2026-07-04"}


def _fused(sales_session):
    catalog = sales_session.catalog
    return observe(
        [
            catalog.require(ref_factory.metric("sales.revenue")).ref,
            catalog.require(ref_factory.metric("sales.order_count")).ref,
        ],
        time_scope=_PROJECTION_WINDOW,
        grain="day",
        session=sales_session,
    )


def test_projection_returns_arity_1_frame(sales_session):
    frame = _fused(sales_session)
    revenue = frame.metric("sales.revenue")
    assert revenue.arity == 1
    assert revenue.meta.metric_id == "sales.revenue"
    assert revenue.meta.unit == frame.meta.measures[0]["unit"]
    assert revenue.meta.additivity == frame.meta.measures[0]["additivity"]
    assert revenue.meta.aggregation == frame.meta.measures[0]["aggregation"]
    assert revenue.meta.status_time_dimension == frame.meta.measures[0]["status_time_dimension"]
    assert list(revenue.columns) == ["bucket_start", "value"]
    assert revenue.meta.lineage.steps[-1].intent == "select_metric"
    assert revenue.meta.lineage.steps[-1].params == {
        "replay_expression": {
            "schema": "marivo.runtime_metric_expr/v1",
            "kind": "metric_ref",
            "metric_ref": {
                "schema": "marivo.semantic_ref/v1",
                "kind": "metric",
                "path": "sales.revenue",
            },
        },
    }


def test_projection_on_arity_1_returns_self(sales_session):
    catalog = sales_session.catalog
    single = observe(
        catalog.require(ref_factory.metric("sales.revenue")).ref,
        time_scope=_PROJECTION_WINDOW,
        grain="day",
        session=sales_session,
    )
    assert single.metric("sales.revenue") is single


def test_projection_unknown_metric_teaches(sales_session):
    from marivo.analysis.errors import MetricArityError

    frame = _fused(sales_session)
    with pytest.raises(MetricArityError) as excinfo:
        frame.metric("sales.gmv")
    assert "sales.revenue" in str(excinfo.value)


def test_projection_is_idempotent(sales_session):
    frame = _fused(sales_session)
    first = frame.metric("sales.revenue")
    second = frame.metric("sales.revenue")
    assert first.meta.artifact_id == second.meta.artifact_id


def test_projection_emits_no_value_findings(sales_session):
    frame = _fused(sales_session)
    projected = frame.metric("sales.revenue")
    findings = sales_session.evidence.findings(artifact_ref=projected.meta.artifact_id)
    assert findings.items == ()
    assert projected.meta.evidence_status == "complete"
    assert projected.meta.issues == ()


def test_projected_frame_flows_into_compare(sales_session):
    from marivo.analysis.errors import MetricArityError
    from marivo.analysis.intents.compare import compare

    frame = _fused(sales_session)
    with pytest.raises(MetricArityError):
        compare(frame, frame, session=sales_session)
    revenue = frame.metric("sales.revenue")
    delta = compare(revenue, revenue, session=sales_session)
    assert delta.meta.kind == "delta_frame"


# ---------------------------------------------------------------------------
# Task 8: arity-aware _card and contract preconditions
# ---------------------------------------------------------------------------


def test_multi_frame_render_lists_measures():
    frame = make_multi_frame()
    text = frame.render()
    assert "sales.revenue" in text
    assert "sales.order_count" in text


def test_multi_frame_contract_marks_single_metric_gate():
    frame = make_multi_frame()
    contract = frame.contract()
    compare_affordance = next(a for a in contract.affordances if a.capability_id == "compare")
    checks = {p.check for p in compare_affordance.preconditions}
    assert "single_metric" in checks
    unmet = next(p for p in compare_affordance.preconditions if p.check == "single_metric")
    assert unmet.status == "fail"
    assert 'metric("sales.revenue")' in (unmet.reason or "")


def test_single_frame_contract_has_no_arity_precondition():
    frame = make_single_frame()
    contract = frame.contract()
    for affordance in contract.affordances:
        assert all(p.check != "single_metric" for p in affordance.preconditions)
