"""Idempotent frame caching: observe/compare return cached frames on repeat calls,
and session.get_frame() recovers frames across script boundaries."""

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    FrameCacheCorruptedError,
    FrameRefNotFound,
)
from marivo.analysis.frames.component import ComponentFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.semantic.catalog import SemanticKind, SemanticRef
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
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


def _make_session(tmp_path, con):
    bootstrap_sales_project(tmp_path)
    return session_attach.get_or_create(name="demo", backends=_backends(con))


# --- observe idempotent caching ---


def test_observe_idempotent_cache_hit(tmp_path):
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = _make_session(tmp_path, con)

    first = s.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))
    assert isinstance(first, MetricFrame)

    # Second call with identical inputs should return the cached frame.
    second = s.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))
    assert isinstance(second, MetricFrame)
    assert second.ref == first.ref


def test_observe_cache_hit_after_reattach(tmp_path):
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = _make_session(tmp_path, con)

    first = s.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))
    ref = first.ref

    # Simulate a new script: reset process state and reattach.
    session_attach._reset_process_state()
    s2 = session_attach.get_or_create(name="demo", backends=_backends(con))

    second = s2.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))
    assert second.ref == ref


def test_observe_different_inputs_cache_miss(tmp_path):
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = _make_session(tmp_path, con)

    first = s.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))
    second = s.observe(
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2026-07-01", "end": "2026-08-01"},
    )
    assert first.ref != second.ref


# --- compare idempotent caching ---


def test_compare_idempotent_cache_hit(tmp_path):
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = _make_session(tmp_path, con)

    cur = s.observe(
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2026-07-01", "end": "2026-10-01"},
    )
    base = s.observe(
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2025-07-01", "end": "2025-10-01"},
    )
    first = s.compare(cur, base)
    assert isinstance(first, DeltaFrame)

    second = s.compare(cur, base)
    assert isinstance(second, DeltaFrame)
    assert second.ref == first.ref


# --- session.get_frame ---


def test_get_frame_returns_live_frame(tmp_path):
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = _make_session(tmp_path, con)

    original = s.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))
    loaded = s.get_frame(original.ref)

    assert isinstance(loaded, MetricFrame)
    assert loaded.ref == original.ref
    assert loaded.meta.metric_id == "sales.revenue"
    assert loaded.to_pandas().equals(original.to_pandas())


def test_get_frame_cross_script(tmp_path):
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = _make_session(tmp_path, con)

    original = s.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))
    ref = original.ref

    # Simulate a new script.
    session_attach._reset_process_state()
    s2 = session_attach.get_or_create(name="demo", backends=_backends(con))

    loaded = s2.get_frame(ref)
    assert loaded.ref == ref
    assert loaded.meta.metric_id == "sales.revenue"


def test_get_frame_not_found(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(name="demo")

    with pytest.raises(FrameRefNotFound):
        s.get_frame("frame_nonexistent")


def test_get_frame_corrupted(tmp_path):
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = _make_session(tmp_path, con)

    original = s.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))
    ref = original.ref

    # Corrupt the parquet file.
    parquet_path = s._layout.frames_dir / ref / "data.parquet"
    parquet_path.write_bytes(b"not a parquet file")

    with pytest.raises(FrameCacheCorruptedError):
        s.get_frame(ref)


# --- session.frame_summaries ---


def test_frame_summaries_contains_rich_metadata(tmp_path):
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = _make_session(tmp_path, con)

    s.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))

    summaries = s.frame_summaries()
    assert len(summaries) >= 1
    entry = summaries[0]
    assert entry.kind == "metric_frame"
    assert entry.metric_id == "sales.revenue"
    assert entry.semantic_kind is not None
    assert entry.semantic_model is not None


# --- derived-metric observe caching ---


def _bootstrap_failure_rate(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)\n"
        "def failed_count(orders):\n"
        "    return (orders.state == 'FAILED').cast('int64').sum()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)\n"
        "def total_count(orders):\n"
        "    return orders.count()\n"
        "\n"
        "ms.derived_metric(\n"
        "    name='failure_rate',\n"
        "    decomposition=ms.ratio(\n"
        "        numerator='sales.failed_count',\n"
        "        denominator='sales.total_count',\n"
        "    ),\n"
        ")\n"
    )


def _seed_failure_rate(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, state VARCHAR)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 'FAILED'),"
        "(2, DATE '2026-07-02', 'SUCCEEDED'),"
        "(3, DATE '2026-07-03', 'FAILED'),"
        "(4, DATE '2026-07-04', 'SUCCEEDED')"
    )


def test_observe_derived_metric_cache_hit(tmp_path):
    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_rate(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    first = s.observe(SemanticRef("sales.failure_rate", kind=SemanticKind.METRIC))
    assert isinstance(first, MetricFrame)

    second = s.observe(SemanticRef("sales.failure_rate", kind=SemanticKind.METRIC))
    assert isinstance(second, MetricFrame)
    assert second.ref == first.ref


def test_observe_derived_metric_cache_hit_components_accessible(tmp_path):
    """After a cache hit, frame.components() returns a valid ComponentFrame."""
    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_rate(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    first = s.observe(SemanticRef("sales.failure_rate", kind=SemanticKind.METRIC))
    first_components = first.components()
    assert isinstance(first_components, ComponentFrame)

    # Second call returns cached frame
    second = s.observe(SemanticRef("sales.failure_rate", kind=SemanticKind.METRIC))
    assert second.ref == first.ref
    # Components must still be accessible
    second_components = second.components()
    assert isinstance(second_components, ComponentFrame)
    assert second_components.meta.parent_ref == first.ref


def test_observe_derived_metric_components_after_reattach(tmp_path):
    """frame.components() works after session re-attachment (new process)."""
    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_rate(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = s.observe(SemanticRef("sales.failure_rate", kind=SemanticKind.METRIC))
    ref = frame.ref

    # Simulate new process
    session_attach._reset_process_state()
    s2 = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    loaded = s2.get_frame(ref)
    components = loaded.components()
    assert isinstance(components, ComponentFrame)
    assert components.meta.parent_ref == ref


def test_components_via_session_get_frame(tmp_path):
    """frame.components() works on a frame loaded via session.get_frame()."""
    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_rate(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = s.observe(SemanticRef("sales.failure_rate", kind=SemanticKind.METRIC))
    ref = frame.ref

    loaded = s.get_frame(ref)
    components = loaded.components()
    assert isinstance(components, ComponentFrame)
    assert components.meta.decomposition_kind == "ratio"


def test_compare_derived_metric_delta_components_after_cache_hit(tmp_path):
    """DeltaFrame.components() works after a cache hit on compare."""
    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_rate(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    cur = s.observe(
        SemanticRef("sales.failure_rate", kind=SemanticKind.METRIC),
        timescope={"start": "2026-07-01", "end": "2026-07-04"},
        grain="day",
    )
    base = s.observe(
        SemanticRef("sales.failure_rate", kind=SemanticKind.METRIC),
        timescope={"start": "2026-07-01", "end": "2026-07-04"},
        grain="day",
    )

    delta = s.compare(cur, base)
    assert delta.meta.component_ref is not None
    delta_comp = delta.components()
    assert isinstance(delta_comp, ComponentFrame)

    # Cache hit
    delta2 = s.compare(cur, base)
    assert delta2.ref == delta.ref
    delta_comp2 = delta2.components()
    assert isinstance(delta_comp2, ComponentFrame)


# --- session.frame_summaries smoke test ---


def test_frame_summaries_returns_refs(tmp_path):
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = _make_session(tmp_path, con)

    s.observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC))

    refs = s.frame_summaries()
    assert len(refs) >= 1
    assert all(r.ref and r.kind for r in refs)
