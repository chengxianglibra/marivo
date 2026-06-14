from __future__ import annotations

import json
from typing import cast

import pytest

from marivo.semantic import ledger as lg


def test_decision_record_round_trips_through_dict() -> None:
    rec = lg.DecisionRecord(
        decision_kind="metric_decomposition",
        chosen="sum",
        agreement_confidence="high",
        qualifying_sources=("source_sql", "comment"),
        materiality="high",
        blast_radius=7,
        evidence_fingerprint="sha256:abc",
        question_id=None,
        decided_at="2026-05-31T10:00:00+00:00",
    )
    assert lg.DecisionRecord.from_dict(rec.to_dict()) == rec


@pytest.mark.parametrize("blast_radius", [[1], (1,), "7", 1.2, True])
def test_decision_record_rejects_non_int_blast_radius(blast_radius: object) -> None:
    with pytest.raises(TypeError, match=r"DecisionRecord.blast_radius.*int"):
        lg.DecisionRecord(
            decision_kind="metric_decomposition",
            chosen="sum",
            agreement_confidence="high",
            qualifying_sources=("source_sql",),
            materiality="high",
            blast_radius=cast("int", blast_radius),
            evidence_fingerprint="sha256:abc",
            question_id=None,
            decided_at="2026-05-31T10:00:00+00:00",
        )


def test_decision_record_rejects_negative_blast_radius() -> None:
    with pytest.raises(ValueError, match=r"DecisionRecord.blast_radius.*non-negative"):
        lg.DecisionRecord(
            decision_kind="metric_decomposition",
            chosen="sum",
            agreement_confidence="high",
            qualifying_sources=("source_sql",),
            materiality="high",
            blast_radius=-1,
            evidence_fingerprint="sha256:abc",
            question_id=None,
            decided_at="2026-05-31T10:00:00+00:00",
        )


def test_decision_record_rejects_none_chosen() -> None:
    with pytest.raises(ValueError, match=r"DecisionRecord.chosen must not be None"):
        lg.DecisionRecord(
            decision_kind="metric_decomposition",
            chosen=None,
            agreement_confidence="high",
            qualifying_sources=("source_sql",),
            materiality="high",
            blast_radius=0,
            evidence_fingerprint="sha256:abc",
            question_id=None,
            decided_at="2026-05-31T10:00:00+00:00",
        )


def test_ledger_store_read_object_reports_invalid_blast_radius_path(tmp_path) -> None:
    path = tmp_path / "evidence" / "sales" / "objects" / "sales.revenue.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "semantic_id": "sales.revenue",
                "authored_at": "2026-05-31T10:00:00+00:00",
                "decisions": [
                    {
                        "decision_kind": "metric_decomposition",
                        "chosen": "sum",
                        "agreement_confidence": "high",
                        "qualifying_sources": ["source_sql"],
                        "materiality": "high",
                        "blast_radius": [1],
                        "evidence_fingerprint": "sha256:a",
                        "question_id": None,
                        "decided_at": "2026-05-31T10:00:00+00:00",
                    }
                ],
                "rejected_candidates": [],
            }
        )
        + "\n"
    )

    with pytest.raises(TypeError) as exc_info:
        lg.LedgerStore(tmp_path).read_object("sales.revenue")

    message = str(exc_info.value)
    assert str(path) in message
    assert "DecisionRecord.blast_radius" in message
    assert "list" in message


def test_rejected_candidate_round_trips_through_dict() -> None:
    rec = lg.RejectedCandidate(
        decision_kind="time_dimension_identity",
        candidate="dt",
        reason="comment: partition load date",
        evidence_fingerprint="sha256:q",
        rejected_at="2026-05-31T10:00:00+00:00",
    )
    assert lg.RejectedCandidate.from_dict(rec.to_dict()) == rec


def test_fingerprint_is_stable_and_order_independent():
    a = lg.evidence_fingerprint(
        columns={"amount": "DECIMAL", "status": "INTEGER"},
        table_comment="orders",
        column_comments={"status": "1=paid"},
    )
    b = lg.evidence_fingerprint(
        columns={"status": "INTEGER", "amount": "DECIMAL"},  # reordered
        table_comment="orders",
        column_comments={"status": "1=paid"},
    )
    assert a == b
    assert a.startswith("sha256:")


def test_fingerprint_changes_when_schema_or_comment_changes():
    base = lg.evidence_fingerprint(
        columns={"status": "INTEGER"}, table_comment=None, column_comments={"status": "1=paid"}
    )
    new_value = lg.evidence_fingerprint(
        columns={"status": "VARCHAR"}, table_comment=None, column_comments={"status": "1=paid"}
    )
    new_comment = lg.evidence_fingerprint(
        columns={"status": "INTEGER"},
        table_comment=None,
        column_comments={"status": "1=paid,2=refunded"},
    )
    assert base != new_value
    assert base != new_comment


def test_ledger_store_writes_and_reads_object_record(tmp_path):
    store = lg.LedgerStore(tmp_path)
    obj = lg.ObjectEvidence(
        semantic_id="sales.revenue",
        authored_at="2026-05-31T10:00:00+00:00",
        decisions=(
            lg.DecisionRecord(
                decision_kind="metric_decomposition",
                chosen="sum",
                agreement_confidence="high",
                qualifying_sources=("source_sql",),
                materiality="high",
                blast_radius=3,
                evidence_fingerprint="sha256:a",
                question_id=None,
                decided_at="2026-05-31T10:00:00+00:00",
            ),
        ),
        rejected_candidates=(),
    )
    store.write_object(obj)

    # file lands under the evidence/<model>/objects dir
    path = tmp_path / "evidence" / "sales" / "objects" / "sales.revenue.json"
    assert path.exists()

    loaded = store.read_object("sales.revenue")
    assert loaded == obj


def test_ledger_store_read_missing_object_is_none(tmp_path):
    assert lg.LedgerStore(tmp_path).read_object("sales.nope") is None


def test_ledger_types_exported():
    import marivo.semantic as ms

    assert "DecisionRecord" not in ms.__all__
    assert "RejectedCandidate" not in ms.__all__


def test_decision_record_persists_fingerprint_inputs():
    rec = lg.DecisionRecord(
        decision_kind="time_dimension_identity",
        chosen="paid_at",
        agreement_confidence="high",
        qualifying_sources=("comment",),
        materiality="high",
        blast_radius=4,
        evidence_fingerprint="sha256:a",
        question_id=None,
        decided_at="2026-05-31T10:00:00+00:00",
        cited_source={
            "datasource": "warehouse",
            "source": {"kind": "table", "table": "orders", "database": None},
        },
        cited_columns=("paid_at",),
    )
    restored = lg.DecisionRecord.from_dict(rec.to_dict())
    assert restored == rec
    assert restored.cited_source == {
        "datasource": "warehouse",
        "source": {"kind": "table", "table": "orders", "database": None},
    }
    assert restored.cited_columns == ("paid_at",)


def test_decision_record_from_dict_defaults_when_fields_absent():
    # records written by Plan 3 (before this field existed) still load
    legacy = {
        "decision_kind": "metric_decomposition",
        "chosen": "sum",
        "agreement_confidence": "high",
        "qualifying_sources": ["source_sql"],
        "materiality": "high",
        "blast_radius": 0,
        "evidence_fingerprint": "sha256:a",
        "question_id": None,
        "decided_at": "t",
    }
    rec = lg.DecisionRecord.from_dict(legacy)
    assert rec.cited_source is None
    assert rec.cited_columns == ()


def test_iter_object_records_globs_all_models(tmp_path):
    store = lg.LedgerStore(tmp_path)
    store.write_object(lg.ObjectEvidence("sales.revenue", "t", (), ()))
    store.write_object(lg.ObjectEvidence("ops.tickets", "t", (), ()))
    ids = {obj.semantic_id for obj in store.iter_object_records()}
    assert ids == {"sales.revenue", "ops.tickets"}


def test_iter_object_records_empty_when_no_ledger(tmp_path):
    assert lg.LedgerStore(tmp_path).iter_object_records() == ()


_DEFAULT_CITED_SOURCE = object()


def _decision(
    fp,
    *,
    cited_source=_DEFAULT_CITED_SOURCE,
    cited_columns=("status",),
):
    if cited_source is _DEFAULT_CITED_SOURCE:
        cited_source = {
            "datasource": "warehouse",
            "source": {"kind": "table", "table": "orders", "database": None},
        }
    return lg.DecisionRecord(
        decision_kind="dimension_meaning",
        chosen="paid",
        agreement_confidence="high",
        qualifying_sources=("comment",),
        materiality="high",
        blast_radius=0,
        evidence_fingerprint=fp,
        question_id=None,
        decided_at="t",
        cited_source=cited_source,
        cited_columns=cited_columns,
    )


def _metadata(status_type="INTEGER", status_comment="1=paid"):
    from marivo.datasource.metadata import ColumnMetadata, TableMetadata

    return TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(ColumnMetadata("status", status_type, True, status_comment, None),),
        partitions=(),
        warnings=(),
    )


def test_decision_not_stale_when_evidence_unchanged():
    md = _metadata()
    fp = lg.evidence_fingerprint({"status": "INTEGER"}, None, {"status": "1=paid"})
    assert lg.is_decision_stale(_decision(fp), md) is False


def test_decision_stale_when_type_changes():
    fp = lg.evidence_fingerprint({"status": "INTEGER"}, None, {"status": "1=paid"})
    assert lg.is_decision_stale(_decision(fp), _metadata(status_type="VARCHAR")) is True


def test_decision_stale_when_comment_changes():
    fp = lg.evidence_fingerprint({"status": "INTEGER"}, None, {"status": "1=paid"})
    assert lg.is_decision_stale(_decision(fp), _metadata(status_comment="1=paid,2=refund")) is True


def test_decision_without_cited_source_is_never_stale():
    assert lg.is_decision_stale(_decision("sha256:x", cited_source=None), _metadata()) is False
