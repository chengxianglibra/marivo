"""End-to-end: semantic_py loader + analysis_py session + observe/compare/load."""

import ibis
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _bootstrap_full_project(tmp_path):
    (tmp_path / ".marivo").mkdir()
    sem_dir = tmp_path / ".marivo" / "semantic" / "sales"
    sem_dir.mkdir(parents=True)
    (sem_dir / "__init__.py").write_text("")
    (sem_dir / "_model.py").write_text("import marivo.semantic_py as ms\nms.model(name='sales')\n")
    (sem_dir / "datasets.py").write_text(
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


def _seed_warehouse():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0),"
        "(2, DATE '2026-07-15', 20.0),"
        "(3, DATE '2026-08-01', 30.0),"
        "(4, DATE '2026-04-10', 5.0),"
        "(5, DATE '2026-05-20', 15.0)"
    )
    return con


def test_end_to_end_sales_observe_compare_load(tmp_path):
    _bootstrap_full_project(tmp_path)
    con = _seed_warehouse()
    s = mv.session.create(
        name="qoq-investigation",
        question="Why did Q3 revenue jump vs Q2?",
        backends={"warehouse": lambda: con},
    )
    assert not s.is_read_only

    q3 = mv.observe(
        mv.MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-09-30"},
        session=s,
    )
    q2 = mv.observe(
        mv.MetricRef("sales.revenue"),
        window={"start": "2026-04-01", "end": "2026-06-30"},
        session=s,
    )
    d = mv.compare(q3, q2, align="sample", compare_type="qoq", session=s)
    df = d.to_pandas()
    assert df.iloc[0]["current"] == pytest.approx(60.0)
    assert df.iloc[0]["baseline"] == pytest.approx(20.0)
    assert df.iloc[0]["delta"] == pytest.approx(40.0)

    assert sorted(j.intent for j in s.jobs()) == ["compare", "observe", "observe"]
    assert {f.kind for f in s.frames()} == {"metric_frame", "delta_frame"}

    reloaded = mv.load_frame(q3.ref, session=s)
    assert reloaded.meta.metric_id == "sales.revenue"
    assert reloaded.meta.session_id == s.id

    session_attach._reset_process_state()
    s_ro = mv.session.attach(name="qoq-investigation")
    assert s_ro.is_read_only
    q3_again = mv.load_frame(q3.ref, session=s_ro)
    q2_again = mv.load_frame(q2.ref, session=s_ro)
    d_again = mv.compare(q3_again, q2_again, align="sample", compare_type="qoq", session=s_ro)
    assert d_again.to_pandas().iloc[0]["delta"] == pytest.approx(40.0)
