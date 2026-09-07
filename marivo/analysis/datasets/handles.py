"""Private immutable definition roots and canonical realization identity."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import SupportsIndex, TypeAlias

from marivo._compat import Never
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetShapeId,
    _canonical_digest,
    _CanonicalValue,
    _is_stable_identifier,
)
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetDefinitionError
from marivo.analysis.refs import ArtifactRef

CanonicalValue: TypeAlias = _CanonicalValue
_LINEAGE_FACT_LIMIT = 16


def _definition_error(expected: str, received: str) -> DatasetDefinitionError:
    return DatasetDefinitionError(
        expected=expected,
        received=received,
        repair="Reconstruct the definition through its registered owner using canonical values.",
        location="dataset.definition",
    )


def _digest(value: CanonicalValue) -> str:
    try:
        return _canonical_digest(value)
    except DatasetConstructionError as exc:
        assert exc.expected is not None and exc.received is not None
        raise _definition_error(exc.expected, exc.received) from exc


def _check_token(token: object) -> None:
    if token is not _CORE_TOKEN:
        raise _definition_error("private Core factory", "direct root construction")


def _check_label(value: str) -> None:
    if not _is_stable_identifier(value):
        raise _definition_error("bounded canonical owner/role identifier", "invalid identifier")


def _check_tuple(value: object) -> None:
    if type(value) is not tuple:
        raise _definition_error("immutable tuple of bound definition facts", "mutable container")


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class RealizationHandle:
    """Opaque graph-local sharing identity, never itself hashed into a definition."""

    def __repr__(self) -> str:
        return "<private realization>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise _definition_error("in-process realization identity", "serialization")


@dataclass(frozen=True, slots=True)
class RealizationRequirement:
    role: str
    handle: RealizationHandle

    def __post_init__(self) -> None:
        _check_label(self.role)
        if type(self.handle) is not RealizationHandle:
            raise _definition_error("opaque realization handle", "invalid sharing requirement")


@dataclass(frozen=True, slots=True)
class LogicalInputToken:
    definition_fingerprint: str


@dataclass(frozen=True, slots=True)
class MaterializedInputToken:
    artifact_ref: ArtifactRef


@dataclass(frozen=True, slots=True, eq=False, repr=False, kw_only=True)
class MaterializedScanLeafHandle:
    _token: InitVar[object]
    session_id: str
    store_id: str
    artifact_session_id: str
    shape_id: DatasetShapeId
    artifact_ref: ArtifactRef
    content_authority_digest: str
    row_contract_fingerprint: str
    row_set_contract_fingerprint: str
    requirements: tuple[str, ...] = ()

    def __post_init__(self, _token: object) -> None:
        _check_token(_token)

    def __repr__(self) -> str:
        return "<private materialized scan leaf>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise _definition_error("runtime-owned scan recovery", "root serialization")


@dataclass(frozen=True, slots=True, eq=False, repr=False, kw_only=True)
class LogicalRootHandle:
    _token: InitVar[object]
    session_id: str
    store_id: str
    shape_id: DatasetShapeId
    row_contract_fingerprint: str
    row_set_contract_fingerprint: str
    definition_fingerprint: str
    operator_id: str
    parameters: CanonicalValue
    dependency_facts: tuple[str, ...]
    contract_versions: tuple[tuple[str, str], ...]
    inputs: tuple[DefinitionInput, ...]
    realizations: tuple[RealizationRequirement, ...]
    has_realizations: bool
    requirements: tuple[str, ...]
    # Replacement/reconstruction clears issuance; it cannot inherit validation.
    _factory_validated: bool = field(default=False, init=False)

    def __post_init__(self, _token: object) -> None:
        _check_token(_token)

    def __repr__(self) -> str:
        return "<private logical root>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise _definition_error("in-process logical definition", "root serialization")


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class DefinitionInput:
    role: str
    token: LogicalInputToken | MaterializedInputToken
    root: LogicalRootHandle | MaterializedScanLeafHandle

    def __post_init__(self) -> None:
        _check_label(self.role)
        if isinstance(self.token, LogicalInputToken):
            if not isinstance(self.root, LogicalRootHandle) or (
                self.token.definition_fingerprint != self.root.definition_fingerprint
            ):
                raise _definition_error("matched logical input token/root", "mismatched authority")
        elif isinstance(self.token, MaterializedInputToken):
            if not isinstance(self.root, MaterializedScanLeafHandle) or (
                self.token.artifact_ref != self.root.artifact_ref
            ):
                raise _definition_error("matched Materialized token/leaf", "mismatched authority")
        else:
            raise _definition_error("closed input authority token", "invalid token")


def _sharing_occurrences(
    inputs: tuple[DefinitionInput, ...],
    realizations: tuple[RealizationRequirement, ...],
) -> tuple[tuple[str, int], ...]:
    """Normalize every significant use occurrence, excluding pure graph sharing."""
    ordinals: dict[RealizationHandle, int] = {}
    occurrences: list[tuple[str, int]] = []

    def visit(requirements: tuple[RealizationRequirement, ...], path: str) -> None:
        for index, requirement in enumerate(requirements):
            ordinal = ordinals.setdefault(requirement.handle, len(ordinals))
            position = _digest((path, "realization", index, requirement.role))
            occurrences.append((position, ordinal))

    visit(realizations, "root")
    stack: list[tuple[DefinitionInput, str]] = [
        (item, _digest(("root", index, item.role)))
        for index, item in reversed(tuple(enumerate(inputs)))
    ]
    while stack:
        item, path = stack.pop()
        root = item.root
        if isinstance(root, MaterializedScanLeafHandle) or not root.has_realizations:
            continue
        visit(root.realizations, path)
        stack.extend(
            (child, _digest((path, index, child.role)))
            for index, child in reversed(tuple(enumerate(root.inputs)))
        )
    return tuple(occurrences)


def _make_logical_root(
    *,
    session_id: str,
    store_id: str,
    shape_id: DatasetShapeId,
    row_contract_fingerprint: str,
    row_set_contract_fingerprint: str,
    operator_id: str,
    inputs: tuple[DefinitionInput, ...] = (),
    parameters: CanonicalValue = (),
    realizations: tuple[RealizationRequirement, ...] = (),
    requirements: tuple[str, ...] = (),
    dependency_facts: tuple[str, ...] = (),
    contract_versions: tuple[tuple[str, str], ...] = (),
) -> LogicalRootHandle:
    for sequence in (inputs, realizations, requirements, dependency_facts, contract_versions):
        _check_tuple(sequence)
    for pair in contract_versions:
        _check_tuple(pair)
        if len(pair) != 2:
            raise _definition_error(
                "named producer contract version pairs", "invalid version binding"
            )
        for version_label in pair:
            _check_label(version_label)
    for fact_id in (*requirements, *dependency_facts):
        _check_label(fact_id)
    for item in inputs:
        if type(item) is not DefinitionInput:
            raise _definition_error("exact immutable input binding", "invalid input record")
        if isinstance(item.root, LogicalRootHandle):
            _validate_logical_root(item.root)
    _check_label(operator_id)
    for role in (*[item.role for item in inputs], *[item.role for item in realizations]):
        _check_label(role)
    if len({item.role for item in inputs}) != len(inputs) or len(
        {item.role for item in realizations}
    ) != len(realizations):
        raise _definition_error("unique ordered input and realization roles", "duplicate role")
    tokens: tuple[CanonicalValue, ...] = tuple(
        (item.role, "logical", item.token.definition_fingerprint)
        if isinstance(item.token, LogicalInputToken)
        else (item.role, "materialized", item.token.artifact_ref.ref)
        for item in inputs
    )
    sharing = _sharing_occurrences(inputs, realizations)
    fingerprint = "ds_" + _digest(
        (
            "marivo.dataset.definition/v1",
            row_contract_fingerprint,
            row_set_contract_fingerprint,
            operator_id,
            parameters,
            dependency_facts,
            contract_versions,
            tokens,
            sharing,
            requirements,
        )
    )
    root = LogicalRootHandle(
        _token=_CORE_TOKEN,
        session_id=session_id,
        store_id=store_id,
        shape_id=shape_id,
        row_contract_fingerprint=row_contract_fingerprint,
        row_set_contract_fingerprint=row_set_contract_fingerprint,
        definition_fingerprint=fingerprint,
        operator_id=operator_id,
        parameters=parameters,
        dependency_facts=dependency_facts,
        contract_versions=contract_versions,
        inputs=inputs,
        realizations=realizations,
        has_realizations=bool(sharing),
        requirements=requirements,
    )
    object.__setattr__(root, "_factory_validated", True)
    return root


def _validate_logical_root(root: LogicalRootHandle) -> None:
    """Require immutable roots issued by the sole canonical construction path.

    Every direct input has the same check at issuance, so the entire DAG is
    validated inductively without rewalking pure ancestors at every operation.
    This marker is in-process construction provenance, never execution authority.
    """
    if type(root) is not LogicalRootHandle or not root._factory_validated:
        raise _definition_error(
            "fingerprint bound to retained normalized definition", "corrupt logical root"
        )


@dataclass(frozen=True, slots=True)
class BoundedLineage:
    facts: tuple[str, ...]
    omitted_count: int


def _make_lineage(
    operator_id: str,
    inputs: tuple[BoundedLineage, ...],
    dependency_facts: tuple[str, ...],
    *,
    definition_fingerprint: str,
    row_contract_fingerprint: str,
    row_set_contract_fingerprint: str,
) -> BoundedLineage:
    own_fact = (
        f"{operator_id} definition={definition_fingerprint} "
        f"row={row_contract_fingerprint} row_set={row_set_contract_fingerprint}"
    )
    candidates = (own_fact, *dependency_facts, *(fact for item in inputs for fact in item.facts))
    facts = tuple(value[:512].replace("\n", " ") for value in candidates[:_LINEAGE_FACT_LIMIT])
    omitted = sum(item.omitted_count for item in inputs) + len(candidates) - len(facts)
    return BoundedLineage(facts=facts, omitted_count=omitted)
