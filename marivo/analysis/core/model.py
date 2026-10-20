"""Bound Query signatures and the source checks attached to their plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import MetricNode
from marivo._definition.types import ExactRef, Owner
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget

if TYPE_CHECKING:
    from marivo.analysis.binding.observe import ObservationBinding


class CoreRuleError(AnalysisError):
    """A private analysis structure has invalid input or lacks a premise."""

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
class BoundQuantity:
    """One named Metric output; physical dtype resolves when its source binds."""

    name: str
    definition: MetricNode
    unit: str | None

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name or any(c in self.name for c in "\n\r\t"):
            reject(
                "a nonempty single-line output name",
                repr(self.name),
                "Give each Metric a distinct output name.",
                "query.metrics",
            )


@dataclass(frozen=True, slots=True)
class ResultQuantity:
    """A computed Query value without a source Metric recipe."""

    name: str
    unit: str | None
    kind: Literal["numeric", "boolean", "string", "date", "timestamp", "duration", "unresolved"] = (
        "numeric"
    )

    def __post_init__(self) -> None:
        _nonempty(self.name, "query.values")


@dataclass(frozen=True, slots=True)
class BoundSignature:
    """Multi-output contract for one bound Query invocation."""

    owner: Owner
    quantities: tuple[BoundQuantity | ResultQuantity, ...]
    axes: tuple[ComparisonAxis, ...] = ()
    declared_metrics: tuple[tuple[ExactRef, MetricNode], ...] = ()
    evidence: tuple[Evidence, ...] = ()
    obligations: tuple[Obligation, ...] = ()
    observation: ObservationBinding | None = None

    def __post_init__(self) -> None:
        if type(self.quantities) is not tuple or any(
            type(item) not in (BoundQuantity, ResultQuantity) for item in self.quantities
        ):
            reject(
                "an immutable tuple of named Metric outputs",
                type(self.quantities).__name__,
                "Construct an AnalysisFrame through session.observe(metrics).",
                "query.signature",
            )
        names = tuple(item.name for item in self.quantities)
        _unique(names, "query.metrics")
        _nonempty(self.owner.session_id, "query.session_id")
        _nonempty(self.owner.owner_id, "query.owner_id")


@dataclass(frozen=True, slots=True)
class Binding:
    """Session, owner, input and scope of one conditional source check."""

    session_id: str
    owner_id: str
    input_id: str
    scope_id: str

    def __post_init__(self) -> None:
        for name in ("session_id", "owner_id", "input_id", "scope_id"):
            _nonempty(getattr(self, name), f"core.binding.{name}")


FactKind: TypeAlias = Literal["finite_numeric"]


@dataclass(frozen=True, slots=True)
class Fact:
    """One source premise bound to its exact Query input and scope."""

    kind: FactKind
    binding: Binding
    subject_id: str
    version: str

    def __post_init__(self) -> None:
        if self.kind != "finite_numeric":
            reject(
                "a finite-numeric premise",
                str(self.kind),
                "Use the current source check.",
                "core.fact.kind",
            )
        _nonempty(self.subject_id, "core.fact.subject")
        _nonempty(self.version, "core.fact.version")


@dataclass(frozen=True, slots=True)
class Evidence:
    """A source check result attached to a Query signature."""

    fact: Fact
    basis: Literal["check"]
    source_id: str
    dependencies: tuple[Fact, ...] = ()

    def __post_init__(self) -> None:
        if type(self.fact) is not Fact or self.basis != "check":
            reject(
                "a checked finite-numeric fact",
                repr(self.fact),
                "Use the owning source check.",
                "core.evidence",
            )
        _nonempty(self.source_id, "core.evidence.source")
        if type(self.dependencies) is not tuple or any(
            type(fact) is not Fact for fact in self.dependencies
        ):
            reject(
                "bound fact dependencies",
                type(self.dependencies).__name__,
                "Bind each premise.",
                "core.evidence",
            )


CheckId: TypeAlias = Literal["source.finite_numeric@v1"]


@dataclass(frozen=True, slots=True)
class Obligation:
    """A finite-value source check due before consumption or publication."""

    fact: Fact
    check_id: CheckId
    before: Literal["consume", "publish"]

    def __post_init__(self) -> None:
        if (
            type(self.fact) is not Fact
            or self.check_id != "source.finite_numeric@v1"
            or self.fact.kind != "finite_numeric"
        ):
            reject(
                "the registered finite-numeric source check",
                f"{self.check_id}: {self.fact.kind}",
                "Use the check owned by this fact or reject the operation.",
                "core.obligation.check",
            )
        if self.before not in ("consume", "publish"):
            reject(
                "consume or publish", str(self.before), "Name the check stage.", "core.obligation"
            )
