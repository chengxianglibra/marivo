"""Intent gates for cumulative frames."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import ibis
import pandas as pd
import pytest
from pydantic import ValidationError

import marivo.analysis.session as session_attach
from marivo.analysis._cumulative import (
    BASELINE_EVALUATION_END_COLUMN,
    CURRENT_EVALUATION_END_COLUMN,
    EVALUATION_END_COLUMN,
    CumulativeAlignmentV1,
    canonical_comparable_period_anchor,
    canonical_cumulative_expression_fingerprint,
)
from marivo.analysis.errors import (
    AnalysisError,
    AttributionMaterializationError,
    CumulativeFrameUnsupportedError,
)
from marivo.analysis.frames.attribution import validate_cumulative_flow_attribution_rows
from marivo.analysis.frames.delta import (
    CumulativeDeltaFrameMetaV1,
    DeltaFrame,
    DeltaFrameMeta,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents.attribute import attribute
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.decompose import decompose
from marivo.analysis.intents.forecast import forecast
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import CalendarRef
from marivo.analysis.session._runtime import persist_frame
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeEquivalentComparisonSemanticsV1,
    CumulativeNodeV1,
    ExactComparisonSemanticsV1,
    ExpressionOccurrenceV1,
    MetricExpressionGraphV1,
    RatioNodeV1,
    SliceNodeV1,
)
from marivo.semantic.metric_graph_canonical import fingerprint, intern_nodes
from tests.shared_fixtures import make_metric_frame, make_test_delta_contract


def _cum_marker() -> dict:
    return {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": "sales.orders.order_date",
        "anchor": "all_history",
        "components": None,
    }


def _bootstrap_project(tmp_path) -> None:
    """Create a minimal semantic project on disk for analysis tests."""
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Data')\n",
        encoding="utf-8",
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True)
    (calendar_dir / "cn_holidays.json").write_text(
        json.dumps(
            {
                "name": "cn_holidays",
                "holidays": [
                    {"date": "2025-05-01", "holiday_id": "labor-day"},
                    {"date": "2026-05-01", "holiday_id": "labor-day"},
                    {"date": "2026-04-30", "holiday_id": "labor-day"},
                    {"date": "2026-05-02", "holiday_id": "other-day"},
                ],
                "adjusted_workdays": [],
            }
        ),
        encoding="utf-8",
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "order_date = ms.time_dimension_column("
        "name='order_date', entity=orders, column='created_at', granularity='day')\n"
        "region = ms.dimension_column(name='region', entity=orders, column='region')\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity='additive', unit='USD')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "cum_gmv = ms.cumulative(name='cum_gmv', base=gmv, over=order_date)\n"
        "mtd_gmv = ms.cumulative(name='mtd_gmv', base=gmv, over=order_date, "
        "anchor=ms.grain_to_date(grain='month'))\n"
        "trailing_2d_gmv = ms.cumulative(name='trailing_2d_gmv', base=gmv, "
        "over=order_date, anchor=ms.trailing(count=2, unit='day'))\n",
        encoding="utf-8",
    )


def _seed(con) -> None:
    con.create_table(
        "orders",
        pd.DataFrame(
            {
                "order_id": [1, 2, 3, 4, 5, 6, 7],
                "created_at": pd.to_datetime(
                    [
                        "2026-06-01",
                        "2026-06-02",
                        "2026-07-01",
                        "2026-07-02",
                        "2026-07-02",
                        "2026-07-03",
                        "2026-07-03",
                    ]
                ),
                "amount": [4.0, 6.0, 10.0, 12.0, 5.0, 18.0, 7.0],
                "region": ["US", "CA", "US", "US", "CA", "CA", "EU"],
            }
        ),
        overwrite=True,
    )


def _session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _bootstrap_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    return session_attach.get_or_create(
        name="cum_gates",
        report_timezone="UTC",
        backends={"warehouse": lambda: con},
    )


def _history(session):
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                "value": [10.0, 12.0, 18.0],
            }
        ),
        metric_id="sales.cum_gmv",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "cum_gmv"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-04", "grain": "day"},
        session=session,
    )
    frame.meta = frame.meta.model_copy(update={"cumulative": _cum_marker()})
    return frame


def _now():
    return datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC)


def _delta(session, *, cumulative: dict | None = None) -> DeltaFrame:
    meta = DeltaFrameMeta(
        **make_test_delta_contract("sales.cum_gmv"),
        kind="delta_frame",
        ref="frame_delta",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_delta",
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="compare",
                    job_ref="job_delta",
                    inputs=["frame_a", "frame_b"],
                    params_digest="sha256:compare",
                )
            ]
        ),
        metric_id="sales.cum_gmv",
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={"kind": "window_bucket"},
        semantic_kind="segmented",
        semantic_model="sales",
        cumulative=cumulative,
    )
    return DeltaFrame(_df=pd.DataFrame({"region": ["US"], "delta": [1.0]}), meta=meta)


def test_compare_requires_evaluation_contract_for_cumulative_metric_frame(
    tmp_path, monkeypatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _history(session)
    baseline = _history(session)

    with pytest.raises(AnalysisError) as exc_info:
        compare(current, baseline, session=session)

    assert exc_info.value._context["kind"] == "CumulativeEvaluationEndMissing"


def test_forecast_rejects_cumulative_history(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    history = _history(session)

    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        forecast(history, horizon=2, session=session)

    assert "forecast the base flow" in exc_info.value.hint.lower()


def test_decompose_rejects_cumulative_delta(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    delta = _delta(session, cumulative=_cum_marker())

    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        decompose(delta, axis="sales.orders.region", session=session)

    assert exc_info.value._context["intent"] == "decompose"
    assert exc_info.value._context["base_metric_id"] == "sales.gmv"


def test_attribute_rejects_legacy_cumulative_delta_schema(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    delta = _delta(session, cumulative=_cum_marker())
    region = ref_factory.dimension("sales.orders.region")

    with pytest.raises(AttributionMaterializationError) as exc_info:
        attribute(delta, axes=[region], session=session)

    assert exc_info.value._context["recoverability_status"] == "unsupported_artifact_schema"


# ---------------------------------------------------------------------------
# Task 10: compare to-date alignment (anchor-dispatched gate)
# ---------------------------------------------------------------------------


def _cum_marker_anchor(anchor: object) -> dict:
    """Cumulative marker with a specific anchor payload."""
    return {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": "sales.orders.order_date",
        "anchor": anchor,
        "components": None,
    }


def _attach_direct_cumulative_contract(
    session, frame: MetricFrame, *, anchor: object
) -> MetricFrame:
    """Attach one complete current cumulative graph to a synthetic metric frame."""

    base_node = AggregateNodeV1(
        kind="aggregate",
        target_ref=RefPayloadV1.from_ref(ref_factory.measure("sales.orders.amount")),
        dependency_fingerprint=fingerprint(("test-base", "sales.orders.amount")),
        agg="sum",
        fold=None,
    )
    base_id = fingerprint(base_node)
    cumulative_node = CumulativeNodeV1(
        kind="cumulative",
        child_id=base_id,
        time_dimension_ref=RefPayloadV1.from_ref(
            ref_factory.time_dimension("sales.orders.order_date")
        ),
        anchor=anchor,
        dependency_fingerprint=fingerprint(("test-time", "sales.orders.order_date")),
    )
    cumulative_id = fingerprint(cumulative_node)
    graph = MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(cumulative_id,),
        nodes=intern_nodes((base_node, cumulative_node)),
        occurrences=(
            ExpressionOccurrenceV1(
                path="root[0]",
                node_id=cumulative_id,
                child_paths=("root[0].base",),
            ),
            ExpressionOccurrenceV1(path="root[0].base", node_id=base_id),
        ),
    )
    comparable = frame.meta.comparable_value_semantics
    assert comparable is not None
    comparable_payload = {
        "expression_fingerprint": cumulative_id,
        "evaluator_contracts": comparable.evaluator_contracts,
        "global_slice": comparable.global_slice,
        "key_schema_fingerprint": comparable.key_schema_fingerprint,
        "unit": comparable.unit,
        "fold": comparable.fold,
        "source_domain_fingerprint": comparable.source_domain_fingerprint,
        "definition_transform_fingerprint": comparable.definition_transform_fingerprint,
    }
    comparable = replace(
        comparable,
        expression_fingerprint=cumulative_id,
        fingerprint=fingerprint(comparable_payload),
    )
    frame.meta = frame.meta.model_copy(
        update={
            "expression_graph": graph,
            "expression_fingerprint": cumulative_id,
            "comparable_value_semantics": comparable,
            "cumulative": _cum_marker_anchor(anchor),
        }
    )
    frame.meta = persist_frame(session, frame)
    return frame


def _attach_ratio_cumulative_contract(
    session, frame: MetricFrame, *, anchor: object
) -> MetricFrame:
    """Attach a real ratio-of-cumulative-components graph to a synthetic frame."""

    time_ref = RefPayloadV1.from_ref(ref_factory.time_dimension("sales.orders.order_date"))
    aggregates = (
        AggregateNodeV1(
            kind="aggregate",
            target_ref=RefPayloadV1.from_ref(ref_factory.measure("sales.orders.amount")),
            dependency_fingerprint=fingerprint(("test-base", "amount")),
            agg="sum",
            fold=None,
        ),
        AggregateNodeV1(
            kind="aggregate",
            target_ref=RefPayloadV1.from_ref(ref_factory.measure("sales.orders.amount")),
            dependency_fingerprint=fingerprint(("test-base", "count")),
            agg="count",
            fold=None,
        ),
    )
    aggregate_ids = tuple(fingerprint(node) for node in aggregates)
    cumulative_nodes = tuple(
        CumulativeNodeV1(
            kind="cumulative",
            child_id=child_id,
            time_dimension_ref=time_ref,
            anchor=anchor,
            dependency_fingerprint=fingerprint(("test-time", "sales.orders.order_date")),
        )
        for child_id in aggregate_ids
    )
    cumulative_ids = tuple(fingerprint(node) for node in cumulative_nodes)
    ratio_node = RatioNodeV1(
        kind="ratio",
        numerator_id=cumulative_ids[0],
        denominator_id=cumulative_ids[1],
        zero_division="null",
    )
    ratio_id = fingerprint(ratio_node)
    graph = MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(ratio_id,),
        nodes=intern_nodes((*aggregates, *cumulative_nodes, ratio_node)),
        occurrences=(
            ExpressionOccurrenceV1(
                path="root[0]",
                node_id=ratio_id,
                child_paths=("root[0].numerator", "root[0].denominator"),
            ),
            ExpressionOccurrenceV1(
                path="root[0].numerator",
                node_id=cumulative_ids[0],
                child_paths=("root[0].numerator.base",),
            ),
            ExpressionOccurrenceV1(path="root[0].numerator.base", node_id=aggregate_ids[0]),
            ExpressionOccurrenceV1(
                path="root[0].denominator",
                node_id=cumulative_ids[1],
                child_paths=("root[0].denominator.base",),
            ),
            ExpressionOccurrenceV1(path="root[0].denominator.base", node_id=aggregate_ids[1]),
        ),
    )
    comparable = frame.meta.comparable_value_semantics
    assert comparable is not None
    comparable_payload = {
        "expression_fingerprint": ratio_id,
        "evaluator_contracts": comparable.evaluator_contracts,
        "global_slice": comparable.global_slice,
        "key_schema_fingerprint": comparable.key_schema_fingerprint,
        "unit": comparable.unit,
        "fold": comparable.fold,
        "source_domain_fingerprint": comparable.source_domain_fingerprint,
        "definition_transform_fingerprint": comparable.definition_transform_fingerprint,
    }
    component = _cum_marker_anchor(anchor)
    frame.meta = frame.meta.model_copy(
        update={
            "expression_graph": graph,
            "expression_fingerprint": ratio_id,
            "comparable_value_semantics": replace(
                comparable,
                expression_fingerprint=ratio_id,
                fingerprint=fingerprint(comparable_payload),
            ),
            "cumulative": {
                "kind": "derived_contains_cumulative",
                "anchor": anchor,
                "compare_blocker": None,
                "components": {"numerator": component, "denominator": component},
            },
        }
    )
    frame.meta = persist_frame(session, frame)
    return frame


def _ts_frame(
    session,
    *,
    bucket_starts: list[str],
    values: list[float],
    window_start: str,
    window_end: str,
    grain: str = "day",
    metric_id: str = "sales.cum_gmv",
    anchor: object = "all_history",
    regions: list[str] | None = None,
) -> MetricFrame:
    """Build a persisted time_series MetricFrame carrying a cumulative marker."""
    data = {
        "bucket_start": pd.to_datetime(bucket_starts),
        "value": values,
    }
    axes: dict[str, object] = {"time": {"role": "time", "column": "bucket_start", "grain": grain}}
    axes["time"] = {
        **axes["time"],
        "time_dimension": "sales.orders.order_date",
    }
    semantic_kind = "time_series"
    if regions is not None:
        data["region"] = regions
        axes["region"] = {"role": "dimension", "column": "region"}
        semantic_kind = "panel"
    if anchor == "all_history":
        window_end_ts = pd.Timestamp(window_end, tz="UTC")
        data[EVALUATION_END_COLUMN] = [
            min(pd.Timestamp(bucket, tz="UTC") + pd.Timedelta(days=1), window_end_ts)
            for bucket in bucket_starts
        ]
    frame = make_metric_frame(
        pd.DataFrame(data),
        metric_id=metric_id,
        axes=axes,
        measure={"name": "cum_gmv"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        window={"start": window_start, "end": window_end, "grain": grain},
        aggregation="sum",
        session=session,
    )
    return _attach_direct_cumulative_contract(session, frame, anchor=anchor)


def _all_history_shape_frame(
    session,
    *,
    semantic_kind: str,
    rows: list[dict[str, object]],
    window_start: str,
    window_end: str,
) -> MetricFrame:
    """Build a persisted all-history frame with explicit canonical cutoffs."""

    axes: dict[str, object] = {}
    if semantic_kind in {"time_series", "panel"}:
        axes["time"] = {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "order_date",
        }
    if semantic_kind in {"segmented", "panel"}:
        axes["region"] = {"role": "dimension", "column": "region"}
    frame = make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.cum_gmv",
        axes=axes,
        measure={"name": "value"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        window={"start": window_start, "end": window_end, "grain": "day"},
        aggregation="sum",
        session=session,
    )
    return _attach_direct_cumulative_contract(session, frame, anchor="all_history")


@pytest.mark.parametrize(
    ("semantic_kind", "current_rows", "baseline_rows", "expected_deltas"),
    [
        (
            "scalar",
            [{"value": 15.0, EVALUATION_END_COLUMN: pd.Timestamp("2026-07-04", tz="UTC")}],
            [{"value": 10.0, EVALUATION_END_COLUMN: pd.Timestamp("2026-06-04", tz="UTC")}],
            [5.0],
        ),
        (
            "segmented",
            [
                {
                    "region": "US",
                    "value": 15.0,
                    EVALUATION_END_COLUMN: pd.Timestamp("2026-07-04", tz="UTC"),
                }
            ],
            [
                {
                    "region": "US",
                    "value": 10.0,
                    EVALUATION_END_COLUMN: pd.Timestamp("2026-06-04", tz="UTC"),
                }
            ],
            [5.0],
        ),
        (
            "time_series",
            [
                {
                    "bucket_start": pd.Timestamp("2026-07-01"),
                    "value": 15.0,
                    EVALUATION_END_COLUMN: pd.Timestamp("2026-07-02", tz="UTC"),
                },
                {
                    "bucket_start": pd.Timestamp("2026-07-02"),
                    "value": 22.0,
                    EVALUATION_END_COLUMN: pd.Timestamp("2026-07-03", tz="UTC"),
                },
            ],
            [
                {
                    "bucket_start": pd.Timestamp("2026-06-01"),
                    "value": 10.0,
                    EVALUATION_END_COLUMN: pd.Timestamp("2026-06-02", tz="UTC"),
                },
                {
                    "bucket_start": pd.Timestamp("2026-06-02"),
                    "value": 18.0,
                    EVALUATION_END_COLUMN: pd.Timestamp("2026-06-03", tz="UTC"),
                },
            ],
            [5.0, 4.0],
        ),
        (
            "panel",
            [
                {
                    "bucket_start": pd.Timestamp("2026-07-01"),
                    "region": "US",
                    "value": 15.0,
                    EVALUATION_END_COLUMN: pd.Timestamp("2026-07-02", tz="UTC"),
                }
            ],
            [
                {
                    "bucket_start": pd.Timestamp("2026-06-01"),
                    "region": "US",
                    "value": 10.0,
                    EVALUATION_END_COLUMN: pd.Timestamp("2026-06-02", tz="UTC"),
                }
            ],
            [5.0],
        ),
    ],
)
def test_compare_all_history_all_shapes_persist_exact_endpoint_evidence(
    tmp_path,
    monkeypatch,
    semantic_kind,
    current_rows,
    baseline_rows,
    expected_deltas,
) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _all_history_shape_frame(
        session,
        semantic_kind=semantic_kind,
        rows=current_rows,
        window_start="2026-07-01",
        window_end="2026-07-03",
    )
    baseline = _all_history_shape_frame(
        session,
        semantic_kind=semantic_kind,
        rows=baseline_rows,
        window_start="2026-06-01",
        window_end="2026-06-03",
    )

    delta = compare(current, baseline, session=session)
    recovered = session.get_frame(delta.ref)
    recovered_df = recovered.to_pandas()

    assert recovered_df["delta"].tolist() == pytest.approx(expected_deltas)
    assert recovered.meta.cumulative_change.model_dump(mode="json") == {
        "schema": "all-history-level-change/v1"
    }
    assert CURRENT_EVALUATION_END_COLUMN in recovered_df
    assert BASELINE_EVALUATION_END_COLUMN in recovered_df
    findings = session.evidence.findings(artifact_ref=delta.ref).items
    assert len(findings) == len(recovered_df)
    expected_endpoint_pairs = sorted(
        (
            pd.Timestamp(current_end).isoformat(),
            pd.Timestamp(baseline_end).isoformat(),
        )
        for current_end, baseline_end in zip(
            recovered_df[CURRENT_EVALUATION_END_COLUMN],
            recovered_df[BASELINE_EVALUATION_END_COLUMN],
            strict=True,
        )
    )
    actual_endpoint_pairs = sorted(
        (finding.value.current_evaluation_end, finding.value.baseline_evaluation_end)
        for finding in findings
    )
    assert actual_endpoint_pairs == expected_endpoint_pairs


def test_compare_all_history_drops_one_sided_and_retains_matched_null(
    tmp_path, monkeypatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _all_history_shape_frame(
        session,
        semantic_kind="segmented",
        rows=[
            {
                "region": "MATCHED_NULL",
                "value": None,
                EVALUATION_END_COLUMN: pd.Timestamp("2026-07-04", tz="UTC"),
            },
            {
                "region": "CURRENT_ONLY",
                "value": 20.0,
                EVALUATION_END_COLUMN: pd.Timestamp("2026-07-04", tz="UTC"),
            },
        ],
        window_start="2026-07-01",
        window_end="2026-07-04",
    )
    baseline = _all_history_shape_frame(
        session,
        semantic_kind="segmented",
        rows=[
            {
                "region": "MATCHED_NULL",
                "value": 10.0,
                EVALUATION_END_COLUMN: pd.Timestamp("2026-06-04", tz="UTC"),
            },
            {
                "region": "BASELINE_ONLY",
                "value": 30.0,
                EVALUATION_END_COLUMN: pd.Timestamp("2026-06-04", tz="UTC"),
            },
        ],
        window_start="2026-06-01",
        window_end="2026-06-04",
    )

    delta = compare(current, baseline, session=session)
    df = delta.to_pandas()
    pair_info = delta.meta.alignment["cumulative_pairs"]

    assert df["region"].tolist() == ["MATCHED_NULL"]
    assert df["presence_status"].tolist() == ["matched"]
    assert pd.isna(df["delta"].iloc[0])
    assert pair_info == {
        "anchor": "all_history",
        "matched_rows": 1,
        "matched_null_rows": 1,
        "current_unpaired_rows": 1,
        "baseline_unpaired_rows": 1,
        "unpaired_action": "dropped",
    }
    typed_pair_info = delta.meta.all_history_pair_alignment()
    assert typed_pair_info is not None
    assert typed_pair_info.model_dump(mode="json") == pair_info
    invalid_meta = delta.meta.model_dump(mode="json")
    invalid_pairs = invalid_meta["alignment"]["cumulative_pairs"]
    invalid_pairs["matching_rows"] = invalid_pairs.pop("matched_rows")
    with pytest.raises(ValueError, match="matched_rows"):
        CumulativeDeltaFrameMetaV1.model_validate(invalid_meta)
    finding = session.evidence.findings(artifact_ref=delta.ref).items[0]
    assert finding.value.presence is None
    assert finding.value.magnitude is None
    assert finding.value.matched_rows == 1
    assert finding.value.matched_null_rows == 1
    assert finding.value.current_unpaired_rows == 1
    assert finding.value.baseline_unpaired_rows == 1
    assert finding.value.unpaired_action == "dropped"
    assert finding.value.cumulative_change == "all-history-level-change/v1"
    assert finding.value.source_revision == "unverified"
    assert finding.value.interval_flow_equivalence == "not_asserted"
    rendered = delta.render()
    assert "matched_null_rows=1" in rendered
    assert "current_unpaired_rows=1" in rendered
    assert "baseline_unpaired_rows=1" in rendered


def test_compare_all_history_no_paired_coordinates_has_structured_repair(
    tmp_path, monkeypatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _all_history_shape_frame(
        session,
        semantic_kind="segmented",
        rows=[
            {
                "region": "CURRENT_ONLY",
                "value": 20.0,
                EVALUATION_END_COLUMN: pd.Timestamp("2026-07-04", tz="UTC"),
            }
        ],
        window_start="2026-07-01",
        window_end="2026-07-04",
    )
    baseline = _all_history_shape_frame(
        session,
        semantic_kind="segmented",
        rows=[
            {
                "region": "BASELINE_ONLY",
                "value": 30.0,
                EVALUATION_END_COLUMN: pd.Timestamp("2026-06-04", tz="UTC"),
            }
        ],
        window_start="2026-06-01",
        window_end="2026-06-04",
    )

    with pytest.raises(AnalysisError) as exc_info:
        compare(current, baseline, session=session)

    error = exc_info.value
    assert error.expected == "at least one business coordinate present in both frames"
    assert error.received == "current_only=1, baseline_only=1"
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "compare"


@pytest.mark.parametrize("semantic_kind", ["time_series", "panel"])
def test_compare_all_history_drops_one_sided_time_and_panel_coordinates(
    tmp_path, monkeypatch, semantic_kind
) -> None:
    session = _session(tmp_path, monkeypatch)

    def rows(prefix: str, values: list[float]) -> list[dict[str, object]]:
        month = "07" if prefix == "current" else "06"
        output: list[dict[str, object]] = []
        for index, value in enumerate(values, start=1):
            row: dict[str, object] = {
                "bucket_start": pd.Timestamp(f"2026-{month}-{index:02d}"),
                "value": value,
                EVALUATION_END_COLUMN: pd.Timestamp(f"2026-{month}-{index + 1:02d}", tz="UTC"),
            }
            if semantic_kind == "panel":
                row["region"] = "US"
            output.append(row)
        return output

    current = _all_history_shape_frame(
        session,
        semantic_kind=semantic_kind,
        rows=rows("current", [10.0, 20.0]),
        window_start="2026-07-01",
        window_end="2026-07-03",
    )
    baseline = _all_history_shape_frame(
        session,
        semantic_kind=semantic_kind,
        rows=rows("baseline", [5.0, 12.0, 18.0]),
        window_start="2026-06-01",
        window_end="2026-06-04",
    )

    delta = compare(current, baseline, session=session)
    pair_info = delta.meta.alignment["cumulative_pairs"]

    assert len(delta.to_pandas()) == 2
    assert pair_info["matched_rows"] == 2
    assert pair_info["current_unpaired_rows"] == 0
    assert pair_info["baseline_unpaired_rows"] == 1
    assert len(session.evidence.findings(artifact_ref=delta.ref).items) == 2


def test_all_history_endpoint_order_is_stable_after_recovery(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)

    def scalar(value: float, endpoint: str) -> MetricFrame:
        return _all_history_shape_frame(
            session,
            semantic_kind="scalar",
            rows=[
                {
                    "value": value,
                    EVALUATION_END_COLUMN: pd.Timestamp(endpoint, tz="UTC"),
                }
            ],
            window_start="2026-01-01",
            window_end="2026-01-02",
        )

    for expected, current_end, baseline_end in (
        ("forward", "2026-07-04", "2026-06-04"),
        ("reverse", "2026-06-04", "2026-07-04"),
        ("same", "2026-07-04", "2026-07-04"),
    ):
        delta = compare(scalar(15.0, current_end), scalar(10.0, baseline_end), session=session)
        assert f"endpoint_order={expected}" in delta.render()
        assert f"endpoint_order={expected}" in session.get_frame(delta.ref).render()

    current = _all_history_shape_frame(
        session,
        semantic_kind="segmented",
        rows=[
            {
                "region": "forward",
                "value": 15.0,
                EVALUATION_END_COLUMN: pd.Timestamp("2026-07-04", tz="UTC"),
            },
            {
                "region": "reverse",
                "value": 8.0,
                EVALUATION_END_COLUMN: pd.Timestamp("2026-06-04", tz="UTC"),
            },
        ],
        window_start="2026-01-01",
        window_end="2026-01-02",
    )
    baseline = _all_history_shape_frame(
        session,
        semantic_kind="segmented",
        rows=[
            {
                "region": "forward",
                "value": 10.0,
                EVALUATION_END_COLUMN: pd.Timestamp("2026-06-04", tz="UTC"),
            },
            {
                "region": "reverse",
                "value": 10.0,
                EVALUATION_END_COLUMN: pd.Timestamp("2026-07-04", tz="UTC"),
            },
        ],
        window_start="2026-01-01",
        window_end="2026-01-02",
    )
    mixed = compare(current, baseline, session=session)
    assert "endpoint_order=mixed" in mixed.render()
    assert "endpoint_order=mixed" in session.get_frame(mixed.ref).render()


def test_compare_all_history_level_change_is_allowed(tmp_path, monkeypatch) -> None:
    """all_history compare records an observed level change."""
    session = _session(tmp_path, monkeypatch)
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02", "2026-07-03"],
        values=[10.0, 22.0, 40.0],
        window_start="2026-07-01",
        window_end="2026-07-04",
        anchor="all_history",
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        anchor="all_history",
    )
    delta = compare(current, baseline, session=session)
    assert delta.meta.cumulative_change is not None
    assert delta.meta.alignment["cumulative_pairs"]["matched_rows"] == 3
    assert isinstance(delta.meta.comparison_identity.semantics, ExactComparisonSemanticsV1)


def test_compare_trailing_same_anchor_allowed(tmp_path, monkeypatch) -> None:
    """trailing frames with identical anchor payloads are allowed through compare."""
    session = _session(tmp_path, monkeypatch)
    anchor = ("trailing", 7, "day")
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02", "2026-07-03"],
        values=[10.0, 22.0, 40.0],
        window_start="2026-07-01",
        window_end="2026-07-04",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        anchor=anchor,
    )
    delta = compare(current, baseline, session=session)
    assert delta is not None


def test_compare_trailing_equivalent_units_use_canonical_identity(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02", "2026-07-03"],
        values=[10.0, 22.0, 40.0],
        window_start="2026-07-01",
        window_end="2026-07-04",
        anchor=("trailing", 7, "day"),
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        anchor=("trailing", 1, "week"),
    )

    delta = compare(current, baseline, session=session)

    semantics = delta.meta.comparison_identity.semantics
    assert isinstance(semantics, CumulativeEquivalentComparisonSemanticsV1)
    assert semantics.current_expression_fingerprint != semantics.baseline_expression_fingerprint
    assert semantics.canonical_expression_fingerprint
    assert delta.meta.cumulative_alignment is not None
    assert delta.meta.cumulative_alignment.canonical_anchor.kind == "trailing"
    assert delta.meta.cumulative_alignment.canonical_anchor.span_seconds == 604_800
    assert delta.meta.cumulative_alignment.pairs.matched_rows == 3


def test_trailing_canonical_duration_is_absolute_across_dst() -> None:
    spring_start = pd.Timestamp("2026-03-08T00:00:00", tz="America/New_York")
    spring_end = pd.Timestamp("2026-03-09T00:00:00", tz="America/New_York")
    assert (spring_end - spring_start).total_seconds() == 82_800

    one_day = canonical_comparable_period_anchor(("trailing", 1, "day"))
    twenty_four_hours = canonical_comparable_period_anchor(("trailing", 24, "hour"))

    assert one_day == twenty_four_hours
    assert one_day.span_seconds == 86_400


def test_compare_trailing_rejects_calendar_bucket_mode(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02"],
        values=[20.0, 30.0],
        window_start="2026-07-01",
        window_end="2026-07-03",
        anchor=("trailing", 7, "day"),
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02"],
        values=[10.0, 15.0],
        window_start="2026-07-01",
        window_end="2026-07-03",
        anchor=("trailing", 1, "week"),
    )

    with pytest.raises(AnalysisError) as exc:
        compare(
            current,
            baseline,
            alignment=AlignmentPolicy(kind="window_bucket", mode="calendar_bucket"),
            session=session,
        )

    assert exc.value.location == "session.compare.alignment"
    assert exc.value._context["kind"] == "CumulativeComparablePeriodAlignmentUnsupported"


def test_trailing_delta_topk_rebuilds_pair_summary(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02", "2026-07-03"],
        values=[10.0, 22.0, 40.0],
        window_start="2026-07-01",
        window_end="2026-07-04",
        anchor=("trailing", 7, "day"),
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        anchor=("trailing", 1, "week"),
    )
    delta = compare(current, baseline, session=session)

    top = delta.transform.topk(by="delta", limit=1)

    assert len(top.to_pandas()) == 1
    assert top.meta.cumulative_alignment is not None
    assert top.meta.cumulative_alignment.pairs.matched_rows == 1
    assert top.meta.cumulative_alignment.pairs.matched_null_rows == 0


def test_cumulative_delta_quality_reads_typed_pairing_caveats(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02"],
        values=[10.0, 22.0],
        window_start="2026-07-01",
        window_end="2026-07-03",
        anchor=("trailing", 7, "day"),
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        anchor=("trailing", 1, "week"),
    )
    delta = compare(current, baseline, session=session)

    report = session.assess_quality(delta)
    pairing = report.to_pandas().set_index("check_kind").loc["cumulative_pairing"]
    details = json.loads(pairing["details_json"])

    assert report.meta.report_shape == "delta"
    assert pairing["severity"] == "warning"
    assert details["matched_rows"] == 2
    assert details["baseline_unpaired_rows"] == 1
    assert any(
        issue.kind == "cumulative_alignment_caveat_present" for issue in report.contract().issues
    )


def test_cumulative_alignment_requires_complete_strict_persisted_payload() -> None:
    incomplete = {
        "current_authored_anchor": {"count": 7, "unit": "day"},
        "baseline_authored_anchor": {"count": 1, "unit": "week"},
        "canonical_anchor": {"span_seconds": 604_800},
        "pairs": {
            "matched_rows": 1,
            "matched_null_rows": 0,
            "current_unpaired_rows": 0,
            "baseline_unpaired_rows": 0,
            "fallback_rows": 0,
        },
    }
    with pytest.raises(ValidationError):
        CumulativeAlignmentV1.model_validate(incomplete)

    wrong_types = {
        "schema": "cumulative-alignment/v1",
        "current_authored_anchor": {"kind": "trailing", "count": 7, "unit": "day"},
        "baseline_authored_anchor": {"kind": "trailing", "count": 1, "unit": "week"},
        "canonical_anchor": {"kind": "trailing", "span_seconds": 604_800},
        "pairs": {
            "schema": "cumulative-pair-summary/v1",
            "matched_rows": "1",
            "matched_null_rows": 0,
            "current_unpaired_rows": 0,
            "baseline_unpaired_rows": 0,
            "fallback_rows": 0,
            "unpaired_action": "dropped",
        },
    }
    with pytest.raises(ValidationError):
        CumulativeAlignmentV1.model_validate(wrong_types)


def test_canonical_cumulative_expression_reinterns_ancestors(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    frames = (
        _ts_frame(
            session,
            bucket_starts=["2026-07-01"],
            values=[10.0],
            window_start="2026-07-01",
            window_end="2026-07-02",
            anchor=("trailing", 7, "day"),
        ),
        _ts_frame(
            session,
            bucket_starts=["2026-06-01"],
            values=[5.0],
            window_start="2026-06-01",
            window_end="2026-06-02",
            anchor=("trailing", 1, "week"),
        ),
    )
    projected_graphs: list[MetricExpressionGraphV1] = []
    for frame in frames:
        graph = frame.meta.expression_graph
        assert graph is not None
        cumulative_root = graph.roots[0]
        slice_node = SliceNodeV1(
            kind="slice",
            child_id=cumulative_root,
            predicates=(),
            predicate_dependencies=(),
        )
        slice_id = fingerprint(slice_node)
        cumulative_record = next(
            record for record in graph.nodes if record.node_id == cumulative_root
        )
        leaf_id = cumulative_record.node.child_id
        projected_graphs.append(
            MetricExpressionGraphV1(
                schema="metric-expression/v1",
                roots=(slice_id,),
                nodes=intern_nodes((*(record.node for record in graph.nodes), slice_node)),
                occurrences=(
                    ExpressionOccurrenceV1(
                        path="root[0]",
                        node_id=slice_id,
                        child_paths=("root[0].child",),
                    ),
                    ExpressionOccurrenceV1(
                        path="root[0].child",
                        node_id=cumulative_root,
                        child_paths=("root[0].child.base",),
                    ),
                    ExpressionOccurrenceV1(
                        path="root[0].child.base",
                        node_id=leaf_id,
                    ),
                ),
            )
        )

    assert canonical_cumulative_expression_fingerprint(
        projected_graphs[0]
    ) == canonical_cumulative_expression_fingerprint(projected_graphs[1])


def test_compare_trailing_anchor_mismatch_rejected(tmp_path, monkeypatch) -> None:
    """trailing frames with different anchor payloads are rejected."""
    session = _session(tmp_path, monkeypatch)
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02", "2026-07-03"],
        values=[10.0, 22.0, 40.0],
        window_start="2026-07-01",
        window_end="2026-07-04",
        anchor=("trailing", 7, "day"),
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        anchor=("trailing", 30, "day"),
    )
    with pytest.raises(Exception) as exc_info:
        compare(current, baseline, session=session)
    assert "anchor" in str(exc_info.value).lower()
    assert "span_seconds=604800" in exc_info.value.expected
    assert "span_seconds=2592000" in exc_info.value.received
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.help_target.canonical_id == "compare"


def test_compare_grain_to_date_single_period_aligned(tmp_path, monkeypatch) -> None:
    """This month so far vs the full prior month, both boundary-anchored single-period."""
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    # Current: July 1..3 (MTD so far, window starts on month boundary).
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02", "2026-07-03"],
        values=[10.0, 22.0, 40.0],
        window_start="2026-07-01",
        window_end="2026-07-04",
        anchor=anchor,
    )
    # Baseline: full prior month June 1..3 (also starts on month boundary).
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        anchor=anchor,
    )
    delta = compare(current, baseline, session=session)
    df = delta.to_pandas()
    # Bucket i pairs with bucket i (period-position alignment).
    assert len(df) == current.meta.row_count
    assert delta.meta.cumulative_alignment is not None
    assert delta.meta.cumulative_alignment.pairs.matched_rows == current.meta.row_count
    assert delta.meta.cumulative_alignment.pairs.baseline_unpaired_rows == 0


@pytest.mark.parametrize("kind", ["dow_aligned", "holiday_and_dow_aligned"])
def test_compare_grain_to_date_supports_calendar_position_alignment(
    tmp_path,
    monkeypatch,
    kind: str,
) -> None:
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    if kind == "holiday_and_dow_aligned":
        current_days = ["2026-05-01", "2026-05-02"]
        baseline_days = ["2025-05-01", "2025-05-02"]
    else:
        current_days = ["2026-07-01", "2026-07-02", "2026-07-03"]
        baseline_days = ["2026-06-01", "2026-06-02", "2026-06-03"]
    current = _ts_frame(
        session,
        bucket_starts=current_days,
        values=[10.0] * len(current_days),
        window_start=current_days[0],
        window_end=str(pd.Timestamp(current_days[-1]) + pd.Timedelta(days=1))[:10],
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=baseline_days,
        values=[5.0] * len(baseline_days),
        window_start=baseline_days[0],
        window_end=str(pd.Timestamp(baseline_days[-1]) + pd.Timedelta(days=1))[:10],
        anchor=anchor,
    )

    delta = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(
            kind=kind,
            calendar=CalendarRef("cn_holidays"),
            period="month",
        ),
        session=session,
    )

    assert delta.meta.cumulative_alignment is not None
    assert delta.meta.cumulative_alignment.pairs.matched_rows == len(delta.to_pandas())
    assert set(delta.to_pandas()["presence_status"]) == {"matched"}


def test_compare_grain_to_date_calendar_period_must_match_reset(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01"],
        values=[10.0],
        window_start="2026-07-01",
        window_end="2026-07-02",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01"],
        values=[5.0],
        window_start="2026-06-01",
        window_end="2026-06-02",
        anchor=anchor,
    )

    with pytest.raises(Exception, match="period='month'"):
        compare(
            current,
            baseline,
            alignment=AlignmentPolicy(
                kind="dow_aligned",
                calendar=CalendarRef("cn_holidays"),
                period="quarter",
            ),
            session=session,
        )


def test_compare_trailing_calendar_panel_drops_and_counts_one_sided_coordinates(
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    anchor = ("trailing", 7, "day")
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-01"],
        values=[10.0, 20.0],
        regions=["US", "CA"],
        window_start="2026-07-01",
        window_end="2026-07-02",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-03"],
        values=[5.0],
        regions=["US"],
        window_start="2026-06-01",
        window_end="2026-06-02",
        anchor=anchor,
    )

    delta = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(
            kind="dow_aligned",
            calendar=CalendarRef("cn_holidays"),
            period="month",
        ),
        session=session,
    )

    result = delta.to_pandas()
    assert list(result["region"]) == ["US"]
    assert delta.meta.cumulative_alignment is not None
    pairs = delta.meta.cumulative_alignment.pairs
    assert pairs.matched_rows == 1
    assert pairs.current_unpaired_rows == 1
    assert pairs.baseline_unpaired_rows == 0
    assert pairs.unpaired_action == "dropped"


def test_compare_trailing_holiday_fallback_is_typed_and_counted(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    anchor = ("trailing", 7, "day")
    current = _ts_frame(
        session,
        bucket_starts=["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"],
        values=[100.0, 70.0, 30.0, 40.0],
        window_start="2026-05-01",
        window_end="2026-05-05",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-04-30", "2026-04-03", "2026-04-02", "2026-04-04"],
        values=[80.0, 10.0, 0.0, 50.0],
        window_start="2026-04-01",
        window_end="2026-04-05",
        anchor=anchor,
    )

    delta = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(
            kind="holiday_aligned",
            calendar=CalendarRef("cn_holidays"),
            period="month",
            fallback="nearest_prior_workday",
        ),
        session=session,
    )

    assert delta.meta.cumulative_alignment is not None
    pairs = delta.meta.cumulative_alignment.pairs
    assert pairs.matched_rows == 4
    assert pairs.fallback_rows == 3
    assert pairs.current_unpaired_rows == 0
    assert pairs.baseline_unpaired_rows == 1
    assert (delta.to_pandas()["align_quality"] == "fallback").sum() == 3

    top = delta.transform.topk(by="delta", limit=1)
    assert top.meta.cumulative_alignment is not None
    assert top.meta.cumulative_alignment.pairs.matched_rows == 1
    assert top.meta.cumulative_alignment.pairs.fallback_rows == 1


def test_compare_grain_to_date_boundary_required(tmp_path, monkeypatch) -> None:
    """Validation 2: window must start on a reset boundary."""
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    # Current window starts mid-month (July 2), not on a month boundary.
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-02", "2026-07-03"],
        values=[22.0, 40.0],
        window_start="2026-07-02",
        window_end="2026-07-04",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02"],
        values=[5.0, 11.0],
        window_start="2026-06-01",
        window_end="2026-06-03",
        anchor=anchor,
    )
    with pytest.raises(Exception) as exc_info:
        compare(current, baseline, session=session)
    assert "boundary" in str(exc_info.value).lower()


def test_compare_grain_to_date_rejects_midnight_offset_with_midday_local_start(
    tmp_path, monkeypatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02"],
        values=[10.0, 22.0],
        window_start="2026-07-01T12:00:00",
        window_end="2026-07-03T12:00:00",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02"],
        values=[5.0, 11.0],
        window_start="2026-06-01T00:00:00",
        window_end="2026-06-03T00:00:00",
        anchor=anchor,
    )

    with pytest.raises(AnalysisError) as exc_info:
        compare(current, baseline, session=session)

    assert exc_info.value._context["kind"] == "GrainToDateBoundaryRequired"


def test_validate_grain_to_date_boundary_in_report_timezone(tmp_path, monkeypatch) -> None:
    from marivo.analysis.intents._validate import validate_compare

    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02"],
        values=[10.0, 22.0],
        window_start="2026-06-30T16:00:00+00:00",
        window_end="2026-07-02T16:00:00+00:00",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02"],
        values=[5.0, 11.0],
        window_start="2026-05-31T16:00:00+00:00",
        window_end="2026-06-02T16:00:00+00:00",
        anchor=anchor,
    )

    assert (
        validate_compare(
            current,
            baseline,
            alignment=AlignmentPolicy(kind="window_bucket"),
            report_tz="Asia/Shanghai",
        )
        == []
    )


def test_compare_grain_to_date_multi_period_rejected(tmp_path, monkeypatch) -> None:
    """Validation 3: window spanning >1 reset period is ambiguous; teach single-period observe."""
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    # Current window spans June 30 .. July 2 (two months), starts on a boundary
    # (June 30 is not a month boundary; July 1 is). Use June 1 .. July 2 to
    # start on a boundary but span >1 month.
    current = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-07-01"],
        values=[5.0, 11.0, 40.0],
        window_start="2026-06-01",
        window_end="2026-07-02",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-05-01", "2026-05-02", "2026-06-01"],
        values=[1.0, 2.0, 5.0],
        window_start="2026-05-01",
        window_end="2026-06-02",
        anchor=anchor,
    )
    with pytest.raises(Exception) as exc_info:
        compare(current, baseline, session=session)
    text = str(exc_info.value).lower()
    assert "single" in text or "period" in text


def test_compare_grain_to_date_rejects_fraction_past_next_reset(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-31"],
        values=[10.0, 40.0],
        window_start="2026-07-01T00:00:00",
        window_end="2026-08-01T00:00:00.500000",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-30"],
        values=[5.0, 18.0],
        window_start="2026-06-01T00:00:00",
        window_end="2026-07-01T00:00:00",
        anchor=anchor,
    )

    with pytest.raises(AnalysisError) as exc_info:
        compare(current, baseline, session=session)

    assert exc_info.value._context["kind"] == "GrainToDateMultiPeriod"


def test_compare_grain_to_date_grain_mismatch_rejected(tmp_path, monkeypatch) -> None:
    """Validation 1: both frames share reset grain and query grain."""
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02", "2026-07-03"],
        values=[10.0, 22.0, 40.0],
        window_start="2026-07-01",
        window_end="2026-07-04",
        grain="day",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01T00:00", "2026-06-01T01:00", "2026-06-01T02:00"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01T00:00",
        window_end="2026-06-01T03:00",
        grain="1hour",
        anchor=anchor,
    )
    with pytest.raises(AnalysisError) as exc_info:
        compare(current, baseline, session=session)
    assert exc_info.value._context["kind"] == "GrainToDateQueryGrainMismatch"
    assert (
        exc_info.value._context["current_query_grain"]
        != exc_info.value._context["baseline_query_grain"]
    )


def test_compare_grain_to_date_scalar_elapsed_span_mismatch(tmp_path, monkeypatch) -> None:
    """Scalar elapsed-span check: current elapsed span must equal baseline elapsed span."""
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    # Scalar frames (no grain) with different elapsed spans.
    from tests.shared_fixtures import make_metric_frame as _mmf

    cur_df = pd.DataFrame({"value": [40.0]})
    cur_frame = _mmf(
        cur_df,
        metric_id="sales.cum_gmv",
        axes={},
        measure={"name": "cum_gmv"},
        semantic_kind="scalar",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-04"},
        session=session,
    )
    cur_frame.meta = cur_frame.meta.model_copy(update={"cumulative": _cum_marker_anchor(anchor)})
    base_df = pd.DataFrame({"value": [18.0]})
    base_frame = _mmf(
        base_df,
        metric_id="sales.cum_gmv",
        axes={},
        measure={"name": "cum_gmv"},
        semantic_kind="scalar",
        semantic_model="sales",
        window={"start": "2026-06-01", "end": "2026-06-10"},
        session=session,
    )
    base_frame.meta = base_frame.meta.model_copy(update={"cumulative": _cum_marker_anchor(anchor)})
    with pytest.raises(Exception) as exc_info:
        compare(cur_frame, base_frame, session=session)
    text = str(exc_info.value).lower()
    assert "elapsed" in text or "window" in text


def test_compare_grain_to_date_scalar_rejects_fractional_elapsed_mismatch(
    tmp_path, monkeypatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    current = make_metric_frame(
        pd.DataFrame({"value": [40.0]}),
        metric_id="sales.cum_gmv",
        axes={},
        measure={"name": "cum_gmv"},
        semantic_kind="scalar",
        semantic_model="sales",
        window={
            "start": "2026-07-01T00:00:00",
            "end": "2026-07-04T00:00:00.500000",
        },
        session=session,
    )
    current.meta = current.meta.model_copy(update={"cumulative": _cum_marker_anchor(anchor)})
    baseline = make_metric_frame(
        pd.DataFrame({"value": [18.0]}),
        metric_id="sales.cum_gmv",
        axes={},
        measure={"name": "cum_gmv"},
        semantic_kind="scalar",
        semantic_model="sales",
        window={"start": "2026-06-01T00:00:00", "end": "2026-06-04T00:00:00"},
        session=session,
    )
    baseline.meta = baseline.meta.model_copy(update={"cumulative": _cum_marker_anchor(anchor)})

    with pytest.raises(AnalysisError) as exc_info:
        compare(current, baseline, session=session)

    assert exc_info.value._context["kind"] == "GrainToDateElapsedSpanMismatch"


def test_compare_grain_to_date_rejects_calendar_bucket_mode(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02"],
        values=[10.0, 22.0],
        window_start="2026-07-01",
        window_end="2026-07-03",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02"],
        values=[5.0, 11.0],
        window_start="2026-06-01",
        window_end="2026-06-03",
        anchor=anchor,
    )

    with pytest.raises(AnalysisError) as exc_info:
        compare(
            current,
            baseline,
            alignment=AlignmentPolicy(kind="window_bucket", mode="calendar_bucket"),
            session=session,
        )

    assert exc_info.value._context["kind"] == "CumulativeComparablePeriodAlignmentUnsupported"


def test_compare_grain_to_date_delta_carries_marker(tmp_path, monkeypatch) -> None:
    """The cumulative marker propagates onto the DeltaFrameMeta when compare is allowed."""
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02", "2026-07-03"],
        values=[10.0, 22.0, 40.0],
        window_start="2026-07-01",
        window_end="2026-07-04",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        anchor=anchor,
    )
    delta = compare(current, baseline, session=session)
    assert delta.meta.cumulative is not None


def test_cumulative_delta_attributes_replayed_business_axis(tmp_path, monkeypatch) -> None:
    """A current cumulative delta replays one missing business dimension."""
    session = _session(tmp_path, monkeypatch)
    metric = session.catalog.require(ref_factory.metric("sales.cum_gmv")).ref
    current = session.observe(
        metric,
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
    )
    baseline = session.observe(
        metric,
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
    )
    delta = compare(current, baseline, session=session)
    region = session.catalog.require(ref_factory.dimension("sales.orders.region")).ref

    drivers = attribute(delta, axes=[region], session=session)
    by_region = dict(
        zip(
            drivers.to_pandas()["region"],
            drivers.to_pandas()["contribution"],
            strict=True,
        )
    )

    assert by_region == {
        "CA": pytest.approx(18.0),
        "EU": pytest.approx(7.0),
        "US": pytest.approx(0.0),
    }
    assert drivers.meta.method == "sum"
    assert "cumulative_route" not in drivers.meta.params
    assert drivers.meta.method_evidence is not None
    assert drivers.meta.method_evidence.kind == "cumulative_business_axes"


def test_cumulative_delta_attributes_all_history_accumulation_time(tmp_path, monkeypatch) -> None:
    """The exact cumulative over axis explains the base flow between cutoffs."""
    session = _session(tmp_path, monkeypatch)
    metric = session.catalog.require(ref_factory.metric("sales.cum_gmv")).ref
    current = session.observe(
        metric,
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
    )
    baseline = session.observe(
        metric,
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
    )
    delta = compare(current, baseline, session=session)
    order_date = session.catalog.require(ref_factory.time_dimension("sales.orders.order_date")).ref

    flow = attribute(delta, axes=[order_date], session=session)
    rows = flow.to_pandas()

    assert flow.meta.row_contract_version == "cumulative-flow-attribution-rows/v1"
    assert flow.meta.method_evidence is not None
    assert flow.meta.method_evidence.kind == "cumulative_all_history_flow"
    assert rows["source_side"].tolist() == ["current"]
    assert rows["effect_kind"].tolist() == ["between_cutoffs"]
    assert rows["contribution"].tolist() == [pytest.approx(25.0)]
    assert rows["flow_interval_start"].tolist() == [pd.Timestamp("2026-07-03T00:00:00Z")]
    assert rows["flow_interval_end"].tolist() == [pd.Timestamp("2026-07-04T00:00:00Z")]
    reloaded = session.get_frame(flow.ref)
    assert reloaded.meta.row_contract_version == "cumulative-flow-attribution-rows/v1"


def test_cumulative_flow_validator_rejects_semantic_evidence_corruption(
    tmp_path, monkeypatch
) -> None:
    """Cold-load validation binds intervals, direction, shares, and summary to evidence."""

    session = _session(tmp_path, monkeypatch)
    metric = session.catalog.require(ref_factory.metric("sales.cum_gmv")).ref
    current = session.observe(
        metric,
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
    )
    baseline = session.observe(
        metric,
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
    )
    delta = compare(current, baseline, session=session)
    order_date = session.catalog.require(ref_factory.time_dimension("sales.orders.order_date")).ref
    flow = attribute(delta, axes=[order_date], session=session)
    rows = flow.to_pandas()

    outside_scope = rows.copy()
    outside_scope.loc[0, "flow_interval_start"] = pd.Timestamp("2026-06-01T00:00:00Z")
    with pytest.raises(ValueError, match="outside the cutoff scope"):
        validate_cumulative_flow_attribution_rows(flow.meta, outside_scope)

    wrong_direction = rows.copy()
    wrong_direction.loc[0, "source_side"] = "baseline"
    wrong_direction.loc[0, "baseline_value"] = wrong_direction.loc[0, "current_value"]
    wrong_direction.loc[0, "current_value"] = float("nan")
    wrong_direction.loc[0, "contribution"] *= -1
    with pytest.raises(ValueError, match="cutoff direction"):
        validate_cumulative_flow_attribution_rows(flow.meta, wrong_direction)

    wrong_share = rows.copy()
    wrong_share.loc[0, "share_of_total_delta"] = 2.0
    with pytest.raises(ValueError, match="share_of_total_delta"):
        validate_cumulative_flow_attribution_rows(flow.meta, wrong_share)

    assert flow.meta.reconciliation is not None
    wrong_summary = flow.meta.model_copy(
        update={
            "reconciliation": flow.meta.reconciliation.model_copy(update={"partition_count": 99})
        }
    )
    with pytest.raises(ValueError, match="partition count mismatch"):
        validate_cumulative_flow_attribution_rows(wrong_summary, rows)


@pytest.mark.parametrize(
    ("metric_path", "expected_kind", "expected_effects"),
    [
        (
            "sales.mtd_gmv",
            "cumulative_grain_to_date_flow",
            {"current_scope", "baseline_scope"},
        ),
        (
            "sales.trailing_2d_gmv",
            "cumulative_trailing_flow",
            {"entering", "leaving"},
        ),
    ],
)
def test_cumulative_delta_attributes_comparable_period_flow(
    tmp_path,
    monkeypatch,
    metric_path: str,
    expected_kind: str,
    expected_effects: set[str],
) -> None:
    """GTD and trailing bridges reconcile independently for every paired cutoff."""
    session = _session(tmp_path, monkeypatch)
    metric = session.catalog.require(ref_factory.metric(metric_path)).ref
    current = session.observe(
        metric,
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
        grain="day",
    )
    baseline = session.observe(
        metric,
        time_scope={"start": "2026-06-01", "end": "2026-06-04"},
        grain="day",
    )
    delta = compare(current, baseline, session=session)
    order_date = session.catalog.require(ref_factory.time_dimension("sales.orders.order_date")).ref

    flow = attribute(delta, axes=[order_date], session=session)
    rows = flow.to_pandas()

    assert flow.meta.method_evidence is not None
    assert flow.meta.method_evidence.kind == expected_kind
    assert set(rows["effect_kind"]) == expected_effects
    assert flow.meta.reconciliation is not None
    assert flow.meta.reconciliation.max_abs_residual <= 1e-9
    assert len(flow.meta.method_evidence.partitions) == len(delta.to_pandas())


def test_grain_to_date_flow_uses_period_owning_exclusive_boundary(tmp_path, monkeypatch) -> None:
    """An endpoint on a reset boundary still belongs to the preceding bucket period."""

    session = _session(tmp_path, monkeypatch)
    metric = session.catalog.require(ref_factory.metric("sales.mtd_gmv")).ref
    current = session.observe(
        metric,
        time_scope={"start": "2026-07-01", "end": "2026-08-01"},
        grain="day",
    )
    baseline = session.observe(
        metric,
        time_scope={"start": "2026-06-01", "end": "2026-07-01"},
        grain="day",
    )
    delta = compare(current, baseline, session=session)
    order_date = session.catalog.require(ref_factory.time_dimension("sales.orders.order_date")).ref

    flow = attribute(delta, axes=[order_date], session=session)

    assert flow.meta.reconciliation is not None
    assert flow.meta.reconciliation.max_abs_residual <= 1e-9
    assert flow.meta.method_evidence is not None
    assert flow.meta.method_evidence.kind == "cumulative_grain_to_date_flow"
    assert all(
        abs(partition.residual) <= partition.tolerance
        for partition in flow.meta.method_evidence.partitions
    )


def test_compare_grain_to_date_tail_shown_in_delta_card(tmp_path, monkeypatch) -> None:
    """DeltaFrame show/contract surfaces matched/tail when baseline tail is non-empty."""
    session = _session(tmp_path, monkeypatch)
    anchor = ("grain_to_date", "month")
    # Current has 2 buckets; baseline has 3 -> baseline_tail_buckets == 1.
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02"],
        values=[10.0, 22.0],
        window_start="2026-07-01",
        window_end="2026-07-03",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        anchor=anchor,
    )
    delta = compare(current, baseline, session=session)
    delta_df = delta.to_pandas()
    assert len(delta_df) == 2
    assert delta_df["current"].notna().all()
    assert delta_df["baseline"].notna().all()
    text = delta.render()
    assert "pairing" in text
    assert "baseline_unpaired=1" in text
    contract = delta.contract()
    assert any(
        p.check == "cumulative_pairing"
        for affordance in contract.affordances
        for p in affordance.preconditions
    )


def test_compare_derived_all_history_components_are_allowed(tmp_path, monkeypatch) -> None:
    """Valid derived all-history cumulative wrappers compare by level."""
    session = _session(tmp_path, monkeypatch)
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02", "2026-07-03"],
        values=[10.0, 22.0, 40.0],
        window_start="2026-07-01",
        window_end="2026-07-04",
        metric_id="sales.derived_over_cum",
    )
    current = _attach_ratio_cumulative_contract(session, current, anchor="all_history")
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        metric_id="sales.derived_over_cum",
    )
    baseline = _attach_ratio_cumulative_contract(session, baseline, anchor="all_history")
    delta = compare(current, baseline, session=session)
    assert delta.meta.cumulative_change is not None


def test_compare_rejects_cumulative_marker_presence_mismatch(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02"],
        values=[10.0, 22.0],
        window_start="2026-07-01",
        window_end="2026-07-03",
        anchor=("trailing", 7, "day"),
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02"],
        values=[5.0, 11.0],
        window_start="2026-06-01",
        window_end="2026-06-03",
        anchor=("trailing", 7, "day"),
    )
    baseline.meta = baseline.meta.model_copy(update={"cumulative": None})

    with pytest.raises(AnalysisError) as exc_info:
        compare(current, baseline, session=session)
    assert exc_info.value._context["kind"] == "CumulativeMarkerPresenceMismatch"


def test_compare_rejects_direct_and_derived_marker_kind_mismatch(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    anchor = ("trailing", 7, "day")
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02"],
        values=[10.0, 22.0],
        window_start="2026-07-01",
        window_end="2026-07-03",
        anchor=anchor,
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02"],
        values=[5.0, 11.0],
        window_start="2026-06-01",
        window_end="2026-06-03",
        anchor=anchor,
    )
    baseline.meta = baseline.meta.model_copy(
        update={
            "cumulative": {
                "kind": "derived_contains_cumulative",
                "anchor": anchor,
                "compare_blocker": None,
                "components": {"component": _cum_marker_anchor(anchor)},
            }
        }
    )

    with pytest.raises(AnalysisError) as exc_info:
        compare(current, baseline, session=session)
    assert exc_info.value._context["kind"] == "CumulativeMarkerKindMismatch"


@pytest.mark.parametrize(
    "derived_marker",
    [
        {
            "kind": "derived_contains_cumulative",
            "anchor": ("trailing", 7, "day"),
            "components": {"component": _cum_marker_anchor(("trailing", 7, "day"))},
        },
        {
            "kind": "derived_contains_cumulative",
            "anchor": ("trailing", 7, "day"),
            "compare_blocker": None,
        },
        {
            "kind": "derived_contains_cumulative",
            "anchor": ("trailing", 7, "day"),
            "compare_blocker": None,
            "components": {"component": _cum_marker_anchor(("trailing", 30, "day"))},
        },
    ],
    ids=["missing-blocker", "missing-components", "component-anchor-mismatch"],
)
def test_compare_rejects_malformed_derived_cumulative_marker(
    tmp_path, monkeypatch, derived_marker
) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02"],
        values=[10.0, 22.0],
        window_start="2026-07-01",
        window_end="2026-07-03",
        anchor=("trailing", 7, "day"),
    )
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02"],
        values=[5.0, 11.0],
        window_start="2026-06-01",
        window_end="2026-06-03",
        anchor=("trailing", 7, "day"),
    )
    current.meta = current.meta.model_copy(update={"cumulative": derived_marker})
    baseline.meta = baseline.meta.model_copy(update={"cumulative": derived_marker})

    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        compare(current, baseline, session=session)
    assert exc_info.value._context["compare_blocker"] == "unresolved_component_anchor"


# ---------------------------------------------------------------------------
# Task 11: anchor-aware dynamic guidance (contract / show / card)
# ---------------------------------------------------------------------------


def _anchor_frame(
    session,
    *,
    anchor: object,
    rollup_fold: str | None = "last",
    metric_id: str = "sales.cum_gmv",
) -> MetricFrame:
    """Build a persisted cumulative MetricFrame with a specific anchor + fold."""
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                "value": [10.0, 22.0, 40.0],
            }
        ),
        metric_id=metric_id,
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "cum_gmv"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-04", "grain": "day"},
        session=session,
    )
    frame.meta = frame.meta.model_copy(
        update={
            "cumulative": _cum_marker_anchor(anchor),
            "rollup_fold": rollup_fold,
        }
    )
    return frame


def test_contract_all_history_compare_is_locally_available(tmp_path, monkeypatch) -> None:
    """A single valid frame does not invent pair-compatibility preconditions."""
    session = _session(tmp_path, monkeypatch)
    frame = _anchor_frame(session, anchor="all_history")
    c = frame.contract()
    cmp = next(a for a in c.affordances if a.capability_id == "compare")
    assert not any(
        p.check in {"running_total_caveat", "compare_anchor_match"} for p in cmp.preconditions
    )


def test_contract_grain_to_date_defers_pair_checks_to_compare(tmp_path, monkeypatch) -> None:
    """Pair-dependent boundary rules are evaluated only with both frames."""
    session = _session(tmp_path, monkeypatch)
    frame = _anchor_frame(session, anchor=("grain_to_date", "month"))
    c = frame.contract()
    cmp = next(a for a in c.affordances if a.capability_id == "compare")
    assert not any(
        p.check in {"compare_anchor_match", "compare_single_period_boundary"}
        for p in cmp.preconditions
    )
    pair_contract = next(
        p for p in cmp.preconditions if p.check == "cumulative_comparable_period_pair"
    )
    assert "same month reset" in (pair_contract.reason or "")
    assert "DOW/holiday positions" in (pair_contract.reason or "")


def test_contract_trailing_autocorrelation_caveat(tmp_path, monkeypatch) -> None:
    """trailing frames surface an autocorrelation caveat in contract preconditions."""
    session = _session(tmp_path, monkeypatch)
    frame = _anchor_frame(session, anchor=("trailing", 7, "day"))
    c = frame.contract()
    reasons = " ".join(p.reason or "" for a in c.affordances for p in a.preconditions)
    assert "autocorrelation" in reasons.lower()


def test_contract_rollup_affordance_iff_rollup_fold(tmp_path, monkeypatch) -> None:
    """Rollup affordance IS present on a rollup_fold='last' frame and ABSENT otherwise."""
    session = _session(tmp_path, monkeypatch)
    fold_frame = _anchor_frame(session, anchor="all_history", rollup_fold="last")
    plain_frame = _anchor_frame(
        session,
        anchor="all_history",
        rollup_fold=None,
        metric_id="sales.cum_gmv_plain",
    )
    # Fold frame: existing transform affordances expose the persisted fold fact.
    c_fold = fold_frame.contract()
    assert any(
        a.capability_id.startswith("transform.")
        and any(p.check == "rollup_fold" for p in a.preconditions)
        for a in c_fold.affordances
    )
    # Non-fold frame: no speculative rollup parameter is synthesized.
    c_plain = plain_frame.contract()
    assert not any(
        a.capability_id.startswith("transform.")
        and any(p.check == "rollup_fold" for p in a.preconditions)
        for a in c_plain.affordances
    )


def test_show_card_dispatches_on_anchor(tmp_path, monkeypatch) -> None:
    """_card() renders an anchor-dispatched cumulative status line."""
    session = _session(tmp_path, monkeypatch)
    rolling7 = _anchor_frame(
        session,
        anchor=("trailing", 7, "day"),
        metric_id="sales.cum_rolling7",
    )
    all_history = _anchor_frame(session, anchor="all_history")
    mtd = _anchor_frame(
        session,
        anchor=("grain_to_date", "month"),
        metric_id="sales.cum_mtd",
    )
    assert "autocorrelation" in rolling7._card().render(max_output_bytes=None).lower()
    assert "running total" in all_history._card().render(max_output_bytes=None).lower()
    assert "reset" in mtd._card().render(max_output_bytes=None).lower()
