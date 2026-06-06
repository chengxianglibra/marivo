from __future__ import annotations

import ibis

from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
from marivo.semantic.evidence import (
    AiContextInput,
    AuthoringEvidenceInput,
    DatasetSource,
    SamplePolicy,
)
from marivo.semantic.reader import SemanticProject


def _fake_inspect_source(datasource, *, source, include_partitions=True):
    return TableMetadata(
        datasource=datasource,
        table=source.table,
        database=source.database,
        backend_type="duckdb",
        comment="orders",
        columns=(
            ColumnMetadata("amount", "DOUBLE", True, "amount", 1),
            ColumnMetadata("paid", "BOOLEAN", True, "paid flag", 2),
        ),
        partitions=(),
        warnings=(),
    )


def _backend_factory(_name):
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (amount DOUBLE, paid BOOLEAN)")
    con.con.execute("INSERT INTO orders VALUES (10.0, true)")
    return con


def _project_with_source(tmp_path) -> SemanticProject:
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)
    project.inspect_source_context(
        datasource="warehouse",
        source=DatasetSource(kind="table", table="orders"),
        inspect_source=_fake_inspect_source,
        backend_factory=_backend_factory,
        sample_policy=SamplePolicy(mode="metadata_only"),
    )
    return project


def test_metric_without_evidence_needs_evidence(tmp_path):
    project = _project_with_source(tmp_path)
    result = project.check_authoring_inputs(
        object_kind="metric",
        subject_ref="sales.revenue",
        datasource="warehouse",
        source=DatasetSource(kind="table", table="orders"),
        columns=("amount", "paid"),
        ai_context=AiContextInput(business_definition="Paid order revenue."),
    )
    assert result.status == "needs_evidence"
    assert any(i.kind == "missing_evidence" for i in result.issues)
    assert "inspect_source_context" not in set(result.next_checks)  # source known


def test_metric_with_source_sql_is_supported(tmp_path):
    project = _project_with_source(tmp_path)
    ref = project.record_authoring_evidence(
        AuthoringEvidenceInput(
            kind="source_sql",
            subject_refs=("sales.revenue",),
            content="select sum(amount) from orders where paid",
        )
    )
    result = project.check_authoring_inputs(
        object_kind="metric",
        subject_ref="sales.revenue",
        datasource="warehouse",
        source=DatasetSource(kind="table", table="orders"),
        columns=("amount", "paid"),
        evidence_refs=(ref.id,),
        ai_context=AiContextInput(business_definition="Paid order revenue."),
    )
    assert result.status == "supported"


def test_missing_column_is_a_blocker(tmp_path):
    project = _project_with_source(tmp_path)
    result = project.check_authoring_inputs(
        object_kind="field",
        subject_ref="sales.orders.nope",
        datasource="warehouse",
        source=DatasetSource(kind="table", table="orders"),
        columns=("nope",),
    )
    assert result.status == "blocked"
    assert any(i.kind == "missing_column" and i.severity == "blocker" for i in result.issues)


def test_unknown_source_returns_needs_evidence_with_next_check(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)
    result = project.check_authoring_inputs(
        object_kind="dataset",
        subject_ref="sales.orders",
        datasource="warehouse",
        source=DatasetSource(kind="table", table="orders"),
    )
    assert result.status == "needs_evidence"
    assert "inspect_source_context" in result.next_checks
