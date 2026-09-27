"""Bound semantic signatures, cells, parts, and evidence for the private core."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, NoReturn, TypeAlias

from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EntityKind, MetricKind, Ref, SemanticKind
from marivo.semantic.ir import TargetSnapshotSelection, TargetValiditySelection


class CoreRuleError(AnalysisError):
    """A private algebra construction has an invalid input or missing premise."""

    def __init__(self, *, expected: str, received: str, repair: str, location: str) -> None:
        super().__init__(
            message="Analysis core rule validation failed.",
            expected=expected,
            received=received,
            location=location,
            repair=AnalysisRepair(
                kind="retry",
                action=repair,
                help_target=LiveHelpTarget(surface="analysis"),
            ),
        )


def reject(expected: str, received: str, repair: str, location: str) -> NoReturn:
    raise CoreRuleError(expected=expected, received=received, repair=repair, location=location)


def _nonempty(value: str, location: str) -> None:
    if type(value) is not str or not value:
        reject(
            "a non-empty bound identifier",
            type(value).__name__,
            "Supply the exact owner value.",
            location,
        )


def _unique(values: tuple[str, ...], location: str) -> None:
    if type(values) is not tuple or any(type(value) is not str or not value for value in values):
        reject(
            "an immutable tuple of identifiers",
            type(values).__name__,
            "Use exact identifiers.",
            location,
        )
    if len(set(values)) != len(values):
        reject("distinct identifiers", repr(values), "Remove the repeated binding.", location)


@dataclass(frozen=True, slots=True)
class Binding:
    """Session, owner, input and scope of one conditional construction."""

    session_id: str
    owner_id: str
    input_id: str
    scope_id: str

    def __post_init__(self) -> None:
        for name in ("session_id", "owner_id", "input_id", "scope_id"):
            _nonempty(getattr(self, name), f"core.binding.{name}")


CoordinateRole: TypeAlias = Literal["identity", "version", "group", "anchor", "instance"]


@dataclass(frozen=True, slots=True)
class Coordinate:
    """A whole typed coordinate component, never a display-value equivalence."""

    entity_ref: Ref[EntityKind]
    field: str
    role: CoordinateRole

    def __post_init__(self) -> None:
        if type(self.entity_ref) is not Ref or self.entity_ref.kind is not SemanticKind.ENTITY:
            reject(
                "an exact Entity Ref",
                type(self.entity_ref).__name__,
                "Bind an Entity Ref.",
                "core.coordinate",
            )
        _nonempty(self.field, "core.coordinate.field")
        if self.role not in ("identity", "version", "group", "anchor", "instance"):
            reject(
                "a closed coordinate role",
                str(self.role),
                "Use the owning coordinate role.",
                "core.coordinate",
            )


DomainKind: TypeAlias = Literal["entity", "group", "singleton", "journey", "interval", "anchor"]


@dataclass(frozen=True, slots=True)
class Correspondence:
    """Role, multiplicity and conditional facts of one explicit domain mapping."""

    source_definition_id: str
    target_definition_id: str
    role: Literal["pair", "group", "subject", "union"]
    multiplicity: Literal["preserve", "set_image", "group", "paired"]
    premises: tuple[FactKind, ...]

    def __post_init__(self) -> None:
        _nonempty(self.source_definition_id, "core.correspondence.source")
        _nonempty(self.target_definition_id, "core.correspondence.target")
        expected_multiplicity = {
            "pair": "paired",
            "group": "group",
            "subject": "set_image",
            "union": "preserve",
        }
        if expected_multiplicity.get(self.role) != self.multiplicity:
            reject(
                "a closed mapping role and multiplicity",
                repr(self),
                "Bind an explicit mapping kind.",
                "core.correspondence",
            )
        if len(set(self.premises)) != len(self.premises):
            reject(
                "distinct mapping premises",
                repr(self.premises),
                "List each premise once.",
                "core.correspondence",
            )
        if any(
            premise not in ("mapping_total", "single_value", "mapping_injective", "key_set_equal")
            for premise in self.premises
        ):
            reject(
                "closed mapping premises",
                repr(self.premises),
                "Bind the mapping's exact conditions.",
                "core.correspondence",
            )


@dataclass(frozen=True, slots=True)
class DomainSignature:
    """Symbolic instance and target coordinates; no actual membership is stored."""

    binding: Binding
    kind: DomainKind
    instance_key: tuple[Coordinate, ...]
    target_key: tuple[Coordinate, ...]
    definition_id: str
    version_selection: TargetSnapshotSelection | TargetValiditySelection | None = None
    correspondence: Correspondence | None = None

    def __post_init__(self) -> None:
        _nonempty(self.definition_id, "core.domain.definition")
        if self.kind not in ("entity", "group", "singleton", "journey", "interval", "anchor"):
            reject(
                "a closed instance kind",
                str(self.kind),
                "Use a registered domain kind.",
                "core.domain",
            )
        for name, coordinates in (
            ("instance_key", self.instance_key),
            ("target_key", self.target_key),
        ):
            if type(coordinates) is not tuple or any(
                type(item) is not Coordinate for item in coordinates
            ):
                reject(
                    "a tuple of typed coordinates",
                    type(coordinates).__name__,
                    "Bind complete coordinates.",
                    f"core.domain.{name}",
                )
            if len(set(coordinates)) != len(coordinates):
                reject(
                    "distinct coordinate components",
                    repr(coordinates),
                    "Remove duplicate coordinates.",
                    f"core.domain.{name}",
                )
        if self.kind != "singleton" and not self.instance_key:
            reject(
                "a complete non-empty instance key",
                "empty key",
                "Bind the full instance identity.",
                "core.domain",
            )
        if self.kind == "singleton" and (self.instance_key or self.target_key):
            reject(
                "empty singleton coordinates",
                "keyed singleton",
                "Use an unkeyed singleton.",
                "core.domain",
            )
        if self.version_selection is not None and not isinstance(
            self.version_selection, (TargetSnapshotSelection, TargetValiditySelection)
        ):
            reject(
                "an exact Semantic version selection",
                type(self.version_selection).__name__,
                "Resolve the version selection through Semantic.",
                "core.domain.version",
            )
        if (
            self.correspondence is not None
            and self.correspondence.target_definition_id != self.definition_id
        ):
            reject(
                "correspondence to this output domain",
                self.correspondence.target_definition_id,
                "Bind the exact target domain.",
                "core.domain.correspondence",
            )


@dataclass(frozen=True, slots=True)
class ObservedQuantity:
    definition_id: str
    metric_ref: Ref[MetricKind]
    graph_fingerprint: str
    unit: str | None
    time_scope: str
    contribution_id: str
    value_policy: str
    method_version: str
    kind: Literal["observed"] = field(default="observed", init=False)


@dataclass(frozen=True, slots=True)
class DerivedQuantity:
    definition_id: str
    method_version: str
    input_ids: tuple[str, ...]
    unit: str | None
    time_scope: str
    value_policy: str
    kind: Literal["derived"] = field(default="derived", init=False)


@dataclass(frozen=True, slots=True)
class RowStatisticQuantity:
    definition_id: str
    method_version: str
    input_id: str
    input_domain_id: str
    unit: str | None
    time_scope: str
    value_policy: str
    weighting: str
    kind: Literal["row_statistic"] = field(default="row_statistic", init=False)


@dataclass(frozen=True, slots=True)
class RolledQuantity:
    definition_id: str
    original_id: str
    method_version: str
    contribution_id: str
    unit: str | None
    time_scope: str
    value_policy: str
    kind: Literal["original_rollup"] = field(default="original_rollup", init=False)


Quantity: TypeAlias = ObservedQuantity | DerivedQuantity | RowStatisticQuantity | RolledQuantity


def validate_quantity(quantity: Quantity) -> None:
    _nonempty(quantity.definition_id, "core.quantity.definition")
    _nonempty(quantity.method_version, "core.quantity.method")
    _nonempty(quantity.time_scope, "core.quantity.time")
    _nonempty(quantity.value_policy, "core.quantity.policy")
    if quantity.unit is not None:
        _nonempty(quantity.unit, "core.quantity.unit")
    if isinstance(quantity, ObservedQuantity):
        if (
            type(quantity.metric_ref) is not Ref
            or quantity.metric_ref.kind is not SemanticKind.METRIC
        ):
            reject(
                "an exact Metric Ref",
                type(quantity.metric_ref).__name__,
                "Bind a Metric Ref.",
                "core.quantity.metric",
            )
        _nonempty(quantity.contribution_id, "core.quantity.contribution")
        _nonempty(quantity.graph_fingerprint, "core.quantity.graph")
    elif isinstance(quantity, DerivedQuantity):
        if (
            type(quantity.input_ids) is not tuple
            or not quantity.input_ids
            or any(type(value) is not str or not value for value in quantity.input_ids)
        ):
            reject(
                "at least one input quantity",
                "none",
                "Bind the input quantity.",
                "core.quantity.inputs",
            )
    elif isinstance(quantity, RowStatisticQuantity):
        for value in (quantity.input_id, quantity.input_domain_id, quantity.weighting):
            _nonempty(value, "core.quantity.row_statistic")
    elif isinstance(quantity, RolledQuantity):
        _nonempty(quantity.original_id, "core.quantity.original")
        _nonempty(quantity.contribution_id, "core.quantity.contribution")
    else:
        reject(
            "a closed quantity definition",
            type(quantity).__name__,
            "Use a core quantity variant.",
            "core.quantity",
        )


@dataclass(frozen=True, slots=True)
class Defined:
    value: bool | int | float | str

    def __post_init__(self) -> None:
        if type(self.value) not in (bool, int, float, str):
            reject(
                "a typed scalar Cell value",
                type(self.value).__name__,
                "Decode the declared value type.",
                "core.cell.defined",
            )


@dataclass(frozen=True, slots=True)
class Null:
    reason: str

    def __post_init__(self) -> None:
        _nonempty(self.reason, "core.cell.null")


@dataclass(frozen=True, slots=True)
class Undefined:
    reason: str

    def __post_init__(self) -> None:
        _nonempty(self.reason, "core.cell.undefined")


@dataclass(frozen=True, slots=True)
class Unknown:
    reason: str

    def __post_init__(self) -> None:
        _nonempty(self.reason, "core.cell.unknown")


Cell: TypeAlias = Defined | Null | Undefined | Unknown


@dataclass(frozen=True, slots=True)
class MissingCoordinate:
    side: Literal["current", "baseline"]
    key: tuple[str | int, ...]

    def __post_init__(self) -> None:
        if self.side not in ("current", "baseline") or not self.key:
            reject(
                "a side and complete missing key",
                repr(self.key),
                "Repair the exact pairing.",
                "core.pair",
            )


@dataclass(frozen=True, slots=True)
class SubjectPart:
    binding: Binding
    entity_ref: Ref[EntityKind]
    source_key: tuple[Coordinate, ...]
    subject_key: tuple[Coordinate, ...]
    injective: bool
    total: bool
    version: str


@dataclass(frozen=True, slots=True)
class EndpointPart:
    binding: Binding
    side: Literal["current", "baseline"]
    quantity_id: str
    version: str


@dataclass(frozen=True, slots=True)
class OriginalStatePart:
    binding: Binding
    quantity_id: str
    method_version: str
    contribution_id: str
    components: tuple[str, ...]
    version: str


@dataclass(frozen=True, slots=True)
class RowStatePart:
    binding: Binding
    quantity_id: str
    method_version: str
    input_domain_id: str
    components: tuple[str, ...]
    version: str


@dataclass(frozen=True, slots=True)
class CoveragePart:
    binding: Binding
    quantity_id: str
    scope_id: str
    version: str


@dataclass(frozen=True, slots=True)
class FixedReferencePart:
    binding: Binding
    reference_id: str
    scope_id: str
    version: str


@dataclass(frozen=True, slots=True)
class StatisticalWeightPart:
    binding: Binding
    role_id: str
    unit: str
    version: str


Part: TypeAlias = (
    SubjectPart
    | EndpointPart
    | OriginalStatePart
    | RowStatePart
    | CoveragePart
    | FixedReferencePart
    | StatisticalWeightPart
)
PartRole: TypeAlias = Literal[
    "subject",
    "current_endpoint",
    "baseline_endpoint",
    "original_state",
    "row_state",
    "coverage",
    "fixed_reference",
    "statistical_weight",
]


def part_role(part: Part) -> PartRole:
    if isinstance(part, SubjectPart):
        return "subject"
    if isinstance(part, EndpointPart):
        return "current_endpoint" if part.side == "current" else "baseline_endpoint"
    if isinstance(part, OriginalStatePart):
        return "original_state"
    if isinstance(part, RowStatePart):
        return "row_state"
    if isinstance(part, CoveragePart):
        return "coverage"
    if isinstance(part, FixedReferencePart):
        return "fixed_reference"
    return "statistical_weight"


def validate_part(part: Part) -> None:
    if isinstance(part, SubjectPart):
        if type(part.entity_ref) is not Ref or part.entity_ref.kind is not SemanticKind.ENTITY:
            reject(
                "an exact Subject Entity Ref",
                repr(part.entity_ref),
                "Bind the Subject Entity.",
                "core.part.subject",
            )
        if (
            not part.source_key
            or not part.subject_key
            or type(part.injective) is not bool
            or type(part.total) is not bool
        ):
            reject(
                "complete subject mapping and cardinality",
                repr(part),
                "Bind the complete Subject map.",
                "core.part.subject",
            )
    elif isinstance(part, EndpointPart):
        if part.side not in ("current", "baseline"):
            reject(
                "a named endpoint side",
                str(part.side),
                "Use current or baseline.",
                "core.part.endpoint",
            )
        _nonempty(part.quantity_id, "core.part.endpoint.quantity")
    elif isinstance(part, (OriginalStatePart, RowStatePart)):
        _nonempty(part.quantity_id, "core.part.state.quantity")
        _nonempty(part.method_version, "core.part.state.method")
        _unique(part.components, "core.part.state.components")
        if not part.components:
            reject(
                "at least one state component",
                "empty",
                "Retain the method state.",
                "core.part.state",
            )
        _nonempty(
            part.contribution_id if isinstance(part, OriginalStatePart) else part.input_domain_id,
            "core.part.state.binding",
        )
    elif isinstance(part, CoveragePart):
        _nonempty(part.quantity_id, "core.part.coverage.quantity")
        _nonempty(part.scope_id, "core.part.coverage.scope")
    elif isinstance(part, FixedReferencePart):
        _nonempty(part.reference_id, "core.part.reference.id")
        _nonempty(part.scope_id, "core.part.reference.scope")
    elif isinstance(part, StatisticalWeightPart):
        _nonempty(part.role_id, "core.part.weight.role")
        _nonempty(part.unit, "core.part.weight.unit")
    else:
        reject(
            "a closed retained part", type(part).__name__, "Use a core part variant.", "core.part"
        )
    _nonempty(part.version, "core.part.version")


FactKind: TypeAlias = Literal[
    "declared_key",
    "unique_key",
    "single_value",
    "mapping_total",
    "mapping_injective",
    "key_set_equal",
    "complete_coverage",
    "contribution_partition",
    "finite_numeric",
    "state_binding",
    "field_ownership",
    "cell_policy",
    "output_key",
    "subject_image",
]


@dataclass(frozen=True, slots=True)
class FactInput:
    """Exact domain and quantity consumed by an ordered relational premise."""

    domain: DomainSignature
    quantity: Quantity | None


@dataclass(frozen=True, slots=True)
class Fact:
    kind: FactKind
    binding: Binding
    subject_id: str
    version: str
    inputs: tuple[FactInput, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in (
            "declared_key",
            "unique_key",
            "single_value",
            "mapping_total",
            "mapping_injective",
            "key_set_equal",
            "complete_coverage",
            "contribution_partition",
            "finite_numeric",
            "state_binding",
            "field_ownership",
            "cell_policy",
            "output_key",
            "subject_image",
        ):
            reject(
                "a closed premise", str(self.kind), "Use a registered fact kind.", "core.fact.kind"
            )
        _nonempty(self.subject_id, "core.fact.subject")
        _nonempty(self.version, "core.fact.version")


EvidenceBasis: TypeAlias = Literal["declaration", "builder", "check", "observation", "deduction"]


@dataclass(frozen=True, slots=True)
class Evidence:
    fact: Fact
    basis: EvidenceBasis
    source_id: str
    dependencies: tuple[Fact, ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.source_id, "core.evidence.source")
        if self.basis not in ("declaration", "builder", "check", "observation", "deduction"):
            reject(
                "a closed evidence basis", str(self.basis), "Use the owning basis.", "core.evidence"
            )
        if self.basis == "declaration" and self.fact.kind not in (
            "declared_key",
            "field_ownership",
        ):
            reject(
                "a declared definition fact",
                self.fact.kind,
                "Use a completed source check for runtime facts.",
                "core.evidence.declaration",
            )
        if self.basis == "builder" and self.fact.kind != "field_ownership":
            reject(
                "a builder-owned definition fact",
                self.fact.kind,
                "Keep conditional Post and source checks pending.",
                "core.evidence.builder",
            )
        if type(self.dependencies) is not tuple or any(
            type(fact) is not Fact for fact in self.dependencies
        ):
            reject(
                "bound fact dependencies",
                type(self.dependencies).__name__,
                "Bind each premise.",
                "core.evidence",
            )
        if self.basis == "deduction" and not self.dependencies:
            reject(
                "deduction with bound premises",
                "no dependencies",
                "Name the facts that justify this deduction.",
                "core.evidence.deduction",
            )


CheckId: TypeAlias = Literal[
    "source.unique_key@v1",
    "source.single_value@v1",
    "source.exact_pairing@v1",
    "source.group_mapping@v1",
    "source.finite_numeric@v1",
    "source.cell_policy@v1",
    "source.contribution_partition@v1",
    "source.complete_coverage@v1",
]

_CHECK_FACTS: dict[CheckId, frozenset[FactKind]] = {
    "source.unique_key@v1": frozenset({"unique_key"}),
    "source.single_value@v1": frozenset({"single_value"}),
    "source.exact_pairing@v1": frozenset({"key_set_equal", "single_value", "mapping_injective"}),
    "source.group_mapping@v1": frozenset({"mapping_total", "single_value"}),
    "source.finite_numeric@v1": frozenset({"finite_numeric"}),
    "source.cell_policy@v1": frozenset({"cell_policy"}),
    "source.contribution_partition@v1": frozenset({"contribution_partition"}),
    "source.complete_coverage@v1": frozenset({"complete_coverage"}),
}


@dataclass(frozen=True, slots=True)
class Obligation:
    """Typed future check handoff; it is never evidence or execution support."""

    fact: Fact
    check_id: CheckId
    before: Literal["consume", "publish"]

    def __post_init__(self) -> None:
        if self.check_id not in _CHECK_FACTS or self.fact.kind not in _CHECK_FACTS[self.check_id]:
            reject(
                "a registered check for this exact premise",
                f"{self.check_id}: {self.fact.kind}",
                "Use the check owned by this fact or reject the operation.",
                "core.obligation.check",
            )
        if self.before not in ("consume", "publish"):
            reject(
                "consume or publish", str(self.before), "Name the check stage.", "core.obligation"
            )


@dataclass(frozen=True, slots=True)
class Signature:
    domain: DomainSignature
    quantity: Quantity | None = None
    parts: tuple[Part, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    obligations: tuple[Obligation, ...] = ()

    def __post_init__(self) -> None:
        if self.quantity is not None:
            validate_quantity(self.quantity)
        roles: list[PartRole] = []
        for part in self.parts:
            validate_part(part)
            if (
                part.binding.session_id != self.domain.binding.session_id
                or part.binding.owner_id != self.domain.binding.owner_id
            ):
                reject(
                    "same Session and owner part",
                    repr(part.binding),
                    "Bind the part to this owner.",
                    "core.signature.parts",
                )
            if isinstance(part, SubjectPart) and (
                part.source_key != self.domain.instance_key
                or any(coordinate.entity_ref != part.entity_ref for coordinate in part.subject_key)
            ):
                reject(
                    "a Subject map from the exact instance key to its Entity key",
                    repr(part.source_key),
                    "Bind the complete source and Subject identities.",
                    "core.signature.subject",
                )
            if isinstance(part, (OriginalStatePart, RowStatePart, CoveragePart)) and (
                part.binding != self.domain.binding
                or self.quantity is None
                or part.quantity_id != self.quantity.definition_id
            ):
                reject(
                    "a part bound to this quantity and input scope",
                    repr(part.binding),
                    "Retain the exact quantity, scope, and state binding.",
                    "core.signature.parts",
                )
            roles.append(part_role(part))
        if len(set(roles)) != len(roles):
            reject(
                "one part per role",
                repr(roles),
                "Use separate bound quantities or roles.",
                "core.signature.parts",
            )
        for item in self.evidence:
            binding = item.fact.binding
            if (
                binding.session_id != self.domain.binding.session_id
                or binding.owner_id != self.domain.binding.owner_id
            ):
                reject(
                    "same Session and owner fact",
                    repr(binding),
                    "Bind the fact to this owner.",
                    "core.signature.evidence",
                )
        for obligation in self.obligations:
            binding = obligation.fact.binding
            if (
                binding.session_id != self.domain.binding.session_id
                or binding.owner_id != self.domain.binding.owner_id
            ):
                reject(
                    "same Session and owner fact",
                    repr(binding),
                    "Bind the fact to this owner.",
                    "core.signature.evidence",
                )


def available_facts(signature: Signature) -> frozenset[Fact]:
    """Close evidence dependencies without promoting checks or conditional Post."""
    pending = {item.fact for item in signature.obligations}
    resolved: set[Fact] = set()
    remaining = list(signature.evidence)
    while remaining:
        ready = [
            item
            for item in remaining
            if item.fact not in pending and set(item.dependencies).issubset(resolved)
        ]
        if not ready:
            break
        resolved.update(item.fact for item in ready)
        remaining = [item for item in remaining if item not in ready]
    return frozenset(resolved)


def require_part(signature: Signature, role: PartRole) -> Part:
    for part in signature.parts:
        if part_role(part) == role:
            return part
    reject(
        f"retained {role} part",
        "missing part",
        "Rebuild from an input retaining this part.",
        "core.parts",
    )
    raise AssertionError("unreachable")
