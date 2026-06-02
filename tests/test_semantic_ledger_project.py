# tests/test_semantic_ledger_project.py
from __future__ import annotations

import pytest

import marivo.semantic as ms
from marivo.semantic import ledger as lg

MODEL_PY = "import marivo.semantic as ms\nms.model(name='sales')\n"
DATASETS_PY = """
import marivo.semantic as ms
import marivo.datasource as md

warehouse = md.ref('warehouse')

orders = ms.dataset(name='orders', datasource=warehouse, source=ms.table('orders'))

@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native',)
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


def test_answer_rejects_none_answer(semantic_project_factory):
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

    with pytest.raises(ValueError, match=r"answer must not be None"):
        project.answer(question, None, evidence_fingerprint="sha256:a")

    store = lg.LedgerStore(project.root_path)
    assert store.read_confirmations("sales") == ()
    assert store.read_object("sales.revenue") is None


def test_answer_records_user_confirmation_decision(semantic_project_factory):
    project = _project(semantic_project_factory)
    question = ms.OpenQuestion(
        id="q-metric-decomposition",
        subject_refs=("sales.revenue",),
        decision_kind="metric_decomposition",
        gated_by=None,
        candidates=(),
        materiality="high",
        blast_radius=3,
        agreement_confidence="low",
        default_if_unanswered=None,
        severity="blocker",
        blocker_reason="high_materiality_low_confidence",
    )

    project.answer(question, "sum", evidence_fingerprint="sha256:answer")

    store = lg.LedgerStore(project.root_path)
    [confirmation] = store.read_confirmations("sales")
    obj = store.read_object("sales.revenue")
    assert obj is not None
    [decision] = obj.decisions
    assert decision.decision_kind == "metric_decomposition"
    assert decision.chosen == "sum"
    assert decision.agreement_confidence == "high"
    assert decision.qualifying_sources == ("user_confirmation",)
    assert decision.materiality == "high"
    assert decision.blast_radius == 3
    assert decision.evidence_fingerprint == "sha256:answer"
    assert decision.question_id == "q-metric-decomposition"
    assert decision.decided_at == confirmation.ts
    assert decision.cited_source is None
    assert decision.cited_columns == ()


def test_record_decision_replaces_answer_decision_for_same_question(semantic_project_factory):
    project = _project(semantic_project_factory)
    question = ms.OpenQuestion(
        id="q-metric-decomposition",
        subject_refs=("sales.revenue",),
        decision_kind="metric_decomposition",
        gated_by=None,
        candidates=(),
        materiality="high",
        blast_radius=0,
        agreement_confidence="low",
        default_if_unanswered=None,
        severity="blocker",
        blocker_reason="high_materiality_low_confidence",
    )
    project.answer(question, "sum", evidence_fingerprint="sha256:answer")
    richer = lg.DecisionRecord(
        decision_kind="metric_decomposition",
        chosen="sum",
        agreement_confidence="high",
        qualifying_sources=("source_sql",),
        materiality="high",
        blast_radius=0,
        evidence_fingerprint="sha256:richer",
        question_id="q-metric-decomposition",
        decided_at="2026-06-02T00:00:00+00:00",
        cited_source={
            "datasource": "warehouse",
            "source": {"kind": "table", "table": "orders", "database": None},
        },
        cited_columns=("amount",),
    )

    project.record_decision("sales.revenue", richer)

    obj = lg.LedgerStore(project.root_path).read_object("sales.revenue")
    assert obj is not None
    assert obj.decisions == (richer,)


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


def test_open_questions_skips_auto_recorded_decisions(semantic_project_factory):
    """Bug 2 fix: open_questions() dedupes questions whose decision_kind
    is already resolved by a DecisionRecord, even without ConfirmationRecords."""
    project = _project_loaded(semantic_project_factory)
    # After load, auto_record has written metric_decomposition for sales.revenue

    # Create a candidate that would produce a metric_decomposition question
    cand = ms.Candidate(
        object_kind="metric",
        proposed_id="sales.revenue",
        decision_kind="metric_decomposition",
        slot_values={"kind": "sum"},
        evidence=(ms.EvidenceRef("structural", "structural:sales.revenue"),),
        semantic_delta="decomposition?",
    )
    # The question should be deduped because auto-record already decided it
    questions = project.open_questions(candidates=[cand])
    assert questions == ()


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
            cited_source={
                "datasource": "warehouse",
                "source": {"kind": "table", "table": "orders", "database": "sales_mart"},
            },
            cited_columns=("status",),
        ),
    )

    # Current metadata now reports status:VARCHAR -> fingerprint changed -> stale.
    def fake_inspect_source(datasource, *, source, include_partitions=True):
        assert datasource == "warehouse"
        assert source == ms.table("orders", database="sales_mart")
        return TableMetadata(
            datasource=datasource,
            table=source.table,
            database=source.database,
            backend_type="duckdb",
            comment=None,
            columns=(ColumnMetadata("status", "VARCHAR", True, "1=paid", None),),
            partitions=(),
            warnings=(),
        )

    questions = project.audit(inspect_source=fake_inspect_source)
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
            cited_source={
                "datasource": "warehouse",
                "source": {"kind": "table", "table": "orders", "database": None},
            },
            cited_columns=("status",),
        ),
    )

    def fake_inspect_source(datasource, *, source, include_partitions=True):
        return TableMetadata(
            datasource=datasource,
            table=source.table,
            database=source.database,
            backend_type="duckdb",
            comment=None,
            columns=(ColumnMetadata("status", "INTEGER", True, "1=paid", None),),
            partitions=(),
            warnings=(),
        )

    assert project.audit(inspect_source=fake_inspect_source) == ()


def test_audit_skips_malformed_cited_source(semantic_project_factory):
    from marivo.semantic import ledger as lg

    project = _project(semantic_project_factory)
    project.record_decision(
        "sales.revenue",
        lg.DecisionRecord(
            decision_kind="metric_decomposition",
            chosen="sum",
            agreement_confidence="high",
            qualifying_sources=("source_sql",),
            materiality="high",
            blast_radius=0,
            evidence_fingerprint="sha256:a",
            question_id=None,
            decided_at="t",
            cited_source={"datasource": "warehouse"},
            cited_columns=("status",),
        ),
    )

    def fake_inspect_source(*args, **kwargs):
        raise AssertionError("malformed cited_source should not be inspected")

    assert project.audit(inspect_source=fake_inspect_source) == ()
