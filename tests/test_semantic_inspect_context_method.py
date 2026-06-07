from __future__ import annotations

import ibis

from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
from marivo.semantic.evidence import DatasetSource, SamplePolicy
from marivo.semantic.reader import SemanticProject


def _fake_inspect_source(datasource, *, source, include_partitions=True):
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


def test_inspect_source_context_returns_pack_and_persists(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    pack = project.inspect_source_context(
        datasource="warehouse",
        source=DatasetSource(kind="table", table="orders"),
        sample_policy=SamplePolicy(mode="bounded_profile", limit=50),
    )
    assert pack.datasource == "warehouse"
    assert {c.column for c in pack.column_profiles} == {"order_id", "amount"}
    refs = project.list_evidence(
        datasource="warehouse", source=DatasetSource(kind="table", table="orders")
    )
    assert len(refs) == 1


def test_inspect_source_context_records_raw_preview_for_readiness(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    project.inspect_source_context(
        datasource="warehouse",
        source=DatasetSource(kind="table", table="orders"),
        sample_policy=SamplePolicy(mode="bounded_profile", limit=50),
    )
    # the dataset-level raw preview ref is now visible to readiness plumbing
    assert any("orders" in ref for ref in project.raw_preview_evidence())


def test_metadata_only_does_not_record_raw_preview(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    project.inspect_source_context(
        datasource="warehouse",
        source=DatasetSource(kind="table", table="orders"),
        sample_policy=SamplePolicy(mode="metadata_only"),
    )
    assert project.raw_preview_evidence() == ()
