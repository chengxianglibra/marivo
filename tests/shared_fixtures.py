"""Shared lightweight fixtures for Python-native analysis tests."""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import duckdb
import ibis

# ---------------------------------------------------------------------------
# Named DuckDB templates (versioned, cached in /tmp)
# ---------------------------------------------------------------------------
# Bump the version string when seeded schema or rows change so cached
# copies rebuild automatically.

_SALES_ORDERS_V = "v1"


def make_metric_frame(
    df: Any,
    *,
    metric_id: str,
    axes: dict[str, Any],
    measure: dict[str, Any],
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
    semantic_model: str,
    window: object | None = None,
    where: dict[str, Any] | None = None,
    session: Any,
) -> Any:
    """Create a persisted MetricFrame for tests without exposing a public constructor."""
    from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
    from marivo.analysis.lineage import Lineage, LineageStep
    from marivo.analysis.session._runtime import persist_frame
    from marivo.analysis.session.core import ensure_session_can_execute
    from marivo.analysis.windows import dump_window, normalize_absolute_window_input

    ensure_session_can_execute(session)
    resolved_window = normalize_absolute_window_input(window)
    frame_ref = f"frame_{secrets.token_hex(4)}"
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="test_make_metric_frame",
                    job_ref=None,
                    inputs=[],
                    params_digest="test",
                )
            ],
            external_inputs=[frame_ref],
        ),
        metric_id=metric_id,
        axes=axes,
        measure=measure,
        window=dump_window(resolved_window),
        where=where or {},
        semantic_kind=semantic_kind,
        semantic_model=semantic_model,
    )
    frame = MetricFrame(_df=df.copy(), meta=meta)
    frame.meta = persist_frame(session, frame)
    return frame


def _template_cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "marivo_test_templates"
    d.mkdir(exist_ok=True)
    return d


def sales_orders_template() -> Path:
    """Cached DuckDB file with the standard orders table.

    Schema: orders(order_id INTEGER, created_at DATE, amount DOUBLE,
                   region VARCHAR, user_id INTEGER)

    Rows: 4 rows covering 2026-07/08/09 with north/south regions.
    """
    cache = _template_cache_dir() / f"sales_orders_{_SALES_ORDERS_V}.duckdb"
    if cache.exists():
        return cache

    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=cache.parent,
        prefix=f"{cache.name}.",
        suffix=".building",
    ) as tmp_file:
        tmp = Path(tmp_file.name)
    try:
        # DuckDB 1.5+ refuses to open an existing 0-byte file, so remove the
        # placeholder NamedTemporaryFile (used only to reserve a unique name)
        # before connecting; duckdb.connect then creates a fresh database.
        tmp.unlink()
        con = duckdb.connect(str(tmp))
        try:
            con.execute(
                "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
                "amount DOUBLE, region VARCHAR, user_id INTEGER)"
            )
            con.execute(
                "INSERT INTO orders VALUES "
                "(1, DATE '2026-07-01', 10.0, 'north', 100),"
                "(2, DATE '2026-07-02', 20.0, 'north', 100),"
                "(3, DATE '2026-08-01', 30.0, 'south', 200),"
                "(4, DATE '2026-09-15', 40.0, 'north', 300)"
            )
        finally:
            con.close()

        os.replace(tmp, cache)
    finally:
        with suppress(FileNotFoundError):
            tmp.unlink()
    return cache


def connect_sales_orders() -> ibis.duckdb.DuckDBBackend:
    """Create an in-memory DuckDB seeded from the sales_orders template.

    Uses ATTACH READ_ONLY to bulk-copy the orders table from the cached
    template file.  READ_ONLY avoids lock conflicts when xdist workers
    share the same template file.
    """
    template = sales_orders_template()
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(f"ATTACH '{template}' AS _tpl (READ_ONLY)")
    con.raw_sql("CREATE TABLE orders AS SELECT * FROM _tpl.orders")
    con.raw_sql("DETACH _tpl")
    return con


def sales_backends(con: ibis.duckdb.DuckDBBackend) -> dict:
    """Standard backends dict wrapping a DuckDB connection as 'warehouse'."""
    return {"warehouse": lambda: con}


# ---------------------------------------------------------------------------
# Project directory templates (versioned, cached in /tmp)
# ---------------------------------------------------------------------------

_SALES_PROJECT_V = "v1"


def sales_project_template(*, with_time: bool = True) -> Path:
    """Cached directory tree with models/semantic/sales/ project files.

    Bump _SALES_PROJECT_V when the project files change.
    """
    tag = "with_time" if with_time else "no_time"
    cache = _template_cache_dir() / f"sales_project_{_SALES_PROJECT_V}" / tag
    if cache.exists():
        return cache

    (cache / "marivo.toml").write_text('[project]\nname = "test"\n')
    semantic_dir = cache / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = cache / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(warehouse)\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    time_dimension = (
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        if with_time
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = md.ref('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=ms.table('orders'))\n"
        "\n"
        f"{time_dimension}"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )
    return cache


def bootstrap_sales_project_from_template(tmp_path: Path, *, with_time: bool = True) -> None:
    """Copy the cached sales project template into tmp_path/.

    Faster than writing files individually per test.
    """
    src = sales_project_template(with_time=with_time)
    shutil.copytree(src / "models", tmp_path / "models")
    shutil.copy2(src / "marivo.toml", tmp_path / "marivo.toml")


# ---------------------------------------------------------------------------
# Lightweight MetricFrame helpers
# ---------------------------------------------------------------------------


def seeded_time_series_metric_frame(
    *,
    session,
    grain: str = "day",
    n_buckets: int = 30,
    segments: list[str] | None = None,
    value_pattern: str = "linear",
    seed: int = 42,
):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    freq_by_grain = {"day": "D", "week": "W-MON"}
    if grain not in freq_by_grain:
        raise ValueError(f"unsupported fixture grain {grain!r}")
    times = pd.date_range("2026-01-01", periods=n_buckets, freq=freq_by_grain[grain])

    def value_at(i: int) -> float:
        if value_pattern == "constant":
            return 10.0
        if value_pattern == "linear":
            return float(10 + i)
        if value_pattern == "seasonal_7":
            return float(100 + (i % 7) * 3)
        if value_pattern == "noisy":
            return float(10 + i + rng.normal(0, 0.1))
        raise ValueError(f"unsupported fixture value_pattern {value_pattern!r}")

    rows: list[dict[str, object]] = []
    if segments is None:
        for idx, bucket in enumerate(times):
            rows.append({"time": bucket, "value": value_at(idx)})
        semantic_kind = "time_series"
        axes = {"time": {"role": "time", "field": "time", "grain": grain}}
    else:
        for segment in segments:
            offset = float(len(rows))
            for idx, bucket in enumerate(times):
                rows.append({"segment": segment, "time": bucket, "value": value_at(idx) + offset})
        semantic_kind = "panel"
        axes = {
            "time": {"role": "time", "field": "time", "grain": grain},
            "dimensions": [{"field": "segment"}],
        }

    return make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes=axes,
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        window={
            "start": str(times[0].date()),
            "end": str(times[-1].date() + timedelta(days=1)),
            "grain": grain,
            "time_dimension": "time",
        },
        session=session,
    )
