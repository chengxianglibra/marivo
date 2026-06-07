"""Source evidence collection to a static authoring-input check.

Shows: collect a SourceEvidencePack with bounded profiles, record source SQL as
evidence, then run check_authoring_inputs for a metric and branch on status.
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
    project = ms.SemanticProject(workspace_dir=Path(tmp))
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

    sql_ref = project.record_authoring_evidence(
        ms.AuthoringEvidenceInput(
            kind="source_sql",
            subject_refs=("sales.revenue",),
            content="select sum(amount) as revenue from orders where paid",
            source_dialect="duckdb",
        )
    )

    result = project.check_authoring_inputs(
        object_kind="metric",
        subject_ref="sales.revenue",
        datasource="warehouse",
        source=ms.TableSource(table="orders"),
        columns=("amount", "paid"),
        evidence_refs=(sql_ref.id,),
        ai_context=ms.AiContextInput(business_definition="Paid order revenue before refunds."),
    )
    print("check status:", result.status)
    print("next checks:", list(result.next_checks))
