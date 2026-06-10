"""Source evidence collection to authoring assessment.

Shows: collect a SourceEvidencePack with bounded profiles, then run
assess_authoring for a metric and branch on status.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import ibis

import marivo.semantic as ms
from marivo.analysis.datasources.metadata import ColumnMetadata, PartitionMetadata, TableMetadata
from marivo.semantic.ir import TableSourceIR


def fake_inspect_source(
    datasource: str, *, source: TableSourceIR, include_partitions: bool = True
) -> TableMetadata:
    return TableMetadata(
        datasource=datasource,
        table=source.table,
        database=source.database,
        backend_type="duckdb",
        comment="Orders fact table. dt is the reporting partition.",
        columns=(
            ColumnMetadata("order_id", "INTEGER", False, "Primary order id", 1),
            ColumnMetadata("dt", "DATE", False, "Partition date for reporting", 2),
            ColumnMetadata("amount", "DOUBLE", True, "Gross order amount", 3),
            ColumnMetadata("paid", "BOOLEAN", True, "Paid flag", 4),
        ),
        partitions=(PartitionMetadata("dt", type="DATE"),) if include_partitions else (),
        warnings=(),
    )


def backend_factory(_name: str) -> ibis.backends.duckdb.Backend:
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (order_id INT, dt DATE, amount DOUBLE, paid BOOLEAN)")
    con.con.execute(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, true), (2, DATE '2026-07-01', 20.0, true), "
        "(3, DATE '2026-07-02', 5.0, false)"
    )
    return con


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / ".marivo" / "semantic"
    root.mkdir(parents=True)
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(root=root)
    project.bind_datasource_access(
        inspect_source=fake_inspect_source, backend_factory=backend_factory
    )

    pack = project.inspect_source_context(
        datasource="warehouse",
        source=ms.TableSource(table="orders"),
        sample_policy=ms.BoundedProfilePolicy(limit=100, max_profiled_columns=50),
    )
    print("partition hints:", list(pack.partition_hints))
    amount = next(p for p in pack.column_profiles if p.column == "amount")
    print("amount sample scope:", amount.sample_scope, "approximate:", amount.approximate)

    assessment = project.assess_authoring(
        object_kind="metric",
        subject_ref="sales.revenue",
        sources=(
            ms.AuthoringSourceInput(
                role="primary",
                datasource="warehouse",
                source=ms.TableSource(table="orders"),
                columns=("amount", "paid"),
            ),
        ),
        semantic_refs=("sales.orders",),
    )
    print("assessment status:", assessment.status)
    print("issue kinds:", [issue.kind for issue in assessment.issues])
