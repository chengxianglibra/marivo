from __future__ import annotations

import inspect

import ibis

from marivo.datasource.metadata import ColumnMetadata, TableMetadata
from marivo.semantic.authoring_check import check_authoring_inputs
from marivo.semantic.dtos import (
    AuthoringSourceInput,
    ColumnProfile,
    MetadataOnlyPolicy,
    SourceEvidencePack,
    TableSource,
)
from marivo.semantic.reader import SemanticProject


def _source_pack(
    *,
    datasource: str = "warehouse",
    table: str,
    columns: tuple[tuple[str, str], ...],
) -> SourceEvidencePack:
    source = TableSource(table=table)
    return SourceEvidencePack(
        datasource=datasource,
        source=source,
        schema=columns,
        table_comment=f"{table} source",
        column_comments=(),
        nullable=tuple((column, True) for column, _type in columns),
        partition_hints=(),
        key_hints=(),
        column_profiles=(
            ColumnProfile(
                column=column,
                data_type=data_type,
                nullable=True,
                comment=None,
                sample_scope="none",
            )
            for column, data_type in columns
        ),
        metadata_warnings=(),
        sample_policy=MetadataOnlyPolicy(),
        truncated=False,
    )


def test_check_authoring_inputs_checks_each_source_role_schema():
    packs = [
        _source_pack(
            table="orders",
            columns=(("order_id", "BIGINT"), ("customer_id", "BIGINT")),
        ),
        _source_pack(
            table="customers",
            columns=(("customer_id", "BIGINT"), ("name", "VARCHAR")),
        ),
    ]

    result = check_authoring_inputs(
        packs=packs,
        object_kind="relationship",
        subject_ref="sales.orders_to_customers",
        sources=(
            AuthoringSourceInput(
                role="from",
                datasource="warehouse",
                source=TableSource(table="orders"),
                columns=("customer_id",),
            ),
            AuthoringSourceInput(
                role="to",
                datasource="warehouse",
                source=TableSource(table="customers"),
                columns=("missing_customer_id",),
            ),
        ),
    )

    assert result.status == "blocked"
    assert result.issues[0].kind == "missing_column"
    assert result.issues[0].refs == (
        "sales.orders_to_customers",
        "role:to",
        "warehouse.customers",
    )
    assert "missing_customer_id" in result.issues[0].message


def test_check_authoring_inputs_requires_relationship_from_and_to_sources():
    packs = [
        _source_pack(
            table="orders",
            columns=(("order_id", "BIGINT"), ("customer_id", "BIGINT")),
        ),
    ]

    result = check_authoring_inputs(
        packs=packs,
        object_kind="relationship",
        subject_ref="sales.orders_to_customers",
        sources=(
            AuthoringSourceInput(
                role="from",
                datasource="warehouse",
                source=TableSource(table="orders"),
                columns=("customer_id",),
            ),
        ),
    )

    assert result.status == "needs_input"
    assert result.issues[0].kind == "missing_source"
    assert result.issues[0].refs == ("sales.orders_to_customers", "role:to")
    assert "requires a 'to' source" in result.issues[0].message


def test_check_authoring_inputs_marks_missing_source_as_needs_input():
    result = check_authoring_inputs(
        packs=[],
        object_kind="metric",
        subject_ref="sales.revenue",
        sources=(
            AuthoringSourceInput(
                role="primary",
                datasource="warehouse",
                source=TableSource(table="orders"),
                columns=("amount",),
            ),
        ),
    )

    assert result.status == "needs_input"
    assert result.issues[0].kind == "missing_source"
    assert result.issues[0].refs == ("sales.revenue", "role:primary", "warehouse.orders")


def test_derived_metric_allows_empty_sources():
    result = check_authoring_inputs(
        packs=[],
        object_kind="derived_metric",
        subject_ref="sales.average_order_value",
        semantic_refs=("sales.revenue", "sales.order_count"),
    )

    assert result.status == "supported"
    assert result.issues == ()
    assert result.facts[0].label == "semantic_dependencies"
    assert result.facts[0].value == ["sales.revenue", "sales.order_count"]


def test_assess_authoring_collects_current_source_context_then_checks(tmp_path):
    def inspect_orders(datasource, *, source, include_partitions=True):
        return TableMetadata(
            datasource=datasource,
            table=source.table,
            database=source.database,
            backend_type="duckdb",
            comment="orders fact",
            columns=(
                ColumnMetadata("order_id", "INTEGER", False, "pk", 1),
                ColumnMetadata("amount", "DOUBLE", True, "gross amount", 2),
            ),
            partitions=(),
            warnings=(),
        )

    def backend_factory(_name):
        con = ibis.duckdb.connect(":memory:")
        con.con.execute("CREATE TABLE orders (order_id INT, amount DOUBLE)")
        con.con.execute("INSERT INTO orders VALUES (1, 10.0), (2, 20.0)")
        return con

    marivo_root = tmp_path / ".marivo"
    root = marivo_root / "semantic"
    root.mkdir(parents=True)
    (marivo_root / "datasource").mkdir(parents=True, exist_ok=True)
    (marivo_root / "datasource" / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(warehouse)\n"
    )
    project = SemanticProject(root=root)
    source = TableSource(table="orders")

    result = project.assess_authoring(
        object_kind="metric",
        subject_ref="sales.revenue",
        sources=(
            AuthoringSourceInput(
                role="primary",
                datasource="warehouse",
                source=source,
                columns=("amount",),
            ),
        ),
        semantic_refs=("sales.orders",),
        inspect_source=inspect_orders,
        backend_factory=backend_factory,
    )

    assert result.status == "supported"
    assert any(fact.label == "source_context" for fact in result.facts)
    assert any(fact.label == "referenced_columns" for fact in result.facts)


def test_assess_authoring_rejects_unregistered_datasource(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)

    result = project.assess_authoring(
        object_kind="metric",
        subject_ref="sales.revenue",
        sources=(
            AuthoringSourceInput(
                role="primary",
                datasource="warehouse",
                source=TableSource(table="orders"),
                columns=("amount",),
            ),
        ),
    )

    assert result.status == "blocked"
    assert result.issues[0].kind == "missing_source"
    assert result.issues[0].message
    assert "md.register()" in result.issues[0].message


def test_assess_authoring_allows_derived_metric_without_sources_or_bound_access(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)

    result = project.assess_authoring(
        object_kind="derived_metric",
        subject_ref="sales.average_order_value",
        sources=(),
        semantic_refs=("sales.revenue", "sales.order_count"),
    )

    assert result.status == "supported"
    assert any(
        fact.label == "semantic_dependencies"
        and fact.value == ["sales.revenue", "sales.order_count"]
        for fact in result.facts
    )


def test_assess_authoring_signature_omits_draft_overrides():
    signature = inspect.signature(SemanticProject.assess_authoring)
    assert not {
        "ai_context",
        "source_sql",
    }.intersection(signature.parameters)
