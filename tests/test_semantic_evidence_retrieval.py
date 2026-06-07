from __future__ import annotations

import ibis

from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
from marivo.semantic.evidence import AuthoringEvidenceInput, DatasetSource, SamplePolicy
from marivo.semantic.reader import SemanticProject


def _fake_inspect_source(datasource, *, source, include_partitions=True):
    return TableMetadata(
        datasource=datasource,
        table=source.table,
        database=source.database,
        backend_type="duckdb",
        comment=None,
        columns=(ColumnMetadata("amount", "DOUBLE", True, None, 1),),
        partitions=(),
        warnings=(),
    )


def _backend_factory(_name):
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (amount DOUBLE)")
    con.con.execute("INSERT INTO orders VALUES (10.0)")
    return con


def test_evidence_survives_a_fresh_project_instance(tmp_path):
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
    # new process / new instance, same root
    reloaded = SemanticProject(root=root)
    refs = reloaded.list_evidence(
        datasource="warehouse", source=DatasetSource(kind="table", table="orders")
    )
    assert len(refs) == 1
    pack = reloaded.get_evidence_pack(refs[0].id)
    assert pack is not None
    assert pack.datasource == "warehouse"  # type: ignore[union-attr]


def test_list_evidence_by_subject_refs(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)
    project.record_authoring_evidence(
        AuthoringEvidenceInput(
            kind="knowledge_document",
            subject_refs=("sales.revenue",),
            content="Revenue is paid order amount before refunds.",
        )
    )
    refs = project.list_evidence(subject_refs=("sales.revenue",))
    assert len(refs) == 1 and refs[0].kind == "knowledge_document"
