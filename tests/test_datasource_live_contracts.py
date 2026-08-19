"""Live state contracts for datasource declaration and management results."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo._authoring.model import (
    AuthoringContract,
    AuthoringEffects,
    AuthoringJudgmentRequirement,
    AuthoringStateRef,
    AuthoringTransition,
)
from marivo._authoring.normalize import normalize_contract
from marivo._compat import UTC
from marivo.datasource._capabilities.contracts import repair_for_authoring_code
from marivo.datasource._capabilities.registry import REGISTRY
from marivo.datasource.errors import repair
from marivo.datasource.manage import DatasourceFailure, DatasourceTestResult
from marivo.introspection.live.model import LiveHelpTarget


@pytest.fixture
def snapshot(tmp_path: Path) -> object:
    from datetime import datetime

    from marivo.datasource.metadata import ColumnMetadata
    from marivo.datasource.snapshot import (
        DiscoverySnapshot,
        SnapshotCoverage,
        _profile_column,
    )

    return DiscoverySnapshot(
        id="snapshot-1",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders"),
        scope=md.unpruned(max_rows=10, timeout_seconds=5),
        columns=("order_id",),
        schema_fingerprint="schema-1",
        profiles=(
            _profile_column(
                pd.DataFrame({"order_id": [1]}),
                ColumnMetadata("order_id", "int64", False, None, 1),
                partition_names=frozenset(),
                scope_exhaustion="exhaustive",
            ),
        ),
        coverage=SnapshotCoverage(0, 0, "exhaustive", "scope_exact", "first_rows_limit", ()),
        persist_values=False,
        value_evidence_state="value_evidence_unavailable",
        cache_status="fresh",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
        _project_root=tmp_path,
    )


def test_snapshot_contract_exposes_all_query_free_projections(snapshot: object) -> None:
    from marivo.datasource.snapshot import DiscoverySnapshot

    assert isinstance(snapshot, DiscoverySnapshot)
    transitions = snapshot.contract().transitions

    assert [transition.help_target.canonical_id for transition in transitions] == [
        "DiscoverySnapshot.dimensions",
        "DiscoverySnapshot.entity",
        "DiscoverySnapshot.measures",
        "DiscoverySnapshot.relationships",
        "DiscoverySnapshot.time_dimensions",
        "DiscoverySnapshot.values",
    ]
    assert all(transition.kind == "project_evidence" for transition in transitions)
    assert all(transition.available for transition in transitions)
    assert all(transition.effects.data_access == "none" for transition in transitions)


def test_projection_result_is_terminal_evidence_not_semantic_recommendation(
    snapshot: object,
) -> None:
    from marivo.datasource.snapshot import DiscoverySnapshot

    assert isinstance(snapshot, DiscoverySnapshot)
    result = snapshot.entity(columns=("order_id",))

    assert result.snapshot_id == snapshot.id
    assert result.contract().states == (
        AuthoringStateRef(
            id="evidence.projected",
            subject_refs=("order_id",),
            evidence_ids=(snapshot.id,),
        ),
    )
    assert result.contract().transitions == ()
    assert [requirement.id for requirement in result.contract().judgment_requirements] == [
        "business_identity",
        "cross_partition_uniqueness",
        "key_reuse",
        "temporal_stability",
    ]
    assert all(
        requirement.authority == "user_or_business_owner"
        for requirement in result.contract().judgment_requirements
    )
    assert not hasattr(result, "next_calls")


def test_contract_normalizes_non_mechanical_judgments_without_transitions() -> None:
    requirement = AuthoringJudgmentRequirement(
        id="business_identity",
        subjects=("order_id",),
        evidence_ids=("snapshot-1",),
    )
    contract = normalize_contract(
        AuthoringContract(
            subject_refs=("order_id",),
            states=(),
            transitions=(),
            judgment_requirements=(
                requirement.model_copy(update={"id": "temporal_stability"}),
                requirement,
                requirement,
            ),
        )
    )

    assert tuple(item.id for item in contract.judgment_requirements) == (
        "business_identity",
        "temporal_stability",
    )
    assert contract.transitions == ()
    assert contract.model_dump()["judgment_requirements"][0] == {
        "id": "business_identity",
        "subjects": ("order_id",),
        "evidence_ids": ("snapshot-1",),
        "authority": "user_or_business_owner",
    }
    rendered = contract.render()
    assert "non-mechanical judgment requirements" in rendered
    assert "business_identity" in rendered


def test_spec_contract_exposes_only_register_transition() -> None:
    spec = md.duckdb(name="warehouse", path="warehouse.duckdb")

    contract = spec.contract()

    assert contract.states == (
        AuthoringStateRef(id="datasource.declared", subject_refs=("datasource:warehouse",)),
    )
    assert [transition.kind for transition in contract.transitions] == ["register"]
    assert contract.transitions[0].help_target == LiveHelpTarget(
        surface="datasource", canonical_id="register"
    )
    assert contract.transitions[0].effects == REGISTRY.by_canonical_id("register").effects
    assert contract.transitions[0].effects is not REGISTRY.by_canonical_id("register").effects


def test_successful_connection_test_proves_connection_validated() -> None:
    result = DatasourceTestResult(
        name="warehouse", ok=True, latency_ms=4, failure=None, repair=None
    )

    assert (
        AuthoringStateRef(
            id="datasource.connection_validated",
            subject_refs=("datasource:warehouse",),
        )
        in result.contract().states
    )


def test_failed_connection_test_exposes_typed_repair_without_error_alias() -> None:
    result = DatasourceTestResult(
        name="warehouse",
        ok=False,
        latency_ms=None,
        failure=DatasourceFailure(
            code="connection_roundtrip_failed",
            exception_type="ProgrammingError",
            backend_code="115",
            backend_name="UNKNOWN_SETTING",
            message="Unknown setting access_mode",
        ),
        repair=repair(
            kind="reconnect",
            canonical_id="test",
            action="Reconnect the datasource after fixing its connection settings.",
        ),
    )

    assert result.contract().states == ()
    assert len(result.contract().transitions) == 1
    transition = result.contract().transitions[0]
    assert transition.kind == "validate_connection"
    assert transition.available is False
    assert transition.blocked_by == ("connection_roundtrip_failed",)
    assert transition.help_target == LiveHelpTarget(surface="datasource", canonical_id="test")
    assert result.contract().model_dump()["transitions"][0]["blocked_by"] == (
        "connection_roundtrip_failed",
    )
    assert result.repair is not None
    assert result.repair.kind == "reconnect"
    assert result.failure is not None
    assert result.failure.backend_name == "UNKNOWN_SETTING"
    assert not hasattr(result, "error")


@pytest.mark.parametrize(
    ("ok", "failure", "has_repair"),
    [
        (
            True,
            DatasourceFailure(
                code="connection_open_failed",
                exception_type="RuntimeError",
                backend_code=None,
                backend_name=None,
                message="failed",
            ),
            False,
        ),
        (False, None, True),
        (
            False,
            DatasourceFailure(
                code="connection_open_failed",
                exception_type="RuntimeError",
                backend_code=None,
                backend_name=None,
                message="failed",
            ),
            False,
        ),
    ],
)
def test_connection_test_result_rejects_inconsistent_success_failure_state(
    ok: bool,
    failure: DatasourceFailure | None,
    has_repair: bool,
) -> None:
    typed_repair = (
        repair(
            kind="reconnect",
            canonical_id="test",
            action="Reconnect the datasource after fixing its connection settings.",
        )
        if has_repair
        else None
    )

    with pytest.raises(ValueError, match="DatasourceTestResult"):
        DatasourceTestResult(
            name="warehouse",
            ok=ok,
            latency_ms=None,
            failure=failure,
            repair=typed_repair,
        )


def test_scope_contract_requires_registered_inspection_and_column_inputs() -> None:
    scope = md.unpruned(max_rows=1000, timeout_seconds=30)

    transition = scope.contract().transitions[0]

    assert transition.kind == "acquire"
    assert transition.available is False
    assert [requirement.family for requirement in transition.input_requirements] == [
        "SourceInspection",
        "Columns",
    ]
    assert transition.blocked_by == ()


def test_registered_datasource_inspection_contract_binds_ref_and_requires_table() -> None:
    summary = md.register(md.duckdb(name="warehouse", path=":memory:"))

    inspect = next(
        transition for transition in summary.contract().transitions if transition.kind == "inspect"
    )

    assert [
        (requirement.role, requirement.family, requirement.subject_refs)
        for requirement in inspect.input_requirements
    ] == [
        ("subject", "Ref[datasource]", ("datasource:warehouse",)),
        ("dependency", "TableSource", ()),
    ]


@pytest.mark.parametrize(
    ("code", "kind", "canonical_id", "preserves_evidence"),
    [
        ("datasource_missing", "register", "register", False),
        ("source_mismatch", "configure", "inspect", False),
        ("selected_columns_required", "inspect", "inspect", True),
        ("unknown_source_column", "inspect", "inspect", True),
        ("partition_state_unknown", "rescope", "SourceInspection.partitions", True),
        ("incomplete_partition_fields", "rescope", "SourceInspection.partitions", True),
        ("partition_predicate_unsupported", "rescope", "SourceInspection.partitions", True),
        ("transformed_partition_unsupported", "configure", "inspect", False),
        ("timeout_not_enforceable", "configure", "inspect", False),
        ("acquisition_connection_failed", "reconnect", "test", True),
        ("acquisition_source_failed", "inspect", "inspect", True),
        (
            "acquisition_execution_failed",
            "reacquire",
            "SourceInspection.sample",
            False,
        ),
        ("cache_stale", "reacquire", "SourceInspection.sample", False),
        ("schema_stale", "reacquire", "SourceInspection.sample", False),
        ("fingerprint_stale", "reacquire", "SourceInspection.sample", False),
    ],
)
def test_authoring_repair_mapping_is_exact(
    code: str,
    kind: str,
    canonical_id: str,
    preserves_evidence: bool,
) -> None:
    result = repair_for_authoring_code(code)

    assert result.kind == kind
    assert result.help_target.canonical_id == canonical_id
    assert result.preserves_evidence is preserves_evidence


def test_execution_failure_repair_has_deterministic_stop_boundary() -> None:
    result = repair_for_authoring_code("acquisition_execution_failed")

    assert "at most once" in result.action
    assert "remaining data-access budget permits" in result.action
    assert "Caller-provided read, row, and timeout limits take precedence" in result.action
    assert "same structured code and backend name" in result.action
    assert "stop and report" in result.action


def test_normalize_contract_sorts_and_deduplicates_contract_fields() -> None:
    state = AuthoringStateRef(id="datasource.declared", subject_refs=("datasource.warehouse",))
    effects = AuthoringEffects(data_access="none", connection="none")
    register = AuthoringTransition(
        kind="register",
        public_entrypoint="md.register(...)",
        expected_output_family="DatasourceSummary",
        help_target=LiveHelpTarget(surface="datasource", canonical_id="register"),
        subject_refs=("datasource.warehouse",),
        effects=effects,
        available=True,
    )
    inspect = AuthoringTransition(
        kind="inspect",
        public_entrypoint="md.inspect(...)",
        expected_output_family="SourceInspection",
        help_target=LiveHelpTarget(surface="datasource", canonical_id="inspect"),
        subject_refs=("datasource.warehouse",),
        effects=effects,
        available=True,
    )

    normalized = normalize_contract(
        AuthoringContract(
            subject_refs=("datasource.warehouse", "datasource.warehouse"),
            states=(state, state),
            transitions=(register, inspect),
        )
    )

    assert normalized.subject_refs == ("datasource.warehouse",)
    assert normalized.states == (state,)
    assert [transition.kind for transition in normalized.transitions] == ["inspect", "register"]
