# tests/test_semantic_proposal.py
from __future__ import annotations

import pytest

import marivo.semantic as ms
from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
from marivo.semantic import proposal
from marivo.semantic.proposal import ProposalResult, ResidualColumn


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
    assert ds[0].slot_values == {
        "datasource": "warehouse",
        "table": "orders",
        "database": None,
        "source": {"kind": "table", "table": "orders", "database": None},
    }
    # comment present -> a comment EvidenceRef is attached alongside metadata
    kinds = {e.evidence_type for e in ds[0].evidence}
    assert kinds == {"metadata", "comment"}


def test_view_definition_attached_as_dataset_evidence():
    md = TableMetadata(
        datasource="warehouse",
        table="v_orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(ColumnMetadata("order_id", "BIGINT", False, None, None),),
        partitions=(),
        warnings=(),
        is_view=True,
        view_definition="SELECT order_id FROM orders",
    )
    cands = proposal.candidates_from_metadata(md, model="sales")
    ds = next(c for c in cands if c.decision_kind == "dataset_identity")
    by_type = {e.evidence_type: e for e in ds.evidence}
    assert "view_definition" in by_type
    assert by_type["view_definition"].excerpt == "SELECT order_id FROM orders"


def test_base_table_has_no_view_definition_evidence():
    md = _md([ColumnMetadata("id", "BIGINT", False, None, None)])
    ds = next(
        c
        for c in proposal.candidates_from_metadata(md, model="sales")
        if c.decision_kind == "dataset_identity"
    )
    assert "view_definition" not in {e.evidence_type for e in ds.evidence}


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
    assert tf == {"sales.orders.created_at", "sales.orders.dt"}


def test_enum_named_column_proposes_field_candidate():
    md = _md(
        [
            ColumnMetadata("pay_status", "INTEGER", True, "1=paid", None),
            ColumnMetadata("amount", "DECIMAL", True, None, None),
        ]
    )
    cands = proposal.candidates_from_metadata(md, model="sales")
    fields = [c for c in cands if c.decision_kind == "field_meaning"]
    assert [c.proposed_id for c in fields] == ["sales.orders.pay_status"]
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


def test_residual_column_is_frozen_and_fields_correct():
    rc = ResidualColumn(
        dataset="sales.orders",
        column="order_id",
        data_type="INTEGER",
        nullable=False,
        comment="Primary order id",
    )
    assert rc.dataset == "sales.orders"
    assert rc.column == "order_id"
    assert rc.data_type == "INTEGER"
    assert rc.nullable is False
    assert rc.comment == "Primary order id"
    with pytest.raises(AttributeError):
        rc.column = "changed"


def test_proposal_result_is_frozen_and_fields_correct():
    cand = ms.Candidate(
        object_kind="dataset",
        proposed_id="sales.orders",
        decision_kind="dataset_identity",
        slot_values={},
        evidence=(),
        semantic_delta="d",
    )
    rc = ResidualColumn(
        dataset="sales.orders",
        column="order_id",
        data_type="INTEGER",
        nullable=False,
        comment=None,
    )
    pr = ProposalResult(candidates=(cand,), residual_columns=(rc,))
    assert pr.candidates == (cand,)
    assert pr.residual_columns == (rc,)
    with pytest.raises(AttributeError):
        pr.candidates = ()


def test_residual_columns_covers_only_time_field_and_field_cited():
    md = _md(
        [
            ColumnMetadata("order_id", "INTEGER", False, "Primary key", None),
            ColumnMetadata("created_at", "TIMESTAMP", True, None, None),  # temporal by type
            ColumnMetadata("pay_status", "INTEGER", True, "1=paid", None),  # enum by name
            ColumnMetadata("amount", "DECIMAL", True, None, None),  # neither
        ]
    )
    cands = proposal.candidates_from_metadata(md, model="sales")
    residuals = proposal.residual_columns(md, cands, model="sales")
    residual_names = {rc.column for rc in residuals}
    # covered = created_at (time_field) + pay_status (field); order_id and amount are residual
    assert "order_id" in residual_names
    assert "amount" in residual_names
    assert "created_at" not in residual_names
    assert "pay_status" not in residual_names


def test_residual_columns_carries_correct_fields():
    md = _md(
        [
            ColumnMetadata("order_id", "INTEGER", False, "Primary key", None),
            ColumnMetadata("created_at", "TIMESTAMP", True, None, None),
            ColumnMetadata("amount", "DOUBLE", True, "Gross amount", None),
        ]
    )
    cands = proposal.candidates_from_metadata(md, model="sales")
    residuals = proposal.residual_columns(md, cands, model="sales")
    order_id_rc = next(rc for rc in residuals if rc.column == "order_id")
    assert order_id_rc.dataset == "sales.orders"
    assert order_id_rc.data_type == "INTEGER"
    assert order_id_rc.nullable is False
    assert order_id_rc.comment == "Primary key"
    amount_rc = next(rc for rc in residuals if rc.column == "amount")
    assert amount_rc.dataset == "sales.orders"
    assert amount_rc.data_type == "DOUBLE"
    assert amount_rc.nullable is True
    assert amount_rc.comment == "Gross amount"


def test_residual_columns_preserves_source_column_order():
    md = _md(
        [
            ColumnMetadata("z_col", "INTEGER", True, None, None),
            ColumnMetadata("a_col", "INTEGER", True, None, None),
            ColumnMetadata("created_at", "TIMESTAMP", True, None, None),  # covered
        ]
    )
    cands = proposal.candidates_from_metadata(md, model="sales")
    residuals = proposal.residual_columns(md, cands, model="sales")
    assert [rc.column for rc in residuals] == ["z_col", "a_col"]


def test_residual_columns_with_source_uses_source_name_for_dataset():
    from marivo.semantic.ir import TableSourceIR

    source = TableSourceIR(kind="table", table="orders", database="sales_mart")
    md = _md(
        [
            ColumnMetadata("order_id", "INTEGER", False, None, None),
            ColumnMetadata("created_at", "TIMESTAMP", True, None, None),
        ]
    )
    cands = proposal.candidates_from_metadata(md, model="sales", source=source)
    residuals = proposal.residual_columns(md, cands, model="sales", source=source)
    order_id_rc = next(rc for rc in residuals if rc.column == "order_id")
    # dataset should use source-derived name, matching the candidate's dataset slot
    assert order_id_rc.dataset == "sales.orders"


def test_residual_columns_with_relationship_join_key_still_residual():
    # join key columns (like user_id) remain residual even though they
    # appear in relationship candidates — they may warrant a dimension/field declaration
    orders = _md(
        [
            ColumnMetadata("user_id", "BIGINT", True, None, None),
            ColumnMetadata("amount", "DECIMAL", True, None, None),
        ],
        table="orders",
    )
    users = _md([ColumnMetadata("id", "BIGINT", False, None, None)], table="users")
    cands = proposal.relationship_candidates([orders, users], model="sales")
    # relationship candidates exist, but user_id is still residual
    residuals = proposal.residual_columns(orders, cands, model="sales")
    assert "user_id" in {rc.column for rc in residuals}
