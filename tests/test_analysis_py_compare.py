"""mv.compare against two MetricFrames."""

import ibis
import pandas as pd
import pytest

import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import (
    AlignmentFailedError,
    SemanticKindMismatchError,
    SessionStateError,
)
from marivo.analysis_py.frames.delta import DeltaFrame
from marivo.analysis_py.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis_py.intents.compare import compare
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.policies import AlignmentPolicy
from marivo.analysis_py.refs import MetricRef
from marivo.analysis_py.session.persistence import read_frame_from_disk
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0),"
        "(2, DATE '2026-07-02', 20.0),"
        "(3, DATE '2026-04-01', 5.0),"
        "(4, DATE '2026-04-02', 15.0)"
    )


def test_compare_returns_delta_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )
    d = compare(q3, q2, alignment=AlignmentPolicy(kind="calendar_bucket"), session=s)
    assert isinstance(d, DeltaFrame)
    assert d.meta.alignment["kind"] == "calendar_bucket"
    assert d.meta.source_a_ref == q3.ref
    assert d.meta.source_b_ref == q2.ref
    df = d.to_pandas()
    assert set(df.columns) >= {"current", "baseline", "delta", "pct_change"}
    assert df.iloc[0]["current"] == pytest.approx(30.0)
    assert df.iloc[0]["baseline"] == pytest.approx(20.0)
    assert df.iloc[0]["delta"] == pytest.approx(10.0)


def test_compare_default_bucket_handles_scalar_window_outputs(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )
    d = compare(q3, q2, session=s)
    assert d.to_pandas().iloc[0]["delta"] == pytest.approx(10.0)


def test_compare_rejects_delta_frame_as_second_argument(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )
    delta = compare(q3, q2, session=s)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(q3, delta, session=s)  # type: ignore[arg-type]

    rendered = str(exc_info.value)
    assert (
        "SemanticKindMismatchError: compare(a, b) expected MetricFrame for `b`, got DeltaFrame."
        in rendered
    )
    assert "正确写法:" in rendered
    assert (
        'delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))'
        in rendered
    )
    assert exc_info.value.details["expected_kind"] == "metric_frame"
    assert exc_info.value.details["got_kind"] == "delta_frame"


def test_compare_semantic_kind_mismatch_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s)
    b = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31", "grain": "day"},
        session=s,
    )
    with pytest.raises(SemanticKindMismatchError):
        compare(a, b, session=s)


def test_compare_rejects_non_alignment_policy(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s)
    b = observe(MetricRef("sales.revenue"), session=s)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(a, b, alignment="calendar_bucket", session=s)  # type: ignore[arg-type]

    assert exc_info.value.details["expected_kind"] == "AlignmentPolicy"
    assert exc_info.value.details["got_kind"] == "str"


def test_compare_rejects_loose_align_parameter(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )

    with pytest.raises(TypeError):
        compare(q3, q2, align="sample", session=s)  # type: ignore[call-arg]


def test_calendar_bucket_aligns_equal_length_time_series_by_ordinal_bucket(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    cur = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-02", "grain": "day"},
        session=s,
    )
    base = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-04-01", "end": "2026-04-02", "grain": "day"},
        session=s,
    )

    delta = compare(cur, base, alignment=AlignmentPolicy(kind="calendar_bucket"), session=s)

    df = delta.to_pandas()
    assert len(df) == 2
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert list(df["bucket_start_b"].astype(str)) == ["2026-04-01", "2026-04-02"]
    assert list(df["delta"]) == [pytest.approx(5.0), pytest.approx(5.0)]
    assert delta.meta.alignment["mode"] == "ordinal_bucket"


def test_calendar_bucket_ordinal_rejects_time_series_grain_mismatch(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = MetricFrame.from_dataframe(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )
    base = MetricFrame.from_dataframe(
        pd.DataFrame({"bucket_start": ["2026-04-01", "2026-04-02"], "revenue": [5.0, 15.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "hour"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(cur, base, alignment=AlignmentPolicy(kind="calendar_bucket"), session=s)

    assert exc_info.value.details["kind"] == "CalendarBucketGrainMismatch"
    assert exc_info.value.details["current_grain"] == "day"
    assert exc_info.value.details["baseline_grain"] == "hour"


def test_calendar_bucket_no_overlap_unequal_lengths_explains_requirement(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    cur = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-02", "grain": "day"},
        session=s,
    )
    base = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-04-01", "end": "2026-04-01", "grain": "day"},
        session=s,
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(cur, base, alignment=AlignmentPolicy(kind="calendar_bucket"), session=s)

    assert "equal-length" in str(exc_info.value)
    assert exc_info.value.details["kind"] == "CalendarBucketNoComparableBuckets"


def test_compare_persists_job_and_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s)
    b = observe(MetricRef("sales.revenue"), session=s)
    d = compare(a, b, alignment=AlignmentPolicy(kind="calendar_bucket"), session=s)
    compare_jobs = [j for j in s.jobs() if j.intent == "compare"]
    assert len(compare_jobs) == 1
    assert compare_jobs[0].output_frame_ref == d.ref
    assert (s.layout.frames_dir / d.ref / "data.parquet").is_file()
    job_record = s.job(compare_jobs[0].id)
    assert job_record["params"]["alignment"]["kind"] == "calendar_bucket"


def test_compare_works_in_read_only_session(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s_write = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s_write)
    b = observe(MetricRef("sales.revenue"), session=s_write)
    s_write.close()
    session_attach._reset_process_state()
    s_read = session_attach.attach(name="demo", use_datasources=False)
    assert s_read.is_read_only
    df_a, meta_a = read_frame_from_disk(s_read.layout, a.ref)
    df_b, meta_b = read_frame_from_disk(s_read.layout, b.ref)
    d = compare(
        MetricFrame(_df=df_a, meta=MetricFrameMeta(**meta_a)),
        MetricFrame(_df=df_b, meta=MetricFrameMeta(**meta_b)),
        alignment=AlignmentPolicy(kind="calendar_bucket"),
        session=s_read,
    )
    assert isinstance(d, DeltaFrame)


def test_compare_archived_session_raises_for_cached_session(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s)
    b = observe(MetricRef("sales.revenue"), session=s)
    session_attach.archive("demo")
    with pytest.raises(SessionStateError):
        compare(a, b, session=s)


def test_compare_stale_archived_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s)
    b = observe(MetricRef("sales.revenue"), session=s)
    session_attach._reset_process_state()
    session_attach.archive("demo")
    assert s.state == "active"
    with pytest.raises(SessionStateError):
        compare(a, b, session=s)
