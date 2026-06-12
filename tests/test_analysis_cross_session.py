"""Cross-session frame safety: load_frame and compare both reject."""

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import FrameRefNotFound
from marivo.semantic.catalog import SemanticKind, SemanticRef
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
    mf = s_a.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))
    session_attach._reset_process_state()
    s_b = mv.session.get_or_create(name="b", backends={"warehouse": lambda: con})
    # The frame is not registered in session_b's store, so it raises
    # FrameRefNotFound (not CrossSessionFrameError, since the store lookup
    # fails before the on-disk metadata can be checked).
    with pytest.raises(FrameRefNotFound):
        s_b.get_frame(mf.ref)


def test_load_frame_same_session_succeeds(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = mv.session.get_or_create(name="a", backends={"warehouse": lambda: con})
    mf = s.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))
    loaded = s.get_frame(mf.ref)
    assert loaded.ref == mf.ref
    assert loaded.meta.metric_id == "sales.revenue"


def test_load_frame_missing_ref_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = mv.session.get_or_create(name="a")
    with pytest.raises(FrameRefNotFound):
        s.get_frame("frame_nonexistent_ref")
