"""Cross-session frame safety: load_frame and compare both reject."""

import ibis
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import CrossSessionFrameError, FrameRefNotFound


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _bootstrap(tmp_path):
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
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, amount DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (1, 10.0), (2, 20.0)")


def test_load_frame_cross_session_raises(tmp_path):
    _bootstrap(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s_a = mv.session.create(name="a", backends={"warehouse": lambda: con})
    mf = mv.observe("sales.revenue", session=s_a)
    session_attach._reset_process_state()
    s_b = mv.session.create(name="b", backends={"warehouse": lambda: con})
    with pytest.raises(CrossSessionFrameError):
        mv.load_frame(mf.ref, session=s_b)


def test_load_frame_same_session_succeeds(tmp_path):
    _bootstrap(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = mv.session.create(name="a", backends={"warehouse": lambda: con})
    mf = mv.observe("sales.revenue", session=s)
    loaded = mv.load_frame(mf.ref, session=s)
    assert loaded.ref == mf.ref
    assert loaded.meta.metric_id == "sales.revenue"


def test_load_frame_missing_ref_raises(tmp_path):
    _bootstrap(tmp_path)
    s = mv.session.create(name="a")
    with pytest.raises(FrameRefNotFound):
        mv.load_frame("frame_nonexistent_ref", session=s)
