"""mv.observe end-to-end against a seeded DuckDB."""

import ibis
import pytest

import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import (
    MetricNotFoundError,
    NoBackendFactoryError,
    SemanticKindMismatchError,
    SessionStateError,
    WindowInvalidError,
)
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.refs import MetricRef
from tests.conftest import bootstrap_sales_project


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


def _backends(con):
    return {"warehouse": lambda: con}


def _bootstrap_sales_with_two_time_fields(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource_py as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        "@ms.dataset(name='orders', datasource='warehouse')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def create_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='timestamp', granularity='hour')\n"
        "def create_time(orders):\n"
        "    return orders.created_ts\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_two_time_fields(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, created_ts TIMESTAMP, amount DOUBLE)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', TIMESTAMP '2026-07-01 08:00:00', 10.0),"
        "(2, DATE '2026-07-02', TIMESTAMP '2026-07-02 09:00:00', 20.0)"
    )


def test_observe_returns_metric_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    mf = observe(MetricRef("sales.revenue"), session=s)
    assert isinstance(mf, MetricFrame)
    assert mf.meta.metric_id == "sales.revenue"
    assert mf.meta.session_id == s.id


def test_observe_rejects_bare_metric_string(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe("sales.revenue", session=s)  # type: ignore[arg-type]

    assert exc_info.value.details["expected_kind"] == "MetricRef"
    assert exc_info.value.details["got_kind"] == "str"
    rendered = str(exc_info.value)
    assert "frame kind" not in rendered
    assert 'mv.MetricRef("sales.revenue")' in rendered


def test_observe_applies_window(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    mf = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)


def test_observe_multiple_time_fields_mentions_time_field_fix(tmp_path):
    _bootstrap_sales_with_two_time_fields(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_two_time_fields(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(WindowInvalidError) as exc_info:
        observe(
            MetricRef("sales.revenue"),
            window={"start": "2026-07-01", "end": "2026-07-31"},
            session=s,
        )

    rendered = str(exc_info.value)
    assert "multiple time_fields" in rendered
    assert "create_date" in rendered
    assert "create_time" in rendered
    assert '"time_field": "create_date"' in rendered


def test_observe_multiple_time_fields_accepts_explicit_time_field(tmp_path):
    _bootstrap_sales_with_two_time_fields(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_two_time_fields(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31", "time_field": "create_date"},
        session=s,
    )

    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)


def test_observe_applies_slice(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    mf = observe(MetricRef("sales.revenue"), where={"region": "NORTH"}, session=s)
    assert mf.to_pandas().iloc[0, 0] == pytest.approx(70.0)


def test_observe_unknown_metric_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    with pytest.raises(MetricNotFoundError):
        observe(MetricRef("sales.nonexistent"), session=s)


def test_observe_errored_project_raises(tmp_path, monkeypatch):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    # Simulate a project that re-loads and stays errored
    from marivo.semantic_py.errors import SemanticLoadFailed

    def fail_load(self):
        from marivo.semantic_py.errors import SemanticError
        from marivo.semantic_py.loader import LoadResult

        err = SemanticError(kind="test_error", message="test error")
        result = LoadResult(status="errored", errors=(err,))
        # Also update the project state
        self._status = result.status
        self._errors = result.errors
        self._registry = result.registry
        self._sidecar = result.sidecar
        return result

    monkeypatch.setattr(type(s.semantic_project), "load", fail_load)
    s.semantic_project._status = "unloaded"

    with pytest.raises(SemanticLoadFailed):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_read_only_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(name="demo", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_persists_job_and_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    mf = observe(MetricRef("sales.revenue"), session=s)
    summaries = s.jobs()
    assert len(summaries) == 1
    assert summaries[0].intent == "observe"
    assert summaries[0].output_frame_ref == mf.ref
    assert (s.layout.frames_dir / mf.ref / "data.parquet").is_file()


def test_observe_archived_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    session_attach.archive("demo")
    with pytest.raises(SessionStateError):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_stale_archived_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    session_attach._reset_process_state()
    session_attach.archive("demo")
    assert s.state == "active"
    with pytest.raises(SessionStateError):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_persists_known_datasources(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    observe(MetricRef("sales.revenue"), session=s)
    session_attach._reset_process_state()
    reattached = session_attach.get_or_create(name="demo")
    assert reattached.known_datasources == {"warehouse"}
