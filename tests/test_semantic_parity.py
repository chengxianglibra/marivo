"""Tests for marivo.semantic.parity -- parity checking and status propagation.

Tests cover:
- Base metric parity ok (matching values)
- Base metric parity fail (mismatched values)
- Base metric parity with rel_tol
- Base metric parity with abs_tol
- Derived metric parity raises error (not supported directly)
- Missing provenance SQL raises error
- Verification mode contract for sql_parity and python_native metrics
- Cross-datasource metric raises error
- Parity status computation: python_native mode -> VERIFIED
- Parity status computation: parity_check ok -> VERIFIED
- Parity status computation: parity_check fail -> DRIFTED
- Derived propagation: all verified -> VERIFIED
- Derived propagation: one drifted -> DRIFTED
- Derived propagation: one unverified -> UNVERIFIED
- Derived propagation: mix of SQL parity verified + python_native -> VERIFIED
- Parity results cached, cleared on reload
- catalog metric details expose propagated parity status
"""

from __future__ import annotations

import textwrap
from contextlib import contextmanager
from unittest.mock import patch

import ibis
import pytest

from marivo.semantic.catalog import SemanticKind
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
# Fake connection service for patching internal backend resolution
# ---------------------------------------------------------------------------


class _FakeConnectionService:
    """Stubs DatasourceConnectionService using a test backend factory."""

    def __init__(self, factory):
        self._factory = factory

    @property
    def project_root(self):
        return None

    def session_backend(self, name: str):
        return self._factory(name)

    @contextmanager
    def use_backend(self, name: str):
        yield self._factory(name)

    def close_all(self):
        pass


@contextmanager
def _patch_project_backends(project, backend_factory):
    """Patch project backend resolution so parity_check uses a test backend.

    Patches two resolution paths:
    - ``project._connection_service_instance`` for internal backend resolution
    - ``DatasourceConnectionService`` constructor inside parity_check()
    """
    fake_service = _FakeConnectionService(backend_factory)
    project._connection_service_instance = fake_service
    with patch(
        "marivo.datasource.runtime.DatasourceConnectionService",
        return_value=fake_service,
    ):
        yield


# ---------------------------------------------------------------------------
# Model file templates
# ---------------------------------------------------------------------------


_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
""")

_DATASET_AND_BASE_METRIC_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.metric(
        entities=[orders],
        additivity="additive",
        provenance=ms.from_sql(sql="SELECT SUM(amount) AS total_amount FROM orders", dialect="duckdb"),
    )
    def total_amount(table):
        return table.amount.sum()
""")

_DATASET_AND_MISMATCHED_METRIC_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        entities=[orders],
        additivity="additive",
        provenance=ms.from_sql(sql="SELECT 999.0 AS total_amount", dialect="duckdb"),
    )
    def total_amount(table):
        return table.amount.sum()
""")

_DATASET_NO_SOURCE_SQL_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        entities=[orders],
        additivity='additive',
    )
    def total_amount(table):
        return table.amount.sum()
""")

_DIALECT_MISMATCH_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        entities=[orders],
        additivity="additive",
        provenance=ms.from_sql(sql="SELECT SUM(amount) FROM orders", dialect="postgres"),
    )
    def total_amount(table):
        return table.amount.sum()
""")

_DERIVED_METRIC_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        entities=[orders],
        additivity="additive",
        provenance=ms.from_sql(sql="SELECT SUM(amount) AS revenue FROM orders", dialect="duckdb"),
    )
    def revenue(table):
        return table.amount.sum()

    @ms.metric(
        entities=[orders],
        additivity="additive",
        provenance=ms.from_sql(sql="SELECT SUM(amount) AS cost FROM orders", dialect="duckdb"),
    )
    def cost(table):
        return table.amount.sum()

    margin = ms.ratio(
        name="margin",
        numerator=revenue,
        denominator=cost,
    )
""")

_NO_SOURCE_SQL_METRIC_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        entities=[orders],
        additivity="additive",
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
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    with _patch_project_backends(project, backend_factory):
        result = project.parity_check("sales.total_amount")
    assert result.ok is True
    assert result.expected == 300.0
    assert result.actual == 300.0
    assert result.error is None


def test_parity_check_accepts_semantic_catalog(semantic_project_factory, backend_factory) -> None:
    """Module-level parity_check should accept SemanticCatalog directly."""
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.parity import parity_check

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    catalog = SemanticCatalog(project)

    with _patch_project_backends(project, backend_factory):
        result = parity_check(catalog, "sales.total_amount")

    assert result.ok is True
    assert project._parity_results["sales.total_amount"] is result


# ---------------------------------------------------------------------------
# Base metric parity fail
# ---------------------------------------------------------------------------


def test_base_metric_parity_fail(semantic_project_factory, backend_factory) -> None:
    """Parity check with mismatched values should return ok=False."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DATASET_AND_MISMATCHED_METRIC_PY,
        }
    )
    with _patch_project_backends(project, backend_factory):
        result = project.parity_check("sales.total_amount")
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
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DATASET_AND_MISMATCHED_METRIC_PY,
        }
    )
    # 300 vs 999 — way off, but with rel_tol=2.0 (200%), isclose considers them close
    with _patch_project_backends(project, backend_factory):
        result = project.parity_check("sales.total_amount", rel_tol=2.0)
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
        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(
            entities=[orders],
            additivity="additive",
            provenance=ms.from_sql(sql="SELECT 300.5 AS total_amount", dialect="duckdb"),
        )
        def total_amount(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": small_mismatch_py,
        }
    )
    with _patch_project_backends(project, backend_factory):
        result = project.parity_check("sales.total_amount", abs_tol=1.0)
    assert result.ok is True
    assert result.abs_tol == 1.0


# ---------------------------------------------------------------------------
# Derived metric parity raises error
# ---------------------------------------------------------------------------


def test_derived_metric_parity_raises(semantic_project_factory, backend_factory) -> None:
    """Parity check on a derived metric should raise SemanticParityError."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DERIVED_METRIC_PY,
        }
    )
    with (
        _patch_project_backends(project, backend_factory),
        pytest.raises(SemanticParityError) as exc_info,
    ):
        project.parity_check("sales.margin")
    assert exc_info.value.kind == ErrorKind.PROVENANCE_DIALECT_MISSING


# ---------------------------------------------------------------------------
# Provenance contracts
# ---------------------------------------------------------------------------


def test_base_metric_without_provenance_sql_loads_ok(semantic_project_factory) -> None:
    """Base metric without provenance SQL loads successfully — no verification needed."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _NO_SOURCE_SQL_METRIC_PY,
        },
        load=False,
    )
    result = project.load()

    assert project.is_ready()


def test_base_metric_provenance_without_dialect_fails_load(semantic_project_factory) -> None:
    """ms.from_sql() requires both sql and dialect, so it is impossible to
    create provenance SQL but no dialect at the
    authoring level. This is now enforced by ms.from_sql() signature."""
    import marivo.semantic as ms

    # ms.from_sql() requires dialect, so this is enforced by construction
    with pytest.raises(TypeError):
        ms.from_sql(sql="SELECT 1")  # type: ignore[call-arg]


def test_derived_metric_with_provenance_sql_fails_load(
    semantic_project_factory,
) -> None:
    """Derived metric constructors do not accept provenance — the module fails
    to load with an organization_error when extra kwargs are passed."""
    metrics_py = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(
            entities=[orders],
            additivity="additive",
        )
        def revenue(table):
            return table.amount.sum()

        margin = ms.ratio(
            name="margin",
            numerator=revenue,
            denominator=revenue,
            provenance=ms.from_sql(sql="SELECT 1", dialect="duckdb"),
        )
    """)
    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/metrics.py": metrics_py},
        load=False,
    )
    result = project.load()

    assert not project.is_ready()
    assert any(error.kind == ErrorKind.ORGANIZATION_ERROR for error in result.errors)


# ---------------------------------------------------------------------------
# Source dialect is provenance, not datasource backend config
# ---------------------------------------------------------------------------


def test_provenance_dialect_does_not_require_semantic_datasource_backend_type(
    semantic_project_factory, backend_factory
) -> None:
    """Profiles own backend_type; semantic datasource refs no longer carry it."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DIALECT_MISMATCH_PY,
        }
    )
    with _patch_project_backends(project, backend_factory):
        result = project.parity_check("sales.total_amount")
    assert isinstance(result.ok, bool)


# ---------------------------------------------------------------------------
# Cross-datasource metric raises error
# ---------------------------------------------------------------------------


def test_cross_datasource_metric_raises(semantic_project_factory, backend_factory) -> None:
    """Parity check on metric with cross-datasource datasets should raise."""
    cross_ds_py = textwrap.dedent("""\
        import marivo.semantic as ms
        orders_a = ms.entity(name="orders_a", datasource="warehouse1", source=ms.table("orders"))

        orders_b = ms.entity(name="orders_b", datasource="warehouse2", source=ms.table("orders"))

        @ms.metric(
            entities=[orders_a, orders_b],
            root_entity=orders_a,
            additivity="additive",
            provenance=ms.from_sql(sql="SELECT SUM(amount) FROM orders", dialect="duckdb"),
        )
        def total_amount(table_a, table_b):
            return table_a.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": cross_ds_py,
        }
    )
    with (
        _patch_project_backends(project, backend_factory),
        pytest.raises(SemanticParityError) as exc_info,
    ):
        project.parity_check("sales.total_amount")
    assert exc_info.value.kind == ErrorKind.CROSS_DATASOURCE_NOT_SUPPORTED


# ---------------------------------------------------------------------------
# Parity status: no provenance SQL -> VERIFIED
# ---------------------------------------------------------------------------


def test_status_no_provenance_sql_is_verified(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _NO_SOURCE_SQL_METRIC_PY,
        }
    )
    status = propagated_parity_status(project, "sales.total_amount")
    assert status == ParityStatus.VERIFIED


# ---------------------------------------------------------------------------
# Parity status: parity_check ok -> VERIFIED
# ---------------------------------------------------------------------------


def test_status_parity_check_ok(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    # Before parity check, status is UNVERIFIED (has provenance SQL but not checked)
    status_before = propagated_parity_status(project, "sales.total_amount")
    assert status_before == ParityStatus.UNVERIFIED

    # Run parity check
    with _patch_project_backends(project, backend_factory):
        project.parity_check("sales.total_amount")

    # After parity check ok, status should be VERIFIED
    status_after = propagated_parity_status(project, "sales.total_amount")
    assert status_after == ParityStatus.VERIFIED


# ---------------------------------------------------------------------------
# Parity status: parity_check fail -> DRIFTED
# ---------------------------------------------------------------------------


def test_status_parity_check_fail(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DATASET_AND_MISMATCHED_METRIC_PY,
        }
    )
    with _patch_project_backends(project, backend_factory):
        project.parity_check("sales.total_amount")

    status = propagated_parity_status(project, "sales.total_amount")
    assert status == ParityStatus.DRIFTED


# ---------------------------------------------------------------------------
# Derived propagation: all verified -> VERIFIED
# ---------------------------------------------------------------------------


def test_derived_propagation_all_verified(semantic_project_factory, backend_factory) -> None:
    """When all component metrics are verified, derived should be VERIFIED."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DERIVED_METRIC_PY,
        }
    )
    # Run parity check on both components to make them verified
    with _patch_project_backends(project, backend_factory):
        project.parity_check("sales.revenue")
        project.parity_check("sales.cost")

    status = propagated_parity_status(project, "sales.margin")
    assert status == ParityStatus.VERIFIED


# ---------------------------------------------------------------------------
# Derived propagation: one drifted -> DRIFTED
# ---------------------------------------------------------------------------


def test_derived_propagation_one_drifted(semantic_project_factory, backend_factory) -> None:
    """When one component metric is drifted, derived should be DRIFTED."""
    drifted_component_py = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(
            entities=[orders],
            additivity="additive",
            provenance=ms.from_sql(sql="SELECT SUM(amount) FROM orders", dialect="duckdb"),
        )
        def revenue(table):
            return table.amount.sum()

        @ms.metric(
            entities=[orders],
            additivity="additive",
            provenance=ms.from_sql(sql="SELECT 999.0 AS cost", dialect="duckdb"),
        )
        def cost(table):
            return table.amount.sum()

        margin = ms.ratio(
            name="margin",
            numerator=revenue,
            denominator=cost,
        )
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": drifted_component_py,
        }
    )
    with _patch_project_backends(project, backend_factory):
        project.parity_check("sales.revenue")
        project.parity_check("sales.cost")

    status = propagated_parity_status(project, "sales.margin")
    assert status == ParityStatus.DRIFTED


# ---------------------------------------------------------------------------
# Derived propagation: one unverified -> UNVERIFIED
# ---------------------------------------------------------------------------


def test_derived_propagation_one_unverified(semantic_project_factory, backend_factory) -> None:
    """When one component metric is unverified, derived should be UNVERIFIED."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DERIVED_METRIC_PY,
        }
    )
    # Verify only revenue, leave cost unverified
    with _patch_project_backends(project, backend_factory):
        project.parity_check("sales.revenue")

    status = propagated_parity_status(project, "sales.margin")
    assert status == ParityStatus.UNVERIFIED


# ---------------------------------------------------------------------------
# Derived propagation: mix of verified SQL parity + no-provenance_sql -> VERIFIED
# ---------------------------------------------------------------------------


def test_derived_propagation_verified_and_no_provenance_sql(
    semantic_project_factory, backend_factory
) -> None:
    """When one component is SQL-verified and another has no provenance SQL, derived is VERIFIED."""
    mixed_py = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(
            entities=[orders],
            additivity="additive",
            provenance=ms.from_sql(sql="SELECT SUM(amount) FROM orders", dialect="duckdb"),
        )
        def revenue(table):
            return table.amount.sum()

        @ms.metric(
            entities=[orders],
            additivity="additive",
        )
        def cost(table):
            return table.amount.sum()

        margin = ms.ratio(
            name="margin",
            numerator=revenue,
            denominator=cost,
        )
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": mixed_py,
        }
    )
    with _patch_project_backends(project, backend_factory):
        project.parity_check("sales.revenue")

    status = propagated_parity_status(project, "sales.margin")
    assert status == ParityStatus.VERIFIED


# ---------------------------------------------------------------------------
# Parity results cached, cleared on reload
# ---------------------------------------------------------------------------


def test_parity_results_cached(semantic_project_factory, backend_factory) -> None:
    """Parity results should be cached on the project."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    # First call
    with _patch_project_backends(project, backend_factory):
        result1 = project.parity_check("sales.total_amount")
        # Second call should return cached result
        result2 = project.parity_check("sales.total_amount")
    # Both should be the same object (cached)
    assert result1 is result2


def test_parity_results_cleared_on_reload(semantic_project_factory, backend_factory) -> None:
    """Parity cache should be cleared on reload."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    with _patch_project_backends(project, backend_factory):
        result1 = project.parity_check("sales.total_amount")
    assert result1.ok is True

    project.load()

    # After reload, parity cache should be empty, so status goes back to UNVERIFIED
    status = propagated_parity_status(project, "sales.total_amount")
    assert status == ParityStatus.UNVERIFIED


# ---------------------------------------------------------------------------
# catalog metric details expose propagated parity status
# ---------------------------------------------------------------------------


def test_catalog_metric_details_reflect_parity_status(
    semantic_project_factory, backend_factory
) -> None:
    """Metric details should reflect propagated parity status."""
    from marivo.semantic.catalog import MetricDetails, SemanticCatalog

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    catalog = SemanticCatalog(project)

    # Before parity check: UNVERIFIED
    metrics = catalog.list(catalog.get("domain.sales").ref, kind=SemanticKind.METRIC).objects
    assert any(metric.ref.id == "sales.total_amount" for metric in metrics)
    details = catalog.get("metric.sales.total_amount").details()
    assert isinstance(details, MetricDetails)
    assert details.parity_status == ParityStatus.UNVERIFIED

    # Run parity check
    with _patch_project_backends(project, backend_factory):
        project.parity_check("sales.total_amount")

    # After parity check: VERIFIED
    details = catalog.get("metric.sales.total_amount").details()
    assert isinstance(details, MetricDetails)
    assert details.parity_status == ParityStatus.VERIFIED


# ---------------------------------------------------------------------------
# Metric not found raises
# ---------------------------------------------------------------------------


def test_parity_check_metric_not_found(semantic_project_factory, backend_factory) -> None:
    """Parity check on non-existent metric should raise."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/metrics.py": _DATASET_AND_BASE_METRIC_PY,
        }
    )
    with (
        _patch_project_backends(project, backend_factory),
        pytest.raises(SemanticParityError) as exc_info,
    ):
        project.parity_check("sales.nonexistent")
    assert exc_info.value.kind == ErrorKind.METRIC_NOT_FOUND
