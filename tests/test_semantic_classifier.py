from __future__ import annotations

import pytest

from marivo.semantic import classifier as clf


def test_floor_table_covers_all_decision_kinds():
    # Every DecisionKind literal must have a floor entry.
    from typing import get_args

    kinds = set(get_args(clf.DecisionKind))
    assert set(clf._FLOOR_TABLE) == kinds


def test_dangerous_is_high_floor():
    assert clf.is_dangerous("time_dimension_identity") is True
    assert clf.is_dangerous("amount_unit") is True
    assert clf.is_dangerous("metric_decomposition") is True
    assert clf.is_dangerous("entity_identity") is False
    assert clf.is_dangerous("time_dimension_format") is False


def test_effective_materiality_is_raise_only():
    # Floor wins when it is higher; agent can raise but never lower.
    assert clf.effective_materiality("amount_unit", "low") == "high"  # floored
    assert clf.effective_materiality("entity_identity", "high") == "high"  # raised
    assert clf.effective_materiality("entity_identity", "low") == "low"
    assert clf.effective_materiality("entity_primary_key", "low") == "medium"


def test_authority_of_maps_every_evidence_type():
    from typing import get_args

    types = set(get_args(clf.EvidenceType))
    assert set(clf._AUTHORITY_OF) == types
    assert set(clf._AUTHORITY_WEIGHT) == types


def test_candidate_confidence_saturates_at_one():
    # source_sql (3.0) + comment (2.0) = 5.0 / 4.0 saturation -> clamp to 1.0
    assert clf.candidate_confidence(["source_sql", "comment"]) == 1.0
    # single comment (2.0) / 4.0 = 0.5
    assert clf.candidate_confidence(["comment"]) == 0.5
    assert clf.candidate_confidence(["view_definition"]) == 0.125
    assert clf.candidate_confidence(["metadata", "view_definition"]) == 0.5
    assert clf.candidate_confidence([]) == 0.0


def test_qualifying_source_count_excludes_candidate_only_and_dedups_type():
    # two comments = one type; structural is candidate_only and never counts
    assert clf.qualifying_source_count(["comment", "comment", "structural"]) == 1
    assert clf.qualifying_source_count(["comment", "sample"]) == 2
    assert clf.qualifying_source_count(["structural"]) == 0
    assert clf.qualifying_source_count(["metadata", "view_definition"]) == 1


def test_effective_agreement_confidence_floor():
    # high verdict needs >= 2 qualifying sources, else downgraded
    assert clf.effective_agreement_confidence("high", 2) == "high"
    assert clf.effective_agreement_confidence("high", 1) == "low"
    assert clf.effective_agreement_confidence("low", 5) == "low"


def _evref(t: str, locator: str) -> clf.EvidenceRef:
    return clf.EvidenceRef(evidence_type=t, locator=locator)


def test_evidence_ref_authority_is_derived():
    assert _evref("comment", "comment:orders.status").authority == "establishes"
    assert _evref("sample", "sample:orders.status").authority == "validates"
    assert _evref("structural", "fact_shape:orders").authority == "candidate_only"
    assert _evref("view_definition", "view_definition:warehouse.v_orders").authority == (
        "candidate_only"
    )


def test_candidate_confidence_property():
    cand = clf.Candidate(
        object_kind="metric",
        proposed_id="sales.revenue",
        decision_kind="metric_decomposition",
        slot_values={"decomposition": "sum"},
        evidence=(
            _evref("source_sql", "sql:kb://sales/revenue"),
            _evref("comment", "comment:orders.amount"),
        ),
        semantic_delta="sum over amount",
    )
    assert cand.candidate_confidence == 1.0


def test_question_id_is_stable_and_order_independent():
    a = clf.question_id("amount_unit", ["sales.revenue", "sales.orders"], "fp1")
    b = clf.question_id("amount_unit", ["sales.orders", "sales.revenue"], "fp1")
    c = clf.question_id("amount_unit", ["sales.orders", "sales.revenue"], "fp2")
    assert a == b  # subject ref order does not matter
    assert a != c  # different evidence fingerprint -> different id
    assert len(a) == 16


def _di(
    kind: clf.DecisionKind,
    *,
    mat: clf.Materiality = "low",
    verdict: clf.AgreementConfidence = "low",
    conflict: bool = False,
    evidence: tuple[clf.EvidenceRef, ...] = (),
) -> clf.DecisionInput:
    cand = clf.Candidate(
        object_kind="metric",
        proposed_id="sales.x",
        decision_kind=kind,
        slot_values={"v": 1},
        evidence=tuple(evidence),
        semantic_delta="d",
    )
    return clf.DecisionInput(
        decision_kind=kind,
        subject_refs=("sales.x",),
        candidates=(cand,),
        agent_materiality=mat,
        agent_verdict=verdict,
        conflict=conflict,
    )


def test_conflict_is_top_priority_blocker():
    q = clf._classify_one(_di("entity_identity", mat="low", verdict="high", conflict=True))
    assert q == ("blocker", "conflict", None)


def test_dangerous_low_confidence_is_blocker_with_no_default():
    # amount_unit floors to high; low verdict -> low confidence -> blocker, default None
    q = clf._classify_one(_di("amount_unit", mat="low", verdict="low"))
    assert q == ("blocker", "high_materiality_low_confidence", None)


def test_non_dangerous_low_confidence_is_assumption_with_default():
    di = _di("entity_identity", mat="low", verdict="low")
    severity, reason, default = clf._classify_one(di)
    assert severity == "optional"
    assert reason is None
    assert default == {"v": 1}  # top candidate slot_values


def test_high_confidence_auto_decides_with_no_default():
    di = _di(
        "entity_identity",
        mat="low",
        verdict="high",
        evidence=(clf.EvidenceRef("comment", "c"), clf.EvidenceRef("sample", "s")),
    )
    # NOTE: _classify_one receives the already-effective confidence; see classify().
    severity, reason, default = clf._classify_one(di, agreement="high")
    assert severity == "optional"
    assert reason is None
    assert default is None


def test_classify_dedups_by_id_and_ranks_blockers_first():
    danger = _di("amount_unit", mat="low", verdict="low")  # blocker
    safe = _di("entity_identity", mat="low", verdict="low")  # optional, low blast
    # duplicate of danger (same kind + subject + evidence) must coalesce to one
    out = clf.classify([safe, danger, danger], blast_radius_of=lambda refs: 3)
    assert len(out) == 2  # duplicate danger coalesced
    assert out[0].severity == "blocker"  # blockers rank first
    assert out[0].decision_kind == "amount_unit"
    assert out[1].severity == "optional"
    assert all(q.blast_radius == 3 for q in out)


def test_classify_ranks_optionals_by_materiality_times_blast_radius():
    # both optional, both low confidence; metric_additivity floors to medium (rank 2),
    # dataset_identity stays low (rank 1). Higher materiality*blast ranks first.
    a = _di("metric_additivity", mat="low", verdict="low")  # eff materiality medium
    b = _di("entity_identity", mat="low", verdict="low")  # eff materiality low
    out = clf.classify([b, a], blast_radius_of=lambda refs: 10)
    assert [q.decision_kind for q in out] == ["metric_additivity", "entity_identity"]


def test_classify_round_index_requires_gated_by():
    di = _di("entity_identity")  # gated_by is None
    with pytest.raises(ValueError, match="gated_by"):
        clf.classify([di], blast_radius_of=lambda refs: 0, round_index=1)


def test_select_for_user_splits_blockers_optionals_assumptions():
    danger = _di("amount_unit", mat="low", verdict="low")  # blocker
    opt1 = _di("entity_identity", mat="low", verdict="low")  # optional w/ default (assumption)
    opt2 = _di("metric_additivity", mat="low", verdict="low")  # optional w/ default (assumption)
    questions = clf.classify([danger, opt1, opt2], blast_radius_of=lambda refs: 2)

    blockers, optionals, assumption_count = clf.select_for_user(questions, k=1)

    assert [q.severity for q in blockers] == ["blocker"]
    assert len(optionals) == 1  # top-K optional surfaced
    assert assumption_count == 1  # the remaining optional becomes a silent assumption


def test_public_exports_available():
    import marivo.semantic as ms

    assert hasattr(ms, "DecisionKind")


def test_enrichment_defaults_are_conservative():
    e = clf.Enrichment(decision_kind="amount_unit", subject_ref="sales.revenue")
    assert e.materiality == "low"
    assert e.agreement_confidence == "low"
    assert e.chosen is None


def test_classifier_evidence_ref_exists():
    from marivo.semantic.classifier import EvidenceRef

    ref = EvidenceRef(evidence_type="comment", locator="c")
    assert ref.evidence_type == "comment"


def _cand(kind, subject, object_kind="metric", evidence=()):
    return clf.Candidate(
        object_kind=object_kind,
        proposed_id=subject,
        decision_kind=kind,
        slot_values={"k": "v"},
        evidence=tuple(evidence),
        semantic_delta="d",
    )


def test_to_decision_inputs_attaches_matching_enrichment():
    cand = _cand("amount_unit", "sales.revenue")
    enr = clf.Enrichment(
        decision_kind="amount_unit",
        subject_ref="sales.revenue",
        materiality="high",
        agreement_confidence="high",
    )
    [di] = clf.to_decision_inputs([cand], [enr])
    assert di.decision_kind == "amount_unit"
    assert di.subject_refs == ("sales.revenue",)
    assert di.candidates == (cand,)
    assert di.agent_materiality == "high"
    assert di.agent_verdict == "high"
    assert di.conflict is False


def test_to_decision_inputs_missing_enrichment_is_conservative():
    [di] = clf.to_decision_inputs([_cand("entity_identity", "sales.orders")], [])
    assert di.agent_materiality == "low"
    assert di.agent_verdict == "low"


def test_to_decision_inputs_groups_candidates_by_kind_and_subject():
    a = _cand("dimension_meaning", "sales.status")
    b = _cand("dimension_meaning", "sales.status")  # same slot -> one group
    c = _cand("entity_identity", "sales.orders")
    out = clf.to_decision_inputs([a, b, c], [])
    assert len(out) == 2
    statuses = next(di for di in out if di.decision_kind == "dimension_meaning")
    assert len(statuses.candidates) == 2


def test_to_decision_inputs_sets_conflict_from_map():
    cand = _cand("time_dimension_identity", "sales.dt")
    out = clf.to_decision_inputs(
        [cand], [], conflicts={("time_dimension_identity", "sales.dt"): True}
    )
    assert out[0].conflict is True
