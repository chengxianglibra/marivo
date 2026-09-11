"""Explicit private assembly of native owner inputs; never installs public Help."""

from __future__ import annotations

import inspect
from collections import Counter
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NoReturn

from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    Descriptor,
    DisclosureProvider,
    FamilyInput,
    NavigationInput,
    TypeInput,
    invalid,
    public_fields,
    public_methods,
    variant_fields,
)
from marivo.analysis._capabilities.dataset_navigation import navigation
from marivo.analysis._capabilities.model import ReadCapability
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.errors import AnalysisError
from marivo.introspection.live.resolve import LiveSurface, ResolvedLiveTarget, resolve_live_target


@dataclass(frozen=True, slots=True)
class DatasetDisclosureRegistry:
    providers: tuple[DisclosureProvider, ...]
    families: DatasetFamilyRegistry
    descriptors: tuple[Descriptor, ...]

    @property
    def retained_catalog_inputs(self) -> tuple[ReadCapability, ...]:
        """Reuse the unchanged native catalog owner inputs during later assembly.

        Catalog reads already have native descriptors. They are intentionally
        neither copied into Dataset providers nor reconstructed from prose.
        Their public activation and semantic handoff remain with Slice 8.
        """
        from marivo.analysis._capabilities.registry import REGISTRY

        return tuple(
            d
            for d in REGISTRY.descriptors
            if isinstance(d, ReadCapability) and d.id.startswith("catalog.")
        )

    @property
    def surface(self) -> Literal["analysis"]:
        return "analysis"

    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(d.canonical_id for d in self.descriptors)

    def discovery_ids(self) -> tuple[str, ...]:
        return self.canonical_ids()

    def by_canonical_id(self, canonical_id: str) -> Descriptor:
        # KeyError is the neutral LiveSurface resolver's lookup-miss protocol.
        # resolve() adapts a final miss to the owning structured error below.
        for descriptor in self.descriptors:
            if descriptor.canonical_id == canonical_id:
                return descriptor
        raise KeyError(canonical_id)

    def by_callable(self, value: object) -> Descriptor:
        receiver = getattr(value, "__self__", None)
        function = getattr(value, "__func__", value)
        matches = [
            d
            for d in self.descriptors
            if isinstance(d, CallableInput)
            and any(
                b.implementation is function
                and (receiver is None or b.receiver is None or isinstance(receiver, b.receiver))
                for b in d.bindings
            )
        ]
        if isinstance(receiver, Dataset) and len(matches) > 1:
            shape = receiver.row_contract.shape_id
            scopes = {
                d.canonical_id: frozenset(
                    s
                    for f in self.families.registrations
                    for c in f.consumers
                    if c.id in d.registration_ids
                    for s in c.accepted_shape_ids
                )
                for d in matches
            }
            admitted = [d for d in matches if shape in scopes[d.canonical_id]]
            # An exact receiver selects the most specific registered shape scope.
            # Equal or overlapping scopes remain ambiguous; ordering never wins.
            matches = [
                d
                for d in admitted
                if not any(
                    scopes[other.canonical_id] < scopes[d.canonical_id] for other in admitted
                )
            ]
        if not matches:
            raise KeyError("unregistered callable")
        if len(matches) > 1:
            defaults = [d for d in matches if d.unbound_default]
            if receiver is None and len(defaults) == 1:
                return defaults[0]
            raise invalid("one exact callable owner", "ambiguous callable identity")
        return matches[0]

    def resolve(self, target: object) -> ResolvedLiveTarget[Descriptor]:
        if isinstance(target, str) and target == "":
            return ResolvedLiveTarget(
                kind="descriptor",
                surface="analysis",
                canonical_id="",
                descriptor=self.by_canonical_id(""),
            )
        types = {
            b.implementation: d.canonical_id
            for d in self.descriptors
            if isinstance(d, (TypeInput, FamilyInput))
            for b in d.bindings
        }
        types.update(
            {
                e.implementation: e.target
                for p in self.providers
                for e in p.exports
                if isinstance(e.implementation, type)
                and isinstance(self.by_canonical_id(e.target), NavigationInput)
            }
        )

        def enrich(value: object) -> ResolvedLiveTarget[Descriptor] | None:
            if isinstance(value, type) and value in types:
                return ResolvedLiveTarget(
                    kind="type_contract", surface="analysis", type_name=types[value]
                )
            for provider in self.providers:
                for export in provider.exports:
                    if export.implementation is value and isinstance(
                        self.by_canonical_id(export.target), NavigationInput
                    ):
                        return ResolvedLiveTarget(
                            kind="descriptor",
                            surface="analysis",
                            canonical_id=export.target,
                            descriptor=self.by_canonical_id(export.target),
                        )
            return None

        for descriptor in self.descriptors:
            if isinstance(descriptor, TypeInput):
                for variant in descriptor.variants:
                    types[variant.implementation] = descriptor.canonical_id

        def help_target_error(value: object, suggestions: tuple[str, ...]) -> NoReturn:
            raise DatasetRegistrationError(
                expected="a registered private Dataset Help string, callable, type or instance",
                received=value if isinstance(value, str) else type(value).__name__,
                repair="Resolve a private discovery target: " + ", ".join(suggestions) + ".",
                location="dataset.disclosure.resolve",
            )

        root = self.by_canonical_id("")
        assert isinstance(root, NavigationInput)
        surface = LiveSurface(
            self,
            MappingProxyType(types),
            MappingProxyType({}),
            AnalysisError,
            enrich=enrich,
            default_suggestions=root.members,
            help_target_error=help_target_error,
        )
        if isinstance(target, str) and target.startswith("analysis."):
            target = target[len("analysis.") :]
        return resolve_live_target(target, surface)

    def validate(self) -> None:
        if tuple(sorted(p.owner for p in self.providers)) != (
            "core",
            "domains",
            "observation",
            "operators",
            "runtime",
        ):
            raise invalid("all five native owners exactly once", "missing or duplicate provider")
        ids = self.canonical_ids()
        if len(set(ids)) != len(ids):
            raise invalid("unique canonical Help targets", "duplicate target")
        exports = tuple(e for p in self.providers for e in p.exports)
        if len({e.name for e in exports}) != len(exports):
            raise invalid("unique export bindings", "duplicate export")
        for export in exports:
            if export.target not in ids:
                raise invalid("a registered export Help target", export.target)
            descriptor = self.by_canonical_id(export.target)
            if isinstance(descriptor, (TypeInput, FamilyInput)) and not any(
                b.implementation is export.implementation for b in descriptor.bindings
            ):
                raise invalid("exact native type export binding", export.name)
            if isinstance(descriptor, CallableInput) and not any(
                b.implementation is export.implementation for b in descriptor.bindings
            ):
                raise invalid("exact native callable export binding", export.name)
        registrations = {
            c.id for f in self.families.registrations for c in f.consumers if c.discoverable
        }
        links: Counter[str] = Counter()
        memberships: Counter[str] = Counter()
        family_ids: list[str] = []
        for descriptor in self.descriptors:
            if not descriptor.summary.strip():
                raise invalid("owned nonempty disclosure content", descriptor.canonical_id)
            targets: tuple[str, ...] = ()
            if isinstance(descriptor, CallableInput):
                if not descriptor.bindings or not all(
                    (
                        descriptor.output,
                        descriptor.constraints,
                        descriptor.effects,
                        descriptor.failures,
                        descriptor.example.code,
                        descriptor.example.outcome,
                    )
                ):
                    raise invalid(
                        "complete callable inputs and executable example", descriptor.canonical_id
                    )
                names = tuple(p.name for p in descriptor.parameters)
                if len(set(names)) != len(names) or any(
                    not p.acquisition for p in descriptor.parameters
                ):
                    raise invalid("unique parameter acquisition contracts", descriptor.canonical_id)
                for binding in descriptor.bindings:
                    if (
                        not callable(binding.implementation)
                        or inspect.signature(binding.implementation) != binding.signature
                    ):
                        raise invalid(
                            "unchanged reflected callable signature", descriptor.canonical_id
                        )
                    if binding.receiver is not None:
                        name = getattr(binding.implementation, "__name__", "")
                        actual: object = inspect.getattr_static(binding.receiver, name, None)
                        if actual is not binding.implementation:
                            raise invalid(
                                "the exact receiver-owned method", descriptor.canonical_id
                            )
                    expected = tuple(
                        n for n in binding.signature.parameters if n not in ("self", "cls")
                    )
                    if names != expected:
                        raise invalid(
                            "parameter names matching the reflected signature",
                            descriptor.canonical_id + ": " + repr(expected),
                        )
                from marivo.analysis.observation.contracts import producer_contract

                for registration_id in descriptor.registration_ids:
                    producer_contract(registration_id)
                links.update(descriptor.registration_ids)
                targets = tuple(t for p in descriptor.parameters for t in p.targets)
            elif isinstance(descriptor, (TypeInput, FamilyInput)):
                for type_binding in descriptor.bindings:
                    if type_binding.fields != public_fields(
                        type_binding.implementation
                    ) or type_binding.methods != public_methods(type_binding.implementation):
                        raise invalid(
                            "complete current fields and methods", descriptor.canonical_id
                        )
                if isinstance(descriptor, FamilyInput):
                    f = descriptor.registration
                    if self.families.get(f.family_id) is not f or tuple(
                        b.implementation for b in descriptor.bindings
                    ) != (f.logical_type, f.materialized_type):
                        raise invalid(
                            "the exact production family registration", descriptor.canonical_id
                        )
                    family_ids.append(f.family_id)
                    if not descriptor.variants:
                        raise invalid("complete family semantics variants", f.family_id)
                    for variant in descriptor.variants:
                        if variant.fields != variant_fields(variant.implementation):
                            raise invalid("complete current semantics fields", f.family_id)
                else:
                    for variant_input in descriptor.variants:
                        if variant_input.fields != variant_fields(variant_input.implementation):
                            raise invalid(
                                "complete current sealed value fields", descriptor.canonical_id
                            )
                    targets = descriptor.producers + descriptor.consumers
            else:
                targets = descriptor.members
                memberships.update(targets)
            for target in targets:
                if target not in ids:
                    raise invalid("independently resolvable linked target", target)
        if set(family_ids) != {f.family_id for f in self.families.registrations} or len(
            family_ids
        ) != len(set(family_ids)):
            raise invalid("one disclosure per registered family", "family coverage mismatch")
        if registrations - links.keys() or any(links[k] != 1 for k in registrations):
            raise invalid(
                "one capability link per discoverable consumer",
                repr(sorted(registrations - links.keys())),
            )
        for descriptor in self.descriptors:
            if isinstance(descriptor, CallableInput):
                for binding in descriptor.bindings:
                    self.by_callable(binding.implementation)
        if any(memberships[target] != 1 for target in ids if target):
            raise invalid(
                "one discovery group per target",
                repr([t for t in ids if t and memberships[t] != 1]),
            )
        reached: set[str] = set()

        def visit(target: str) -> None:
            if target in reached:
                return
            reached.add(target)
            node = self.by_canonical_id(target)
            if isinstance(node, NavigationInput):
                for child in node.members:
                    visit(child)

        visit("")
        if reached != set(ids):
            raise invalid("root-reachable native topology", repr(sorted(set(ids) - reached)))


def assemble(
    families: DatasetFamilyRegistry,
    providers: tuple[DisclosureProvider, ...],
) -> DatasetDisclosureRegistry:
    result = DatasetDisclosureRegistry(
        providers,
        families,
        tuple(d for p in providers for d in p.descriptors) + navigation(providers),
    )
    result.validate()
    return result


def prepare() -> DatasetDisclosureRegistry:
    """Build the production private inputs explicitly, without activation or I/O."""
    from marivo.analysis.datasets import _disclosure as core
    from marivo.analysis.domains import _disclosure as domains
    from marivo.analysis.observation import _disclosure as observation
    from marivo.analysis.observation.contracts import make_family_registry, make_ids
    from marivo.analysis.operators import _disclosure as operators
    from marivo.analysis.session import _disclosure as runtime
    from marivo.analysis.session._lazy_sources import LazySources

    families = make_family_registry(make_ids(()))
    return assemble(
        families,
        (
            core.provider(families),
            observation.provider(families, source_receiver=LazySources),
            operators.provider(families),
            domains.provider(families),
            runtime.provider(families),
        ),
    )
