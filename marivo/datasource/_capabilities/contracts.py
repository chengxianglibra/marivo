"""Runtime contracts derived from the datasource capability registry."""

from __future__ import annotations

import hashlib
import json

from marivo.datasource._capabilities.registry import REGISTRY
from marivo.datasource.errors import repair
from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR
from marivo.introspection.live.model import (
    AuthoringContract,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringRepair,
    AuthoringStateId,
    AuthoringStateRef,
    AuthoringTransition,
    LiveHelpTarget,
    TransitionKind,
)

type ContractSource = TableSourceIR | ParquetSourceIR | CsvSourceIR | JsonSourceIR


def transition_sort_key(transition: AuthoringTransition) -> tuple[object, ...]:
    """Return the canonical ordering key for one authoring transition."""
    canonical_id = transition.help_target.canonical_id
    requirements = tuple(
        (
            requirement.role,
            requirement.family,
            requirement.subject_refs,
            requirement.exact_keys,
        )
        for requirement in transition.input_requirements
    )
    return (
        transition.help_target.surface,
        (0, "") if canonical_id is None else (1, canonical_id),
        transition.kind,
        transition.subject_refs,
        requirements,
    )


def normalize_contract(contract: AuthoringContract) -> AuthoringContract:
    """Return a deduplicated contract with canonical state and transition order."""
    return AuthoringContract(
        subject_refs=tuple(sorted(set(contract.subject_refs))),
        states=tuple(
            sorted(
                set(contract.states),
                key=lambda state: (state.id, state.subject_refs, state.evidence_ids),
            )
        ),
        transitions=tuple(sorted(contract.transitions, key=transition_sort_key)),
    )


def _subject(name: str) -> tuple[str, ...]:
    return (f"datasource.{name}",)


def source_subject_ref(source: ContractSource) -> str:
    """Return a deterministic identity for one physical source descriptor."""
    encoded = json.dumps(source.to_dict(), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()[:16]
    return f"source.{source.kind}.{digest}"


def _inspection_subjects(datasource_id: str, source: ContractSource) -> tuple[str, ...]:
    return (datasource_id, source_subject_ref(source))


def _state(state_id: AuthoringStateId, subject_refs: tuple[str, ...]) -> AuthoringStateRef:
    return AuthoringStateRef(id=state_id, subject_refs=subject_refs)


def _effects(canonical_id: str) -> AuthoringEffects:
    effects = REGISTRY.by_canonical_id(canonical_id).effects
    assert effects is not None
    return effects.model_copy(deep=True)


def _transition(
    canonical_id: str,
    *,
    kind: TransitionKind,
    subject_refs: tuple[str, ...],
    required_states: tuple[AuthoringStateRef, ...] = (),
    produced_state: AuthoringStateRef | None = None,
    available: bool,
    input_requirements: tuple[AuthoringInputRequirement, ...] = (),
    blocked_by: tuple[str, ...] = (),
) -> AuthoringTransition:
    descriptor = REGISTRY.by_canonical_id(canonical_id)
    return AuthoringTransition(
        kind=kind,
        help_target=LiveHelpTarget(surface="datasource", canonical_id=descriptor.canonical_id),
        subject_refs=subject_refs,
        required_states=required_states,
        produced_state=produced_state,
        effects=_effects(canonical_id),
        available=available,
        input_requirements=input_requirements,
        blocked_by=blocked_by,
    )


def contract_for_spec(name: str) -> AuthoringContract:
    """Describe the register transition for one declared datasource spec."""
    subject_refs = _subject(name)
    declared = _state("datasource.declared", subject_refs)
    return normalize_contract(
        AuthoringContract(
            subject_refs=subject_refs,
            states=(declared,),
            transitions=(
                _transition(
                    "register",
                    kind="register",
                    subject_refs=subject_refs,
                    required_states=(declared,),
                    produced_state=_state("datasource.registered", subject_refs),
                    available=True,
                ),
            ),
        )
    )


def contract_for_registered(name: str) -> AuthoringContract:
    """Describe validation and inspection transitions for a registered datasource."""
    subject_refs = _subject(name)
    registered = _state("datasource.registered", subject_refs)
    return normalize_contract(
        AuthoringContract(
            subject_refs=subject_refs,
            states=(registered,),
            transitions=(
                _transition(
                    "test",
                    kind="validate_connection",
                    subject_refs=subject_refs,
                    required_states=(registered,),
                    produced_state=_state("datasource.connection_validated", subject_refs),
                    available=True,
                    input_requirements=(
                        AuthoringInputRequirement(
                            role="subject",
                            family="DatasourceReferenceInput",
                            subject_refs=subject_refs,
                        ),
                    ),
                ),
                _transition(
                    "inspect",
                    kind="inspect",
                    subject_refs=subject_refs,
                    required_states=(registered,),
                    produced_state=_state("source.inspected", subject_refs),
                    available=True,
                    input_requirements=(
                        AuthoringInputRequirement(
                            role="subject", family="DatasourceRef", subject_refs=subject_refs
                        ),
                        AuthoringInputRequirement(role="dependency", family="TableSource"),
                    ),
                ),
            ),
        )
    )


def contract_for_connection_test(name: str, *, ok: bool) -> AuthoringContract:
    """Describe the observed outcome of one datasource connection test."""
    subject_refs = _subject(name)
    states = (_state("datasource.connection_validated", subject_refs),) if ok else ()
    return normalize_contract(
        AuthoringContract(subject_refs=subject_refs, states=states, transitions=())
    )


def contract_for_scope(scope_kind: str) -> AuthoringContract:
    """Describe the blocked evidence-acquisition transition for an explicit scope."""
    subject_refs = (f"scope.{scope_kind}",)
    explicit = _state("scope.explicit", subject_refs)
    return normalize_contract(
        AuthoringContract(
            subject_refs=subject_refs,
            states=(explicit,),
            transitions=(
                _transition(
                    "SourceInspection.sample",
                    kind="acquire",
                    subject_refs=subject_refs,
                    required_states=(explicit,),
                    produced_state=None,
                    available=False,
                    input_requirements=(
                        AuthoringInputRequirement(role="receiver", family="SourceInspection"),
                        AuthoringInputRequirement(role="dependency", family="Columns"),
                    ),
                ),
            ),
        )
    )


def _scope_transition(
    canonical_id: str,
    *,
    subject_refs: tuple[str, ...],
    partition_fields: tuple[str, ...] = (),
) -> AuthoringTransition:
    input_requirements = tuple(
        requirement.model_copy(
            update={"exact_keys": partition_fields}
            if canonical_id == "partition" and requirement.role == "mapping_key"
            else {}
        )
        for requirement in REGISTRY.by_canonical_id(canonical_id).input_requirements
    )
    inspected = _state("source.inspected", subject_refs)
    return _transition(
        canonical_id,
        kind="scope",
        subject_refs=subject_refs,
        required_states=(inspected,),
        produced_state=_state("scope.explicit", subject_refs),
        available=True,
        input_requirements=input_requirements,
    )


def _inspection_contract(
    *,
    datasource_id: str,
    source: ContractSource,
    partition_state: str,
    partition_fields: tuple[str, ...],
    include_acquire: bool,
) -> AuthoringContract:
    subject_refs = _inspection_subjects(datasource_id, source)
    registered = _state("datasource.registered", subject_refs)
    inspected = _state("source.inspected", subject_refs)
    transitions: list[AuthoringTransition] = []
    if include_acquire:
        transitions.append(
            _transition(
                "SourceInspection.sample",
                kind="acquire",
                subject_refs=subject_refs,
                required_states=(inspected, _state("scope.explicit", subject_refs)),
                produced_state=_state("evidence.acquired", subject_refs),
                available=False,
                input_requirements=(
                    AuthoringInputRequirement(
                        role="receiver", family="SourceInspection", subject_refs=subject_refs
                    ),
                    AuthoringInputRequirement(role="scope", family="AuthoringScope"),
                    AuthoringInputRequirement(role="dependency", family="Columns"),
                ),
            )
        )
    if partition_state == "known":
        transitions.append(
            _scope_transition(
                "partition",
                subject_refs=subject_refs,
                partition_fields=partition_fields,
            )
        )
    else:
        transitions.append(_scope_transition("unpruned", subject_refs=subject_refs))
    return normalize_contract(
        AuthoringContract(
            subject_refs=subject_refs,
            states=(registered, inspected),
            transitions=tuple(transitions),
        )
    )


def contract_for_source_inspection(
    *,
    datasource_id: str,
    source: ContractSource,
    partition_state: str,
    partition_fields: tuple[str, ...],
) -> AuthoringContract:
    """Return the factual acquisition contract for one inspected source."""
    return _inspection_contract(
        datasource_id=datasource_id,
        source=source,
        partition_state=partition_state,
        partition_fields=partition_fields,
        include_acquire=True,
    )


def contract_for_partition_inspection(
    *,
    datasource_id: str,
    source: ContractSource,
    partition_state: str,
    partition_fields: tuple[str, ...],
) -> AuthoringContract:
    """Return factual scope constructors for captured partition evidence."""
    return _inspection_contract(
        datasource_id=datasource_id,
        source=source,
        partition_state=partition_state,
        partition_fields=partition_fields,
        include_acquire=False,
    )


def contract_for_snapshot(
    *, datasource_id: str, source: ContractSource, snapshot_id: str
) -> AuthoringContract:
    """Return acquired evidence and its query-free projection transitions."""
    subject_refs = _inspection_subjects(datasource_id, source)
    acquired = AuthoringStateRef(
        id="evidence.acquired",
        subject_refs=subject_refs,
        evidence_ids=(snapshot_id,),
    )
    projected = AuthoringStateRef(
        id="evidence.projected",
        subject_refs=subject_refs,
        evidence_ids=(snapshot_id,),
    )
    projection_ids = (
        "DiscoverySnapshot.dimensions",
        "DiscoverySnapshot.entity",
        "DiscoverySnapshot.measures",
        "DiscoverySnapshot.relationships",
        "DiscoverySnapshot.time_dimensions",
        "DiscoverySnapshot.values",
    )
    return normalize_contract(
        AuthoringContract(
            subject_refs=subject_refs,
            states=(
                _state("scope.explicit", subject_refs),
                acquired,
            ),
            transitions=tuple(
                _transition(
                    canonical_id,
                    kind="project_evidence",
                    subject_refs=subject_refs,
                    required_states=(acquired,),
                    produced_state=projected,
                    available=True,
                    input_requirements=REGISTRY.by_canonical_id(canonical_id).input_requirements,
                )
                for canonical_id in projection_ids
            ),
        )
    )


def repair_for_authoring_code(code: str) -> AuthoringRepair:
    """Return the exact typed repair registered for one authoring blocker."""
    if code == "datasource_missing":
        return repair(
            kind="register",
            canonical_id="register",
            action="Register the datasource before inspecting this source.",
            preserves_evidence=False,
        )
    if code in {"source_mismatch", "transformed_partition_unsupported", "timeout_not_enforceable"}:
        return repair(
            kind="configure",
            canonical_id="inspect",
            action="Inspect the datasource configuration before retrying this operation.",
            preserves_evidence=False,
        )
    if code in {"selected_columns_required", "unknown_source_column"}:
        return repair(
            kind="inspect",
            canonical_id="inspect",
            action="Inspect the captured source schema before selecting columns.",
            preserves_evidence=True,
        )
    if code in {
        "partition_state_unknown",
        "incomplete_partition_fields",
        "partition_predicate_unsupported",
    }:
        return repair(
            kind="rescope",
            canonical_id="SourceInspection.partitions",
            action="Rescope using the captured partition evidence.",
            preserves_evidence=True,
        )
    if code in {
        "cache_stale",
        "schema_stale",
        "fingerprint_stale",
        "acquisition_execution_failed",
    }:
        return repair(
            kind="reacquire",
            canonical_id="SourceInspection.sample",
            action="Reacquire bounded evidence from the inspected source.",
            preserves_evidence=False,
        )
    if code == "typed_schema_required":
        return repair(
            kind="configure",
            canonical_id="inspect",
            action="Configure a non-empty authored schema before inspection.",
            preserves_evidence=False,
        )
    raise ValueError(f"No typed authoring repair is registered for {code!r}.")
