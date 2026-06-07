from __future__ import annotations

import ibis

from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
from marivo.semantic.evidence import SelectedColumnsPolicy, TableSource
from marivo.semantic.reader import SemanticProject


def _fake_inspect_source(datasource, *, source, include_partitions=True):
    return TableMetadata(
        datasource=datasource,
        table=source.table,
        database=source.database,
        backend_type="duckdb",
        comment=None,
        columns=(
            ColumnMetadata("status", "VARCHAR", True, "status", 1),
            ColumnMetadata("amount", "DOUBLE", True, "amount", 2),
        ),
        partitions=(),
        warnings=(),
    )


def _backend_factory(_name):
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (status VARCHAR, amount DOUBLE)")
    con.con.execute("INSERT INTO orders VALUES ('paid',10.0),('paid',20.0),('void',5.0)")
    return con


def test_inspect_column_context_profiles_selected_columns(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    project = SemanticProject(root=root)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )
    evidence = project.inspect_column_context(
        datasource="warehouse",
        source=TableSource(table="orders"),
        columns=("status", "amount"),
        sample_policy=SelectedColumnsPolicy(limit=100, columns=("status", "amount")),
    )
    by_col = {e.column: e for e in evidence}
    assert by_col["status"].profile.distinct_count == 2
    assert by_col["amount"].profile.min_value == 5.0
    # retrievable as a pack by its evidence id
    pack = project.get_evidence_pack(by_col["status"].evidence_refs[0])
    assert pack is not None
