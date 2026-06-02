"""Candidate proposal to open questions, with ambiguous time axis handling."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import marivo.semantic as ms
from marivo.analysis.datasources.metadata import ColumnMetadata, PartitionMetadata, TableMetadata
from marivo.semantic.ir import TableSourceIR


def decision_record_from_question(
    question: ms.OpenQuestion,
    chosen: str,
    *,
    cited_source: dict[str, object],
) -> ms.DecisionRecord:
    evidence_types = sorted(
        {e.evidence_type for candidate in question.candidates for e in candidate.evidence}
    )
    evidence_fingerprint = "|".join(
        sorted(e.locator for candidate in question.candidates for e in candidate.evidence)
    )
    return ms.DecisionRecord(
        decision_kind=question.decision_kind,
        chosen=chosen,
        agreement_confidence=question.agreement_confidence,
        qualifying_sources=tuple(evidence_types),
        materiality=question.materiality,
        blast_radius=question.blast_radius,
        evidence_fingerprint=evidence_fingerprint,
        question_id=question.id,
        decided_at=datetime.now(UTC).isoformat(),
        cited_source=cited_source,
        cited_columns=(chosen,),
    )


def fake_inspect_source(
    datasource: str,
    *,
    source: TableSourceIR,
    include_partitions: bool = True,
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
            ColumnMetadata("created_at", "TIMESTAMP", True, "Order creation timestamp", 3),
            ColumnMetadata("amount", "DOUBLE", True, "Gross order amount", 4),
            ColumnMetadata("status_code", "INTEGER", True, "Order status code", 5),
        ),
        partitions=(PartitionMetadata("dt", type="DATE", comment="Daily partition"),)
        if include_partitions
        else (),
        warnings=(),
    )


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / ".marivo" / "semantic" / "sales"
    root.mkdir(parents=True)

    project = ms.SemanticProject(root=root.parent)
    result = project.propose_candidates(
        datasource="warehouse",
        sources=[ms.table("orders")],
        model="sales",
        inspect_source=fake_inspect_source,
    )
    questions = project.open_questions(candidates=result.candidates)
    time_candidates = [c for c in result.candidates if c.decision_kind == "time_field_identity"]
    chosen_time = "dt" if any(c.slot_values.get("column") == "dt" for c in time_candidates) else ""
    print("ambiguous time axis candidates:", [c.slot_values.get("column") for c in time_candidates])
    print("chosen partition time field:", chosen_time)
    print(
        "residual columns:",
        [(rc.column, rc.data_type, rc.comment) for rc in result.residual_columns],
    )
    print("open questions:", [(q.severity, q.decision_kind) for q in questions])

    (root / "_model.py").write_text("import marivo.semantic as ms\nms.model(name='sales')\n")
    project.reload()

    blocker = next(
        q
        for q in questions
        if q.decision_kind == "time_field_identity"
        and any(c.slot_values.get("column") == chosen_time for c in q.candidates)
    )
    project.answer(
        blocker,
        f"Use {chosen_time} as the reporting time axis",
        evidence_fingerprint="|".join(
            sorted(e.locator for candidate in blocker.candidates for e in candidate.evidence)
        ),
    )
    record = decision_record_from_question(
        blocker,
        chosen_time,
        cited_source={
            "datasource": "warehouse",
            "source": {"kind": "table", "table": "orders", "database": None},
        },
    )
    project.record_decision(blocker.subject_refs[0], record)
    print("recorded decision:", record.decision_kind, record.chosen)
