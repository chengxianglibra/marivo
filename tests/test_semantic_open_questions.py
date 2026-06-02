# tests/test_semantic_open_questions.py
from __future__ import annotations

import pytest

import marivo.semantic as ms

DATASETS_PY = """
import marivo.semantic as ms
import marivo.datasource as md

warehouse = md.ref('warehouse')

orders = ms.dataset(name='orders', datasource=warehouse, source=ms.table('orders'))

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
    assert project.blast_radius_of(("sales.orders",)) == 2


def test_blast_radius_of_unknown_ref_is_zero(semantic_project_factory):
    project = _project(semantic_project_factory)
    # a not-yet-declared candidate dataset has no dependents
    assert project.blast_radius_of(("sales.not_declared_yet",)) == 0


def test_backfill_blast_radii_corrects_cold_start_zeros(semantic_project_factory):
    from marivo.semantic.ledger import DecisionRecord, LedgerStore

    project = _project(semantic_project_factory)
    # Record a decision with blast_radius=0 (cold-start artifact)
    project.record_decision(
        "sales.orders",
        DecisionRecord(
            decision_kind="dataset_identity",
            chosen={"name": "orders"},
            agreement_confidence="low",
            qualifying_sources=("metadata",),
            materiality="low",
            blast_radius=0,
            evidence_fingerprint="sha256:cold_start",
            question_id=None,
            decided_at="t0",
        ),
    )

    # Reload triggers backfill
    project.reload()

    store = LedgerStore(project.root_path)
    obj = store.read_object("sales.orders")
    assert obj is not None
    cold_start_record = [d for d in obj.decisions if d.evidence_fingerprint == "sha256:cold_start"]
    assert len(cold_start_record) == 1
    # Backfill replaced 0 with the real transitive-dependent count (2)
    assert cold_start_record[0].blast_radius == 2


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


def test_open_questions_uses_zero_blast_radius_when_registry_is_unavailable(tmp_path):
    root = tmp_path / ".marivo" / "semantic"
    (root / "sales").mkdir(parents=True)
    project = ms.SemanticProject(root=root)

    result = project.load()
    assert result.status == "errored"
    assert [error.kind for error in result.errors] == ["model_file_missing"]

    cand = ms.Candidate(
        object_kind="dataset",
        proposed_id="sales.orders",
        decision_kind="amount_unit",
        slot_values={"column": "amount"},
        evidence=(ms.EvidenceRef("metadata", "metadata:warehouse.orders.amount"),),
        semantic_delta="unit?",
    )

    questions = project.open_questions(candidates=[cand])

    assert len(questions) == 1
    assert questions[0].severity == "blocker"
    assert questions[0].decision_kind == "amount_unit"
    assert questions[0].blast_radius == 0


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


def test_propose_candidates_calls_inspect_source_and_builds_candidates(
    semantic_project_factory,
):
    from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata

    project = _project(semantic_project_factory)

    def fake_inspect_source(datasource, *, source, include_partitions=True):
        assert source == ms.table("orders", database="sales_mart")
        return TableMetadata(
            datasource=datasource,
            table=source.table,
            database=source.database,
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
        sources=[ms.table("orders", database="sales_mart")],
        model="sales",
        inspect_source=fake_inspect_source,
    )
    by_kind = {c.decision_kind for c in cands}
    assert "dataset_identity" in by_kind  # the orders dataset
    assert "time_field_identity" in by_kind  # created_at
    assert "field_meaning" in by_kind  # pay_status
    proposed_ids = {c.proposed_id for c in cands}
    assert "sales.orders" in proposed_ids
    assert "sales.created_at" in proposed_ids
    [dataset_candidate] = [c for c in cands if c.decision_kind == "dataset_identity"]
    assert dataset_candidate.slot_values["database"] == "sales_mart"
    assert dataset_candidate.slot_values["source"] == {
        "kind": "table",
        "table": "orders",
        "database": "sales_mart",
    }
    assert dataset_candidate.evidence[0].locator == "metadata:warehouse.sales_mart.orders"


def test_propose_candidates_includes_relationships_across_tables(
    semantic_project_factory,
):
    from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata

    project = _project(semantic_project_factory)

    tables = {
        "orders": (ColumnMetadata("user_id", "BIGINT", True, None, None),),
        "users": (ColumnMetadata("id", "BIGINT", False, None, None),),
    }

    def fake_inspect_source(datasource, *, source, include_partitions=True):
        return TableMetadata(
            datasource=datasource,
            table=source.table,
            database=source.database,
            backend_type="duckdb",
            comment=None,
            columns=tables[source.table],
            partitions=(),
            warnings=(),
        )

    cands = project.propose_candidates(
        datasource="warehouse",
        sources=[ms.table("orders"), ms.table("users")],
        model="sales",
        inspect_source=fake_inspect_source,
    )
    rels = [c for c in cands if c.decision_kind == "relationship_join_keys"]
    assert len(rels) == 1
    assert rels[0].proposed_id == "sales.orders_to_users"


def test_propose_candidates_preserves_file_source_in_dataset_slot(
    semantic_project_factory,
):
    from marivo.analysis.datasources.metadata import TableMetadata

    project = _project(semantic_project_factory)
    source = ms.file("/data/orders/*.parquet", format="parquet", hive_partitioning=True)

    def fake_inspect_source(datasource, *, source, include_partitions=True):
        return TableMetadata(
            datasource=datasource,
            table="orders_file",
            database=None,
            backend_type="duckdb",
            comment=None,
            columns=(),
            partitions=(),
            warnings=(),
        )

    cands = project.propose_candidates(
        datasource="warehouse",
        sources=[source],
        model="sales",
        inspect_source=fake_inspect_source,
    )
    [dataset_candidate] = [c for c in cands if c.decision_kind == "dataset_identity"]
    assert dataset_candidate.slot_values["source"] == {
        "kind": "file",
        "path": "/data/orders/*.parquet",
        "format": "parquet",
        "options": {"hive_partitioning": True},
    }


def test_propose_candidates_derives_valid_dataset_id_for_file_source_path(
    semantic_project_factory,
):
    from marivo.analysis.datasources.metadata import TableMetadata

    project = _project(semantic_project_factory)
    source = ms.file("/data/orders/*.parquet", format="parquet")

    def fake_inspect_source(datasource, *, source, include_partitions=True):
        return TableMetadata(
            datasource=datasource,
            table=source.path,
            database=None,
            backend_type="duckdb",
            comment=None,
            columns=(),
            partitions=(),
            warnings=(),
        )

    cands = project.propose_candidates(
        datasource="warehouse",
        sources=[source],
        model="sales",
        inspect_source=fake_inspect_source,
    )

    [dataset_candidate] = [c for c in cands if c.decision_kind == "dataset_identity"]
    assert dataset_candidate.proposed_id == "sales.orders"
