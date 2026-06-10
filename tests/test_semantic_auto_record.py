"""Tests for auto-record authoring decisions (declaration-as-decision)."""

from __future__ import annotations

from marivo.semantic import ledger as lg
from marivo.semantic.auto_record import _AUTHORING_QUALIFYING_SOURCE

DOMAIN_PY = "import marivo.semantic as ms\nms.domain(name='sales')\n"

DATASETS_PY = """
import marivo.semantic as ms
import marivo.datasource as md

warehouse = md.ref('warehouse')

orders = ms.entity(name='orders', datasource=warehouse, source=ms.table('orders'))

@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native',)
def revenue(orders):
    return orders.amount.sum()
"""

DATASETS_WITH_TIME_PY = """
import marivo.semantic as ms
import marivo.datasource as md

warehouse = md.ref('warehouse')

orders = ms.entity(name='orders', datasource=warehouse, source=ms.table('orders'))

@ms.time_dimension(entity=orders, data_type='date', granularity='day')
def order_date(orders):
    return orders.created_at.cast('date')

@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native',)
def revenue(orders):
    return orders.amount.sum()
"""

WAREHOUSE_PY = (
    "import marivo.datasource as md\n"
    "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    "md.datasource(warehouse)\n"
)


def _project_with_metric(semantic_project_factory):
    return semantic_project_factory(
        {
            "datasource/warehouse.py": WAREHOUSE_PY,
            "sales/_domain.py": DOMAIN_PY,
            "sales/datasets.py": DATASETS_PY,
        }
    )


def _project_with_metric_and_time_field(semantic_project_factory):
    return semantic_project_factory(
        {
            "datasource/warehouse.py": WAREHOUSE_PY,
            "sales/_domain.py": DOMAIN_PY,
            "sales/datasets.py": DATASETS_WITH_TIME_PY,
        }
    )


def test_auto_record_creates_metric_decomposition_decision(semantic_project_factory):
    project = _project_with_metric(semantic_project_factory)
    store = lg.LedgerStore(project.semantic_root)
    obj = store.read_object("sales.revenue")
    assert obj is not None
    decisions = [d for d in obj.decisions if d.decision_kind == "metric_decomposition"]
    assert len(decisions) == 1
    d = decisions[0]
    assert d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)
    assert d.agreement_confidence == "high"
    assert d.materiality == "high"
    assert d.question_id is None
    chosen = d.chosen
    assert isinstance(chosen, dict)
    assert chosen["kind"] == "sum"
    assert chosen["is_derived"] is False
    assert chosen["additivity"] == "additive"


def test_auto_record_creates_time_dimension_identity_decision(semantic_project_factory):
    project = _project_with_metric_and_time_field(semantic_project_factory)
    store = lg.LedgerStore(project.semantic_root)
    obj = store.read_object("sales.orders.order_date")
    assert obj is not None
    decisions = [d for d in obj.decisions if d.decision_kind == "time_dimension_identity"]
    assert len(decisions) == 1
    d = decisions[0]
    assert d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)
    assert d.agreement_confidence == "high"
    assert d.materiality == "high"
    assert d.question_id is None
    chosen = d.chosen
    assert isinstance(chosen, dict)
    assert chosen["entity"] == "sales.orders"
    assert chosen["name"] == "order_date"
    assert chosen["data_type"] == "date"
    assert chosen["granularity"] == "day"


def test_auto_record_idempotent_on_reload(semantic_project_factory):
    project = _project_with_metric(semantic_project_factory)
    store = lg.LedgerStore(project.semantic_root)
    obj_before = store.read_object("sales.revenue")
    assert obj_before is not None
    n_before = len([d for d in obj_before.decisions if d.decision_kind == "metric_decomposition"])

    project.load()

    obj_after = store.read_object("sales.revenue")
    assert obj_after is not None
    n_after = len([d for d in obj_after.decisions if d.decision_kind == "metric_decomposition"])
    assert n_after == n_before


def test_auto_record_preserves_richer_answer_decision(semantic_project_factory):
    project = _project_with_metric(semantic_project_factory)
    store = lg.LedgerStore(project.semantic_root)
    n_authoring_before = len(
        [
            d
            for d in store.read_object("sales.revenue").decisions
            if d.decision_kind == "metric_decomposition"
            and d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)
        ]
    )

    # Simulate a richer answer-based decision recorded alongside the auto-record
    richer = lg.DecisionRecord(
        decision_kind="metric_decomposition",
        chosen="sum",
        agreement_confidence="high",
        qualifying_sources=("user_confirmation",),
        materiality="high",
        blast_radius=3,
        evidence_fingerprint="sha256:answer",
        question_id="q-1",
        decided_at="2026-06-01T00:00:00+00:00",
        cited_source={"datasource": "warehouse"},
        cited_columns=("amount",),
    )
    # Write directly to LedgerStore (same pattern as auto_record internals)
    existing = store.read_object("sales.revenue")
    existing_decisions = existing.decisions if existing else ()
    store.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.revenue",
            authored_at=existing.authored_at if existing else "2026-06-01T00:00:00+00:00",
            decisions=(*existing_decisions, richer),
            rejected_candidates=existing.rejected_candidates if existing else (),
        )
    )

    project.load()

    obj = lg.LedgerStore(project.semantic_root).read_object("sales.revenue")
    assert obj is not None
    md_decisions = [d for d in obj.decisions if d.decision_kind == "metric_decomposition"]
    # The richer decision should be preserved
    assert any(d.qualifying_sources == ("user_confirmation",) for d in md_decisions)
    # Reload should not add a new authoring auto-record (the richer one supersedes it)
    n_authoring_after = len(
        [d for d in md_decisions if d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)]
    )
    assert n_authoring_after == n_authoring_before


def test_auto_record_replaces_old_authoring_on_definition_change(semantic_project_factory):
    project = _project_with_metric(semantic_project_factory)
    store = lg.LedgerStore(project.semantic_root)
    obj_first = store.read_object("sales.revenue")
    assert obj_first is not None
    fp_first = next(
        d.evidence_fingerprint
        for d in obj_first.decisions
        if d.decision_kind == "metric_decomposition"
    )

    # Change the metric definition: additivity changes
    datasets_py_changed = """
import marivo.semantic as ms
import marivo.datasource as md

warehouse = md.ref('warehouse')

orders = ms.entity(name='orders', datasource=warehouse, source=ms.table('orders'))

@ms.metric(entities=[orders], additivity='non_additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native',)
def revenue(orders):
    return orders.amount.sum()
"""
    model_dir = project.semantic_root / "sales"
    (model_dir / "datasets.py").write_text(datasets_py_changed)

    project.load()

    obj_second = store.read_object("sales.revenue")
    assert obj_second is not None
    md_decisions = [d for d in obj_second.decisions if d.decision_kind == "metric_decomposition"]
    # Only one authoring auto-record (old one replaced)
    authoring = [d for d in md_decisions if d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)]
    assert len(authoring) == 1
    # Fingerprint changed
    assert authoring[0].evidence_fingerprint != fp_first
    # chosen reflects the new additivity
    assert authoring[0].chosen["additivity"] == "non_additive"


def test_readiness_passes_after_auto_record(semantic_project_factory):
    project = _project_with_metric_and_time_field(semantic_project_factory)
    report = project.readiness()
    blockers = [
        i
        for i in report.blockers
        if i.kind == "unresolved_clarification" and i.severity == "blocker"
    ]
    # No metric_decomposition or time_dimension_identity blockers
    blocker_refs = [i.refs for i in blockers]
    assert ("sales.revenue",) not in blocker_refs
    assert ("sales.orders.order_date",) not in blocker_refs


def test_auto_record_only_records_missing_decisions(semantic_project_factory):
    project = _project_with_metric_and_time_field(semantic_project_factory)

    # Record a user-confirmed time_dimension_identity decision directly in ledger
    user_tf_decision = lg.DecisionRecord(
        decision_kind="time_dimension_identity",
        chosen="date",
        agreement_confidence="high",
        qualifying_sources=("user_confirmation",),
        materiality="high",
        blast_radius=0,
        evidence_fingerprint="sha256:user-tf",
        question_id="q-tf",
        decided_at="2026-06-01T00:00:00+00:00",
    )
    store_before = lg.LedgerStore(project.semantic_root)
    n_authoring_tf_before = len(
        [
            d
            for d in store_before.read_object("sales.orders.order_date").decisions
            if d.decision_kind == "time_dimension_identity"
            and d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)
        ]
    )
    n_authoring_md_before = len(
        [
            d
            for d in store_before.read_object("sales.revenue").decisions
            if d.decision_kind == "metric_decomposition"
            and d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)
        ]
    )

    # Write user confirmation decision directly to LedgerStore
    tf_existing = store_before.read_object("sales.orders.order_date")
    tf_existing_decisions = tf_existing.decisions if tf_existing else ()
    store_before.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.orders.order_date",
            authored_at=tf_existing.authored_at if tf_existing else "2026-06-01T00:00:00+00:00",
            decisions=(*tf_existing_decisions, user_tf_decision),
            rejected_candidates=tf_existing.rejected_candidates if tf_existing else (),
        )
    )

    project.load()

    store = lg.LedgerStore(project.semantic_root)
    # Time field: user_confirmation decision preserved; no new authoring auto-record added
    tf_obj = store.read_object("sales.orders.order_date")
    assert tf_obj is not None
    tf_decisions = [d for d in tf_obj.decisions if d.decision_kind == "time_dimension_identity"]
    assert any(d.qualifying_sources == ("user_confirmation",) for d in tf_decisions)
    n_authoring_tf_after = len(
        [d for d in tf_decisions if d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)]
    )
    assert n_authoring_tf_after == n_authoring_tf_before

    # Metric: authoring auto-record count unchanged (no new auto-record added)
    m_obj = store.read_object("sales.revenue")
    assert m_obj is not None
    md_decisions = [d for d in m_obj.decisions if d.decision_kind == "metric_decomposition"]
    n_authoring_md_after = len(
        [d for d in md_decisions if d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)]
    )
    assert n_authoring_md_after == n_authoring_md_before


def test_auto_record_writes_alongside_user_confirmation_when_no_prior_authoring(
    semantic_project_factory,
):
    """Bug 1 fix: auto_record always writes its DecisionRecord, even when
    a non-authoring decision of the same decision_kind already exists and
    no prior authoring decision is present."""
    project = _project_with_metric(semantic_project_factory)
    store = lg.LedgerStore(project.semantic_root)

    # Strip the authoring auto-record, leaving only a user_confirmation decision
    obj = store.read_object("sales.revenue")
    assert obj is not None
    user_confirmation = lg.DecisionRecord(
        decision_kind="metric_decomposition",
        chosen="Use revenue as a sum metric",
        agreement_confidence="high",
        qualifying_sources=("user_confirmation",),
        materiality="high",
        blast_radius=0,
        evidence_fingerprint="sha256:user-answer",
        question_id="q-1",
        decided_at="2026-06-01T00:00:00+00:00",
    )
    store.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.revenue",
            authored_at=obj.authored_at,
            decisions=(user_confirmation,),
            rejected_candidates=obj.rejected_candidates,
        )
    )

    project.load()

    obj = store.read_object("sales.revenue")
    assert obj is not None
    md_decisions = [d for d in obj.decisions if d.decision_kind == "metric_decomposition"]
    # Both user_confirmation and authoring_declaration should exist
    assert any(d.qualifying_sources == ("user_confirmation",) for d in md_decisions)
    assert any(d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,) for d in md_decisions)
    # Authoring decision has structured chosen (dict), not raw string
    authoring = [d for d in md_decisions if d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)]
    assert isinstance(authoring[0].chosen, dict)
    assert "kind" in authoring[0].chosen
