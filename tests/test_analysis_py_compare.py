"""mv.compare against two MetricFrames."""

import ibis
import pytest

import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import SemanticKindMismatchError, SessionStateError
from marivo.analysis_py.frames.delta import DeltaFrame
from marivo.analysis_py.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis_py.intents.compare import compare
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.session.persistence import read_frame_from_disk


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _bootstrap_sales(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse(): ...\n"
        "@ms.dataset(name='orders', datasource=warehouse)\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "@ms.time_field(dataset='orders', data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


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
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe("sales.revenue", window={"start": "2026-07-01", "end": "2026-07-31"}, session=s)
    q2 = observe("sales.revenue", window={"start": "2026-04-01", "end": "2026-04-30"}, session=s)
    d = compare(q3, q2, align="sample", compare_type="qoq", session=s)
    assert isinstance(d, DeltaFrame)
    assert d.meta.compare_type == "qoq"
    assert d.meta.source_a_ref == q3.ref
    assert d.meta.source_b_ref == q2.ref
    df = d.to_pandas()
    assert set(df.columns) >= {"current", "baseline", "delta", "pct_change"}
    assert df.iloc[0]["current"] == pytest.approx(30.0)
    assert df.iloc[0]["baseline"] == pytest.approx(20.0)
    assert df.iloc[0]["delta"] == pytest.approx(10.0)


def test_compare_default_bucket_handles_scalar_window_outputs(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe("sales.revenue", window={"start": "2026-07-01", "end": "2026-07-31"}, session=s)
    q2 = observe("sales.revenue", window={"start": "2026-04-01", "end": "2026-04-30"}, session=s)
    d = compare(q3, q2, compare_type="qoq", session=s)
    assert d.to_pandas().iloc[0]["delta"] == pytest.approx(10.0)


def test_compare_semantic_kind_mismatch_raises(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe("sales.revenue", session=s)
    b = observe("sales.revenue", window={"start": "2026-07-01", "end": "2026-07-31"}, session=s)
    with pytest.raises(SemanticKindMismatchError):
        compare(a, b, session=s)


def test_compare_persists_job_and_frame(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe("sales.revenue", session=s)
    b = observe("sales.revenue", session=s)
    d = compare(a, b, align="sample", session=s)
    compare_jobs = [j for j in s.jobs() if j.intent == "compare"]
    assert len(compare_jobs) == 1
    assert compare_jobs[0].output_frame_ref == d.ref
    assert (s.layout.frames_dir / d.ref / "data.parquet").is_file()


def test_compare_works_in_read_only_session(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s_write = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe("sales.revenue", session=s_write)
    b = observe("sales.revenue", session=s_write)
    s_write.close()
    session_attach._reset_process_state()
    s_read = session_attach.attach(name="demo")
    assert s_read.is_read_only
    df_a, meta_a = read_frame_from_disk(s_read.layout, a.ref)
    df_b, meta_b = read_frame_from_disk(s_read.layout, b.ref)
    d = compare(
        MetricFrame(_df=df_a, meta=MetricFrameMeta(**meta_a)),
        MetricFrame(_df=df_b, meta=MetricFrameMeta(**meta_b)),
        align="sample",
        session=s_read,
    )
    assert isinstance(d, DeltaFrame)


def test_compare_archived_session_raises_for_cached_session(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe("sales.revenue", session=s)
    b = observe("sales.revenue", session=s)
    session_attach.archive("demo")
    with pytest.raises(SessionStateError):
        compare(a, b, align="sample", session=s)


def test_compare_stale_archived_session_raises(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends={"warehouse": lambda: con})
    a = observe("sales.revenue", session=s)
    b = observe("sales.revenue", session=s)
    session_attach._reset_process_state()
    session_attach.archive("demo")
    assert s.state == "active"
    with pytest.raises(SessionStateError):
        compare(a, b, align="sample", session=s)
