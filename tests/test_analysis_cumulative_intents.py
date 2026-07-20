"""Intent gates for cumulative frames."""

from __future__ import annotations

from datetime import UTC, datetime

import ibis
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import AnalysisError, CumulativeFrameUnsupportedError
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents.attribute import attribute
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.decompose import decompose
from marivo.analysis.intents.forecast import forecast
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import CalendarRef
from tests.shared_fixtures import make_metric_frame, make_test_delta_contract


def _cum_marker() -> dict:
    return {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": "sales.orders.event_time",
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
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = ms.Ref.datasource('warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "order_date = ms.time_dimension_column("
        "name='order_date', entity=orders, column='created_at', granularity='day')\n"
        "region = ms.dimension_column(name='region', entity=orders, column='region')\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity='additive', unit='USD')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "cum_gmv = ms.cumulative(name='cum_gmv', base=gmv, over=order_date)\n",
        encoding="utf-8",
    )


def _seed(con) -> None:
    con.create_table(
        "orders",
        pd.DataFrame(
            {
                "order_id": [1, 2, 3],
                "created_at": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                "amount": [10.0, 12.0, 18.0],
                "region": ["US", "US", "CA"],
            }
        ),
        overwrite=True,
    )


def _session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _bootstrap_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    return session_attach.get_or_create(name="cum_gates", backends={"warehouse": lambda: con})


def _history(session):
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                "value": [10.0, 12.0, 18.0],
            }
        ),
        metric_id="sales.cum_gmv",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "1day"}},
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


def test_compare_rejects_cumulative_metric_frame(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _history(session)
    baseline = _history(session)

    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        compare(current, baseline, session=session)

    assert exc_info.value._context["intent"] == "compare"
    assert exc_info.value._context["base_metric_id"] == "sales.gmv"


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


def test_attribute_rejects_cumulative_delta(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    delta = _delta(session, cumulative=_cum_marker())

    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        attribute(delta, axes=["sales.orders.region"], session=session)

    assert exc_info.value._context["intent"] == "attribute"
    assert exc_info.value._context["base_metric_id"] == "sales.gmv"


# ---------------------------------------------------------------------------
# Task 10: compare to-date alignment (anchor-dispatched gate)
# ---------------------------------------------------------------------------


def _cum_marker_anchor(anchor: object) -> dict:
    """Cumulative marker with a specific anchor payload."""
    return {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": "sales.orders.event_time",
        "anchor": anchor,
        "components": None,
    }


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
) -> MetricFrame:
    """Build a persisted time_series MetricFrame carrying a cumulative marker."""
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(bucket_starts),
                "value": values,
            }
        ),
        metric_id=metric_id,
        axes={"time": {"role": "time", "column": "bucket_start", "grain": grain}},
        measure={"name": "cum_gmv"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": window_start, "end": window_end, "grain": grain},
        session=session,
    )
    frame.meta = frame.meta.model_copy(update={"cumulative": _cum_marker_anchor(anchor)})
    return frame


def test_compare_all_history_still_rejected(tmp_path, monkeypatch) -> None:
    """all_history cumulative frames stay compare-gated (names base ref)."""
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
    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        compare(current, baseline, session=session)
    assert "base" in str(exc_info.value).lower()


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
    assert delta.meta.alignment["to_date"]["matched_buckets"] == current.meta.row_count
    assert delta.meta.alignment["to_date"]["baseline_tail_buckets"] >= 0


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


@pytest.mark.parametrize(
    "alignment",
    [
        AlignmentPolicy(kind="window_bucket", mode="calendar_bucket"),
        AlignmentPolicy(kind="dow_aligned", calendar=CalendarRef("test_calendar")),
    ],
)
def test_compare_grain_to_date_requires_ordinal_window_bucket(
    tmp_path, monkeypatch, alignment: AlignmentPolicy
) -> None:
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
        compare(current, baseline, alignment=alignment, session=session)

    assert exc_info.value._context["kind"] == "GrainToDateAlignmentPolicyUnsupported"


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


def test_compare_grain_to_date_delta_attribute_still_gated(tmp_path, monkeypatch) -> None:
    """A cumulative DeltaFrame stays attribute-gated even after compare is allowed."""
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
    with pytest.raises(CumulativeFrameUnsupportedError):
        attribute(delta, axes=["sales.orders.region"], session=session)


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
    assert "matched_buckets" in text
    assert "baseline_tail_buckets" in text
    # The contract affordances should carry a to_date tail note (prose).
    contract = delta.contract()
    rendered_contract = "\n".join(
        f"{a.capability_id}: {[p.reason for p in a.preconditions]}" for a in contract.affordances
    )
    assert "tail bucket" in rendered_contract
    assert "ordinal alignment matched" in rendered_contract


def test_compare_derived_all_history_components_still_rejected(tmp_path, monkeypatch) -> None:
    """Derived all-history cumulative wrappers stay compare-gated."""
    session = _session(tmp_path, monkeypatch)
    component_marker = _cum_marker_anchor("all_history")
    derived_marker = {
        "kind": "derived_contains_cumulative",
        "anchor": "all_history",
        "compare_blocker": None,
        "components": {
            "numerator": component_marker,
            "denominator": component_marker,
        },
    }
    current = _ts_frame(
        session,
        bucket_starts=["2026-07-01", "2026-07-02", "2026-07-03"],
        values=[10.0, 22.0, 40.0],
        window_start="2026-07-01",
        window_end="2026-07-04",
        metric_id="sales.derived_over_cum",
    )
    current.meta = current.meta.model_copy(update={"cumulative": derived_marker})
    baseline = _ts_frame(
        session,
        bucket_starts=["2026-06-01", "2026-06-02", "2026-06-03"],
        values=[5.0, 11.0, 18.0],
        window_start="2026-06-01",
        window_end="2026-06-04",
        metric_id="sales.derived_over_cum",
    )
    baseline.meta = baseline.meta.model_copy(update={"cumulative": derived_marker})
    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        compare(current, baseline, session=session)
    assert "all-history" in exc_info.value.hint.lower()


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


def test_contract_all_history_compare_gated(tmp_path, monkeypatch) -> None:
    """all_history compare stays a hard caveat (running_total_caveat on compare)."""
    session = _session(tmp_path, monkeypatch)
    frame = _anchor_frame(session, anchor="all_history")
    c = frame.contract()
    cmp = next(a for a in c.affordances if a.capability_id == "compare")
    assert any(p.check == "running_total_caveat" for p in cmp.preconditions)


def test_contract_grain_to_date_compare_condional(tmp_path, monkeypatch) -> None:
    """grain_to_date compare is a conditional affordance stating preconditions."""
    session = _session(tmp_path, monkeypatch)
    frame = _anchor_frame(session, anchor=("grain_to_date", "month"))
    c = frame.contract()
    cmp = next(a for a in c.affordances if a.capability_id == "compare")
    # compare is a conditional affordance stating preconditions (not a hard fail)
    reasons = " ".join(p.reason or "" for p in cmp.preconditions)
    assert "single-period" in reasons.lower() or "boundary" in reasons.lower()


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
