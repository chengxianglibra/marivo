from __future__ import annotations

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


def test_confirmation_record_round_trips_through_dict() -> None:
    rec = lg.ConfirmationRecord(
        ts="2026-05-31T10:00:00+00:00",
        question_id="deadbeefdeadbeef",
        decision_kind="amount_unit",
        subject_refs=("sales.revenue",),
        answer="cents",
        evidence_fingerprint="sha256:xyz",
    )
    assert lg.ConfirmationRecord.from_dict(rec.to_dict()) == rec


def test_rejected_candidate_round_trips_through_dict() -> None:
    rec = lg.RejectedCandidate(
        decision_kind="time_field_identity",
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

    # file lands under the model's _evidence/objects dir
    path = tmp_path / "sales" / "_evidence" / "objects" / "sales.revenue.json"
    assert path.exists()

    loaded = store.read_object("sales.revenue")
    assert loaded == obj


def test_ledger_store_read_missing_object_is_none(tmp_path):
    assert lg.LedgerStore(tmp_path).read_object("sales.nope") is None


def test_confirmation_log_appends_and_reads_in_order(tmp_path):
    store = lg.LedgerStore(tmp_path)
    first = lg.ConfirmationRecord(
        ts="2026-05-31T10:00:00+00:00",
        question_id="q1",
        decision_kind="amount_unit",
        subject_refs=("sales.revenue",),
        answer="cents",
        evidence_fingerprint="sha256:a",
    )
    second = lg.ConfirmationRecord(
        ts="2026-05-31T11:00:00+00:00",
        question_id="q2",
        decision_kind="time_field_identity",
        subject_refs=("sales.order_date",),
        answer="paid_at",
        evidence_fingerprint="sha256:b",
    )
    store.append_confirmation(first)
    store.append_confirmation(second)

    path = tmp_path / "sales" / "_evidence" / "confirmations.jsonl"
    assert path.exists()
    assert store.read_confirmations("sales") == (first, second)


def test_ledger_types_exported():
    import marivo.semantic as ms

    assert hasattr(ms, "DecisionRecord")
    assert hasattr(ms, "ConfirmationRecord")
    assert hasattr(ms, "RejectedCandidate")


def test_read_confirmations_missing_model_is_empty(tmp_path):
    assert lg.LedgerStore(tmp_path).read_confirmations("sales") == ()
