"""Selection-wide evidence compatibility contracts."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis._evidence_compatibility import (
    _SCOPE_COMPARATOR_TYPES,
    _SUBJECT_COMPARATOR_TYPES,
    _aggregate_evidence,
    _aggregate_quality,
)
from marivo.analysis.errors import (
    EvidenceIntegrityError,
    EvidenceSelectionError,
    EvidenceStoreUnavailableError,
    FindingNotFoundError,
)
from marivo.analysis.evidence.digest import inference_boundaries_for_operator
from marivo.analysis.evidence.identity import canonical_json, make_digest_fingerprint
from marivo.analysis.evidence.types import EvidenceScope, EvidenceSubject, InferenceBoundary
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref
from tests.shared_fixtures import (
    connect_sales_orders,
    sales_backends,
)


def _bootstrap_project(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "compatibility"\n')
    datasource_dir = tmp_path / "models" / "datasources"
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    datasource_dir.mkdir(parents=True)
    semantic_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "@ms.time_dimension(entity=orders, granularity='day', is_default=True)\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "@ms.metric(entities=[orders], additivity='additive', name='order_count')\n"
        "def order_count(orders):\n"
        "    return orders.order_id.count()\n"
    )


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    _bootstrap_project(tmp_path)
    connection = connect_sales_orders()
    return mv.session.get_or_create(
        name="compatibility",
        backends=sales_backends(connection),
        use_datasources=False,
    )


def _observe(session, *, start: str, end: str, metric: str = "sales.revenue", grain=None):
    return session.observe(
        metrics=make_ref(metric, SemanticKind.METRIC),
        time_scope=mv.time_scope(start=start, end=end),
        grain=grain,
    )


def _first_finding(session, artifact_ref: str):
    return session.evidence.findings(artifact_ref=artifact_ref, limit=100).items[0]


def _annotated_union_members(value: object) -> tuple[type[object], ...]:
    return get_args(get_args(value)[0])


def test_single_canonical_finding_is_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, start="2026-07-01", end="2026-07-31")
    finding = session.evidence.findings(artifact_ref=frame.ref, limit=100).items[0]

    result = session.evidence.compatibility([finding.finding_id])

    assert isinstance(result, mv.EvidenceCompatibility)
    assert result.status == "compatible"
    assert result.subject_status == "compatible"
    assert result.scope_status == "compatible"
    assert result.semantic_status == "compatible"
    assert result.evaluated_pair_count == 0
    assert result.finding_ids == (finding.finding_id,)
    assert repr(result).startswith("<EvidenceCompatibility status=compatible")

    with pytest.raises(ValidationError):
        result.status = "incompatible"  # type: ignore[misc]
    assert "EvidenceCompatibility status=compatible" in result.render()


def test_input_order_is_normalized_and_scope_mismatch_is_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    first = _observe(session, start="2026-07-01", end="2026-07-31")
    second = _observe(session, start="2026-08-01", end="2026-08-31")
    first_finding = session.evidence.findings(artifact_ref=first.ref, limit=100).items[0]
    second_finding = session.evidence.findings(artifact_ref=second.ref, limit=100).items[0]

    forward = session.evidence.compatibility([first_finding.finding_id, second_finding.finding_id])
    reverse = session.evidence.compatibility([second_finding.finding_id, first_finding.finding_id])

    assert forward == reverse
    assert forward.status == "incompatible"
    assert forward.subject_status == "compatible"
    assert forward.scope_status == "incompatible"
    assert forward.evaluated_pair_count == 1
    assert any(
        "scope.window" in issue.detail.incompatible_fields
        for issue in forward.issues
        if isinstance(issue.detail, mv.ComparabilityIssue)
    )


def test_multiple_findings_from_one_artifact_share_canonical_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, start="2026-07-01", end="2026-07-31")
    findings = session.evidence.findings(artifact_ref=frame.ref, limit=100).items
    assert len(findings) >= 2

    result = session.evidence.compatibility([finding.finding_id for finding in findings[:2]])

    assert result.status == "compatible"
    assert result.evaluated_pair_count == 1


def test_multi_metric_finding_subjects_validate_against_shared_artifact_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = session.observe(
        metrics=(
            make_ref("sales.revenue", SemanticKind.METRIC),
            make_ref("sales.order_count", SemanticKind.METRIC),
        ),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-31"),
    )
    findings = tuple(
        finding
        for finding in session.evidence.findings(artifact_ref=frame.ref, limit=100).items
        if finding.finding_type == "metric_value"
    )
    assert len(findings) == 2

    result = session.evidence.compatibility([finding.finding_id for finding in findings])

    assert result.status == "incompatible"
    assert result.subject_status == "incompatible"


def test_subject_and_grain_mismatches_are_attributed_to_the_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    revenue = _observe(session, start="2026-07-01", end="2026-07-31")
    orders = _observe(
        session,
        start="2026-07-01",
        end="2026-07-31",
        metric="sales.order_count",
    )
    monthly = _observe(
        session,
        start="2026-07-01",
        end="2026-07-31",
        grain=mv.grain("month"),
    )
    revenue_finding = _first_finding(session, revenue.ref)
    orders_finding = _first_finding(session, orders.ref)
    monthly_finding = _first_finding(session, monthly.ref)

    subject_result = session.evidence.compatibility(
        [revenue_finding.finding_id, orders_finding.finding_id]
    )
    grain_result = session.evidence.compatibility(
        [revenue_finding.finding_id, monthly_finding.finding_id]
    )

    assert subject_result.subject_status == "incompatible"
    assert any(
        "subject.identity" in issue.detail.incompatible_fields
        for issue in subject_result.issues
        if isinstance(issue.detail, mv.ComparabilityIssue)
    )
    assert grain_result.scope_status == "incompatible"
    assert any(
        "scope.grain" in issue.detail.incompatible_fields
        for issue in grain_result.issues
        if isinstance(issue.detail, mv.ComparabilityIssue)
    )


def test_delta_comparison_direction_is_scope_sensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    july = _observe(session, start="2026-07-01", end="2026-07-31")
    august = _observe(session, start="2026-08-01", end="2026-08-31")
    forward = session.compare(august, july)
    reverse = session.compare(july, august)
    forward_finding = _first_finding(session, forward.ref)
    reverse_finding = _first_finding(session, reverse.ref)

    result = session.evidence.compatibility(
        [forward_finding.finding_id, reverse_finding.finding_id]
    )

    assert result.subject_status == "compatible"
    assert result.scope_status == "incompatible"
    assert any(
        "scope.comparison_direction" in issue.detail.incompatible_fields
        for issue in result.issues
        if isinstance(issue.detail, mv.ComparabilityIssue)
    )


@pytest.mark.parametrize(
    "finding_ids",
    (
        [],
        ["fnd_duplicate", "fnd_duplicate"],
        [f"fnd_{index}" for index in range(21)],
    ),
)
def test_invalid_selection_raises_one_structured_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    finding_ids: list[str],
) -> None:
    session = _session(tmp_path, monkeypatch)

    with pytest.raises(EvidenceSelectionError) as exc_info:
        session.evidence.compatibility(finding_ids)

    assert exc_info.value.expected
    assert exc_info.value.received
    assert exc_info.value.repair is not None


def test_complete_artifact_without_digest_fails_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, start="2026-07-01", end="2026-07-31")
    finding = session.evidence.findings(artifact_ref=frame.ref, limit=100).items[0]
    store = session._evidence_store()
    assert store is not None
    store.read().execute("PRAGMA foreign_keys = OFF")
    store.read().execute("DELETE FROM artifact_digests WHERE artifact_id = ?", (frame.ref,))

    with pytest.raises(EvidenceIntegrityError, match="failed integrity validation"):
        session.evidence.compatibility([finding.finding_id])


def test_subject_and_scope_comparator_cover_the_closed_unions() -> None:
    assert _annotated_union_members(EvidenceSubject) == _SUBJECT_COMPARATOR_TYPES
    assert _annotated_union_members(EvidenceScope) == _SCOPE_COMPARATOR_TYPES


def test_evidence_and_quality_aggregation_use_fail_closed_precedence() -> None:
    records = (
        SimpleNamespace(
            evidence_status="complete",
            finding=SimpleNamespace(quality_status=None),
        ),
        SimpleNamespace(
            evidence_status="partial",
            finding=SimpleNamespace(quality_status="needs_attention"),
        ),
        SimpleNamespace(
            evidence_status="unavailable",
            finding=SimpleNamespace(quality_status="not_ready"),
        ),
    )

    assert _aggregate_evidence(records) == "unavailable"  # type: ignore[arg-type]
    assert _aggregate_quality(records) == "not_ready"  # type: ignore[arg-type]


def test_cross_session_selection_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, start="2026-07-01", end="2026-07-31")
    finding = _first_finding(session, frame.ref)
    other = mv.session.get_or_create(name="other", use_datasources=False)

    with pytest.raises(FindingNotFoundError):
        other.evidence.compatibility([finding.finding_id])


def test_store_unavailable_fails_before_selection_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    store = session._evidence_store()
    assert store is not None
    store.close()
    session._judgment_store = None
    session._judgment_store_unavailable = True

    with pytest.raises(EvidenceStoreUnavailableError):
        session.evidence.compatibility(["fnd_unreadable"])


def test_store_read_failure_preserves_unavailable_error_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, start="2026-07-01", end="2026-07-31")
    finding = _first_finding(session, frame.ref)
    store = session._evidence_store()
    assert store is not None
    store.close()

    with pytest.raises(EvidenceStoreUnavailableError) as exc_info:
        session.evidence.compatibility([finding.finding_id])

    assert exc_info.value.received == "ProgrammingError"
    assert exc_info.value.repair is not None


def test_finding_subject_must_match_its_canonical_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    revenue = _observe(session, start="2026-07-01", end="2026-07-31")
    orders = _observe(
        session,
        start="2026-07-01",
        end="2026-07-31",
        metric="sales.order_count",
    )
    revenue_finding = _first_finding(session, revenue.ref)
    orders_finding = _first_finding(session, orders.ref)
    store = session._evidence_store()
    assert store is not None
    store.read().execute(
        "UPDATE findings SET subject_payload = ? WHERE finding_id = ?",
        (canonical_json(orders_finding.subject), revenue_finding.finding_id),
    )

    with pytest.raises(EvidenceIntegrityError, match="failed integrity validation"):
        session.evidence.compatibility([revenue_finding.finding_id])


def test_unrelated_existing_refs_do_not_satisfy_finding_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    revenue = _observe(session, start="2026-07-01", end="2026-07-31")
    orders = _observe(
        session,
        start="2026-07-01",
        end="2026-07-31",
        metric="sales.order_count",
    )
    revenue_finding = _first_finding(session, revenue.ref)
    orders_finding = _first_finding(session, orders.ref)
    derivation = revenue_finding.derivation.model_copy(
        update={"source_finding_refs": (orders_finding.finding_id,)}
    )
    store = session._evidence_store()
    assert store is not None
    store.read().execute(
        "UPDATE findings SET source_refs_payload = ?, derivation_payload = ? WHERE finding_id = ?",
        (
            canonical_json((orders.ref,)),
            canonical_json(derivation),
            revenue_finding.finding_id,
        ),
    )

    with pytest.raises(EvidenceIntegrityError, match="failed integrity validation"):
        session.evidence.compatibility([revenue_finding.finding_id])


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_artifact",
        "malformed_scope",
        "malformed_digest",
        "digest_fingerprint",
        "missing_derivation_finding",
        "missing_source_ref",
        "frame_sha",
        "sidecar_digest",
    ),
)
def test_committed_identity_corruption_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, start="2026-07-01", end="2026-07-31")
    finding = _first_finding(session, frame.ref)
    store = session._evidence_store()
    assert store is not None
    conn = store.read()
    if corruption == "missing_artifact":
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (frame.ref,))
    elif corruption == "malformed_scope":
        conn.execute(
            "UPDATE artifacts SET analysis_scope = '{}' WHERE artifact_id = ?",
            (frame.ref,),
        )
    elif corruption == "malformed_digest":
        conn.execute(
            "UPDATE artifact_digests SET digest_payload = '{}' WHERE artifact_id = ?",
            (frame.ref,),
        )
    elif corruption == "digest_fingerprint":
        conn.execute(
            "UPDATE artifact_digests SET fingerprint = 'tampered' WHERE artifact_id = ?",
            (frame.ref,),
        )
    elif corruption == "missing_derivation_finding":
        derivation = finding.derivation.model_copy(update={"source_finding_refs": ("fnd_missing",)})
        conn.execute(
            "UPDATE findings SET derivation_payload = ? WHERE finding_id = ?",
            (canonical_json(derivation), finding.finding_id),
        )
    elif corruption == "missing_source_ref":
        conn.execute(
            "UPDATE findings SET source_refs_payload = ? WHERE finding_id = ?",
            (json.dumps(["art_missing"]), finding.finding_id),
        )
    elif corruption == "frame_sha":
        conn.execute(
            "UPDATE artifacts SET frame_sha = 'tampered' WHERE artifact_id = ?",
            (frame.ref,),
        )
    else:
        digest = frame.evidence_digest
        assert digest is not None
        operator = digest.operator.model_copy(update={"operator": "observe.metric"})
        changed = digest.model_copy(update={"operator": operator, "fingerprint": "pending"})
        changed = changed.model_copy(update={"fingerprint": make_digest_fingerprint(changed)})
        conn.execute(
            "UPDATE artifact_digests SET operator = ?, digest_payload = ?, fingerprint = ? "
            "WHERE artifact_id = ?",
            (
                operator.operator,
                canonical_json(changed),
                changed.fingerprint,
                frame.ref,
            ),
        )

    with pytest.raises(EvidenceIntegrityError):
        session.evidence.compatibility([finding.finding_id])


def test_current_definition_drift_is_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, start="2026-07-01", end="2026-07-31")
    finding = _first_finding(session, frame.ref)
    semantic_file = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    semantic_file.write_text(semantic_file.read_text().replace("amount.sum()", "amount.mean()"))
    session._catalog = ms.load()

    result = session.evidence.compatibility([finding.finding_id])

    assert result.status == "incompatible"
    assert result.semantic_status == "incompatible"
    assert any(issue.detail.kind == "definition_drift_detected" for issue in result.issues)


def test_unprovable_current_authority_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, start="2026-07-01", end="2026-07-31")
    finding = _first_finding(session, frame.ref)
    semantic_file = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    source = semantic_file.read_text()
    source = source[: source.index("@ms.metric")]
    semantic_file.write_text(source)
    session._catalog = ms.load()

    result = session.evidence.compatibility([finding.finding_id])

    assert result.status == "indeterminate"
    assert result.semantic_status == "indeterminate"
    assert any(issue.detail.kind == "semantic_authority_unknown" for issue in result.issues)


def test_not_ready_quality_is_selection_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, start="2026-07-01", end="2026-07-31")
    finding = _first_finding(session, frame.ref)
    store = session._evidence_store()
    assert store is not None
    store.read().execute(
        "UPDATE findings SET quality_status = 'not_ready' WHERE finding_id = ?",
        (finding.finding_id,),
    )

    result = session.evidence.compatibility([finding.finding_id])

    assert result.status == "incompatible"
    assert result.quality_status == "not_ready"


def test_twenty_findings_cover_all_190_pairs_and_bound_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    finding_ids: list[str] = []
    start = date(2026, 1, 1)
    for offset in range(20):
        window_start = start + timedelta(days=offset * 2)
        frame = _observe(
            session,
            start=window_start.isoformat(),
            end=(window_start + timedelta(days=1)).isoformat(),
        )
        finding_ids.append(_first_finding(session, frame.ref).finding_id)

    result = session.evidence.compatibility(list(reversed(finding_ids)))

    assert result.evaluated_pair_count == 190
    assert len(result.finding_ids) == 20
    assert len(result.issues) == 20
    assert result.omitted_issue_count == 190
    assert result.omitted_issue_kinds == (
        "comparability_incompatible",
        "null_rate_high",
    )
    assert result.status == "incompatible"

    finding_only = result.model_copy(update={"issues": (), "omitted_issue_count": 0})
    rendered_findings = finding_only.render()
    assert result.finding_ids[4] in rendered_findings
    assert result.finding_ids[5] not in rendered_findings
    rendered = result.render()
    assert rendered.count("comparability_incompatible findings=") == 5


def test_render_projects_only_three_distinct_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, start="2026-07-01", end="2026-07-31")
    finding = _first_finding(session, frame.ref)
    result = session.evidence.compatibility([finding.finding_id])
    boundaries = (
        InferenceBoundary(
            kind="significance_not_computed",
            reason="operator_did_not_compute",
            required_evidence=("significance_statistic",),
        ),
        InferenceBoundary(
            kind="interval_not_computed",
            reason="operator_did_not_compute",
            required_evidence=("uncertainty_interval",),
        ),
        InferenceBoundary(
            kind="causal_effect_not_estimated",
            reason="requires_independent_evidence",
            required_evidence=("causal_design",),
        ),
        InferenceBoundary(
            kind="business_impact_not_provided",
            reason="requires_independent_evidence",
            required_evidence=("business_policy",),
        ),
    )

    rendered = result.model_copy(update={"boundaries": boundaries}).render()

    assert "causal_effect_not_estimated" in rendered
    assert "business_impact_not_provided" not in rendered
    assert "boundaries omitted: 1" in rendered


def test_unknown_operator_evidence_rule_fails_closed() -> None:
    with pytest.raises(ValueError, match="no evidence rule registered"):
        inference_boundaries_for_operator("future.operator", ())
