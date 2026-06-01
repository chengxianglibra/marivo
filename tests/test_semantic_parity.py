"""Tests for marivo.semantic.parity -- parity checking and status propagation.

Tests cover:
- Base metric parity ok (matching values)
- Base metric parity fail (mismatched values)
- Base metric parity with rel_tol
- Base metric parity with abs_tol
- Derived metric parity raises error (not supported directly)
- Missing source_sql raises error
- Dialect mismatch raises error
- Cross-datasource metric raises error
- Parity status computation: declared python_native -> PYTHON_NATIVE
- Parity status computation: declared unverified -> UNVERIFIED
- Parity status computation: no source_sql -> UNVERIFIED
- Parity status computation: parity_check ok -> VERIFIED
- Parity status computation: parity_check fail -> DRIFTED
- Derived propagation: all verified -> VERIFIED
- Derived propagation: one drifted -> DRIFTED
- Derived propagation: one unverified -> UNVERIFIED
- Derived propagation: mix of verified + python_native -> PYTHON_NATIVE
- Parity results cached, cleared on reload
- list_metrics(provenance_status=...) filter works
"""

from __future__ import annotations

import textwrap

import ibis
import pytest

from marivo.semantic.errors import ErrorKind, SemanticParityError
from marivo.semantic.ir import ParityStatus
from marivo.semantic.parity import propagated_parity_status

# ---------------------------------------------------------------------------
# DuckDB backend fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_backend():
    """In-memory DuckDB backend with a test orders table."""
    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-02-01')"
    )
    return con


@pytest.fixture
def backend_factory(duckdb_backend):
    """A backend_factory callable that always returns the shared DuckDB backend."""

    def _factory(datasource_semantic_id: str):
        return duckdb_backend

    return _factory


# ---------------------------------------------------------------------------
# Model file templates
# ---------------------------------------------------------------------------


_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.model(name="sales", default=True)
""")

_DATASET_AND_BASE_METRIC_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.field(dataset=orders)
    def amount(table):
        return table.amount

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        source_sql="SELECT SUM(amount) AS total_amount FROM orders",
        source_dialect="duckdb",
    )
    def total_amount(table):
        return table.amount.sum()
""")

_DATASET_AND_MISMATCHED_METRIC_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        source_sql="SELECT 999.0 AS total_amount",
        source_dialect="duckdb",
    )
    def total_amount(table):
        return table.amount.sum()
""")

_DATASET_NO_SOURCE_SQL_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum())
    def total_amount(table):
        return table.amount.sum()
""")

_DIALECT_MISMATCH_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        source_sql="SELECT SUM(amount) FROM orders",
        source_dialect="postgres",
    )
    def total_amount(table):
        return table.amount.sum()
""")

_DERIVED_METRIC_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        source_sql="SELECT SUM(amount) AS revenue FROM orders",
        source_dialect="duckdb",
    )
    def revenue(table):
        return table.amount.sum()

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        source_sql="SELECT SUM(amount) AS cost FROM orders",
        source_dialect="duckdb",
    )
    def cost(table):
        return table.amount.sum()

    @ms.metric(
        decomposition=ms.ratio(numerator="sales.revenue", denominator="sales.cost"),
        source_sql="SELECT 0.5 AS margin",
        source_dialect="duckdb",
    )
    def margin():
        return ms.component("numerator") / ms.component("denominator")
""")

_DECLARED_PYTHON_NATIVE_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        declared_status="python_native",
    )
    def total_amount(table):
        return table.amount.sum()
""")

_DECLARED_UNVERIFIED_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        source_sql="SELECT SUM(amount) FROM orders",
        source_dialect="duckdb",
        declared_status="unverified",
    )
    def total_amount(table):
        return table.amount.sum()
""")


# ---------------------------------------------------------------------------
# Base metric parity ok
# ---------------------------------------------------------------------------


def test_base_metric_parity_ok(semantic_project_factory, backend_factory) -> None:
    """Parity check with matching values should return ok=True."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    result = project.parity_check("sales.total_amount", backend_factory=backend_factory)
    assert result.ok is True
    assert result.expected == 300.0
    assert result.actual == 300.0
    assert result.error is None


# ---------------------------------------------------------------------------
# Base metric parity fail
# ---------------------------------------------------------------------------


def test_base_metric_parity_fail(semantic_project_factory, backend_factory) -> None:
    """Parity check with mismatched values should return ok=False."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_AND_MISMATCHED_METRIC_PY,
        }
    )
    result = project.parity_check("sales.total_amount", backend_factory=backend_factory)
    assert result.ok is False
    assert result.actual == 300.0
    assert result.expected == 999.0


# ---------------------------------------------------------------------------
# Base metric parity with rel_tol
# ---------------------------------------------------------------------------


def test_base_metric_parity_rel_tol(semantic_project_factory, backend_factory) -> None:
    """Parity check with rel_tol should pass within tolerance."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_AND_MISMATCHED_METRIC_PY,
        }
    )
    # 300 vs 999 — way off, but with rel_tol=2.0 (200%), isclose considers them close
    result = project.parity_check(
        "sales.total_amount", backend_factory=backend_factory, rel_tol=2.0
    )
    assert result.ok is True
    assert result.rel_tol == 2.0


# ---------------------------------------------------------------------------
# Base metric parity with abs_tol
# ---------------------------------------------------------------------------


def test_base_metric_parity_abs_tol(semantic_project_factory, backend_factory) -> None:
    """Parity check with abs_tol should pass within tolerance."""
    # Create a project where expected and actual differ by a small amount
    small_mismatch_py = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(
            datasets=[orders],
            additivity="additive",
            decomposition=ms.sum(),
            source_sql="SELECT 300.5 AS total_amount",
            source_dialect="duckdb",
        )
        def total_amount(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": small_mismatch_py,
        }
    )
    result = project.parity_check(
        "sales.total_amount", backend_factory=backend_factory, abs_tol=1.0
    )
    assert result.ok is True
    assert result.abs_tol == 1.0


# ---------------------------------------------------------------------------
# Derived metric parity raises error
# ---------------------------------------------------------------------------


def test_derived_metric_parity_raises(semantic_project_factory, backend_factory) -> None:
    """Parity check on a derived metric should raise SemanticParityError."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DERIVED_METRIC_PY,
        }
    )
    with pytest.raises(SemanticParityError) as exc_info:
        project.parity_check("sales.margin", backend_factory=backend_factory)
    assert exc_info.value.kind == ErrorKind.SOURCE_SQL_MISSING


# ---------------------------------------------------------------------------
# Missing source_sql raises error
# ---------------------------------------------------------------------------


def test_missing_source_sql_raises(semantic_project_factory, backend_factory) -> None:
    """Parity check on metric without source_sql should raise."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_NO_SOURCE_SQL_PY,
        }
    )
    with pytest.raises(SemanticParityError) as exc_info:
        project.parity_check("sales.total_amount", backend_factory=backend_factory)
    assert exc_info.value.kind == ErrorKind.SOURCE_SQL_MISSING


# ---------------------------------------------------------------------------
# Source dialect is provenance, not datasource backend config
# ---------------------------------------------------------------------------


def test_source_dialect_does_not_require_semantic_datasource_backend_type(
    semantic_project_factory, backend_factory
) -> None:
    """Profiles own backend_type; semantic datasource refs no longer carry it."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DIALECT_MISMATCH_PY,
        }
    )
    result = project.parity_check("sales.total_amount", backend_factory=backend_factory)
    assert isinstance(result.ok, bool)


# ---------------------------------------------------------------------------
# Cross-datasource metric raises error
# ---------------------------------------------------------------------------


def test_cross_datasource_metric_raises(semantic_project_factory, backend_factory) -> None:
    """Parity check on metric with cross-datasource datasets should raise."""
    cross_ds_py = textwrap.dedent("""\
        import marivo.semantic as ms
        orders_a = ms.dataset(name="orders_a", datasource="warehouse1", source=ms.table("orders"))

        orders_b = ms.dataset(name="orders_b", datasource="warehouse2", source=ms.table("orders"))

        @ms.metric(
            datasets=[orders_a, orders_b],
            root_dataset=orders_a,
            additivity="additive",
            decomposition=ms.sum(),
            source_sql="SELECT SUM(amount) FROM orders",
            source_dialect="duckdb",
        )
        def total_amount(table_a, table_b):
            return table_a.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": cross_ds_py,
        }
    )
    with pytest.raises(SemanticParityError) as exc_info:
        project.parity_check("sales.total_amount", backend_factory=backend_factory)
    assert exc_info.value.kind == ErrorKind.CROSS_DATASOURCE_NOT_SUPPORTED


# ---------------------------------------------------------------------------
# Parity status: declared python_native -> PYTHON_NATIVE
# ---------------------------------------------------------------------------


def test_status_declared_python_native(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DECLARED_PYTHON_NATIVE_PY,
        }
    )
    status = propagated_parity_status(project, "sales.total_amount")
    assert status == ParityStatus.PYTHON_NATIVE


# ---------------------------------------------------------------------------
# Parity status: declared unverified -> UNVERIFIED
# ---------------------------------------------------------------------------


def test_status_declared_unverified(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DECLARED_UNVERIFIED_PY,
        }
    )
    status = propagated_parity_status(project, "sales.total_amount")
    assert status == ParityStatus.UNVERIFIED


# ---------------------------------------------------------------------------
# Parity status: no source_sql -> UNVERIFIED
# ---------------------------------------------------------------------------


def test_status_no_source_sql(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_NO_SOURCE_SQL_PY,
        }
    )
    status = propagated_parity_status(project, "sales.total_amount")
    assert status == ParityStatus.UNVERIFIED


# ---------------------------------------------------------------------------
# Parity status: parity_check ok -> VERIFIED
# ---------------------------------------------------------------------------


def test_status_parity_check_ok(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    # Before parity check, status is UNVERIFIED (has source_sql but not checked)
    status_before = propagated_parity_status(project, "sales.total_amount")
    assert status_before == ParityStatus.UNVERIFIED

    # Run parity check
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    # After parity check ok, status should be VERIFIED
    status_after = propagated_parity_status(project, "sales.total_amount")
    assert status_after == ParityStatus.VERIFIED


# ---------------------------------------------------------------------------
# Parity status: parity_check fail -> DRIFTED
# ---------------------------------------------------------------------------


def test_status_parity_check_fail(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_AND_MISMATCHED_METRIC_PY,
        }
    )
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    status = propagated_parity_status(project, "sales.total_amount")
    assert status == ParityStatus.DRIFTED


# ---------------------------------------------------------------------------
# Derived propagation: all verified -> VERIFIED
# ---------------------------------------------------------------------------


def test_derived_propagation_all_verified(semantic_project_factory, backend_factory) -> None:
    """When all component metrics are verified, derived should be VERIFIED."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DERIVED_METRIC_PY,
        }
    )
    # Run parity check on both components to make them verified
    project.parity_check("sales.revenue", backend_factory=backend_factory)
    project.parity_check("sales.cost", backend_factory=backend_factory)

    status = propagated_parity_status(project, "sales.margin")
    assert status == ParityStatus.VERIFIED


# ---------------------------------------------------------------------------
# Derived propagation: one drifted -> DRIFTED
# ---------------------------------------------------------------------------


def test_derived_propagation_one_drifted(semantic_project_factory, backend_factory) -> None:
    """When one component metric is drifted, derived should be DRIFTED."""
    drifted_component_py = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(
            datasets=[orders],
            additivity="additive",
            decomposition=ms.sum(),
            source_sql="SELECT SUM(amount) FROM orders",
            source_dialect="duckdb",
        )
        def revenue(table):
            return table.amount.sum()

        @ms.metric(
            datasets=[orders],
            additivity="additive",
            decomposition=ms.sum(),
            source_sql="SELECT 999.0 AS cost",
            source_dialect="duckdb",
        )
        def cost(table):
            return table.amount.sum()

        @ms.metric(
            decomposition=ms.ratio(numerator="sales.revenue", denominator="sales.cost"),
        )
        def margin():
            return ms.component("numerator") / ms.component("denominator")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": drifted_component_py,
        }
    )
    project.parity_check("sales.revenue", backend_factory=backend_factory)
    project.parity_check("sales.cost", backend_factory=backend_factory)

    status = propagated_parity_status(project, "sales.margin")
    assert status == ParityStatus.DRIFTED


# ---------------------------------------------------------------------------
# Derived propagation: one unverified -> UNVERIFIED
# ---------------------------------------------------------------------------


def test_derived_propagation_one_unverified(semantic_project_factory, backend_factory) -> None:
    """When one component metric is unverified, derived should be UNVERIFIED."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DERIVED_METRIC_PY,
        }
    )
    # Verify only revenue, leave cost unverified
    project.parity_check("sales.revenue", backend_factory=backend_factory)

    status = propagated_parity_status(project, "sales.margin")
    assert status == ParityStatus.UNVERIFIED


# ---------------------------------------------------------------------------
# Derived propagation: mix of verified + python_native -> PYTHON_NATIVE
# ---------------------------------------------------------------------------


def test_derived_propagation_verified_and_python_native(
    semantic_project_factory, backend_factory
) -> None:
    """When one component is verified and another is python_native, derived is PYTHON_NATIVE."""
    mixed_py = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(
            datasets=[orders],
            additivity="additive",
            decomposition=ms.sum(),
            source_sql="SELECT SUM(amount) FROM orders",
            source_dialect="duckdb",
        )
        def revenue(table):
            return table.amount.sum()

        @ms.metric(
            datasets=[orders],
            additivity="additive",
            decomposition=ms.sum(),
            declared_status="python_native",
        )
        def cost(table):
            return table.amount.sum()

        @ms.metric(
            decomposition=ms.ratio(numerator="sales.revenue", denominator="sales.cost"),
        )
        def margin():
            return ms.component("numerator") / ms.component("denominator")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": mixed_py,
        }
    )
    project.parity_check("sales.revenue", backend_factory=backend_factory)

    status = propagated_parity_status(project, "sales.margin")
    assert status == ParityStatus.PYTHON_NATIVE


# ---------------------------------------------------------------------------
# Parity results cached, cleared on reload
# ---------------------------------------------------------------------------


def test_parity_results_cached(semantic_project_factory, backend_factory) -> None:
    """Parity results should be cached on the project."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    # First call
    result1 = project.parity_check("sales.total_amount", backend_factory=backend_factory)
    # Second call should return cached result
    result2 = project.parity_check("sales.total_amount", backend_factory=backend_factory)
    # Both should be the same object (cached)
    assert result1 is result2


def test_parity_results_cleared_on_reload(semantic_project_factory, backend_factory) -> None:
    """Parity cache should be cleared on reload."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    result1 = project.parity_check("sales.total_amount", backend_factory=backend_factory)
    assert result1.ok is True

    project.reload()

    # After reload, parity cache should be empty, so status goes back to UNVERIFIED
    status = propagated_parity_status(project, "sales.total_amount")
    assert status == ParityStatus.UNVERIFIED


# ---------------------------------------------------------------------------
# list_metrics(provenance_status=...) filter works
# ---------------------------------------------------------------------------


def test_list_metrics_provenance_status_filter(semantic_project_factory, backend_factory) -> None:
    """list_metrics(provenance_status=...) should filter by propagated status."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )

    # Before parity check: UNVERIFIED
    unverified = project.list_metrics(provenance_status=ParityStatus.UNVERIFIED)
    assert any(m.semantic_id == "sales.total_amount" for m in unverified)

    verified = project.list_metrics(provenance_status=ParityStatus.VERIFIED)
    assert not any(m.semantic_id == "sales.total_amount" for m in verified)

    # Run parity check
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    # After parity check: VERIFIED
    verified = project.list_metrics(provenance_status=ParityStatus.VERIFIED)
    assert any(m.semantic_id == "sales.total_amount" for m in verified)

    unverified = project.list_metrics(provenance_status=ParityStatus.UNVERIFIED)
    assert not any(m.semantic_id == "sales.total_amount" for m in unverified)


# ---------------------------------------------------------------------------
# Metric not found raises
# ---------------------------------------------------------------------------


def test_parity_check_metric_not_found(semantic_project_factory, backend_factory) -> None:
    """Parity check on non-existent metric should raise."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    with pytest.raises(SemanticParityError) as exc_info:
        project.parity_check("sales.nonexistent", backend_factory=backend_factory)
    assert exc_info.value.kind == ErrorKind.METRIC_NOT_FOUND
