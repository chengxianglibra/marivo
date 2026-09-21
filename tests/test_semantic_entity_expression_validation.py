"""Output-schema validation and health evidence for expression Entities."""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path
from typing import Any

import pytest

import marivo.semantic as ms
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind
from tests.shared_fixtures import load_inline_semantic

_PROJECTED_MODEL = """\
import marivo.datasource as md
import marivo.semantic as ms

raw_sales = ms.entity(
    name="raw_sales",
    datasource=ms.ref.datasource("wh"),
    source=md.table(
        "raw_events",
        columns={
            "order_id": md.source_column("payload.id", data_type="string"),
            "dt": md.source_column("event.dt", data_type="string"),
            "amount": md.source_column("generated.amount", data_type="float64"),
        },
    ),
    primary_key=["order_id"],
)


@ms.entity(
    datasource=ms.ref.datasource("wh"),
    source=md.table(
        "raw_events",
        columns={
            "order_id": md.source_column("payload.id", data_type="string"),
            "dt": md.source_column("event.dt", data_type="string"),
            "amount": md.source_column("generated.amount", data_type="float64"),
        },
    ),
    primary_key=["dt"],
)
def daily_sales(raw):
    '''Daily totals per day.'''
    return raw.group_by("dt").aggregate(
        net=raw["amount"].sum(),
        order_count=raw.count(),
    )


net = ms.measure_column(
    name="net",
    entity=daily_sales,
    column="net",
    additivity="additive",
)
daily_revenue = ms.aggregate(name="daily_revenue", measure=net, agg="sum")
"""


def test_expression_entity_output_column_passes_downstream_validation() -> None:
    """Derived output columns satisfy downstream field and key validation."""
    with load_inline_semantic(_PROJECTED_MODEL) as result:
        assert result.errors == ()
        assert result.registry is not None


def test_expression_entity_primary_key_may_be_output_only() -> None:
    """Declared keys validate against the output schema, not input aliases."""
    source = _PROJECTED_MODEL.replace('primary_key=["dt"],', 'primary_key=["order_count"],')
    with load_inline_semantic(source) as result:
        matching = [
            error for error in result.errors if error.details.get("entity") == "test.daily_sales"
        ]

    assert matching == []


def test_expression_entity_dropped_column_fails_at_consumer_with_output_repair() -> None:
    """A column dropped by the Entity body fails at its consumer against output."""
    source = (
        _PROJECTED_MODEL
        + """
qty = ms.measure_column(
    name="qty",
    entity=daily_sales,
    column="qty",
    additivity="additive",
)
"""
    )
    with load_inline_semantic(source) as result:
        matching = [
            error
            for error in result.errors
            if error.details.get("entity") == "test.daily_sales"
            and error.constraint_id == ConstraintId.REF_SHAPE
        ]

    assert len(matching) == 1
    error = matching[0]
    assert error.kind == ErrorKind.INVALID_REF
    assert error.details["available_output_columns"] == ["dt", "net", "order_count"]
    assert error.details["missing_references"][0]["received_column"] == "qty"
    assert "Entity body output" in error.hint


def test_expression_entity_missing_input_column_fails_at_load() -> None:
    """An input column absent from the declared Source fails the Entity body."""
    source = _PROJECTED_MODEL.replace(
        'net=raw["amount"].sum(),',
        'net=raw["missing_input"].sum(),',
    )
    with load_inline_semantic(source) as result:
        matching = [
            error for error in result.errors if error.details.get("entity") == "test.daily_sales"
        ]

    assert len(matching) == 1
    error = matching[0]
    assert error.kind == ErrorKind.INVALID_COMPONENT_BODY
    assert error.details["available_input_columns"] == ["amount", "dt", "order_id"]
    assert "missing_input" in error.message


def test_expression_entity_non_table_return_fails_at_load() -> None:
    """A body returning a column instead of a Table fails at assembly time."""
    source = _PROJECTED_MODEL.replace(
        """    return raw.group_by("dt").aggregate(
        net=raw["amount"].sum(),
        order_count=raw.count(),
    )""",
        """    return raw["amount"]""",
    )
    with load_inline_semantic(source) as result:
        matching = [
            error for error in result.errors if error.details.get("entity") == "test.daily_sales"
        ]

    assert len(matching) == 1
    error = matching[0]
    assert error.kind == ErrorKind.BINDING_RESULT_INVALID
    assert error.expected == "ibis.expr.types.Table"
    assert error.received == "FloatingColumn"


def test_expression_entity_unprojected_source_defers_output_validation() -> None:
    """Without declared input metadata, output validation stays deferred to runtime."""
    source = _PROJECTED_MODEL.replace(
        """        columns={
            "order_id": md.source_column("payload.id", data_type="string"),
            "dt": md.source_column("event.dt", data_type="string"),
            "amount": md.source_column("generated.amount", data_type="float64"),
        },
    ),
    primary_key=["dt"],""",
        """    ),
    primary_key=["dt"],""",
    )
    with load_inline_semantic(source) as result:
        matching = [
            error for error in result.errors if error.details.get("entity") == "test.daily_sales"
        ]

    assert matching == []


@pytest.mark.parametrize(
    "body",
    [
        # Aggregating a string input builds fine in some shapes but fails in
        # others; neither may crash assembly with a bare exception.
        'return raw.group_by(raw["order_id"]).aggregate(x=raw["dt"].sum())',
        'return raw.group_by("missing").aggregate(x=raw["amount"].sum())',
    ],
)
def test_expression_entity_ambiguous_build_failure_defers_without_crash(body: str) -> None:
    """Build failures other than missing input defer without crashing assembly."""
    source = _PROJECTED_MODEL.replace(
        """    return raw.group_by("dt").aggregate(
        net=raw["amount"].sum(),
        order_count=raw.count(),
    )""",
        f"    {body}",
    )
    with load_inline_semantic(source) as result:
        assert isinstance(result.errors, tuple)


# ---------------------------------------------------------------------------
# Source health: input build failure vs output-field failures
# ---------------------------------------------------------------------------


def _seed_health_sources(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE order_changes (
                order_id INTEGER,
                dt TEXT,
                amount REAL
            );
            INSERT INTO order_changes VALUES
                (1, '2020-01-01', 10.0),
                (2, '2020-01-01', 5.0),
                (1, '2020-01-02', 7.0);
            """
        )
        connection.commit()
    finally:
        connection.close()


_HEALTH_MODEL = textwrap.dedent(
    """\
    import marivo.datasource as md
    import marivo.semantic as ms


    @ms.entity(
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("order_changes"),
        primary_key=["dt"],
    )
    def daily_orders(raw):
        '''Daily net per day.'''
        return raw.group_by("dt").aggregate(net=raw["amount"].sum())


    net = ms.measure_column(
        name="net",
        entity=daily_orders,
        column="net",
        additivity="additive",
    )
    daily_revenue = ms.aggregate(name="daily_revenue", measure=net, agg="sum")
    """
)


def _health_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Any,
    model: str,
) -> tuple[SemanticCatalog, Path]:
    database_path = tmp_path / "warehouse.sqlite"
    _seed_health_sources(database_path)
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.sqlite(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Analytics', default=True)\n"
            ),
            "sales/model.py": model,
        }
    )
    monkeypatch.chdir(tmp_path)
    return SemanticCatalog(project), database_path


def test_source_health_accepts_derived_output_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Any,
) -> None:
    """A valid derived field is current, not failed against physical input."""
    catalog, _database_path = _health_catalog(
        tmp_path, monkeypatch, semantic_project_factory, _HEALTH_MODEL
    )

    report = catalog.source_health([ms.ref.metric("sales.daily_revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "current"
    assert schema.status == "current"
    assert schema.observed["entity_output_established"] is True
    assert schema.observed["entity_output_columns"] == ["dt", "net"]


def test_source_health_classifies_invalid_expression_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Any,
) -> None:
    """Input drift fails the Entity expression build, not downstream fields."""
    catalog, database_path = _health_catalog(
        tmp_path, monkeypatch, semantic_project_factory, _HEALTH_MODEL
    )
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("ALTER TABLE order_changes RENAME COLUMN amount TO amount_old")
        connection.commit()
    finally:
        connection.close()

    report = catalog.source_health([ms.ref.metric("sales.daily_revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert schema.observed["reason"] == "entity_expression_invalid"
    assert schema.observed["entity_output_established"] is False
    assert schema.repair is not None
    assert schema.repair.kind == "reauthor"


def test_source_health_reports_missing_output_fields_with_output_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Any,
) -> None:
    """A field referencing a dropped column fails against bounded output columns."""
    model = (
        _HEALTH_MODEL
        + """
qty = ms.measure_column(
    name="qty",
    entity=daily_orders,
    column="qty",
    additivity="additive",
)
"""
    )
    catalog, _database_path = _health_catalog(
        tmp_path, monkeypatch, semantic_project_factory, model
    )

    report = catalog.source_health([ms.ref.metric("sales.daily_revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert schema.observed["missing_field_refs"] == ["measure:sales.daily_orders.qty"]
    assert schema.observed["entity_output_columns"] == ["dt", "net"]
    assert schema.repair is not None
    assert schema.repair.kind == "reauthor"
    assert "Entity body output" in schema.repair.action


def test_direct_entity_source_health_keeps_input_column_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Any,
) -> None:
    """Direct entities keep comparing field bindings to physical input columns."""
    direct_model = (
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(\n"
        '    name="orders",\n'
        '    datasource=ms.ref.datasource("warehouse"),\n'
        '    source=md.table("order_changes"),\n'
        '    primary_key=["order_id"],\n'
        ")\n"
        "net = ms.measure_column(\n"
        '    name="net",\n'
        "    entity=orders,\n"
        '    column="net",\n'
        '    additivity="additive",\n'
        ")\n"
        'daily_revenue = ms.aggregate(name="daily_revenue", measure=net, agg="sum")\n'
    )
    catalog, _database_path = _health_catalog(
        tmp_path, monkeypatch, semantic_project_factory, direct_model
    )

    report = catalog.source_health([ms.ref.metric("sales.daily_revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert schema.observed["missing_field_refs"] == ["measure:sales.orders.net"]
    assert "entity_output_columns" not in schema.observed


def test_source_health_reports_missing_output_primary_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Any,
) -> None:
    """Declared keys missing from the established output schema fail health."""
    model = _HEALTH_MODEL.replace('primary_key=["dt"],', 'primary_key=["order_id"],')
    catalog, _database_path = _health_catalog(
        tmp_path, monkeypatch, semantic_project_factory, model
    )

    report = catalog.source_health([ms.ref.metric("sales.daily_revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert schema.observed["missing_output_primary_key"] == ["order_id"]
    assert schema.observed["entity_output_columns"] == ["dt", "net"]
    assert schema.repair is not None
    assert "order_id" in schema.repair.action


def test_source_health_defers_field_presence_when_output_types_unusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Any,
) -> None:
    """An unresolvable inspected type reports undetermined output, not missing fields."""
    catalog, database_path = _health_catalog(
        tmp_path, monkeypatch, semantic_project_factory, _HEALTH_MODEL
    )
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            ALTER TABLE order_changes ADD COLUMN weird NUMERIC(10, 2);
            UPDATE order_changes SET weird = 1.0;
            """
        )
        connection.commit()
    finally:
        connection.close()

    report = catalog.source_health([ms.ref.metric("sales.daily_revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert schema.observed["reason"] == "declared_input_types_unusable"
    assert schema.observed["entity_output_established"] is False
    assert schema.observed["missing_field_refs"] == []
    assert schema.repair is not None
    assert "could not be established" in schema.repair.action
