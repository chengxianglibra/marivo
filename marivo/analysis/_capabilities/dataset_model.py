"""Native, private Dataset disclosure inputs; independent of the eager vocabulary."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from typing import Literal

from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.datasets.registry import DatasetFamilyRegistration


def invalid(expected: str, received: str) -> DatasetRegistrationError:
    return DatasetRegistrationError(
        expected=expected,
        received=received,
        repair="Repair the owning private disclosure provider and reassemble before public activation.",
        location="dataset.disclosure",
    )


@dataclass(frozen=True, slots=True)
class ParameterInput:
    name: str
    acquisition: str
    targets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExampleInput:
    code: str
    requires: tuple[str, ...]
    result: str
    outcome: str
    runtime: bool = False


@dataclass(frozen=True, slots=True)
class CallableBinding:
    implementation: object
    signature: inspect.Signature
    receiver: type[object] | None = None

    @property
    def path(self) -> str:
        value = self.implementation
        return f"{getattr(value, '__module__', '')}.{getattr(value, '__qualname__', '')}"


def bind(value: object, receiver: type[object] | None = None) -> CallableBinding:
    if not callable(value):
        raise invalid("an executable callable", "non-callable binding")
    return CallableBinding(value, inspect.signature(value), receiver)


@dataclass(frozen=True, slots=True)
class CallableInput:
    canonical_id: str
    public_entrypoint: str
    summary: str
    bindings: tuple[CallableBinding, ...]
    parameters: tuple[ParameterInput, ...]
    output: str
    constraints: tuple[str, ...]
    effects: str
    failures: tuple[str, ...]
    example: ExampleInput
    registration_ids: tuple[str, ...] = ()
    discovery_group: Literal["filters", "session.namespace"] | None = None
    unbound_default: bool = False
    kind: Literal["callable"] = field(default="callable", init=False)

    @property
    def callable_path(self) -> str:
        return self.bindings[0].path


def operation(
    target: str,
    entrypoint: str,
    value: object,
    *,
    summary: str,
    parameters: tuple[ParameterInput, ...],
    output: str,
    constraints: tuple[str, ...],
    effects: str,
    failures: tuple[str, ...],
    example: ExampleInput,
    registration_ids: tuple[str, ...] = (),
    bindings: tuple[CallableBinding, ...] = (),
    discovery_group: Literal["filters", "session.namespace"] | None = None,
    unbound_default: bool = False,
) -> CallableInput:
    return CallableInput(
        target,
        entrypoint,
        summary,
        bindings or (bind(value),),
        parameters,
        output,
        constraints,
        effects,
        failures,
        example,
        registration_ids,
        discovery_group,
        unbound_default,
    )


@dataclass(frozen=True, slots=True)
class FieldInput:
    name: str
    annotation: str


def public_fields(value: type[object]) -> tuple[FieldInput, ...]:
    result: dict[str, str] = {}
    if is_dataclass(value):
        result.update((f.name, str(f.type)) for f in fields(value) if not f.name.startswith("_"))
    for base in reversed(value.__mro__):
        if not base.__module__.startswith("marivo."):
            continue
        result.update(
            (n, str(a))
            for n, a in getattr(base, "__annotations__", {}).items()
            if not n.startswith("_") and n != "model_config" and "ClassVar" not in str(a)
        )
        for name, member in vars(base).items():
            if isinstance(member, property) and member.fget and not name.startswith("_"):
                annotation = inspect.signature(member.fget).return_annotation
                result[name] = str(annotation)
    return tuple(FieldInput(name, annotation) for name, annotation in sorted(result.items()))


def public_methods(value: type[object]) -> tuple[str, ...]:
    names: set[str] = set()
    for base in value.__mro__:
        if base.__module__.startswith("marivo."):
            names.update(
                name
                for name, member in vars(base).items()
                if not name.startswith("_") and inspect.isfunction(member)
            )
    return tuple(sorted(names))


@dataclass(frozen=True, slots=True)
class TypeBinding:
    implementation: type[object]
    fields: tuple[FieldInput, ...]
    methods: tuple[str, ...]


def type_binding(value: type[object]) -> TypeBinding:
    return TypeBinding(value, public_fields(value), public_methods(value))


@dataclass(frozen=True, slots=True)
class VariantInput:
    """Complete owner row-semantics fields; implementation classes stay private."""

    implementation: type[object]
    fields: tuple[FieldInput, ...]


def variant_fields(value: type[object]) -> tuple[FieldInput, ...]:
    if is_dataclass(value):
        return tuple(
            sorted(
                (
                    FieldInput(f.name, str(f.type))
                    for f in fields(value)
                    if not f.name.startswith("_")
                ),
                key=lambda f: f.name,
            )
        )
    return public_fields(value)


def variant(value: type[object]) -> VariantInput:
    return VariantInput(value, variant_fields(value))


@dataclass(frozen=True, slots=True)
class TypeInput:
    canonical_id: str
    summary: str
    bindings: tuple[TypeBinding, ...]
    acquisition: str
    producers: tuple[str, ...]
    consumers: tuple[str, ...]
    constraints: tuple[str, ...]
    variants: tuple[VariantInput, ...] = ()
    kind: Literal["type"] = field(default="type", init=False)
    public_entrypoint: None = field(default=None, init=False)
    callable_path: None = field(default=None, init=False)


@dataclass(frozen=True, slots=True)
class FamilyInput:
    canonical_id: str
    summary: str
    registration: DatasetFamilyRegistration
    bindings: tuple[TypeBinding, ...]
    variants: tuple[VariantInput, ...]
    acquisition: str
    constraints: tuple[str, ...]
    kind: Literal["family"] = field(default="family", init=False)
    public_entrypoint: None = field(default=None, init=False)
    callable_path: None = field(default=None, init=False)


@dataclass(frozen=True, slots=True)
class NavigationInput:
    canonical_id: str
    summary: str
    members: tuple[str, ...]
    render_class: Literal["root", "decision_hub", "navigation"] = "navigation"
    kind: Literal["navigation"] = field(default="navigation", init=False)
    public_entrypoint: None = field(default=None, init=False)
    callable_path: None = field(default=None, init=False)


Descriptor = CallableInput | TypeInput | FamilyInput | NavigationInput


def with_sealed_variants(
    descriptors: Sequence[Descriptor], variants: Mapping[str, tuple[type[object], ...]]
) -> tuple[Descriptor, ...]:
    """Attach owner-declared sealed variants to their exact type descriptors."""
    type_ids = {d.canonical_id for d in descriptors if isinstance(d, TypeInput)}
    if variants.keys() - type_ids:
        raise invalid(
            "registered type owners for sealed variants", repr(sorted(variants.keys() - type_ids))
        )
    return tuple(
        replace(item, variants=tuple(variant(t) for t in variants[item.canonical_id]))
        if isinstance(item, TypeInput) and item.canonical_id in variants
        else item
        for item in descriptors
    )


@dataclass(frozen=True, slots=True)
class ExportInput:
    name: str
    implementation: object
    target: str


@dataclass(frozen=True, slots=True)
class DisclosureProvider:
    owner: str
    descriptors: tuple[Descriptor, ...]
    exports: tuple[ExportInput, ...]


def value_type(
    target: str,
    value: type[object],
    *,
    summary: str,
    acquisition: str,
    producers: tuple[str, ...] = (),
    consumers: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (
        "Use the owning producer; type discovery does not open a constructor.",
    ),
) -> TypeInput:
    return TypeInput(
        target, summary, (type_binding(value),), acquisition, producers, consumers, constraints
    )


def family(
    target: str,
    registration: DatasetFamilyRegistration,
    *,
    summary: str,
    variants: tuple[type[object], ...],
    acquisition: str,
    constraints: tuple[str, ...],
) -> FamilyInput:
    return FamilyInput(
        target,
        summary,
        registration,
        (type_binding(registration.logical_type), type_binding(registration.materialized_type)),
        tuple(variant(v) for v in variants),
        acquisition,
        constraints,
    )


CONSTRUCTION_EFFECT = (
    "Construct an immutable Logical Dataset or value; no query, Run, or Artifact publication."
)
CONSTRUCTION_FAILURES = (
    "DatasetConstructionError: expected an admitted shape and concrete inputs; inspect dataset.contract() and repair the reported parameter.",
    "DatasetOwnershipError: expected inputs and selectors from the same Session; reconstruct them in the receiving Session.",
)
