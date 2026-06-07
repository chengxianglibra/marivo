from __future__ import annotations

import ibis

from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
from marivo.semantic.authoring_check import check_authoring_inputs
from marivo.semantic.evidence import (
    AuthoringSourceInput,
    BoundedProfilePolicy,
    ColumnProfile,
    SourceEvidencePack,
    TableSource,
)
from marivo.semantic.evidence_store import EvidenceStore, structural_fingerprint
from marivo.semantic.reader import SemanticProject


def _write_source_pack(
    store: EvidenceStore,
    *,
    datasource: str,
    table: str,
    columns: tuple[tuple[str, str], ...],
) -> None:
    source = TableSource(table=table)
    table_comment = f"{table} table"
    column_comments = tuple((name, f"{name} column") for name, _type in columns)
    fp = structural_fingerprint(
        datasource=datasource,
        source=source,
        schema=columns,
        table_comment=table_comment,
        column_comments=column_comments,
    )
    ref = store.make_source_ref(
        datasource=datasource,
        source=source,
        structural_fp=fp,
        collected_at="2026-06-07T00:00:00Z",
    )
    pack = SourceEvidencePack(
        datasource=datasource,
        source=source,
        schema=columns,
        table_comment=table_comment,
        column_comments=column_comments,
        nullable=tuple((name, None) for name, _type in columns),
        partition_hints=(),
        key_hints=(),
        column_profiles=tuple(
            ColumnProfile(
                column=name,
                data_type=type_name,
                nullable=None,
                comment=f"{name} column",
            )
            for name, type_name in columns
        ),
        metadata_warnings=(),
        evidence_refs=(ref,),
        sample_policy=BoundedProfilePolicy(limit=100, max_profiled_columns=50),
        redaction_status="not_redacted",
        truncated=False,
    )
    store.write_source_pack(pack)


def test_check_authoring_inputs_checks_each_source_role_schema(tmp_path):
    store = EvidenceStore(tmp_path)
    _write_source_pack(
        store,
        datasource="warehouse",
        table="orders",
        columns=(("order_id", "int64"), ("customer_id", "int64")),
    )
    _write_source_pack(
        store,
        datasource="warehouse",
        table="customers",
        columns=(("customer_id", "int64"), ("segment", "string")),
    )

    result = check_authoring_inputs(
        store=store,
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
        semantic_refs=("sales.orders", "sales.customers"),
    )

    assert result.status == "blocked"
    assert result.issues[0].kind == "missing_column"
    assert result.issues[0].refs == ("sales.orders_to_customers", "role:to", "warehouse.customers")
    assert "missing_customer_id" in result.issues[0].message


def test_check_authoring_inputs_marks_missing_source_as_needs_input(tmp_path):
    store = EvidenceStore(tmp_path)

    result = check_authoring_inputs(
        store=store,
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
        semantic_refs=("sales.orders",),
    )

    assert result.status == "needs_input"
    assert result.issues[0].kind == "missing_source"
    assert result.issues[0].refs == ("sales.revenue", "role:primary", "warehouse.orders")


def test_derived_metric_allows_empty_sources(tmp_path):
    store = EvidenceStore(tmp_path)

    result = check_authoring_inputs(
        store=store,
        object_kind="derived_metric",
        subject_ref="sales.aov",
        sources=(),
        semantic_refs=("sales.revenue", "sales.order_count"),
    )

    assert result.status == "supported"
    assert result.issues == ()


# ---------------------------------------------------------------------------
# SemanticProject.assess_authoring integration tests
# ---------------------------------------------------------------------------


def _inspect_source(datasource, *, source, include_partitions=True):
    return TableMetadata(
        datasource=datasource,
        table=source.table,
        database=source.database,
        backend_type="duckdb",
        comment="orders fact",
        columns=(
            ColumnMetadata("order_id", "INTEGER", False, "Primary id", 1),
            ColumnMetadata("amount", "DOUBLE", True, "Gross amount", 2),
        ),
        partitions=(),
        warnings=(),
    )


def _backend_factory(_name):
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (order_id INT, amount DOUBLE)")
    con.con.execute("INSERT INTO orders VALUES (1, 10.0), (2, 20.0)")
    return con


def test_assess_authoring_collects_current_source_context_then_checks(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)
    project.bind_datasource_access(
        inspect_source=_inspect_source,
        backend_factory=_backend_factory,
    )

    assessment = project.assess_authoring(
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
        semantic_refs=("sales.orders",),
    )

    assert assessment.status == "supported"
    assert any(fact.label == "source_context" for fact in assessment.facts)
    assert any(fact.label == "referenced_columns" for fact in assessment.facts)
    assert project.list_evidence(datasource="warehouse", source=TableSource(table="orders"))


def test_assess_authoring_rejects_unbound_datasource_access(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)

    assessment = project.assess_authoring(
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
        semantic_refs=("sales.orders",),
    )

    assert assessment.status == "blocked"
    assert assessment.issues[0].kind == "missing_source"
    assert "bind_datasource_access" in assessment.issues[0].message
