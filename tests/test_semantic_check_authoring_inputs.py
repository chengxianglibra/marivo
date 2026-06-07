from __future__ import annotations

import ibis

from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
from marivo.semantic.evidence import (
    AuthoringSourceInput,
    MetadataOnlyPolicy,
    TableSource,
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
    project = SemanticProject(workspace_dir=tmp_path)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    project.inspect_source_context(
        datasource="warehouse",
        source=TableSource(table="orders"),
        sample_policy=MetadataOnlyPolicy(),
    )
    return project


def test_metric_with_source_columns_supported(tmp_path):
    project = _project_with_source(tmp_path)
    result = project.check_authoring_inputs(
        object_kind="metric",
        subject_ref="sales.revenue",
        sources=(
            AuthoringSourceInput(
                role="primary",
                datasource="warehouse",
                source=TableSource(table="orders"),
                columns=("amount", "paid"),
            ),
        ),
    )
    assert result.status == "supported"


def test_missing_column_is_a_blocker(tmp_path):
    project = _project_with_source(tmp_path)
    result = project.check_authoring_inputs(
        object_kind="field",
        subject_ref="sales.orders.nope",
        sources=(
            AuthoringSourceInput(
                role="primary",
                datasource="warehouse",
                source=TableSource(table="orders"),
                columns=("nope",),
            ),
        ),
    )
    assert result.status == "blocked"
    assert any(i.kind == "missing_column" and i.severity == "blocker" for i in result.issues)


def test_unknown_source_returns_needs_input(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(workspace_dir=tmp_path)
    result = project.check_authoring_inputs(
        object_kind="dataset",
        subject_ref="sales.orders",
        sources=(
            AuthoringSourceInput(
                role="primary",
                datasource="warehouse",
                source=TableSource(table="orders"),
            ),
        ),
    )
    assert result.status == "needs_input"


def test_metric_without_sources_requires_at_least_one(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)
    result = project.check_authoring_inputs(
        object_kind="metric",
        subject_ref="sales.revenue",
        sources=(),
    )
    assert result.status == "needs_input"
    assert any(i.kind == "missing_source" for i in result.issues)
