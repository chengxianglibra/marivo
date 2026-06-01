# tests/test_semantic_proposal.py
from __future__ import annotations

from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
from marivo.semantic import proposal


def _md(columns, *, table="orders", comment=None):
    return TableMetadata(
        datasource="warehouse",
        table=table,
        database=None,
        backend_type="duckdb",
        comment=comment,
        columns=tuple(columns),
        partitions=(),
        warnings=(),
    )


def test_dataset_candidate_is_proposed_with_metadata_evidence():
    md = _md([ColumnMetadata("id", "BIGINT", False, None, None)], comment="orders fact")
    cands = proposal.candidates_from_metadata(md, model="sales")
    ds = [c for c in cands if c.decision_kind == "dataset_identity"]
    assert len(ds) == 1
    assert ds[0].object_kind == "dataset"
    assert ds[0].proposed_id == "sales.orders"
    assert ds[0].slot_values == {"datasource": "warehouse", "table": "orders"}
    # comment present -> a comment EvidenceRef is attached alongside metadata
    kinds = {e.evidence_type for e in ds[0].evidence}
    assert kinds == {"metadata", "comment"}


def test_temporal_column_proposes_time_field_candidate():
    md = _md(
        [
            ColumnMetadata("created_at", "TIMESTAMP", True, None, None),  # by type
            ColumnMetadata("dt", "VARCHAR", True, None, None),  # by name
            ColumnMetadata("amount", "DECIMAL", True, None, None),  # neither
        ]
    )
    cands = proposal.candidates_from_metadata(md, model="sales")
    tf = {c.proposed_id for c in cands if c.decision_kind == "time_field_identity"}
    assert tf == {"sales.created_at", "sales.dt"}


def test_enum_named_column_proposes_field_candidate():
    md = _md(
        [
            ColumnMetadata("pay_status", "INTEGER", True, "1=paid", None),
            ColumnMetadata("amount", "DECIMAL", True, None, None),
        ]
    )
    cands = proposal.candidates_from_metadata(md, model="sales")
    fields = [c for c in cands if c.decision_kind == "field_meaning"]
    assert [c.proposed_id for c in fields] == ["sales.pay_status"]
    assert {e.evidence_type for e in fields[0].evidence} == {"metadata", "comment"}


def test_relationship_candidate_on_foreign_key_name_match():
    orders = _md(
        [
            ColumnMetadata("user_id", "BIGINT", True, None, None),
            ColumnMetadata("amount", "DECIMAL", True, None, None),
        ],
        table="orders",
    )
    users = _md([ColumnMetadata("id", "BIGINT", False, None, None)], table="users")
    cands = proposal.relationship_candidates([orders, users], model="sales")
    rels = [c for c in cands if c.decision_kind == "relationship_join_keys"]
    assert len(rels) == 1
    assert rels[0].object_kind == "relationship"
    assert rels[0].slot_values == {
        "from_dataset": "sales.orders",
        "to_dataset": "sales.users",
        "from_column": "user_id",
        "to_column": "id",
    }
    assert {e.evidence_type for e in rels[0].evidence} == {"structural"}


def test_no_relationship_when_no_key_name_match():
    a = _md([ColumnMetadata("amount", "DECIMAL", True, None, None)], table="orders")
    b = _md([ColumnMetadata("id", "BIGINT", False, None, None)], table="users")
    assert proposal.relationship_candidates([a, b], model="sales") == ()


def test_detect_structural_conflict_flags_missing_column():
    md = _md([ColumnMetadata("created_at", "TIMESTAMP", True, None, None)])
    # chosen a column that does not exist in the schema -> conflict
    assert proposal.detect_structural_conflict({"column": "paid_at"}, md) is True
    # chosen an existing column -> no conflict
    assert proposal.detect_structural_conflict({"column": "created_at"}, md) is False


def test_detect_structural_conflict_checks_from_column_only():
    md = _md([ColumnMetadata("user_id", "BIGINT", True, None, None)], table="orders")
    # from_column exists -> no conflict (to_column belongs to another table, not checked here)
    assert (
        proposal.detect_structural_conflict({"from_column": "user_id", "to_column": "id"}, md)
        is False
    )
    assert proposal.detect_structural_conflict({"from_column": "missing"}, md) is True


def test_detect_structural_conflict_ignores_non_column_slots():
    md = _md([ColumnMetadata("id", "BIGINT", False, None, None)])
    assert (
        proposal.detect_structural_conflict({"datasource": "warehouse", "table": "orders"}, md)
        is False
    )
