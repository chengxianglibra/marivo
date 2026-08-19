"""MetricFrame arity accessors, gate, and projection."""

from datetime import datetime

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._compat import UTC
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
    return make_multi_frame_for(
        ("sales.revenue", "revenue"),
        ("sales.order_count", "order_count"),
    )


def make_multi_frame_for(*metrics: tuple[str, str]) -> MetricFrame:
    metric_ids = tuple(metric_id for metric_id, _column in metrics)
    meta = MetricFrameMeta(
        **make_test_multi_metric_contract(*metric_ids),
        metric_id=None,
        measure={},
        measures=[
            {
                "metric_id": metric_id,
                "name": metric_id.rsplit(".", 1)[-1],
                "column": column,
                "unit": "usd" if metric_id == "sales.revenue" else None,
                "additivity": "additive",
                "reaggregatable": True,
            }
            for metric_id, column in metrics
        ],
        **_meta_kwargs(),
    )
    values = {
        column: [float(index + 1), float(index + 2)]
        for index, (_metric_id, column) in enumerate(metrics)
    }
    df = pd.DataFrame({"bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02"]), **values})
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
            "unit_state": None,
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


def test_require_single_metric_error_carries_structured_repair_fields():
    """Issue #67: MetricArityError must fill expected/received and a typed
    repair so an agent can read the arity precondition and the canonical
    frame.metric(...) projection directly off the error object."""
    from marivo.analysis.errors import MetricArityError
    from marivo.analysis.intents._validate import require_single_metric

    with pytest.raises(MetricArityError) as excinfo:
        require_single_metric(make_multi_frame(), intent="compare")
    err = excinfo.value

    assert err.expected == "a single-metric frame (arity=1)"
    assert err.received == "arity=2 with metrics ['sales.revenue', 'sales.order_count']"
    assert err.location == "session.compare"
    assert err.repair is not None
    assert err.repair.kind == "retry"
    assert err.repair.help_target.surface == "analysis"
    assert err.repair.help_target.canonical_id == "MetricFrame.metric"
    assert err.repair.snippet == 'frame.metric("sales.revenue")'
    assert err.repair.candidates == ("sales.revenue", "sales.order_count")
    # The typed repair is rendered into the error string.
    assert "Repair:" in str(err)
    assert 'frame.metric("sales.revenue")' in str(err)
    assert "Help: marivo.help('analysis.MetricFrame.metric')" in str(err)


def test_assess_quality_rejects_multi_metric_frame_without_typed_bindings() -> None:
    from marivo.analysis.errors import FrameMetaInvalidError
    from marivo.analysis.intents.assess_quality import assess_quality

    session = mv.session.get_or_create(name="demo")
    with pytest.raises(FrameMetaInvalidError) as excinfo:
        assess_quality(make_multi_frame(), session=session)

    err = excinfo.value
    assert err.location == "session.assess_quality frame.meta.measure_bindings"
    assert err.repair is not None
    assert err.repair.snippet == "frame = session.observe(metrics=[metric_a, metric_b], ...)"


@pytest.mark.parametrize(
    ("binding_columns", "expected_received"),
    [
        (("revenue",), "measure_bindings=1, metric_identities=2"),
        (("does_not_exist", "order_count"), "missing_value_columns=['does_not_exist']"),
        (("revenue", "revenue"), "duplicate_value_columns=['revenue']"),
    ],
)
def test_assess_quality_rejects_malformed_multi_metric_bindings(
    binding_columns: tuple[str, ...],
    expected_received: str,
) -> None:
    from marivo.analysis._semantic_persistence import MeasureBindingV1
    from marivo.analysis.errors import FrameMetaInvalidError
    from marivo.analysis.intents.assess_quality import assess_quality

    session = mv.session.get_or_create(name="demo")
    frame = make_multi_frame()
    bindings = tuple(
        MeasureBindingV1(identity=identity, value_column=column)
        for identity, column in zip(
            frame.meta.metric_identities,
            binding_columns,
            strict=False,
        )
    )
    malformed = MetricFrame(
        _df=frame._dataframe_copy(),
        meta=frame.meta.model_copy(update={"measure_bindings": bindings}),
    )

    with pytest.raises(FrameMetaInvalidError) as excinfo:
        assess_quality(malformed, session=session)

    err = excinfo.value
    assert expected_received in err.received
    assert err.location == "session.assess_quality frame.meta.measure_bindings"
    assert err._context["metric_ids"] == ["sales.revenue", "sales.order_count"]


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


_PROJECTION_WINDOW = mv.time_scope(start="2026-07-01", end="2026-07-04")


def _fused(sales_session):
    catalog = sales_session.catalog
    return observe(
        [
            catalog.require(ref_factory.metric("sales.revenue")).ref,
            catalog.require(ref_factory.metric("sales.order_count")).ref,
        ],
        time_scope=_PROJECTION_WINDOW,
        grain=mv.grain("day"),
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
    assert list(revenue.columns) == ["bucket_start", "revenue"]
    assert list(revenue.to_pandas().columns) == revenue.columns
    assert [column.name for column in revenue.contract().artifact_schema.columns] == revenue.columns
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
        grain=mv.grain("day"),
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
    with pytest.raises(MetricArityError) as excinfo:
        compare(frame, frame, session=sales_session)
    # Issue #67: the real intent path must fail closed with structured fields
    # the agent can execute directly (expected/received + typed repair).
    err = excinfo.value
    assert err.expected == "a single-metric frame (arity=1)"
    assert err.repair is not None
    assert err.repair.help_target.canonical_id == "MetricFrame.metric"
    assert err.repair.snippet.startswith('frame.metric("')
    revenue = frame.metric("sales.revenue")
    delta = compare(revenue, revenue, session=sales_session)
    assert delta.meta.kind == "delta_frame"


def test_projected_frame_reloads_with_unit_state(sales_session):
    """Projection product must survive a cold reload with unit_state intact.

    Issue #54 P1 regression: the typed ``measures_meta()`` branch dropped the
    ``unit_state`` key, so the projected frame's binding carried ``None`` and
    ``load_frame`` rejected it as corrupt current-schema state.
    """
    from marivo.analysis.session._load import load_frame

    frame = _fused(sales_session)
    revenue = frame.metric("sales.revenue")
    # The projected binding must carry a typed unit_state (not None).
    assert revenue.meta.unit_state is not None
    assert revenue.meta.measure_bindings[0].unit_state is not None

    reloaded = load_frame(revenue.ref, session=sales_session)
    assert reloaded.meta.kind == "metric_frame"
    assert reloaded.meta.unit_state is not None
    assert reloaded.meta.measure_bindings[0].unit_state is not None


def test_projected_frame_measures_meta_has_unit_state(sales_session):
    """The projected frame's render dict must still expose unit_state."""
    frame = _fused(sales_session)
    revenue = frame.metric("sales.revenue")
    entry = revenue.measures_meta()[0]
    assert "unit_state" in entry
    assert entry["unit_state"] is not None


def test_legacy_projection_unit_state_is_typed_not_dict(sales_session):
    """A legacy multi-metric frame must project a *typed* unit_state.

    Issue #54 P2 regression: ``_semantic_unit_state``'s legacy fallback returned
    the canonical *dict* form persisted in compact ``measures``, so the same
    ``.metric()`` call produced a dict on the compute path but a typed object
    after a disk-backed reload — ``binding.unit_state.schema`` raised
    AttributeError on the cold path. The fallback must rebuild the typed
    ``MetricUnitStateV2``.
    """
    from marivo.semantic.metric_graph_canonical import canonical_value
    from marivo.semantic.unit_algebra import FactorizedUnitV2, UnknownUnitV2

    # Simulate a legacy v7 multi-metric artifact: the fused observe frame with
    # its typed bindings stripped, so projection falls back to the compact
    # ``measures`` dicts whose ``unit_state`` is the canonical dict form.
    fused = _fused(sales_session)
    legacy_meta = fused.meta.model_copy(
        update={
            "measure_bindings": (),
            "measures": [
                {
                    **dict(entry),
                    "unit_state": canonical_value(
                        FactorizedUnitV2(
                            schema="metric-unit-algebra/v2",
                            numerator=("kg",),
                            denominator=(),
                        )
                        if entry["metric_id"] == "sales.order_count"
                        else UnknownUnitV2(schema="metric-unit-unknown/v2")
                    ),
                }
                for entry in fused.meta.measures
            ],
        }
    )
    legacy = MetricFrame(_df=fused._df, meta=legacy_meta)
    assert legacy.meta.measure_bindings == ()

    projected = legacy.metric("sales.revenue")
    state = projected.meta.measure_bindings[0].unit_state
    assert isinstance(state, (FactorizedUnitV2, UnknownUnitV2))
    assert isinstance(state, UnknownUnitV2)
    assert state.schema == "metric-unit-unknown/v2"

    second = legacy.metric("sales.order_count")
    factorized = second.meta.measure_bindings[0].unit_state
    assert isinstance(factorized, FactorizedUnitV2)
    assert factorized.numerator == ("kg",)
    assert factorized.schema == "metric-unit-algebra/v2"

    # Cold/hot consistency: the projected artifact reloaded from disk must show
    # the same typed state, never a raw dict.
    from marivo.analysis.session._load import load_frame

    reloaded = load_frame(projected.ref, session=sales_session)
    reloaded_state = reloaded.meta.measure_bindings[0].unit_state
    assert isinstance(reloaded_state, (FactorizedUnitV2, UnknownUnitV2))
    assert type(reloaded_state) is type(state)


def test_metric_malformed_legacy_unit_state_raises_analysis_error(sales_session):
    """A malformed legacy ``unit_state`` must fail closed as an AnalysisError.

    Issue #57 review P2: ``unit_state_from_dict`` raises the semantic-layer
    ``UnitStatePayloadError``; on the public ``.metric()`` surface that must be
    wrapped into an analysis typed error (with repair/kind/hint), not escape as
    a bare ``ValueError``.
    """
    from marivo.analysis.errors import AnalysisError

    fused = _fused(sales_session)
    legacy_meta = fused.meta.model_copy(
        update={
            "measure_bindings": (),
            "measures": [
                {
                    **dict(entry),
                    # A forward schema that unit_state_from_dict rejects.
                    "unit_state": {
                        "schema": "metric-unit-algebra/v3",
                        "numerator": [],
                        "denominator": [],
                    },
                }
                for entry in fused.meta.measures
            ],
        }
    )
    legacy = MetricFrame(_df=fused._df, meta=legacy_meta)

    with pytest.raises(AnalysisError, match="unit state payload") as exc_info:
        legacy.metric("sales.revenue")

    from marivo.analysis.errors import FrameCacheCorruptedError

    assert isinstance(exc_info.value, FrameCacheCorruptedError)
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.action
    # Issue #63 review P2: the repair snippet must be built from the real
    # parent frame ref (not the measure's display name), and the measure name
    # must be preserved in its own context key.
    assert exc_info.value._context["ref"] == legacy.ref
    assert exc_info.value._context["measure"] == "revenue"
    assert legacy.ref in exc_info.value.repair.snippet


# ---------------------------------------------------------------------------
# Task 8: arity-aware _card and contract preconditions
# ---------------------------------------------------------------------------


def test_multi_frame_render_lists_measures():
    frame = make_multi_frame()
    text = frame.render()
    assert "sales.revenue" in text
    assert "sales.order_count" in text


def test_multi_frame_contract_keeps_quality_while_gating_single_metric_intents():
    frame = make_multi_frame()
    contract = frame.contract()
    compare = next(item for item in contract.affordances if item.capability_id == "compare")
    compare_checks = {precondition.check for precondition in compare.preconditions}
    assert "single_metric" in compare_checks
    unmet = next(
        precondition
        for precondition in compare.preconditions
        if precondition.check == "single_metric"
    )
    assert unmet.status == "fail"
    assert unmet.repair is None
    assert tuple(repair.snippet for repair in unmet.repair_options) == (
        'frame.metric("sales.revenue")',
        'frame.metric("sales.order_count")',
    )

    quality = next(item for item in contract.affordances if item.capability_id == "assess_quality")
    assert "single_metric" not in {precondition.check for precondition in quality.preconditions}

    quality_requirement = next(
        requirement
        for requirement in quality.input_requirements
        if requirement.parameter == "frame"
    )
    assert quality_requirement.accepted_semantic_shapes == (
        "panel",
        "scalar",
        "segmented",
        "time_series",
    )


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (
            make_multi_frame(),
            ("sales.revenue", "sales.order_count"),
        ),
        (
            make_multi_frame_for(
                ("sales.revenue", "revenue"),
                ("sales.order_count", "order_count"),
                ("sales.average_order_value", "average_order_value"),
            ),
            ("sales.revenue", "sales.order_count", "sales.average_order_value"),
        ),
        (
            make_multi_frame_for(
                ("sales.revenue", "sales_revenue"),
                ("finance.revenue", "finance_revenue"),
            ),
            ("sales.revenue", "finance.revenue"),
        ),
    ],
)
def test_multi_metric_contract_lists_every_full_id_projection_once(
    frame: MetricFrame,
    expected: tuple[str, ...],
) -> None:
    affordance = next(
        item for item in frame.contract().affordances if item.capability_id == "compare"
    )
    precondition = next(item for item in affordance.preconditions if item.check == "single_metric")

    assert tuple(repair.snippet for repair in precondition.repair_options) == tuple(
        f'frame.metric("{metric_id}")' for metric_id in expected
    )
    assert len(precondition.repair_options) == len(set(expected))
    rendered = frame.contract().render()
    for metric_id in expected:
        assert rendered.count(f'frame.metric("{metric_id}")') >= 1


def test_every_emitted_projection_snippet_executes_with_frame_bound(sales_session) -> None:
    frame = _fused(sales_session)
    affordance = next(
        item for item in frame.contract().affordances if item.capability_id == "compare"
    )
    precondition = next(item for item in affordance.preconditions if item.check == "single_metric")

    for repair in precondition.repair_options:
        assert repair.snippet is not None
        projected = eval(repair.snippet, {}, {"frame": frame})
        assert projected.arity == 1
        assert projected.metrics == (repair.snippet.split('"')[1],)


def test_single_frame_contract_has_no_arity_precondition():
    frame = make_single_frame()
    contract = frame.contract()
    for affordance in contract.affordances:
        assert all(p.check != "single_metric" for p in affordance.preconditions)
