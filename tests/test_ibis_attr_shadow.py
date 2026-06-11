"""Tests for ibis attribute shadowing: runtime guards and integration.

Covers:
- _validate_field_expr rejects non-ibis return values
- integration: bracket-notation "schema" dimension works end-to-end
"""

from __future__ import annotations

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.intents.observe_errors import ObservePlanningError
from marivo.analysis.intents.observe_planner import _validate_field_expr

# ---------------------------------------------------------------------------
# Runtime guard: _validate_field_expr
# ---------------------------------------------------------------------------


def test_validate_field_expr_passes_for_ibis_value() -> None:
    t = ibis.table([("a", "int")], name="t")
    col = t.a
    result = _validate_field_expr(col, field_id="test.a")
    assert result is col


def test_validate_field_expr_passes_for_ibis_table() -> None:
    t = ibis.table([("a", "int")], name="t")
    result = _validate_field_expr(t, field_id="test.t")
    assert result is t


def test_validate_field_expr_rejects_function() -> None:
    t = ibis.table([("a", "int")], name="t")
    with pytest.raises(ObservePlanningError) as exc_info:
        _validate_field_expr(t.schema, field_id="orders.schema")
    assert exc_info.value.details["code"] == "field-expr-type-error"
    assert "bracket notation" in exc_info.value.message
    assert 'table["schema"]' in exc_info.value.message


def test_validate_field_expr_rejects_plain_object() -> None:
    with pytest.raises(ObservePlanningError) as exc_info:
        _validate_field_expr(42, field_id="orders.value")
    assert exc_info.value.details["code"] == "field-expr-type-error"
    assert "int" in exc_info.value.message


# ---------------------------------------------------------------------------
# Integration: bracket-notation "schema" dimension works end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _bootstrap_schema_project(tmp_path):
    """Create a semantic project with a 'schema' dimension using bracket notation."""
    semantic_dir = tmp_path / ".marivo" / "semantic" / "analytics"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(warehouse)\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='analytics')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = md.ref('warehouse')\n"
        "\n"
        "queries = ms.entity(name='queries', datasource=warehouse, source=ms.table('queries'))\n"
        "\n"
        "@ms.time_dimension(entity=queries, data_type='date', granularity='day')\n"
        "def query_date(queries):\n"
        "    return queries.query_date.cast('date')\n\n"
        "@ms.dimension(name='schema', entity=queries)\n"
        "def schema(queries):\n"
        "    return queries['schema']\n\n"
        "@ms.metric(entities=[queries], additivity='additive', decomposition=ms.sum(), "
        "name='query_count', verification_mode='python_native')\n"
        "def query_count(queries):\n"
        "    return queries.query_id.count()\n\n"
        "@ms.metric(entities=[queries], additivity='additive', decomposition=ms.sum(), "
        "name='elapsed_total', verification_mode='python_native')\n"
        "def elapsed_total(queries):\n"
        "    return queries.elapsed_time.sum()\n\n"
        "ms.derived_metric(\n"
        "    name='avg_elapsed_time',\n"
        "    decomposition=ms.ratio(\n"
        "        numerator='analytics.elapsed_total',\n"
        "        denominator='analytics.query_count',\n"
        "    ),\n"
        ")\n"
    )


def _seed_warehouse():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE queries (query_id INTEGER, query_date DATE, "
        "schema VARCHAR, elapsed_time DOUBLE)"
    )
    con.raw_sql(
        "INSERT INTO queries VALUES "
        "(1, DATE '2026-07-01', 'public', 0.5),"
        "(2, DATE '2026-07-01', 'internal', 1.2),"
        "(3, DATE '2026-07-02', 'public', 0.8),"
        "(4, DATE '2026-07-02', 'internal', 2.1)"
    )
    return con


def test_schema_dimension_base_metric_observe(tmp_path):
    """Bracket-notation 'schema' dimension works for base metric observe."""
    _bootstrap_schema_project(tmp_path)
    con = _seed_warehouse()
    s = mv.session.get_or_create(
        name="schema-test",
        question="Test schema dimension",
        backends={"warehouse": lambda: con},
    )
    frame = s.observe(
        mv.MetricRef("analytics.query_count"),
        dimensions=[mv.DimensionRef("analytics.queries.schema")],
    )
    df = frame.to_pandas()
    assert "schema" in df.columns
    assert len(df) == 2


def test_schema_dimension_derived_metric_observe(tmp_path):
    """Bracket-notation 'schema' dimension works for derived metric observe."""
    _bootstrap_schema_project(tmp_path)
    con = _seed_warehouse()
    s = mv.session.get_or_create(
        name="schema-derived-test",
        question="Test schema dimension on derived metric",
        backends={"warehouse": lambda: con},
    )
    frame = s.observe(
        mv.MetricRef("analytics.avg_elapsed_time"),
        dimensions=[mv.DimensionRef("analytics.queries.schema")],
    )
    df = frame.to_pandas()
    assert "schema" in df.columns
    assert len(df) == 2
