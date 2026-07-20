"""Shared lightweight fixtures for Python-native analysis tests."""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from contextlib import contextmanager, suppress
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
_AUTHORING_EVIDENCE_V = "v2"


def make_test_metric_contract(
    df: Any,
    *,
    metric_id: str,
    axes: dict[str, Any],
    where: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build current typed identity/key/comparability state for synthetic frames."""

    from marivo.analysis._semantic_persistence import AxisBindingV1, SlicePredicateV1
    from marivo.refs import Ref, RefPayloadV1
    from marivo.semantic.metric_graph import (
        CanonicalSliceEntryV1,
        CatalogMetricIdentity,
        ComparableValueSemanticsV1,
        MetricKeyFieldV1,
        MetricKeySchemaV1,
        SemanticDependencyDigestV1,
        SemanticDependencyEntryV1,
    )
    from marivo.semantic.metric_graph_canonical import fingerprint

    axis_columns = tuple(
        str(axis["column"])
        for axis in axes.values()
        if isinstance(axis, dict)
        and isinstance(axis.get("column"), str)
        and axis["column"] in df.columns
    )
    key_fields = tuple(
        MetricKeyFieldV1(
            name=column,
            dtype=str(df[column].dtype),
            nullable=True,
        )
        for column in axis_columns
    )
    key_schema = MetricKeySchemaV1(
        schema="metric-key-schema/v1",
        fields=key_fields,
        fingerprint=fingerprint(key_fields),
    )
    expression_fingerprint = fingerprint(("test-metric", metric_id))
    domain = metric_id.split(".", 1)[0]
    global_slice = tuple(
        CanonicalSliceEntryV1(
            dimension_ref=RefPayloadV1.from_ref(
                Ref.dimension(str(key) if str(key).count(".") == 2 else f"{domain}.orders.{key}")
            ),
            value=fingerprint(value),
        )
        for key, value in sorted((where or {}).items())
    )
    comparable_payload = {
        "expression_fingerprint": expression_fingerprint,
        "evaluator_contracts": ("test-evaluation/v1",),
        "global_slice": global_slice,
        "key_schema_fingerprint": key_schema.fingerprint,
        "unit": None,
        "fold": None,
        "source_domain_fingerprint": "test-source-domain",
        "definition_transform_fingerprint": None,
    }
    metric_identity = CatalogMetricIdentity(
        kind="catalog",
        metric_ref=RefPayloadV1.from_ref(Ref.metric(metric_id)),
    )
    axis_bindings: list[AxisBindingV1] = []
    for key, axis in axes.items():
        if not isinstance(axis, dict):
            continue
        role = "time_dimension" if axis.get("role") == "time" or key == "time" else "dimension"
        short_path = str(
            axis.get("ref")
            or axis.get("time_dimension")
            or axis.get("field")
            or axis.get("column")
            or key
        )
        path = short_path if short_path.count(".") == 2 else f"{domain}.orders.{short_path}"
        ref = Ref.time_dimension(path) if role == "time_dimension" else Ref.dimension(path)
        column = str(axis.get("column") or axis.get("field") or key)
        axis_bindings.append(
            AxisBindingV1(
                ref=RefPayloadV1.from_ref(ref),
                column=column,
                role=role,
                grain=str(axis["grain"]) if axis.get("grain") is not None else None,
            )
        )
    slice_predicates = tuple(
        SlicePredicateV1(
            dimension_ref=RefPayloadV1.from_ref(
                Ref.dimension(str(key) if str(key).count(".") == 2 else f"{domain}.orders.{key}")
            ),
            value=value,
        )
        for key, value in sorted((where or {}).items())
    )
    dependency_entries = (
        SemanticDependencyEntryV1(
            ref=metric_identity.metric_ref,
            body_digest=expression_fingerprint,
        ),
    )
    return {
        "catalog_definition_fingerprint": fingerprint(("test-catalog", domain)),
        "metric_identity": metric_identity,
        "metric_identities": (metric_identity,),
        "semantic_dependency_digest": SemanticDependencyDigestV1(
            schema="marivo.semantic_dependency_digest/v1",
            entries=dependency_entries,
            digest=f"sha256:{fingerprint(dependency_entries)}",
        ),
        "key_schema": key_schema,
        "axis_bindings": tuple(axis_bindings),
        "slice_predicates": slice_predicates,
        "comparable_value_semantics": ComparableValueSemanticsV1(
            schema="comparable-value-semantics/v1",
            expression_fingerprint=expression_fingerprint,
            evaluator_contracts=("test-evaluation/v1",),
            global_slice=global_slice,
            key_schema_fingerprint=key_schema.fingerprint,
            unit=None,
            fold=None,
            source_domain_fingerprint="test-source-domain",
            definition_transform_fingerprint=None,
            fingerprint=fingerprint(comparable_payload),
        ),
    }


def make_test_metric_meta_contract(
    metric_id: str,
    *,
    axes: dict[str, Any] | None = None,
    where: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build v4 semantic provenance for direct MetricFrameMeta test construction."""

    import pandas as pd

    return make_test_metric_contract(
        pd.DataFrame(),
        metric_id=metric_id,
        axes=axes or {},
        where=where,
    )


def make_test_multi_metric_contract(
    *metric_ids: str,
    axes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build v4 semantic provenance for a synthetic multi-metric frame."""

    from marivo.refs import Ref, RefPayloadV1
    from marivo.semantic.metric_graph import (
        CatalogMetricIdentity,
        SemanticDependencyDigestV1,
        SemanticDependencyEntryV1,
    )
    from marivo.semantic.metric_graph_canonical import fingerprint

    if len(metric_ids) < 2:
        raise ValueError("multi-metric test contract requires at least two metrics")
    base = make_test_metric_meta_contract(metric_ids[0], axes=axes)
    identities = tuple(
        CatalogMetricIdentity(
            kind="catalog",
            metric_ref=RefPayloadV1.from_ref(Ref.metric(metric_id)),
        )
        for metric_id in metric_ids
    )
    entries = tuple(
        SemanticDependencyEntryV1(
            ref=identity.metric_ref,
            body_digest=fingerprint(("test-metric", metric_id)),
        )
        for metric_id, identity in zip(metric_ids, identities, strict=True)
    )
    return {
        **base,
        "metric_identity": None,
        "metric_identities": identities,
        "semantic_dependency_digest": SemanticDependencyDigestV1(
            schema="marivo.semantic_dependency_digest/v1",
            entries=entries,
            digest=f"sha256:{fingerprint(entries)}",
        ),
    }


def make_test_delta_contract(
    metric_id: str,
    *,
    baseline_metric_id: str | None = None,
    current_artifact_id: str = "frame_current",
    baseline_artifact_id: str = "frame_baseline",
    status_time_dimension: str | None = None,
) -> dict[str, Any]:
    """Build current structured comparison identity for synthetic delta frames."""

    from marivo.refs import Ref, RefPayloadV1
    from marivo.semantic.metric_graph import (
        CatalogMetricIdentity,
        DeltaComparisonIdentityV1,
        SemanticDependencyDigestV1,
        SemanticDependencyEntryV1,
    )
    from marivo.semantic.metric_graph_canonical import fingerprint

    def identity(path: str) -> CatalogMetricIdentity:
        return CatalogMetricIdentity(
            kind="catalog",
            metric_ref=RefPayloadV1.from_ref(Ref.metric(path)),
        )

    current = identity(metric_id)
    baseline = identity(baseline_metric_id or metric_id)
    dependency_digests = tuple(
        SemanticDependencyDigestV1(
            schema="marivo.semantic_dependency_digest/v1",
            entries=(
                entry := SemanticDependencyEntryV1(
                    ref=metric_identity.metric_ref,
                    body_digest=fingerprint(("test-metric", metric_identity.metric_ref.path)),
                ),
            ),
            digest=f"sha256:{fingerprint((entry,))}",
        )
        for metric_identity in dict.fromkeys((current, baseline))
    )
    return {
        "catalog_definition_fingerprint": "sha256:test-catalog",
        "source_dependency_digests": dependency_digests,
        "status_time_dimension_ref": (
            RefPayloadV1.from_ref(Ref.time_dimension(status_time_dimension))
            if status_time_dimension is not None
            else None
        ),
        "comparison_identity": DeltaComparisonIdentityV1(
            schema="delta-comparison/v1",
            current=current,
            baseline=baseline,
            current_artifact_id=current_artifact_id,
            baseline_artifact_id=baseline_artifact_id,
            comparable_semantics_fingerprint="sha256:test-comparable",
            alignment_policy_fingerprint="sha256:test-alignment",
        ),
    }


def make_test_component_contract(
    *,
    metric_id: str,
    components: dict[str, str],
    axes: dict[str, Any],
) -> dict[str, Any]:
    """Build structured metric/component/axis bindings for synthetic component frames."""

    from marivo.analysis._semantic_persistence import AxisBindingV1, ComponentBindingV1
    from marivo.refs import Ref, RefPayloadV1
    from marivo.semantic.metric_graph import CatalogMetricIdentity

    domain = metric_id.split(".", 1)[0]

    def identity(path: str) -> CatalogMetricIdentity:
        qualified = path if path.count(".") == 1 else f"{domain}.{path}"
        return CatalogMetricIdentity(
            kind="catalog",
            metric_ref=RefPayloadV1.from_ref(Ref.metric(qualified)),
        )

    short_names = [path.rsplit(".", 1)[-1] for path in components.values()]
    duplicate_short_names = len(short_names) != len(set(short_names))
    component_bindings = tuple(
        ComponentBindingV1(
            role=role,
            column=role if duplicate_short_names else path.rsplit(".", 1)[-1],
            metric_identity=identity(path),
        )
        for role, path in components.items()
    )
    axis_bindings: list[AxisBindingV1] = []
    for key, axis in axes.items():
        role = "time_dimension" if axis.get("role") == "time" or key == "time" else "dimension"
        short_path = str(
            axis.get("ref")
            or axis.get("time_dimension")
            or axis.get("field")
            or axis.get("column")
            or key
        )
        path = short_path if short_path.count(".") == 2 else f"{domain}.orders.{short_path}"
        ref = Ref.time_dimension(path) if role == "time_dimension" else Ref.dimension(path)
        axis_bindings.append(
            AxisBindingV1(
                ref=RefPayloadV1.from_ref(ref),
                column=str(axis.get("column") or axis.get("field") or key),
                role=role,
                grain=str(axis["grain"]) if axis.get("grain") is not None else None,
            )
        )
    return {
        "metric_identity": identity(metric_id),
        "component_bindings": component_bindings,
        "axis_bindings": tuple(axis_bindings),
    }


def make_test_subject(
    *,
    metric_id: str | None = None,
    analysis_axis: Any,
    slice_by: dict[str, Any] | None = None,
    grain: str | None = None,
    session_id: str = "sess_test",
    artifact_id: str = "art_test",
) -> Any:
    """Build a structured evidence subject for synthetic evidence tests."""

    from marivo.analysis._semantic_persistence import SlicePredicateV1
    from marivo.analysis.evidence.types import Subject
    from marivo.refs import Ref, RefPayloadV1
    from marivo.semantic.metric_graph import CatalogMetricSubjectV1

    qualified_metric = None
    if metric_id is not None:
        qualified_metric = metric_id if metric_id.count(".") == 1 else f"sales.{metric_id}"
    typed_subject = (
        CatalogMetricSubjectV1(
            kind="catalog_metric",
            session_id=session_id,
            metric_ref=RefPayloadV1.from_ref(Ref.metric(qualified_metric)),
            artifact_id=artifact_id,
            scope_fingerprint="sha256:test-scope",
        )
        if qualified_metric is not None
        else None
    )
    predicates = tuple(
        SlicePredicateV1(
            dimension_ref=RefPayloadV1.from_ref(
                Ref.dimension(key if key.count(".") == 2 else f"sales.orders.{key}")
            ),
            value=value,
        )
        for key, value in sorted((slice_by or {}).items())
    )
    return Subject(
        typed_metric_subject=typed_subject,
        slice_predicates=predicates,
        grain=grain,
        analysis_axis=analysis_axis,
    )


def make_test_analysis_scope(
    *metric_ids: str,
    assumptions: tuple[str, ...] = (),
    segment_keys: dict[str, Any] | None = None,
) -> Any:
    """Build a structured analysis scope for synthetic evidence tests."""

    from marivo.analysis._semantic_persistence import SlicePredicateV1
    from marivo.analysis.evidence.types import AnalysisScope
    from marivo.refs import Ref, RefPayloadV1
    from marivo.semantic.metric_graph import CatalogMetricIdentity

    identities = tuple(
        CatalogMetricIdentity(
            kind="catalog",
            metric_ref=RefPayloadV1.from_ref(
                Ref.metric(metric_id if metric_id.count(".") == 1 else f"sales.{metric_id}")
            ),
        )
        for metric_id in metric_ids
    )
    predicates = tuple(
        SlicePredicateV1(
            dimension_ref=RefPayloadV1.from_ref(
                Ref.dimension(key if key.count(".") == 2 else f"sales.orders.{key}")
            ),
            value=value,
        )
        for key, value in sorted((segment_keys or {}).items())
    )
    return AnalysisScope(
        metric_identities=identities,
        segment_predicates=predicates,
        assumptions=assumptions,
    )


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
    additivity: Literal["additive", "semi_additive", "non_additive"] | None = "additive",
    aggregation: str | None = None,
    status_time_dimension: str | None = None,
    session: Any,
) -> Any:
    """Create a persisted MetricFrame for tests without exposing a public constructor."""
    from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
    from marivo.analysis.lineage import Lineage, LineageStep
    from marivo.analysis.session._runtime import persist_frame
    from marivo.analysis.session.core import ensure_session_can_execute
    from marivo.analysis.windows import dump_window, normalize_absolute_window_input
    from marivo.refs import Ref, RefPayloadV1

    ensure_session_can_execute(session)
    resolved_window = normalize_absolute_window_input(window)

    # Normalize the value column to the canonical "value" name.  Callers may
    # pass a DataFrame whose value column matches the metric name (legacy
    # convention); rename it so the frame matches production observe() output.
    df = df.copy()
    measure_name = measure.get("name") or measure.get("column")
    if measure_name and str(measure_name) in df.columns and "value" not in df.columns:
        df = df.rename(columns={str(measure_name): "value"})
    # Ensure measure always has a "name" key for downstream discovery.
    if "name" not in measure and measure_name:
        measure = {**measure, "name": str(measure_name)}

    frame_ref = f"frame_{secrets.token_hex(4)}"
    metric_contract = make_test_metric_contract(
        df,
        metric_id=metric_id,
        axes=axes,
        where=where,
    )
    metric_contract["catalog_definition_fingerprint"] = session.catalog.definition_fingerprint
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
        **metric_contract,
        axes=axes,
        measure=measure,
        window=dump_window(resolved_window),
        where=where or {},
        semantic_kind=semantic_kind,
        semantic_model=semantic_model,
        additivity=additivity,
        aggregation=aggregation,
        status_time_dimension=status_time_dimension,
        status_time_dimension_ref=(
            RefPayloadV1.from_ref(
                Ref.time_dimension(
                    status_time_dimension
                    if status_time_dimension.count(".") == 2
                    else f"{metric_id.split('.', 1)[0]}.orders.{status_time_dimension}"
                )
            )
            if status_time_dimension is not None
            else None
        ),
    )
    frame = MetricFrame(_df=df, meta=meta)
    frame.meta = persist_frame(session, frame)
    return frame


def build_session_over_catalog(catalog: Any, tmp_path: Path) -> Any:
    """Build an analysis :class:`Session` backed by an existing semantic catalog.

    Constructs the persistence layout, inserts a known session row, and returns
    a Session whose ``project_root`` is ``tmp_path`` and whose
    ``semantic_catalog`` is the supplied catalog. Shared by the semantic-to-
    analysis handoff round-trip tests so the producer and validator build the
    same session shape. The caller is responsible for the invariant that
    ``catalog.workspace_dir`` resolves to ``tmp_path``.
    """
    from marivo.analysis.session._layout import PersistenceLayout
    from marivo.analysis.session._runtime import _build_connection_runtime
    from marivo.analysis.session._store import SessionStore
    from marivo.analysis.session.core import Session

    now = datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)
    layout = PersistenceLayout(project_root=tmp_path, session_id="sess_h01")
    store = SessionStore(project_root=tmp_path)
    with store._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, name, question, cwd, default_calendar, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "sess_h01",
                "handoff",
                "q",
                str(tmp_path),
                None,
                "2026-05-24T10:00:00+00:00",
                "2026-05-24T10:00:00+00:00",
            ),
        )
    return Session(
        id="sess_h01",
        name="handoff",
        question="q",
        cwd=tmp_path,
        project_root=tmp_path,
        created_at=now,
        updated_at=now,
        connection_runtime=_build_connection_runtime(tmp_path, None, None, use_datasources=False),
        layout=layout,
        semantic_catalog=catalog,
        store=store,
    )


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


def authoring_evidence_template() -> Path:
    """Return a cached DuckDB fixture for the complete authoring workflow."""
    cache = _template_cache_dir() / f"authoring_evidence_{_AUTHORING_EVIDENCE_V}.duckdb"
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
        tmp.unlink()
        con = duckdb.connect(str(tmp))
        try:
            con.execute(
                "CREATE TABLE orders ("
                "query_id INTEGER, self VARCHAR, region VARCHAR, log_date VARCHAR, "
                "log_hour INTEGER, amount DOUBLE, uncommon_date VARCHAR, epoch_like BIGINT)"
            )
            con.execute(
                "INSERT INTO orders VALUES "
                "(1, 'https://private.example/orders/1', 'moon-base', '20410717', "
                "0, 125.25, '17-Jul-2041', 2257632000),"
                "(2, 'https://private.example/orders/2', 'orbital', '20410717', "
                "12, 250.50, '18-Jul-2041', 2257718400),"
                "(3, 'https://private.example/orders/3', 'moon-base', '20410718', "
                "23, 375.75, '19-Jul-2041', 2257804800),"
                "(4, 'https://private.example/orders/4', 'orbital', '20410230', "
                "24, 0.0, '20-Jul-2041', 2257891200)"
            )
            con.execute("CREATE TABLE orders_replica AS SELECT * FROM orders")
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
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    time_dimension = (
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        if with_time
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = ms.Ref.datasource('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "\n"
        f"{time_dimension}"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
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


# ---------------------------------------------------------------------------
# Authoring session helper (metric-split foundation tests)
# ---------------------------------------------------------------------------


@contextmanager
def authoring_session(*, domain: str):
    """Context manager that enters a LoaderContext with a default domain.

    Exposes helpers for declaring measure dimensions and inspecting pending
    metric IR objects. Used by tests/test_metric_split_foundation.py.
    """
    from marivo.semantic import authoring
    from marivo.semantic.ir import MetricIR
    from marivo.semantic.loader import _LOADER_CTX, LoaderContext

    ctx = LoaderContext(default_domain=domain)
    _LOADER_CTX.set(ctx)
    try:

        class _Session:
            @staticmethod
            def measure(*, entity: str, name: str, additivity: Any = None) -> Any:
                """Declare a measure and return its exact measure ref."""
                decorator = authoring.measure(
                    entity=entity, name=name, additivity=additivity or "additive"
                )

                # Apply the decorator to a dummy function that returns an ibis-like expression.
                def _dummy_body(table: Any) -> Any:
                    return getattr(table, name)

                return decorator(_dummy_body)

            @staticmethod
            def pending_metric(semantic_id: str) -> MetricIR:
                """Retrieve a pending MetricIR by semantic_id."""
                for pending in ctx.pending_definitions:
                    if (
                        isinstance(pending.definition, MetricIR)
                        and pending.definition.semantic_id == semantic_id
                    ):
                        return pending.definition
                raise KeyError(f"no pending MetricIR with semantic_id={semantic_id!r}")

            @staticmethod
            def pending_dimension(semantic_id: str) -> Any:
                """Retrieve a pending DimensionIR by semantic_id."""
                from marivo.semantic.ir import DimensionIR

                for pending in ctx.pending_definitions:
                    if (
                        isinstance(pending.definition, DimensionIR)
                        and pending.definition.semantic_id == semantic_id
                    ):
                        return pending.definition
                raise KeyError(f"no pending DimensionIR with semantic_id={semantic_id!r}")

        yield _Session()
    finally:
        _LOADER_CTX.set(None)


# ---------------------------------------------------------------------------
# Inline semantic project loader (metric-split resolution tests)
# ---------------------------------------------------------------------------


@contextmanager
def load_inline_semantic(
    source: str,
    *,
    domain: str = "test",
    expect_errors: bool = False,
):
    """Write an inline semantic source to a temp project and load it.

    Creates a minimal project with a single domain file containing *source*,
    plus a DuckDB datasource.  Returns the ``LoadResult`` from
    ``load_project``.

    When *expect_errors* is True, suppress the ``SemanticLoadError`` that
    ``assembly_validate`` would raise and return the result with errors
    attached instead.
    """
    from marivo.semantic.loader import load_project

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
        semantic_dir = tmp_path / "models" / "semantic" / domain
        semantic_dir.mkdir(parents=True)
        datasource_dir = tmp_path / "models" / "datasources"
        datasource_dir.mkdir(parents=True)
        (datasource_dir / "wh.py").write_text(
            "import marivo.datasource as md\nmd.duckdb(name='wh', path=':memory:')\n"
        )
        (semantic_dir / "__init__.py").write_text("")
        (semantic_dir / "_domain.py").write_text(
            f"import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name={domain!r}, owner='Mina Zhang', default=True)\n"
        )
        (semantic_dir / "models.py").write_text(source)
        result = load_project(semantic_dir.parent)
        yield result


# ---------------------------------------------------------------------------
# Multi-metric sales project (two entities, three metrics)
# ---------------------------------------------------------------------------


def bootstrap_multi_metric_sales_project(tmp_path: Path) -> None:
    """Semantic project with two entities and three simple metrics.

    orders: order_date (day), region dimension, revenue + order_count metrics.
    users: signup_date (day), user_count metric. Same warehouse datasource.
    """
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "warehouse = ms.Ref.datasource('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "users = ms.entity(name='users', datasource=warehouse, source=md.table('users'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.time_dimension(entity=users, granularity='day')\n"
        "def signup_date(users):\n"
        "    return users.signed_up_at.cast('date')\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='order_count', )\n"
        "def order_count(orders):\n"
        "    return orders.order_id.count()\n"
        "\n"
        "@ms.metric(entities=[users], additivity='additive', name='user_count', )\n"
        "def user_count(users):\n"
        "    return users.user_id.count()\n"
        "\n"
        "amount_col = ms.measure_column(name='amount_col', entity=orders, column='amount', additivity='additive', unit='USD')\n"
        "revenue_agg = ms.aggregate(name='revenue_agg', measure=amount_col, agg='sum')\n"
        "cumulative_revenue = ms.cumulative(name='cumulative_revenue', base=revenue_agg, over=order_date)\n"
    )


def seed_multi_metric_tables(con: ibis.duckdb.DuckDBBackend) -> None:
    """Seed orders and users tables matching bootstrap_multi_metric_sales_project."""
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 'north', 100),"
        "(2, DATE '2026-07-02', 20.0, 'north', 100),"
        "(3, DATE '2026-07-02', 30.0, 'south', 200)"
    )
    con.raw_sql("CREATE TABLE users (user_id INTEGER, signed_up_at DATE)")
    con.raw_sql("INSERT INTO users VALUES (100, DATE '2026-07-01'), (200, DATE '2026-07-03')")
