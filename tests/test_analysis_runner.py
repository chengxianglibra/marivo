"""apply_window_to_dataset / apply_slice_to_dataset / execute against ibis."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import ibis
import pytest

from marivo.analysis.datasources import registry as datasource_registry
from marivo.analysis.errors import BackendError, SliceInvalidError, WindowInvalidError
from marivo.analysis.executor.backend import BackendCache
from marivo.analysis.executor.runner import (
    ExecutionResult,
    apply_slice_to_dataset,
    apply_time_series_bucket,
    apply_window_to_dataset,
    execute,
)
from marivo.analysis.windows.spec import AbsoluteWindow
from marivo.semantic import SemanticProject

# ---------------------------------------------------------------------------
# Helper: build a SemanticProject with files on disk so the loader works
# ---------------------------------------------------------------------------


def _bootstrap_project(
    tmp_path,
    *,
    with_time_field: bool = True,
    model_name: str = "sales",
    dataset_name: str = "orders",
    datasource_name: str = "warehouse",
    time_field_data_type: str = "date",
) -> SemanticProject:
    """Write semantic model files on disk and return a loaded SemanticProject."""
    semantic_dir = tmp_path / ".marivo" / "semantic" / model_name
    semantic_dir.mkdir(parents=True, exist_ok=True)
    datasource_dir = semantic_dir.parent.parent / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / f"{datasource_name}.py").write_text(
        "import marivo.datasource as md\n"
        f"md.datasource(name='{datasource_name}', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        f"import marivo.semantic as ms\nms.model(name='{model_name}')\n"
    )

    time_field_block = ""
    if with_time_field:
        time_field_block = (
            f"\n@ms.time_field(dataset={dataset_name}, "
            f"data_type='{time_field_data_type}', granularity='day')\n"
            f"def created_at({dataset_name}):\n"
            f"    return {dataset_name}.created_at\n"
        )

    (semantic_dir / "definitions.py").write_text(
        f"import marivo.semantic as ms\n"
        f"\n"
        f"@ms.dataset(name='{dataset_name}', datasource='{datasource_name}')\n"
        f"def {dataset_name}(backend):\n"
        f"    return backend.table('{dataset_name}')\n"
        f"\n"
        f"@ms.field(dataset={dataset_name})\n"
        f"def region({dataset_name}):\n"
        f"    return {dataset_name}.region.upper()\n"
        f"{time_field_block}"
    )

    project = SemanticProject(root=tmp_path / ".marivo" / "semantic")
    project.load()
    return project


def _build_dataset_adapter(sp: SemanticProject, dataset_semantic_id: str) -> object:
    """Build an adapter object that mimics old-style DatasetIR for runner.py.

    The runner expects dataset_ir.fn(backend), dataset_ir.fields, etc.
    """
    from marivo.analysis.intents.observe import _build_dataset_adapter as _build

    dataset_ir = sp.get_dataset(dataset_semantic_id)
    assert dataset_ir is not None, f"Dataset {dataset_semantic_id} not found"
    return _build(sp, dataset_ir)


def _seed_backend(table_name: str = "orders") -> ibis.duckdb.DuckDBBackend:
    con = ibis.duckdb.connect(":memory:")
    if table_name == "orders":
        con.raw_sql(
            "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, region VARCHAR)"
        )
        con.raw_sql(
            "INSERT INTO orders VALUES "
            "(1, DATE '2026-07-01', 10.0, 'north'),"
            "(2, DATE '2026-08-01', 20.0, 'south')"
        )
    elif table_name == "t":
        con.raw_sql("CREATE TABLE t (x INTEGER)")
    return con


def test_apply_window_filters_rows(tmp_path):
    sp = _bootstrap_project(tmp_path)
    con = _seed_backend()
    ds_adapter = _build_dataset_adapter(sp, "sales.orders")
    filtered = apply_window_to_dataset(
        ds_adapter.fn(con),
        {"start": "2026-07-01", "end": "2026-07-31"},
        dataset_ir=ds_adapter,
    )
    df = filtered.execute()
    assert len(df) == 1
    assert df.iloc[0]["order_id"] == 1


def test_apply_slice_filters_by_declared_field(tmp_path):
    sp = _bootstrap_project(tmp_path)
    con = _seed_backend()
    ds_adapter = _build_dataset_adapter(sp, "sales.orders")
    filtered = apply_slice_to_dataset(
        ds_adapter.fn(con), {"region": "NORTH"}, dataset_ir=ds_adapter
    )
    df = filtered.execute()
    assert len(df) == 1
    assert df.iloc[0]["region"] == "north"


def test_apply_slice_unknown_field_raises(tmp_path):
    sp = _bootstrap_project(tmp_path)
    con = _seed_backend()
    ds_adapter = _build_dataset_adapter(sp, "sales.orders")
    with pytest.raises(SliceInvalidError):
        apply_slice_to_dataset(ds_adapter.fn(con), {"bogus_field": 1}, dataset_ir=ds_adapter)


def test_apply_window_dataset_without_time_field_raises(tmp_path):
    sp = _bootstrap_project(
        tmp_path, with_time_field=False, model_name="x", dataset_name="t", datasource_name="w"
    )
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE t (x INTEGER)")
    ds_adapter = _build_dataset_adapter(sp, "x.t")
    with pytest.raises(WindowInvalidError):
        apply_window_to_dataset(
            ds_adapter.fn(con),
            {"start": "2026-01-01", "end": "2026-12-31"},
            dataset_ir=ds_adapter,
        )


def test_execute_returns_dataframe_with_timing():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (1),(2),(3);")
    cache = BackendCache(lambda name: con)
    result = execute(con.table("t").x.sum(), datasource_name="warehouse", cache=cache)
    assert isinstance(result, ExecutionResult)
    assert result.row_count >= 1
    assert result.duration_ms >= 0


def test_execute_persists_backend_env_sourced_secrets_once_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class FakeBackend:
        def execute(self, expr):
            return 1

    backend = FakeBackend()
    cache = BackendCache(lambda name: backend)
    monkeypatch.setattr(
        datasource_registry,
        "_persist_backend_env_sourced_secrets",
        lambda received: calls.append(received),
    )

    execute(object(), datasource_name="warehouse", cache=cache)
    execute(object(), datasource_name="warehouse", cache=cache)

    assert calls == [backend]


def test_execute_prefixes_compiled_sql_with_session_comment():
    class FakeBackend:
        def __init__(self):
            self.executed_sql = []

        def compile(self, expr):
            return "SELECT 1"

        def execute(self, expr):
            self.executed_sql.append(self.compile(expr))
            return 1

    backend = FakeBackend()
    original_compile = backend.compile
    cache = BackendCache(lambda name: backend)

    execute(object(), datasource_name="warehouse", cache=cache, session_id="sess_abc123")

    assert backend.executed_sql == ["/* from=marivo,session=sess_abc123 */\nSELECT 1"]
    assert backend.compile.__func__ is original_compile.__func__


def test_execute_without_session_id_leaves_compiled_sql_unmodified():
    class FakeBackend:
        def __init__(self):
            self.executed_sql = []

        def compile(self, expr):
            return "SELECT 1"

        def execute(self, expr):
            self.executed_sql.append(self.compile(expr))
            return 1

    backend = FakeBackend()
    cache = BackendCache(lambda name: backend)

    execute(object(), datasource_name="warehouse", cache=cache)

    assert backend.executed_sql == ["SELECT 1"]


def test_execute_sanitizes_session_id_in_sql_comment():
    class FakeBackend:
        def __init__(self):
            self.executed_sql = []

        def compile(self, expr):
            return "SELECT 1"

        def execute(self, expr):
            self.executed_sql.append(self.compile(expr))
            return 1

    backend = FakeBackend()
    cache = BackendCache(lambda name: backend)

    execute(object(), datasource_name="warehouse", cache=cache, session_id="sess_bad*/\nnext")

    assert backend.executed_sql == ["/* from=marivo,session=sess_bad* / next */\nSELECT 1"]


def test_execute_wraps_backend_errors():
    class FakeBackend:
        def execute(self, expr):
            raise RuntimeError("backend exploded")

    cache = BackendCache(lambda name: FakeBackend())
    with pytest.raises(BackendError):
        execute(object(), datasource_name="warehouse", cache=cache)


def test_execute_does_not_persist_backend_env_sourced_secrets_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class FakeBackend:
        def execute(self, expr):
            raise RuntimeError("backend exploded")

    cache = BackendCache(lambda name: FakeBackend())
    monkeypatch.setattr(
        datasource_registry,
        "_persist_backend_env_sourced_secrets",
        lambda received: calls.append(received),
    )

    with pytest.raises(BackendError):
        execute(object(), datasource_name="warehouse", cache=cache)

    assert calls == []


def test_apply_time_series_bucket_adds_bucket_start(tmp_path):
    sp = _bootstrap_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (created_at DATE, amount DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (DATE '2026-05-01', 10.0), (DATE '2026-05-02', 20.0)")

    ds_adapter = _build_dataset_adapter(sp, "sales.orders")
    bucketed = apply_time_series_bucket(
        ds_adapter.fn(con),
        field_ir=ds_adapter.fields["created_at"],
        window=AbsoluteWindow(start="2026-05-01", end="2026-05-31", grain="day"),
        session_tz=ZoneInfo("UTC"),
    )
    assert "bucket_start" in bucketed.columns
    df = bucketed.order_by("created_at").execute()
    assert df["bucket_start"].tolist() == df["created_at"].tolist()
    assert str(df.iloc[0]["bucket_start"]) == "2026-05-01 00:00:00"


def test_apply_time_series_bucket_day_respects_session_tz_for_timestamp(tmp_path):
    sp = _bootstrap_project(tmp_path, time_field_data_type="timestamp")
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (created_at TIMESTAMP, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(TIMESTAMP '2026-04-30 23:59:59', 10.0), "
        "(TIMESTAMP '2026-05-01 00:00:00', 20.0)"
    )

    ds_adapter = _build_dataset_adapter(sp, "sales.orders")
    bucketed = apply_time_series_bucket(
        ds_adapter.fn(con),
        field_ir=ds_adapter.fields["created_at"],
        window=AbsoluteWindow(start="2026-05-01", end="2026-05-01", grain="day"),
        session_tz=ZoneInfo("Asia/Shanghai"),
    )
    df = bucketed.order_by("created_at").execute()
    assert [item.strftime("%Y-%m-%d") for item in df["bucket_start"]] == [
        "2026-04-30",
        "2026-05-01",
    ]
