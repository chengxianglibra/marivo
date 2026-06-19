"""End-to-end: semantic loader + analysis session + observe/compare/load."""

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


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
    bootstrap_sales_project(tmp_path)
    con = _seed_warehouse()
    s = mv.session.get_or_create(
        name="qoq-investigation",
        question="Why did Q3 revenue jump vs Q2?",
        backends={"warehouse": lambda: con},
    )
    assert not s.is_read_only

    q3 = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        timescope={"start": "2026-07-01", "end": "2026-09-30"},
    )
    q2 = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        timescope={"start": "2026-04-01", "end": "2026-06-30"},
    )
    d = s.compare(
        q3,
        q2,
        alignment=mv.AlignmentPolicy(kind="window_bucket"),
    )
    df = d.to_pandas()
    assert df.iloc[0]["current"] == pytest.approx(60.0)
    assert df.iloc[0]["baseline"] == pytest.approx(20.0)
    assert df.iloc[0]["delta"] == pytest.approx(40.0)

    assert sorted(j.intent for j in s.jobs()) == ["compare", "observe", "observe"]
    assert {f.kind for f in s.frame_summaries()} == {"metric_frame", "delta_frame"}

    reloaded = s.get_frame(q3.ref)
    assert reloaded.meta.metric_id == "sales.revenue"
    assert reloaded.meta.session_id == s.id

    session_attach._reset_process_state()
    s_ro = mv.session.get_or_create(name="qoq-investigation", use_datasources=False)
    assert s_ro.is_read_only
    q3_again = s_ro.get_frame(q3.ref)
    q2_again = s_ro.get_frame(q2.ref)
    d_again = s_ro.compare(
        q3_again,
        q2_again,
        alignment=mv.AlignmentPolicy(kind="window_bucket"),
    )
    assert d_again.to_pandas().iloc[0]["delta"] == pytest.approx(40.0)
