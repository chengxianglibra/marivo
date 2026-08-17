"""Real-backend E2E: DuckDB DECIMAL(12,2) survives observe and exports as float64.

issue #93 — the MR !84 unit tests cover the coercion in isolation, but there is
no committed end-to-end regression proving that a DECIMAL column actually flows
from DuckDB through ``observe`` into a ``MetricFrame`` whose ``to_pandas()`` and
``contract().artifact_schema`` agree on float64. This fixture is dedicated to
that path and deliberately avoids mutating the shared ``sales`` fixture.
"""

from decimal import Decimal

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    yield


def _bootstrap_decimal_project(tmp_path) -> None:
    """Minimal semantic project with a single DECIMAL-backed sum metric."""
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
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='gmv')\n"
        "def gmv(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_decimal_orders(con) -> None:
    """Seed a table whose amount column is a real DECIMAL(12,2), not a DOUBLE."""
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DECIMAL(12,2))")
    con.raw_sql(
        "INSERT INTO orders VALUES (1, DATE '2026-07-01', 15.75),(2, DATE '2026-07-01', 4.25)"
    )


def test_decimal_measure_exports_float64_end_to_end(tmp_path):
    _bootstrap_decimal_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_decimal_orders(con)
    s = session_attach.get_or_create(name="decimal", backends={"warehouse": lambda: con})

    frame = observe(
        make_ref("sales.gmv", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-02"),
        grain=mv.grain("day"),
        session=s,
    )

    # DuckDB materialized the DECIMAL(12,2) sum as a Python Decimal, proving the
    # test actually exercises the Decimal path rather than a silently-float one.
    assert isinstance(frame["gmv"].iloc[0], Decimal)

    # to_pandas() coerces the Decimal column to float64.
    exported = frame.to_pandas()
    assert exported["gmv"].dtype == "float64"
    assert exported["gmv"].iloc[0] == pytest.approx(20.0)

    # The artifact contract declares the exported dtype, matching to_pandas().
    schema = {column.name: column.dtype for column in frame.contract().artifact_schema.columns}
    assert schema["gmv"] == "float64"

    # Terminal float arithmetic over the formerly-Decimal value no longer raises
    # TypeError (issue #86 regression).
    assert 100.0 - exported["gmv"].iloc[0] == pytest.approx(80.0)
