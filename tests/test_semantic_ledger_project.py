# tests/test_semantic_ledger_project.py
from __future__ import annotations

import marivo.semantic as ms
from marivo.semantic import ledger as lg

MODEL_PY = "import marivo.semantic as ms\nms.model(name='sales')\n"
DATASETS_PY = """
import marivo.semantic as ms
import marivo.datasource as md

warehouse = md.ref('warehouse')

@ms.dataset(name='orders', datasource=warehouse)
def orders(backend):
    return backend.table('orders')

@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')
def revenue(orders):
    return orders.amount.sum()
"""


WAREHOUSE_PY = (
    "import marivo.datasource as md\n"
    "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    "md.datasource(warehouse)\n"
)


def _project(semantic_project_factory):
    return semantic_project_factory({"sales/_model.py": MODEL_PY, "sales/datasets.py": DATASETS_PY})


def _project_loaded(semantic_project_factory):
    """Project that includes the datasource so load() succeeds."""
    return semantic_project_factory(
        {
            "datasource/warehouse.py": WAREHOUSE_PY,
            "sales/_model.py": MODEL_PY,
            "sales/datasets.py": DATASETS_PY,
        }
    )


def test_record_decision_appends_to_object_ledger(semantic_project_factory):
    project = _project(semantic_project_factory)
    rec = lg.DecisionRecord(
        decision_kind="metric_decomposition",
        chosen="sum",
        agreement_confidence="high",
        qualifying_sources=("source_sql",),
        materiality="high",
        blast_radius=0,
        evidence_fingerprint="sha256:a",
        question_id=None,
        decided_at="2026-05-31T10:00:00+00:00",
    )
    project.record_decision("sales.revenue", rec)

    store = lg.LedgerStore(project.root_path)
    obj = store.read_object("sales.revenue")
    assert obj is not None
    assert obj.decisions == (rec,)


def test_record_decision_accumulates(semantic_project_factory):
    project = _project(semantic_project_factory)
    rec1 = lg.DecisionRecord(
        decision_kind="metric_additivity",
        chosen="additive",
        agreement_confidence="high",
        qualifying_sources=("comment",),
        materiality="medium",
        blast_radius=0,
        evidence_fingerprint="sha256:a",
        question_id=None,
        decided_at="t1",
    )
    rec2 = lg.DecisionRecord(
        decision_kind="metric_decomposition",
        chosen="sum",
        agreement_confidence="high",
        qualifying_sources=("source_sql",),
        materiality="high",
        blast_radius=0,
        evidence_fingerprint="sha256:b",
        question_id=None,
        decided_at="t2",
    )
    project.record_decision("sales.revenue", rec1)
    project.record_decision("sales.revenue", rec2)
    obj = lg.LedgerStore(project.root_path).read_object("sales.revenue")
    assert obj is not None
    assert obj.decisions == (rec1, rec2)


def test_answer_appends_confirmation(semantic_project_factory):
    project = _project(semantic_project_factory)
    question = ms.OpenQuestion(
        id="q-amount-unit",
        subject_refs=("sales.revenue",),
        decision_kind="amount_unit",
        gated_by=None,
        candidates=(),
        materiality="high",
        blast_radius=0,
        agreement_confidence="low",
        default_if_unanswered=None,
        severity="blocker",
        blocker_reason="high_materiality_low_confidence",
    )
    project.answer(question, "cents", evidence_fingerprint="sha256:a")

    confirmations = lg.LedgerStore(project.root_path).read_confirmations("sales")
    assert len(confirmations) == 1
    assert confirmations[0].question_id == "q-amount-unit"
    assert confirmations[0].answer == "cents"
    assert confirmations[0].decision_kind == "amount_unit"
    assert confirmations[0].subject_refs == ("sales.revenue",)


def test_open_questions_skips_confirmed_questions(semantic_project_factory):
    project = _project_loaded(semantic_project_factory)
    cand = ms.Candidate(
        object_kind="dataset",
        proposed_id="sales.orders",
        decision_kind="amount_unit",
        slot_values={"column": "amount"},
        evidence=(ms.EvidenceRef("metadata", "metadata:warehouse.orders.amount"),),
        semantic_delta="unit?",
    )
    # First pass: one blocker surfaces.
    [question] = project.open_questions(candidates=[cand])
    assert question.severity == "blocker"

    # Agent answers it -> confirmation recorded.
    project.answer(question, "cents")

    # Second pass with identical candidate: the confirmed question is deduped away.
    assert project.open_questions(candidates=[cand]) == ()


def test_audit_resurfaces_stale_dangerous_decision(semantic_project_factory):
    from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
    from marivo.semantic import ledger as lg

    project = _project_loaded(semantic_project_factory)
    project.load()

    # Record a dangerous decision whose fingerprint reflects status:INTEGER.
    old_fp = lg.evidence_fingerprint({"status": "INTEGER"}, None, {"status": "1=paid"})
    project.record_decision(
        "sales.revenue",
        lg.DecisionRecord(
            decision_kind="metric_decomposition",
            chosen="sum",
            agreement_confidence="high",
            qualifying_sources=("source_sql",),
            materiality="high",
            blast_radius=0,
            evidence_fingerprint=old_fp,
            question_id=None,
            decided_at="t",
            cited_table="warehouse.orders",
            cited_columns=("status",),
        ),
    )

    # Current metadata now reports status:VARCHAR -> fingerprint changed -> stale.
    def fake_inspect_table(datasource, *, table, database=None, include_partitions=True):
        return TableMetadata(
            datasource=datasource,
            table=table,
            database=None,
            backend_type="duckdb",
            comment=None,
            columns=(ColumnMetadata("status", "VARCHAR", True, "1=paid", None),),
            partitions=(),
            warnings=(),
        )

    questions = project.audit(inspect_table=fake_inspect_table)
    assert len(questions) == 1
    assert questions[0].decision_kind == "metric_decomposition"
    assert questions[0].subject_refs == ("sales.revenue",)
    assert questions[0].severity == "blocker"  # dangerous kind + stale (low verdict)


def test_audit_returns_nothing_when_evidence_unchanged(semantic_project_factory):
    from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
    from marivo.semantic import ledger as lg

    project = _project(semantic_project_factory)
    fp = lg.evidence_fingerprint({"status": "INTEGER"}, None, {"status": "1=paid"})
    project.record_decision(
        "sales.revenue",
        lg.DecisionRecord(
            decision_kind="metric_decomposition",
            chosen="sum",
            agreement_confidence="high",
            qualifying_sources=("source_sql",),
            materiality="high",
            blast_radius=0,
            evidence_fingerprint=fp,
            question_id=None,
            decided_at="t",
            cited_table="warehouse.orders",
            cited_columns=("status",),
        ),
    )

    def fake_inspect_table(datasource, *, table, database=None, include_partitions=True):
        return TableMetadata(
            datasource=datasource,
            table=table,
            database=None,
            backend_type="duckdb",
            comment=None,
            columns=(ColumnMetadata("status", "INTEGER", True, "1=paid", None),),
            partitions=(),
            warnings=(),
        )

    assert project.audit(inspect_table=fake_inspect_table) == ()
