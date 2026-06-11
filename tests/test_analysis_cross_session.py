"""Cross-session frame safety: load_frame and compare both reject."""

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import CrossSessionFrameError, FrameRefNotFound
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, amount DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (1, 10.0), (2, 20.0)")


def test_load_frame_cross_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s_a = mv.session.get_or_create(name="a", backends={"warehouse": lambda: con})
    mf = s_a.observe(mv.MetricRef("sales.revenue"))
    session_attach._reset_process_state()
    s_b = mv.session.get_or_create(name="b", backends={"warehouse": lambda: con})
    with pytest.raises(CrossSessionFrameError):
        s_b.get_frame(mf.ref)


def test_load_frame_same_session_succeeds(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = mv.session.get_or_create(name="a", backends={"warehouse": lambda: con})
    mf = s.observe(mv.MetricRef("sales.revenue"))
    loaded = s.get_frame(mf.ref)
    assert loaded.ref == mf.ref
    assert loaded.meta.metric_id == "sales.revenue"


def test_load_frame_missing_ref_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = mv.session.get_or_create(name="a")
    with pytest.raises(FrameRefNotFound):
        s.get_frame("frame_nonexistent_ref")
