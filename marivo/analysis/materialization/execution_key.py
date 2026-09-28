"""Versioned private execution keys for legacy and staged graph routes."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, is_dataclass
from typing import TypeAlias

from marivo.analysis.compiler.graph_plan import GraphPlan, LocalMethodStage, SourceMethodStage
from marivo.analysis.compiler.normalize import artifact_inputs, require_unmixed_inputs
from marivo.analysis.core import model as core_model
from marivo.analysis.core.graph import FixedLeaf, MethodNode, Node, SourceLeaf
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.descriptors import _canonical_digest, _is_stable_identifier
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.contracts import ArtifactRecord, LocalReceipt
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.methods import physical as method_physical
from marivo.analysis.methods.semantics import MethodKey
from marivo.refs import Ref, RefPayloadV1, SemanticKind
from marivo.semantic.ir import TargetSnapshotSelection, TargetValiditySelection

_DEFINITION = re.compile(r"ds_[0-9a-f]{64}\Z")
_DSL_PROTOCOL = "marivo.dataset_execution_key/v2"
_GRAPH_PROTOCOL = "marivo.analysis.execution_key/v1"
_CanonicalValue: TypeAlias = None | bool | int | float | str | tuple["_CanonicalValue", ...]

_WIRE_TAGS: dict[type[object], str] = {
    core_model.Binding: "binding",
    core_model.Coordinate: "coordinate",
    core_model.Correspondence: "correspondence",
    core_model.DomainSignature: "domain",
    core_model.ObservedQuantity: "observed_quantity",
    core_model.DerivedQuantity: "derived_quantity",
    core_model.RowStatisticQuantity: "row_statistic_quantity",
    core_model.RolledQuantity: "rolled_quantity",
    core_model.SubjectPart: "subject_part",
    core_model.EndpointPart: "endpoint_part",
    core_model.OriginalStatePart: "original_state_part",
    core_model.RowStatePart: "row_state_part",
    core_model.CoveragePart: "coverage_part",
    core_model.FixedReferencePart: "fixed_reference_part",
    core_model.StatisticalWeightPart: "statistical_weight_part",
    core_model.FactInput: "fact_input",
    core_model.Fact: "fact",
    core_model.Evidence: "evidence",
    core_model.Obligation: "obligation",
    core_model.Signature: "signature",
    TargetSnapshotSelection: "snapshot_selection",
    TargetValiditySelection: "validity_selection",
    RefPayloadV1: "semantic_ref_payload",
    MethodKey: "method",
    method_physical.ScalarType: "scalar_type",
    method_physical.DecimalType: "decimal_type",
    method_physical.NoTime: "no_time",
    method_physical.TimeShape: "time_shape",
    method_physical.SourceShape: "source_shape",
    method_physical.FixedShape: "fixed_shape",
    method_physical.QualificationKey: "qualification",
}


def _key_error(expected: str, received: str) -> IntegrityError:
    return IntegrityError(
        expected=expected,
        received=received,
        repair="Rebuild the exact admitted graph and verified input bindings.",
        stage="graph_execution_key",
    )


def _wire(value: object) -> _CanonicalValue:
    """Encode only closed contract values, using stable tags instead of class names."""
    if value is None:
        return None
    if type(value) is Ref:
        return ("semantic_ref", value.kind.value, value.path)
    if type(value) is SemanticKind:
        return value.value
    if isinstance(value, (bool, int, float, str)):
        return value
    if type(value) is tuple:
        return tuple(_wire(item) for item in value)
    tag = _WIRE_TAGS.get(type(value))
    if tag is None or not is_dataclass(value):
        raise _key_error("a closed canonical graph contract value", type(value).__name__)
    return (tag, tuple((item.name, _wire(getattr(value, item.name))) for item in fields(value)))


def _ordered_plan(admitted: GraphPlan) -> _CanonicalValue:
    nodes = {
        stage.node.identity: stage.node
        for stage in admitted.stages
        if isinstance(stage, (SourceMethodStage, LocalMethodStage))
    }
    ordered: list[_CanonicalValue] = []
    for requirement in admitted.physical_requirements:
        qualified = requirement.implementation.qualification
        node = nodes.get(requirement.node_id)
        if not isinstance(qualified, method_physical.Qualified) or node is None:
            raise _key_error("a selected qualified method for every reachable stage", "mismatch")
        ordered.append(
            (
                requirement.key.method.name,
                requirement.key.method.version,
                requirement.key.route,
                _wire(requirement.key),
                qualified.implementation_id,
                requirement.implementation.contract_version,
                _wire(node.signature),
            )
        )
    return tuple(ordered)


def _fixed_input_occurrences(root: Node) -> tuple[FixedLeaf, ...]:
    """Retain ordered fixed operand slots while expanding shared methods once."""
    pending = [root]
    expanded: set[str] = set()
    leaves: list[FixedLeaf] = []
    while pending:
        node = pending.pop()
        if isinstance(node, FixedLeaf):
            leaves.append(node)
        elif isinstance(node, MethodNode) and node.identity not in expanded:
            expanded.add(node.identity)
            pending.extend(edge.node for edge in reversed(node.inputs))
    return tuple(leaves)


@dataclass(frozen=True, slots=True)
class SourceKeyBinding:
    """Selected source metadata; this value alone never certifies a source read."""

    leaf: SourceLeaf
    physical_shape: method_physical.SourceShape
    semantic_dependency_digest: str
    selected_binding_fingerprint: str


@dataclass(frozen=True, slots=True)
class FixedPartKey:
    role: str
    contract_id: str
    contract_version: int
    receipt_digest: str


@dataclass(frozen=True, slots=True)
class FixedKeyInput:
    """Exact fixed metadata; R4.4 must verify receipts before using a hit."""

    leaf: FixedLeaf
    session_ref: str
    producing_run_ref: str
    primary_receipt_digest: str
    ordered_parts: tuple[FixedPartKey, ...]
    input_binding: str
    method_state_contract_id: str
    method_state_version: int
    snapshot_digest: str


def graph_source_execution_key(
    admitted: GraphPlan, bindings: tuple[SourceKeyBinding, ...], run_ref: str
) -> str:
    """Hash one newly allocated source evaluation without consulting old outputs."""
    if (
        admitted.classification.kind != "source"
        or not _is_stable_identifier(run_ref)
        or type(bindings) is not tuple
        or len(bindings) != len(admitted.classification.sources)
        or any(
            binding.leaf is not leaf
            for binding, leaf in zip(bindings, admitted.classification.sources, strict=True)
        )
        or any(
            type(binding.physical_shape) is not method_physical.SourceShape
            or binding.physical_shape != binding.leaf.definition.shape
            or not binding.semantic_dependency_digest
            or not binding.selected_binding_fingerprint
            for binding in bindings
        )
    ):
        raise _key_error("one admitted source plan and exact ordered source bindings", "mismatch")
    ordered_bindings: _CanonicalValue = tuple(
        (
            binding.leaf.definition.fingerprint,
            _wire(binding.leaf.definition.datasource),
            _wire(binding.physical_shape),
            binding.semantic_dependency_digest,
            binding.selected_binding_fingerprint,
        )
        for binding in bindings
    )
    return _canonical_digest(
        (
            _GRAPH_PROTOCOL,
            "source",
            admitted.root.fingerprint,
            _ordered_plan(admitted),
            ordered_bindings,
            run_ref,
        )
    )


def graph_fixed_execution_key(admitted: GraphPlan, inputs: tuple[FixedKeyInput, ...]) -> str:
    """Hash ordered fixed metadata, without claiming receipt or cache authority."""
    occurrences = _fixed_input_occurrences(admitted.root)
    if (
        admitted.classification.kind != "artifact"
        or type(inputs) is not tuple
        or len(inputs) != len(occurrences)
        or any(value.leaf is not leaf for value, leaf in zip(inputs, occurrences, strict=True))
        or any(
            value.session_ref != value.leaf.signature.domain.binding.session_id
            or type(value.ordered_parts) is not tuple
            or type(value.method_state_version) is not int
            or value.method_state_version < 1
            or not all(
                (
                    value.session_ref,
                    value.producing_run_ref,
                    value.primary_receipt_digest,
                    value.input_binding,
                    value.method_state_contract_id,
                    value.snapshot_digest,
                )
            )
            or any(
                type(part.contract_version) is not int
                or part.contract_version < 1
                or not all((part.role, part.contract_id, part.receipt_digest))
                for part in value.ordered_parts
            )
            for value in inputs
        )
    ):
        raise _key_error("one admitted fixed plan and exact ordered Artifact inputs", "mismatch")
    ordered_inputs: _CanonicalValue = tuple(
        (
            value.session_ref,
            value.leaf.artifact.ref,
            value.producing_run_ref,
            value.primary_receipt_digest,
            tuple(
                (part.role, part.contract_id, part.contract_version, part.receipt_digest)
                for part in value.ordered_parts
            ),
            value.input_binding,
            value.method_state_contract_id,
            value.method_state_version,
            value.snapshot_digest,
        )
        for value in inputs
    )
    return _canonical_digest(
        (
            _GRAPH_PROTOCOL,
            "fixed",
            admitted.root.fingerprint,
            _ordered_plan(admitted),
            ordered_inputs,
        )
    )


def _definition_root(dataset: LogicalDataset) -> LogicalRootHandle:
    if not isinstance(dataset, LogicalDataset):
        raise IntegrityError(
            expected="one normalized versioned DSL definition",
            received="invalid definition identity",
            repair="Reconstruct the logical Dataset through its owning method contract.",
            stage="admission",
        )
    root = dataset._root
    if (
        not isinstance(root, LogicalRootHandle)
        or _DEFINITION.fullmatch(dataset.definition_fingerprint) is None
    ):
        raise IntegrityError(
            expected="one normalized versioned DSL definition",
            received="invalid definition identity",
            repair="Reconstruct the logical Dataset through its owning method contract.",
            stage="admission",
        )
    return root


def _fixed_input_error() -> IntegrityError:
    return IntegrityError(
        expected="the exact selected fixed Artifact, producing Run and complete receipts",
        received="fixed input binding mismatch",
        repair="Select and inspect the exact input Artifacts before retrying this definition.",
        stage="admission",
    )


def execution_key(definition_fingerprint: str) -> str:
    """Bind a definition to common materialization protocol v1, within one Session."""
    return _canonical_digest(("marivo.dataset_execution_key/v1", definition_fingerprint, 1))


def source_execution_key(dataset: LogicalDataset, run_ref: str) -> str:
    """Bind one admitted source evaluation to its already allocated Run ref."""
    root = _definition_root(dataset)
    classification = require_unmixed_inputs(root)
    if classification.kind != "source" or not _is_stable_identifier(run_ref):
        raise IntegrityError(
            expected="a source-only DSL definition and an allocated stable Run ref",
            received="invalid source evaluation identity",
            repair="Classify the graph and allocate one Run ref at the guarded admission boundary.",
            stage="admission",
        )
    return _canonical_digest((_DSL_PROTOCOL, "source", root.definition_fingerprint, run_ref))


def fixed_execution_key(dataset: LogicalDataset, records: tuple[ArtifactRecord, ...]) -> str:
    """Bind a fixed-only definition to selected immutable receipt occurrences."""
    root = _definition_root(dataset)
    classification = require_unmixed_inputs(root)
    if classification.kind != "artifact":
        raise _fixed_input_error()
    retained = artifact_inputs(dataset)
    if type(records) is not tuple or len(records) != len(retained):
        raise _fixed_input_error()

    bindings: list[
        tuple[str, str, str, str, tuple[tuple[str, str, int, str], ...]]
        | tuple[str, str, str, str, tuple[tuple[str, str, int, str], ...], str]
    ] = []
    for value, record in zip(retained, records, strict=True):
        if not isinstance(record, ArtifactRecord):
            raise _fixed_input_error()
        descriptor = record.descriptor
        receipt = descriptor.storage_receipt
        state = value.state
        if (
            value._owner.store_id != dataset._owner.store_id
            or record.artifact_ref != state.artifact_ref.ref
            or record.session_ref != state.artifact_session_ref
            or record.producing_run_ref != state.producing_run_ref
            or record.evidence.evidence_digest != state.evidence_authority_digest
            or record.evidence.quality_summary_digest != state.quality_authority_digest
            or descriptor.definition_fingerprint != value.definition_fingerprint
            or descriptor.row_contract_fingerprint != value._root.row_contract_fingerprint
            or descriptor.row_set_contract_fingerprint != value._root.row_set_contract_fingerprint
            or not isinstance(receipt, LocalReceipt)
            or receipt.identity_digest != state.content_authority_digest
        ):
            raise _fixed_input_error()
        parts = descriptor.retained_parts
        if type(parts) is not tuple or any(
            not isinstance(part.storage_receipt, LocalReceipt) for part in parts
        ):
            raise _fixed_input_error()
        binding = (
            record.session_ref,
            record.artifact_ref,
            record.producing_run_ref,
            receipt.identity_digest,
            tuple(
                (
                    part.role,
                    part.contract_id,
                    part.contract_version,
                    part.storage_receipt.identity_digest,
                )
                for part in parts
            ),
        )
        member_binding = (
            descriptor.j1_exchange.member_binding if descriptor.j1_exchange is not None else None
        )
        bindings.append((*binding, member_binding) if member_binding is not None else binding)
    return _canonical_digest((_DSL_PROTOCOL, "fixed", root.definition_fingerprint, tuple(bindings)))
