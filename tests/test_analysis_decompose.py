"""Internal decompose and public attribute for scalar, time-series, and segmented DeltaFrames."""

from datetime import UTC, datetime

import ibis
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    CrossSessionFrameError,
    NoBackendFactoryError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents.decompose import decompose
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _delta(session, df, *, semantic_kind="time_series", ref="frame_delta"):
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref=ref,
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
                    inputs=["frame_a", "frame_b"],
                    params_digest="sha256:compare",
                )
            ]
        ),
        metric_id="sales.revenue",
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={"kind": "window_bucket"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
    )
    return DeltaFrame(_df=df, meta=meta)


def _metric(session):
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref="frame_metric",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_observe",
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="sales",
    )
    return MetricFrame(_df=pd.DataFrame({"value": [10.0]}), meta=meta)


def test_session_decompose_is_no_longer_public_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = session_attach.get_or_create(name="demo")

    assert not hasattr(session, "decompose")


def test_decompose_time_series_uses_axis_ref():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "bucket": ["2026-07-01", "2026-07-02", "2026-07-03"],
                "delta": [10.0, -2.0, 4.0],
            }
        ),
        semantic_kind="time_series",
    )

    out = decompose(frame, axis=make_ref("bucket", SemanticKind.DIMENSION), session=session)

    assert isinstance(out, AttributionFrame)
    assert out.meta.attribution_kind == "decomposition"
    assert out.meta.driver_field == "path"
    assert out.meta.metric_ids == ["sales.revenue"]
    df = out.to_pandas()
    assert list(df["driver"]) == ["2026-07-01", "2026-07-03", "2026-07-02"]
    assert list(df["rank"]) == [1, 2, 3]
    assert df.iloc[0]["contribution"] == pytest.approx(10.0)


def test_decompose_segmented_uses_axis_ref():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["north", "north", "south"],
                "delta": [10.0, 5.0, -3.0],
            }
        ),
        semantic_kind="segmented",
    )

    out = decompose(frame, axis=make_ref("region", SemanticKind.DIMENSION), session=session)

    df = out.to_pandas()
    assert list(df["driver"]) == ["north", "south"]
    assert list(df["contribution"]) == [pytest.approx(15.0), pytest.approx(-3.0)]
    assert list(df["rank"]) == [1, 2]


def test_decompose_axes_single_axis_returns_level_one_hierarchy_rows():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["north", "north", "south"],
                "delta": [10.0, 5.0, -3.0],
            }
        ),
        semantic_kind="segmented",
    )

    out = decompose(frame, axes=[make_ref("region", SemanticKind.DIMENSION)], session=session)

    assert out.meta.driver_field == "path"
    assert out.meta.method == "ordered_hierarchy_sum"
    assert out.meta.params["axes"] == ["region"]
    assert "mode" not in out.meta.params
    assert out.to_pandas().to_dict("records") == [
        {
            "level": 1,
            "axis": "region",
            "driver": "north",
            "path": "north",
            "contribution": 15.0,
            "pct_contribution": 1.25,
            "rank": 1,
        },
        {
            "level": 1,
            "axis": "region",
            "driver": "south",
            "path": "south",
            "contribution": -3.0,
            "pct_contribution": -0.25,
            "rank": 2,
        },
    ]


def test_decompose_axes_multi_axis_returns_ordered_hierarchy_rows():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "US", "CN", "CN"],
                "platform": ["ios", "android", "ios", "android"],
                "delta": [6.0, 4.0, -3.0, 1.0],
            }
        ),
        semantic_kind="segmented",
    )

    out = decompose(
        frame,
        axes=[
            make_ref("region", SemanticKind.DIMENSION),
            make_ref("platform", SemanticKind.DIMENSION),
        ],
        session=session,
    )

    assert out.meta.driver_field == "path"
    assert out.meta.method == "ordered_hierarchy_sum"
    assert out.meta.params["axis_columns"] == ["region", "platform"]
    assert out.to_pandas().to_dict("records") == [
        {
            "level": 1,
            "axis": "region",
            "driver": "US",
            "path": "US",
            "contribution": 10.0,
            "pct_contribution": 1.25,
            "rank": 1,
        },
        {
            "level": 1,
            "axis": "region",
            "driver": "CN",
            "path": "CN",
            "contribution": -2.0,
            "pct_contribution": -0.25,
            "rank": 2,
        },
        {
            "level": 2,
            "axis": "platform",
            "driver": "ios",
            "path": "US > ios",
            "contribution": 6.0,
            "pct_contribution": 0.75,
            "rank": 1,
        },
        {
            "level": 2,
            "axis": "platform",
            "driver": "android",
            "path": "US > android",
            "contribution": 4.0,
            "pct_contribution": 0.5,
            "rank": 2,
        },
        {
            "level": 2,
            "axis": "platform",
            "driver": "ios",
            "path": "CN > ios",
            "contribution": -3.0,
            "pct_contribution": -0.375,
            "rank": 3,
        },
        {
            "level": 2,
            "axis": "platform",
            "driver": "android",
            "path": "CN > android",
            "contribution": 1.0,
            "pct_contribution": 0.125,
            "rank": 4,
        },
    ]


def test_decompose_rejects_duplicate_axes():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"region": ["north"], "delta": [1.0]}),
        semantic_kind="segmented",
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        decompose(
            frame,
            axes=[
                make_ref("region", SemanticKind.DIMENSION),
                make_ref("region", SemanticKind.DIMENSION),
            ],
            session=session,
        )

    assert exc_info.value.details["reason"] == "duplicate_axes"


def test_decompose_accepts_model_prefixed_axis_ref():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "department": ["analytics", "search", "analytics"],
                "delta": [10.0, -3.0, 4.0],
            }
        ),
        semantic_kind="segmented",
    )

    out = decompose(
        frame, axis=make_ref("trino_query.department", SemanticKind.DIMENSION), session=session
    )

    assert out.meta.driver_field == "path"
    df = out.to_pandas()
    assert list(df["driver"]) == ["analytics", "search"]
    assert list(df["contribution"]) == [pytest.approx(14.0), pytest.approx(-3.0)]


def test_decompose_accepts_catalog_dimension_ref(tmp_path):
    bootstrap_sales_project(tmp_path)
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["north", "north", "south"],
                "delta": [10.0, 5.0, -3.0],
            }
        ),
        semantic_kind="segmented",
    )
    axis = session.catalog.get("dimension.sales.orders.region").ref

    out = decompose(frame, axis=axis, session=session)

    assert out.meta.driver_field == "path"


def test_decompose_requires_axis_argument():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["north", "south"],
                "cohort": ["new", "existing"],
                "delta": [5.0, 2.0],
            }
        ),
        semantic_kind="segmented",
    )

    with pytest.raises(SemanticKindMismatchError):
        decompose(frame, session=session)


def test_decompose_scalar_rejects_missing_axis_column():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"delta": [8.0]}),
        semantic_kind="scalar",
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        decompose(frame, axis=make_ref("region", SemanticKind.DIMENSION), session=session)

    assert exc_info.value.details["requested_axis"] == "region"
    assert exc_info.value.details["normalized_axis"] == "region"
    assert exc_info.value.details["available_columns"] == ["delta"]


def test_decompose_writes_job_and_frame():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))

    out = decompose(frame, axis=make_ref("bucket", SemanticKind.DIMENSION), session=session)

    jobs = [job for job in session.jobs() if job.intent == "decompose"]
    assert len(jobs) == 1
    assert jobs[0].output_frame_ref == out.ref
    assert (session._layout.frames_dir / out.ref / "data.parquet").is_file()


def test_decompose_rejects_metric_frame():
    session = session_attach.get_or_create(name="demo")
    with pytest.raises(SemanticKindMismatchError):
        decompose(
            _metric(session), axis=make_ref("bucket", SemanticKind.DIMENSION), session=session
        )  # type: ignore[arg-type]


def test_decompose_rejects_panel_delta():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"bucket": ["a"], "delta": [1.0]}),
        semantic_kind="panel",
    )
    with pytest.raises(SemanticKindMismatchError):
        decompose(frame, axis=make_ref("bucket", SemanticKind.DIMENSION), session=session)


def test_decompose_rejects_non_dimension_ref_axis():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"region": ["north", "south"], "delta": [1.0, 2.0]}),
        semantic_kind="segmented",
    )
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        decompose(frame, axis="region", session=session)  # type: ignore[arg-type]

    assert exc_info.value.details["expected_kind"] == "dimension"
    assert exc_info.value.details["actual_kind"] == "str"


def test_decompose_rejects_missing_axis_column():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"delta": [1.0, 2.0]}),
        semantic_kind="time_series",
    )
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        decompose(frame, axis=make_ref("bucket", SemanticKind.DIMENSION), session=session)

    assert exc_info.value.details["requested_axis"] == "bucket"
    assert exc_info.value.details["normalized_axis"] == "bucket"
    assert exc_info.value.details["available_columns"] == ["delta"]


def test_decompose_time_series_rejects_missing_non_bucket_dimension():
    """Decompose on a time_series delta without the requested dimension should
    raise, NOT silently fall back to bucket_start."""
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "bucket_start": ["2026-07-01", "2026-07-02", "2026-07-03"],
                "delta": [10.0, -2.0, 4.0],
            }
        ),
        semantic_kind="time_series",
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        decompose(frame, axis=make_ref("cluster", SemanticKind.DIMENSION), session=session)

    assert exc_info.value.details["requested_axis"] == "cluster"
    assert exc_info.value.details["normalized_axis"] == "cluster"
    # Must NOT silently use bucket_start
    assert "bucket_start" not in exc_info.value.details.get("available_columns", []) or (
        exc_info.value.details["available_columns"].count("bucket_start") == 1
    )


def test_decompose_time_series_bucket_start_axis_still_works():
    """Regression guard: decompose by the bucket column on a time_series delta
    should still produce per-bucket attribution."""
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "bucket_start": ["2026-07-01", "2026-07-02", "2026-07-03"],
                "delta": [10.0, -2.0, 4.0],
            }
        ),
        semantic_kind="time_series",
    )

    out = decompose(frame, axis=make_ref("bucket_start", SemanticKind.DIMENSION), session=session)

    assert isinstance(out, AttributionFrame)
    assert out.meta.driver_field == "path"
    df = out.to_pandas()
    assert len(df) == 3


def test_decompose_rejects_missing_delta_column():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "value": [1.0]}))
    with pytest.raises(SemanticKindMismatchError):
        decompose(frame, axis=make_ref("bucket", SemanticKind.DIMENSION), session=session)


def test_decompose_rejects_measure_column_kwarg():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    with pytest.raises(TypeError):
        decompose(
            frame,
            axis=make_ref("bucket", SemanticKind.DIMENSION),
            measure_column="delta",
            session=session,
        )  # type: ignore[call-arg]


def test_decompose_rejects_non_numeric_value_column():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": ["bad"]}))
    with pytest.raises(SemanticKindMismatchError):
        decompose(frame, axis=make_ref("bucket", SemanticKind.DIMENSION), session=session)


def test_decompose_rejects_cross_session_frame():
    session_a = session_attach.get_or_create(name="a")
    frame = _delta(session_a, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    session_b = session_attach.get_or_create(name="b")
    with pytest.raises(CrossSessionFrameError):
        decompose(frame, axis=make_ref("bucket", SemanticKind.DIMENSION), session=session_b)


def test_decompose_read_only_session_without_backend_raises():
    session = session_attach.get_or_create(name="demo", use_datasources=False)
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    with pytest.raises(NoBackendFactoryError):
        decompose(frame, axis=make_ref("bucket", SemanticKind.DIMENSION), session=session)


def test_decompose_stale_session_without_backend_raises():
    session = session_attach.get_or_create(name="demo", use_datasources=False)
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    # Session without backend factory cannot execute decompose intents.
    with pytest.raises(NoBackendFactoryError):
        decompose(frame, axis=make_ref("bucket", SemanticKind.DIMENSION), session=session)


# ---------------------------------------------------------------------------
# Sampled semi-additive decompose gate
# ---------------------------------------------------------------------------


def _bootstrap_bandwidth_for_decompose(tmp_path):
    """Bootstrap a bandwidth semantic project for decompose gate tests."""
    from marivo.analysis.timezone import resolve_system_timezone

    report_tz_name = resolve_system_timezone().name
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "bandwidth_samples = ms.entity(\n"
        "    name='bandwidth_samples',\n"
        "    datasource=md.ref('datasource.warehouse'),\n"
        "    primary_key=['sample_id'],\n"
        "    source=md.table('bandwidth_samples'),\n"
        ")\n"
        "\n"
        "@ms.time_dimension(entity=bandwidth_samples, granularity='day')\n"
        "def dt(bandwidth_samples):\n"
        "    return bandwidth_samples.dt.cast('date')\n"
        "\n"
        "@ms.time_dimension(\n"
        "    name='sample_ts',\n"
        "    entity=bandwidth_samples,\n"
        "    granularity='minute',\n"
        f"    parse=ms.datetime(timezone='{report_tz_name}', sample_interval=(5, 'minute')),\n"
        ")\n"
        "def sample_ts(bandwidth_samples):\n"
        "    return bandwidth_samples.sample_ts\n"
        "\n"
        "@ms.dimension(entity=bandwidth_samples)\n"
        "def province(bandwidth_samples):\n"
        "    return bandwidth_samples.province\n"
        "\n"
        "@ms.metric(\n"
        "    name='upstream_bw_p95',\n"
        "    entities=[bandwidth_samples],\n"
        "    additivity=ms.semi_additive(over=sample_ts, fold=('percentile', 0.95)),\n"
        ")\n"
        "def upstream_bw_p95(bandwidth_samples):\n"
        "    return bandwidth_samples.upstream_bw_var.sum()\n"
    )


def _seed_bandwidth_for_decompose(con):
    """Seed bandwidth_samples with two days of data for decompose gate tests."""
    con.raw_sql(
        "CREATE TABLE bandwidth_samples ("
        "sample_id INTEGER, dt DATE, sample_ts TIMESTAMP, "
        "upstream_bw DOUBLE, upstream_bw_var DOUBLE, reserved_bw DOUBLE, province VARCHAR)"
    )
    rows = []
    sid = 1
    for day in ("2026-01-01", "2026-01-02"):
        for i in range(12):
            minute = i * 5
            ts = f"TIMESTAMP '{day} 00:{minute:02d}:00'"
            rows.append(f"({sid}, DATE '{day}', {ts}, 100.0, {(i + 1) * 10.0}, 200.0, 'beijing')")
            sid += 1
            rows.append(f"({sid}, DATE '{day}', {ts}, 200.0, 0.0, 0.0, 'beijing')")
            sid += 1
            rows.append(f"({sid}, DATE '{day}', {ts}, 90.0, 0.0, 0.0, 'shanghai')")
            sid += 1
    con.raw_sql("INSERT INTO bandwidth_samples VALUES " + ",".join(rows))


@pytest.fixture()
def sampled_bandwidth_for_decompose(tmp_path):
    _bootstrap_bandwidth_for_decompose(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_bandwidth_for_decompose(con)
    return session_attach.get_or_create(name="demo_decompose", backends={"warehouse": lambda: con})


def test_decompose_rejects_non_linear_fold_delta(sampled_bandwidth_for_decompose) -> None:
    from marivo.analysis.errors import ComponentDecompositionError
    from marivo.semantic.catalog import SemanticKind

    cur = sampled_bandwidth_for_decompose.observe(
        make_ref("sales.upstream_bw_p95", SemanticKind.METRIC),
        time_scope={"start": "2026-01-02", "end": "2026-01-03"},
        dimensions=[make_ref("sales.bandwidth_samples.province", SemanticKind.DIMENSION)],
    )
    base = sampled_bandwidth_for_decompose.observe(
        make_ref("sales.upstream_bw_p95", SemanticKind.METRIC),
        time_scope={"start": "2026-01-01", "end": "2026-01-02"},
        dimensions=[make_ref("sales.bandwidth_samples.province", SemanticKind.DIMENSION)],
    )
    delta = sampled_bandwidth_for_decompose.compare(cur, base)

    with pytest.raises(ComponentDecompositionError) as exc_info:
        decompose(
            delta,
            axis=make_ref("province", SemanticKind.DIMENSION),
            session=sampled_bandwidth_for_decompose,
        )

    assert exc_info.value.details["reason"] == "non_linear_time_fold"


def test_decompose_axes_empty_delta_returns_empty_hierarchy():
    """An empty DeltaFrame (zero rows) must produce an empty AttributionFrame
    with the correct hierarchy columns, not a KeyError."""
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": pd.Series([], dtype="object"),
                "delta": pd.Series([], dtype="float64"),
            }
        ),
        semantic_kind="segmented",
    )

    out = decompose(frame, axes=[make_ref("region", SemanticKind.DIMENSION)], session=session)

    assert isinstance(out, AttributionFrame)
    assert out.meta.driver_field == "path"
    assert out.meta.method == "ordered_hierarchy_sum"
    df = out.to_pandas()
    assert df.empty
    assert list(df.columns) == [
        "level",
        "axis",
        "driver",
        "path",
        "contribution",
        "pct_contribution",
        "rank",
    ]


def test_decompose_axes_multi_axis_handles_nan_in_level_two():
    """A level-2 axis with NaN values must be included in the hierarchy output
    (groupby dropna=False). The path for a NaN group is 'CN > nan'."""
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "US", "CN"],
                "platform": ["ios", "android", None],
                "delta": [6.0, 4.0, -3.0],
            }
        ),
        semantic_kind="segmented",
    )

    out = decompose(
        frame,
        axes=[
            make_ref("region", SemanticKind.DIMENSION),
            make_ref("platform", SemanticKind.DIMENSION),
        ],
        session=session,
    )

    assert isinstance(out, AttributionFrame)
    assert out.meta.driver_field == "path"
    assert out.meta.method == "ordered_hierarchy_sum"
    df = out.to_pandas()
    level2 = df[df["level"] == 2]
    nan_rows = level2[level2["path"] == "CN > nan"]
    assert len(nan_rows) == 1
    assert nan_rows.iloc[0]["contribution"] == pytest.approx(-3.0)
    assert nan_rows.iloc[0]["axis"] == "platform"
    assert nan_rows.iloc[0]["rank"] == 3
