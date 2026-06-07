# tests/test_semantic_ledger_project.py
from __future__ import annotations

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


def _project(semantic_project_factory):
    return semantic_project_factory({"sales/_model.py": MODEL_PY, "sales/datasets.py": DATASETS_PY})


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
    store = lg.LedgerStore(project.semantic_root)
    store.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.revenue",
            authored_at="2026-05-31T10:00:00+00:00",
            decisions=(rec,),
            rejected_candidates=(),
        )
    )

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
    store = lg.LedgerStore(project.semantic_root)
    store.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.revenue",
            authored_at="t1",
            decisions=(rec1, rec2),
            rejected_candidates=(),
        )
    )
    obj = store.read_object("sales.revenue")
    assert obj is not None
    assert obj.decisions == (rec1, rec2)


def test_decision_upsert_replaces_same_question_id(semantic_project_factory):
    project = _project(semantic_project_factory)
    first = lg.DecisionRecord(
        decision_kind="metric_decomposition",
        chosen="sum",
        agreement_confidence="high",
        qualifying_sources=("user_confirmation",),
        materiality="high",
        blast_radius=0,
        evidence_fingerprint="sha256:answer",
        question_id="q-metric-decomposition",
        decided_at="2026-06-01T00:00:00+00:00",
    )
    store = lg.LedgerStore(project.semantic_root)
    store.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.revenue",
            authored_at="2026-06-01T00:00:00+00:00",
            decisions=(first,),
            rejected_candidates=(),
        )
    )

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
    existing = store.read_object("sales.revenue")
    existing_decisions = existing.decisions if existing else ()
    replacement_key = (richer.question_id, richer.decision_kind)
    decisions = (
        *(d for d in existing_decisions if (d.question_id, d.decision_kind) != replacement_key),
        richer,
    )
    store.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.revenue",
            authored_at=existing.authored_at if existing else "2026-06-02T00:00:00+00:00",
            decisions=decisions,
            rejected_candidates=(),
        )
    )

    obj = store.read_object("sales.revenue")
    assert obj is not None
    assert obj.decisions == (richer,)
