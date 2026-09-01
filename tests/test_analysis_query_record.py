"""Tests for QueryExecution record, SQL normalization, and audit capture."""

from __future__ import annotations

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.executor.query_record import (
    QueryExecution,
    compute_sql_digest,
    gen_query_ref,
    normalize_sql,
)
from marivo.analysis.executor.runner import ExecutionResult, execute
from marivo.analysis.session._connections import AnalysisConnectionRuntime
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.semantic.catalog import SemanticKind
from tests.conftest import bootstrap_sales_project
from tests.ref_helpers import make_ref
from tests.run_read_helpers import run_queries


def _runtime(factory=None) -> AnalysisConnectionRuntime:
    return AnalysisConnectionRuntime(
        DatasourceConnectionService(backend_factory=factory, use_datasources=False)
    )


def test_query_execution_keeps_only_executed_sql_and_shape_digest() -> None:
    qe = QueryExecution(
        query_id=gen_query_ref(),
        datasource="warehouse",
        dialect="duckdb",
        sql="SELECT 1",
        sql_digest="abc123",
        row_count=1,
        duration_ms=10,
        started_at="2026-06-03T08:00:00.000000+00:00",
        finished_at="2026-06-03T08:00:00.010000+00:00",
        status="succeeded",
    )
    assert qe.query_id.startswith("query_")
    assert qe.datasource == "warehouse"
    assert qe.dialect == "duckdb"
    assert qe.sql == "SELECT 1"
    assert qe.sql_digest == "abc123"
    assert qe.row_count == 1
    assert qe.duration_ms == 10
    assert qe.status == "succeeded"
    assert not hasattr(qe, "normalized_sql")
    assert not hasattr(qe, "bind_params")


def test_gen_query_ref_format() -> None:
    ref = gen_query_ref()
    assert ref.startswith("query_")
    assert len(ref) == len("query_") + 8  # 8 hex chars from token_hex(4)


def test_normalize_sql_replaces_string_and_number_literals() -> None:
    sql = "SELECT SUM(amount) AS revenue FROM orders WHERE pay_status = 1 AND order_date >= '2026-07-01'"
    normalized = normalize_sql(sql, dialect="duckdb")
    assert "1" not in normalized.replace("?", "")
    assert "'2026-07-01'" not in normalized
    assert "?" in normalized


def test_normalize_sql_strips_session_comment() -> None:
    sql = "/* from=marivo,session=sess_abc123 */\nSELECT 1"
    normalized = normalize_sql(sql, dialect="duckdb")
    assert "from=marivo" not in normalized
    assert "?" in normalized


def test_normalize_sql_fallback_on_parse_failure() -> None:
    sql_a = "TOTAL GARBAGE !@#$% 'a' 1 TRUE"
    sql_b = "TOTAL GARBAGE !@#$% 'b' 2 FALSE"
    normalized_a = normalize_sql(sql_a, dialect="duckdb")
    normalized_b = normalize_sql(sql_b, dialect="duckdb")
    assert compute_sql_digest(normalized_a) == compute_sql_digest(normalized_b)


def test_same_shape_produces_same_digest() -> None:
    sql_a = "SELECT * FROM t WHERE x = 1 AND y = 'a'"
    sql_b = "SELECT * FROM t WHERE x = 2 AND y = 'b'"
    norm_a = normalize_sql(sql_a, dialect="duckdb")
    norm_b = normalize_sql(sql_b, dialect="duckdb")
    assert compute_sql_digest(norm_a) == compute_sql_digest(norm_b)


def test_boolean_literals_produce_same_digest() -> None:
    sql_a = "SELECT * FROM t WHERE enabled = TRUE"
    sql_b = "SELECT * FROM t WHERE enabled = FALSE"
    norm_a = normalize_sql(sql_a, dialect="duckdb")
    norm_b = normalize_sql(sql_b, dialect="duckdb")
    assert compute_sql_digest(norm_a) == compute_sql_digest(norm_b)


def test_different_shape_produces_different_digest() -> None:
    sql_a = "SELECT * FROM t WHERE x = 1"
    sql_b = "SELECT * FROM t WHERE y = 1"
    norm_a = normalize_sql(sql_a, dialect="duckdb")
    norm_b = normalize_sql(sql_b, dialect="duckdb")
    assert compute_sql_digest(norm_a) != compute_sql_digest(norm_b)


# --- AnalysisConnectionRuntime query capture buffer tests ---


def _make_qe(datasource: str = "warehouse") -> QueryExecution:
    return QueryExecution(
        query_id=gen_query_ref(),
        datasource=datasource,
        dialect="duckdb",
        sql="SELECT 1",
        sql_digest="abc123def4567890",
        row_count=1,
        duration_ms=10,
        started_at="2026-06-03T08:00:00.000000+00:00",
        finished_at="2026-06-03T08:00:00.010000+00:00",
        status="succeeded",
    )


def test_no_capture_open_by_default() -> None:
    cache = _runtime()
    qe = _make_qe()
    cache.record_query(qe)
    assert cache.take_captured_queries() == []


def test_begin_record_drain_cycle() -> None:
    cache = _runtime()
    cache.begin_query_capture()
    qe = _make_qe()
    cache.record_query(qe)
    queries = cache.take_captured_queries()
    assert len(queries) == 1
    assert queries[0].query_id == qe.query_id
    assert queries[0].datasource == "warehouse"


def test_drain_clears_buffer() -> None:
    cache = _runtime()
    cache.begin_query_capture()
    cache.record_query(_make_qe())
    cache.take_captured_queries()
    assert cache.take_captured_queries() == []


def test_second_begin_clears_previous() -> None:
    cache = _runtime()
    cache.begin_query_capture()
    cache.record_query(_make_qe())
    cache.begin_query_capture()
    assert cache.take_captured_queries() == []


def test_multiple_queries() -> None:
    cache = _runtime()
    cache.begin_query_capture()
    cache.record_query(_make_qe("ds_a"))
    cache.record_query(_make_qe("ds_b"))
    queries = cache.take_captured_queries()
    assert len(queries) == 2
    assert queries[0].datasource == "ds_a"
    assert queries[1].datasource == "ds_b"


# --- execute() QueryExecution integration tests ---


def _duckdb_cache() -> tuple[AnalysisConnectionRuntime, ibis.duckdb.DuckDBBackend]:
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE t (x INTEGER)")
    con.raw_sql("INSERT INTO t VALUES (1), (2), (3)")
    cache = _runtime(lambda _name: con)
    return cache, con


def test_execute_returns_query_execution() -> None:
    cache, con = _duckdb_cache()
    try:
        expr = con.table("t").select("x")
        result = execute(expr, datasource_name="test", cache=cache, session_id="sess_test")
        assert result.query is not None
        assert result.query.datasource == "test"
        assert result.query.dialect == "duckdb"
        assert "from=marivo,session=sess_test" in result.query.sql
        assert result.query.row_count == 3
        assert result.query.duration_ms >= 0
        assert result.query.status == "succeeded"
        assert result.backend_dialect == "duckdb"
        assert result.backend_datetime_decode_policy == "local_naive_label"
    finally:
        cache.close_all()


def test_execute_records_to_capture_buffer() -> None:
    cache, con = _duckdb_cache()
    try:
        expr = con.table("t").select("x")
        cache.begin_query_capture()
        result = execute(expr, datasource_name="test", cache=cache, session_id="sess_test")
        queries = cache.take_captured_queries()
        assert len(queries) == 1
        assert queries[0].query_id == result.query.query_id
        assert queries[0].sql == result.query.sql
    finally:
        cache.close_all()


def test_execute_failure_without_compiled_sql_records_no_query(monkeypatch) -> None:
    cache, con = _duckdb_cache()
    try:
        expr = con.table("t").select("x")

        def fail_before_compile(*_args, **_kwargs):
            raise RuntimeError("compile was never reached")

        monkeypatch.setattr(con, "execute", fail_before_compile)
        cache.begin_query_capture()

        with pytest.raises(mv.errors.BackendError):
            execute(expr, datasource_name="test", cache=cache, session_id="sess_test")

        assert cache.take_captured_queries() == []
    finally:
        cache.close_all()


def test_execute_no_capture_without_session_id() -> None:
    cache, con = _duckdb_cache()
    try:
        expr = con.table("t").select("x")
        result = execute(expr, datasource_name="test", cache=cache, session_id=None)
        assert isinstance(result, ExecutionResult)
    finally:
        cache.close_all()


# --- observe() query capture integration tests ---


def test_scalar_observe_has_queries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
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
    s = mv.session.get_or_create(
        name="audit-test",
        question="audit",
        backends={"warehouse": lambda: con},
    )
    frame = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-09-30"),
    )
    queries = run_queries(s, output_ref=frame.ref)
    assert len(queries) >= 1
    q = queries[0]
    assert q.datasource == "warehouse"
    assert q.row_count == 1
    assert q.duration_ms >= 0
    assert q.status == "succeeded"
    assert q.sql_digest
    assert "from=marivo,session=" in q.sql


def test_decompose_job_record_has_queries_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 'north', 100),"
        "(2, DATE '2026-07-02', 20.0, 'south', 200),"
        "(3, DATE '2026-08-01', 30.0, 'north', 300)"
    )
    s = mv.session.get_or_create(
        name="decompose-audit",
        question="decompose audit",
        backends={"warehouse": lambda: con},
    )
    frame = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-08-31"),
        dimensions=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )
    delta = s.compare(
        frame,
        frame,
        alignment=mv.window_bucket(),
    )
    attr = s.attribute(delta, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)])
    # Frame-consuming intents issue no datasource SQL
    assert run_queries(s, output_ref=attr.ref) == ()


def test_time_series_observe_has_queries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
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
    s = mv.session.get_or_create(
        name="audit-ts-test",
        question="audit ts",
        backends={"warehouse": lambda: con},
    )
    frame = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-09-30"),
        grain=mv.grain("day"),
    )
    queries = run_queries(s, output_ref=frame.ref)
    assert len(queries) >= 1
    q = queries[0]
    assert q.dialect == "duckdb"


# --- Acceptance criteria tests (spec §11) ---


def test_observe_shapes_have_queries(tmp_path, monkeypatch):
    """§11: After each observe shape, queries[] has >=1 entry with correct datasource/row_count/duration_ms."""
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
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
    s = mv.session.get_or_create(
        name="acceptance",
        question="acceptance tests",
        backends={"warehouse": lambda: con},
    )
    # Scalar
    scalar = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-09-30"),
    )
    queries = run_queries(s, output_ref=scalar.ref)
    assert len(queries) >= 1
    q = queries[0]
    assert q.datasource == "warehouse"
    assert q.row_count >= 1
    assert q.duration_ms >= 0
    assert q.sql_digest

    # Time series
    ts = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-09-30"),
        grain=mv.grain("month"),
    )
    queries = run_queries(s, output_ref=ts.ref)
    assert len(queries) >= 1
    assert queries[0].datasource == "warehouse"

    # Segmented
    seg = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-09-30"),
        dimensions=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )
    assert len(run_queries(s, output_ref=seg.ref)) >= 1

    # Panel
    panel = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-09-30"),
        grain=mv.grain("month"),
        dimensions=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )
    assert len(run_queries(s, output_ref=panel.ref)) >= 1


def test_same_query_shape_same_digest(tmp_path, monkeypatch):
    """§11: Two observes differing only in literal filter values produce identical sql_digest."""
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
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
    s = mv.session.get_or_create(
        name="digest-acceptance",
        question="digest acceptance",
        backends={"warehouse": lambda: con},
    )
    f1 = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-31"),
    )
    f2 = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-08-01", end="2026-08-31"),
    )
    assert (
        run_queries(s, output_ref=f1.ref)[0].sql_digest
        == run_queries(s, output_ref=f2.ref)[0].sql_digest
    )


def test_evidence_chain_reaches_queries(tmp_path, monkeypatch):
    """§11: Starting from a finding's artifact_id, the chain reaches queries[]."""
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
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
    s = mv.session.get_or_create(
        name="chain-acceptance",
        question="chain acceptance",
        backends={"warehouse": lambda: con},
    )
    frame = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-09-30"),
    )
    job_id = frame.meta.produced_by_job
    queries = run_queries(s, output_ref=frame.ref)
    assert len(queries) >= 1
    # The frame's lineage carries the job_ref
    assert frame.meta.lineage.steps[0].job_ref == job_id
    run = s.get_run(job_id)
    assert isinstance(run, mv.SucceededRun)
    assert run.output_artifact_ref == frame.ref


def test_failed_query_is_persisted_on_failed_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
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
    s = mv.session.get_or_create(
        name="fail-acceptance",
        question="fail acceptance",
        backends={"warehouse": lambda: con},
    )

    def _failing_execute(expr, *args, **kwargs):
        con.compile(expr)
        raise RuntimeError("forced failure for test")

    monkeypatch.setattr(con, "execute", _failing_execute)

    with pytest.raises(mv.errors.BackendError):
        s.observe(
            make_ref("sales.revenue", SemanticKind.METRIC),
            time_scope=mv.time_scope(start="2026-07-01", end="2026-09-30"),
        )

    failed = next(
        run
        for run in s.runs(status="failed", capability_id="observe", limit=100).items
        if isinstance(run, mv.FailedRun)
    )
    assert len(failed.queries) == 1
    query = failed.queries[0]
    assert query.status == "failed"
    assert query.row_count == 0
    assert query.datasource == "warehouse"
    assert "from=marivo,session=" in query.sql
