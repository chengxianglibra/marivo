# ClickHouse Datasource Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `clickhouse` as a supported `backend_type` in Marivo's datasource backend dispatch.

**Architecture:** Extend the existing `_build_*` dispatch pattern in `backends.py` with a `_build_clickhouse` function that maps datasource fields to `ibis.clickhouse.connect()` kwargs. No new abstractions or error classes.

**Tech Stack:** Python, ibis 12.x, clickhouse-connect driver (optional extra)

---

### Task 1: Add ClickHouse backend dispatch and `_build_clickhouse`

**Files:**
- Modify: `marivo/analysis/datasources/backends.py:16` (SUPPORTED_BACKEND_TYPES)
- Modify: `marivo/analysis/datasources/backends.py:47-75` (build_backend dispatch)
- Modify: `marivo/analysis/datasources/backends.py` (add _build_clickhouse after _build_postgres)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_analysis_profiles_backends.py`:

```python
def test_clickhouse_dispatch_with_host(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    captured: dict[str, object] = {}

    class _FakeClickhouse:
        @staticmethod
        def connect(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

    class _FakeIbis:
        clickhouse = _FakeClickhouse()

    monkeypatch.setitem(__import__("sys").modules, "ibis", _FakeIbis())
    datasource = datasource_store.save_one(
        name="ch_ds",
        backend_type="clickhouse",
        fields={"host": "ch.example.com"},
    )

    datasource_backends.build_backend(datasource)

    assert captured["host"] == "ch.example.com"
    assert captured["database"] == "default"
    assert captured["user"] == "default"


def test_clickhouse_required_field_missing(project_root: Path) -> None:
    datasource = datasource_store.save_one(name="ch_ds", backend_type="clickhouse", fields={})
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_backends.build_backend(datasource)
    assert exc_info.value.details["field"] == "host"


def test_clickhouse_optional_fields_pass_through(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    captured: dict[str, object] = {}

    class _FakeClickhouse:
        @staticmethod
        def connect(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

    class _FakeIbis:
        clickhouse = _FakeClickhouse()

    monkeypatch.setitem(__import__("sys").modules, "ibis", _FakeIbis())
    datasource = datasource_store.save_one(
        name="ch_ds",
        backend_type="clickhouse",
        fields={
            "host": "ch.example.com",
            "port": 9440,
            "database": "analytics",
            "user_env": "CLICKHOUSE_USER",
            "password_env": "CLICKHOUSE_PASSWORD",
            "client_name": "marivo",
            "secure": True,
            "compression": "lz4",
            "settings": {"max_execution_time": 60},
        },
    )
    monkeypatch.setenv("CLICKHOUSE_USER", "reader")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "secret123")

    datasource_backends.build_backend(datasource)

    assert captured["host"] == "ch.example.com"
    assert captured["port"] == 9440
    assert captured["database"] == "analytics"
    assert captured["user"] == "reader"
    assert captured["password"] == "secret123"
    assert captured["client_name"] == "marivo"
    assert captured["secure"] is True
    assert captured["compression"] == "lz4"
    assert captured["settings"] == {"max_execution_time": 60}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_analysis_profiles_backends.py::test_clickhouse_dispatch_with_host -v`
Expected: FAIL — `"clickhouse"` is not in `SUPPORTED_BACKEND_TYPES`, so `DatasourceBackendTypeUnsupportedError` is raised

- [ ] **Step 3: Write minimal implementation**

In `marivo/analysis/datasources/backends.py`, make three changes:

1. Update `SUPPORTED_BACKEND_TYPES` on line 16:

```python
SUPPORTED_BACKEND_TYPES: Final[tuple[str, ...]] = ("duckdb", "trino", "mysql", "postgres", "clickhouse")
```

2. Add dispatch branch in `build_backend` after the postgres branch (after line 67):

```python
    if datasource.backend_type == "clickhouse":
        return _build_clickhouse(datasource.name, kwargs)
```

3. Add `_build_clickhouse` function after `_build_postgres` (after line 137):

```python
def _build_clickhouse(name: str, kwargs: Mapping[str, Any]) -> Any:
    import ibis

    host = _require(name, kwargs, "host")
    connect_kwargs: dict[str, Any] = {"host": host}
    connect_kwargs["database"] = kwargs.get("database", "default")
    connect_kwargs["user"] = kwargs.get("user", "default")
    for key in ("port", "password", "client_name", "compression"):
        if key in kwargs:
            connect_kwargs[key] = kwargs[key]
    if "secure" in kwargs:
        connect_kwargs["secure"] = bool(kwargs["secure"])
    if "settings" in kwargs and isinstance(kwargs["settings"], dict):
        connect_kwargs["settings"] = dict(kwargs["settings"])
    return ibis.clickhouse.connect(**connect_kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_analysis_profiles_backends.py -v`
Expected: All tests PASS (both new and existing)

- [ ] **Step 5: Run full test suite and typecheck**

Run: `make test`
Run: `make typecheck`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add marivo/analysis/datasources/backends.py tests/test_analysis_profiles_backends.py
git commit -m "feat: add clickhouse backend_type to datasource dispatch"
```
