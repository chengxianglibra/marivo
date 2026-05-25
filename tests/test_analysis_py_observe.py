"""mv.observe end-to-end against a seeded DuckDB."""

import ibis
import pytest

import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import (
    MetricNotFoundError,
    NoBackendFactoryError,
    SemanticKindMismatchError,
    SessionStateError,
)
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.refs import MetricRef
from marivo.semantic_py.errors import SemanticLoadError


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 'north', 100),"
        "(2, DATE '2026-07-02', 20.0, 'north', 100),"
        "(3, DATE '2026-08-01', 30.0, 'south', 200),"
        "(4, DATE '2026-09-15', 40.0, 'north', 300)"
    )


def _bootstrap_sales(tmp_path, *, with_time: bool = True):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    time_field = (
        "@ms.time_field(dataset='orders', data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        if with_time
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse(): ...\n"
        "\n"
        "@ms.dataset(name='orders', datasource=warehouse)\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        f"{time_field}"
        "@ms.field(dataset='orders')\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _backends(con):
    return {"warehouse": lambda: con}


def test_observe_returns_metric_frame(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))
    mf = observe(MetricRef("sales.revenue"), session=s)
    assert isinstance(mf, MetricFrame)
    assert mf.meta.metric_id == "sales.revenue"
    assert mf.meta.session_id == s.id


def test_observe_rejects_bare_metric_string(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe("sales.revenue", session=s)  # type: ignore[arg-type]

    assert exc_info.value.details["expected_kind"] == "MetricRef"
    assert exc_info.value.details["got_kind"] == "str"
    rendered = str(exc_info.value)
    assert "frame kind" not in rendered
    assert 'mv.MetricRef("sales.revenue")' in rendered


def test_observe_applies_window(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))
    mf = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)


def test_observe_applies_slice(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))
    mf = observe(MetricRef("sales.revenue"), slice={"region": "NORTH"}, session=s)
    assert mf.to_pandas().iloc[0, 0] == pytest.approx(70.0)


def test_observe_unknown_metric_raises(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))
    with pytest.raises(MetricNotFoundError):
        observe(MetricRef("sales.nonexistent"), session=s)


def test_observe_semantic_load_error_is_not_metric_not_found(tmp_path, monkeypatch):
    _bootstrap_sales(tmp_path)
    s = session_attach.create(name="demo")
    error = SemanticLoadError([])

    def fail_load(*, project=None):
        raise error

    monkeypatch.setattr("marivo.semantic_py.reader.ensure_loaded", fail_load)

    with pytest.raises(SemanticLoadError) as exc_info:
        observe(MetricRef("sales.revenue"), session=s)

    assert exc_info.value is error


def test_observe_read_only_session_raises(tmp_path):
    _bootstrap_sales(tmp_path)
    s = session_attach.create(name="demo")
    with pytest.raises(NoBackendFactoryError):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_persists_job_and_frame(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))
    mf = observe(MetricRef("sales.revenue"), session=s)
    summaries = s.jobs()
    assert len(summaries) == 1
    assert summaries[0].intent == "observe"
    assert summaries[0].output_frame_ref == mf.ref
    assert (s.layout.frames_dir / mf.ref / "data.parquet").is_file()


def test_observe_archived_session_raises(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))
    session_attach.archive("demo")
    with pytest.raises(SessionStateError):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_stale_archived_session_raises(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))
    session_attach._reset_process_state()
    session_attach.archive("demo")
    assert s.state == "active"
    with pytest.raises(SessionStateError):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_persists_known_datasources(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))
    observe(MetricRef("sales.revenue"), session=s)
    session_attach._reset_process_state()
    reattached = session_attach.attach(name="demo")
    assert reattached.known_datasources == {"warehouse"}
