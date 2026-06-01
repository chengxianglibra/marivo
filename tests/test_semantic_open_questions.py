# tests/test_semantic_open_questions.py
from __future__ import annotations

import pytest

import marivo.semantic as ms

DATASETS_PY = """
import marivo.semantic as ms
import marivo.datasource as md

warehouse = md.ref('warehouse')

@ms.dataset(name='orders', datasource=warehouse)
def orders(backend):
    return backend.table('orders')

@ms.field(dataset=orders)
def region(orders):
    return orders.region.upper()

@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')
def revenue(orders):
    return orders.amount.sum()
"""

MODEL_PY = "import marivo.semantic as ms\nms.model(name='sales')\n"


WAREHOUSE_PY = (
    "import marivo.datasource as md\n"
    "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    "md.datasource(warehouse)\n"
)


def _project(semantic_project_factory):
    return semantic_project_factory(
        {
            "datasource/warehouse.py": WAREHOUSE_PY,
            "sales/_model.py": MODEL_PY,
            "sales/datasets.py": DATASETS_PY,
        }
    )


def test_blast_radius_counts_transitive_dependents(semantic_project_factory):
    project = _project(semantic_project_factory)
    # orders has two dependents: field sales.region and metric sales.revenue
    assert project._blast_radius_of(("sales.orders",)) == 2


def test_blast_radius_of_unknown_ref_is_zero(semantic_project_factory):
    project = _project(semantic_project_factory)
    # a not-yet-declared candidate dataset has no dependents
    assert project._blast_radius_of(("sales.not_declared_yet",)) == 0


def test_open_questions_dangerous_low_confidence_is_blocker(semantic_project_factory):
    project = _project(semantic_project_factory)
    cand = ms.Candidate(
        object_kind="dataset",
        proposed_id="sales.orders",
        decision_kind="amount_unit",
        slot_values={"column": "amount"},
        evidence=(ms.EvidenceRef("metadata", "metadata:warehouse.orders.amount"),),
        semantic_delta="unit?",
    )
    # no enrichment -> conservative low verdict; amount_unit floors to high -> blocker
    questions = project.open_questions(candidates=[cand])
    assert len(questions) == 1
    assert questions[0].severity == "blocker"
    assert questions[0].decision_kind == "amount_unit"
    # sales.orders has two dependents (sales.region, sales.revenue)
    assert questions[0].blast_radius == 2


def test_open_questions_high_confidence_auto_decides(semantic_project_factory):
    project = _project(semantic_project_factory)
    cand = ms.Candidate(
        object_kind="dataset",
        proposed_id="sales.orders",
        decision_kind="dataset_identity",
        slot_values={"datasource": "warehouse", "table": "orders"},
        evidence=(
            ms.EvidenceRef("metadata", "metadata:warehouse.orders"),
            ms.EvidenceRef("comment", "comment:orders"),
        ),
        semantic_delta="declare orders",
    )
    enr = ms.Enrichment(
        decision_kind="dataset_identity",
        subject_ref="sales.orders",
        materiality="low",
        agreement_confidence="high",
    )
    questions = project.open_questions(candidates=[cand], enrichments=[enr])
    assert questions[0].severity == "optional"
    assert questions[0].agreement_confidence == "high"


def test_open_questions_round_index_requires_gated_by(semantic_project_factory):

    project = _project(semantic_project_factory)
    cand = ms.Candidate(
        object_kind="dataset",
        proposed_id="sales.orders",
        decision_kind="dataset_identity",
        slot_values={},
        evidence=(),
        semantic_delta="d",
    )
    with pytest.raises(ValueError, match="gated_by"):
        project.open_questions(candidates=[cand], round_index=1)


def test_propose_candidates_calls_inspect_table_and_builds_candidates(
    semantic_project_factory,
):
    from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata

    project = _project(semantic_project_factory)

    def fake_inspect_table(datasource, *, table, database=None, include_partitions=True):
        return TableMetadata(
            datasource=datasource,
            table=table,
            database=None,
            backend_type="duckdb",
            comment="orders fact",
            columns=(
                ColumnMetadata("user_id", "BIGINT", True, None, None),
                ColumnMetadata("created_at", "TIMESTAMP", True, None, None),
                ColumnMetadata("pay_status", "INTEGER", True, "1=paid", None),
            ),
            partitions=(),
            warnings=(),
        )

    cands = project.propose_candidates(
        datasource="warehouse",
        tables=["orders"],
        model="sales",
        inspect_table=fake_inspect_table,
    )
    by_kind = {c.decision_kind for c in cands}
    assert "dataset_identity" in by_kind  # the orders dataset
    assert "time_field_identity" in by_kind  # created_at
    assert "field_meaning" in by_kind  # pay_status
    proposed_ids = {c.proposed_id for c in cands}
    assert "sales.orders" in proposed_ids
    assert "sales.created_at" in proposed_ids


def test_propose_candidates_includes_relationships_across_tables(
    semantic_project_factory,
):
    from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata

    project = _project(semantic_project_factory)

    tables = {
        "orders": (ColumnMetadata("user_id", "BIGINT", True, None, None),),
        "users": (ColumnMetadata("id", "BIGINT", False, None, None),),
    }

    def fake_inspect_table(datasource, *, table, database=None, include_partitions=True):
        return TableMetadata(
            datasource=datasource,
            table=table,
            database=None,
            backend_type="duckdb",
            comment=None,
            columns=tables[table],
            partitions=(),
            warnings=(),
        )

    cands = project.propose_candidates(
        datasource="warehouse",
        tables=["orders", "users"],
        model="sales",
        inspect_table=fake_inspect_table,
    )
    rels = [c for c in cands if c.decision_kind == "relationship_join_keys"]
    assert len(rels) == 1
    assert rels[0].proposed_id == "sales.orders_to_users"
