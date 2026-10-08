"""Explicit private assembly of native owner inputs; never installs public Help."""

from __future__ import annotations

import ast
import inspect
import re
from collections import Counter
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, NoReturn

from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    Descriptor,
    DisclosureProvider,
    NavigationInput,
    TypeInput,
    invalid,
    public_fields,
    public_methods,
    variant_fields,
)
from marivo.analysis._capabilities.dataset_navigation import navigation
from marivo.analysis._capabilities.model import ReadCapability
from marivo.analysis.errors import AnalysisError
from marivo.introspection.live.resolve import (
    LiveSuggestionIndex,
    LiveSurface,
    ResolvedLiveTarget,
    build_suggestion_index,
    resolve_live_target,
)


def _unwrapped(value: object) -> object:
    """Compare native ownership beneath transparent telemetry wrappers."""
    return inspect.unwrap(value) if callable(value) else value


@dataclass(frozen=True, slots=True)
class DatasetDisclosureRegistry:
    providers: tuple[DisclosureProvider, ...]
    descriptors: tuple[Descriptor, ...]
    _suggestions: LiveSuggestionIndex = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_suggestions", build_suggestion_index(self))

    @property
    def retained_catalog_inputs(self) -> tuple[ReadCapability, ...]:
        """Reuse the unchanged native catalog owner inputs during later assembly.

        Catalog reads already have native descriptors. They are intentionally
        neither copied into Dataset providers nor reconstructed from prose.
        Their public activation and semantic handoff remain with Slice 8.
        """
        from marivo.analysis._capabilities.catalog_inputs import CATALOG_INPUTS

        return CATALOG_INPUTS

    @property
    def surface(self) -> Literal["analysis"]:
        return "analysis"

    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(d.canonical_id for d in self.descriptors)

    def discovery_ids(self) -> tuple[str, ...]:
        """Return intentional discovery members, not every resolvable leaf."""
        members = {m for d in self.descriptors if isinstance(d, NavigationInput) for m in d.members}
        return tuple(d.canonical_id for d in self.descriptors if d.canonical_id in members)

    def callable_routes(self, descriptor: CallableInput) -> tuple[str, ...]:
        """Link prerequisites and exact exported return types from native facts."""
        names = set(re.findall(r"\b[A-Z][A-Za-z0-9_]+\b", descriptor.output))
        outputs = tuple(
            dict.fromkeys(e.target for p in self.providers for e in p.exports if e.name in names)
        )
        outputs = tuple(
            dict.fromkeys(
                (
                    *outputs,
                    *(
                        d.canonical_id
                        for d in self.descriptors
                        if isinstance(d, TypeInput)
                        and any(b.implementation.__name__ in names for b in d.bindings)
                    ),
                )
            )
        )
        return tuple(
            dict.fromkeys(
                (
                    *descriptor.related,
                    *(t for p in descriptor.parameters for t in p.targets),
                    *outputs,
                )
            )
        )

    def by_canonical_id(self, canonical_id: str) -> Descriptor:
        # KeyError is the neutral LiveSurface resolver's lookup-miss protocol.
        # resolve() adapts a final miss to the owning structured error below.
        for descriptor in self.descriptors:
            if descriptor.canonical_id == canonical_id or (
                isinstance(descriptor, ReadCapability) and descriptor.help_target == canonical_id
            ):
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
                _unwrapped(b.implementation) is _unwrapped(function)
                and (receiver is None or b.receiver is None or isinstance(receiver, b.receiver))
                for b in d.bindings
            )
        ]
        if not matches:
            from marivo.introspection.live.reflect import callable_identity

            path = callable_identity(value)
            for descriptor in self.retained_catalog_inputs:
                if descriptor.callable_path == path:
                    return descriptor
            raise KeyError("unregistered callable")
        if len(matches) > 1:
            defaults = [d for d in matches if d.unbound_default]
            if receiver is None and len(defaults) == 1:
                return defaults[0]
            raise invalid("one exact callable owner", "ambiguous callable identity")
        return matches[0]

    def live_surface(self) -> LiveSurface[Descriptor]:
        """Bind this exact owner registry to the neutral public resolver."""
        types = {
            b.implementation: d.canonical_id
            for d in self.descriptors
            if isinstance(d, TypeInput)
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
            if isinstance(value, AnalysisError):
                return ResolvedLiveTarget(
                    kind="error_briefing",
                    surface="analysis",
                    error_name=type(value).__name__,
                    original=value,
                )
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
            from marivo.analysis.errors import HelpTargetError

            raise HelpTargetError(target=value, suggestions=suggestions)

        root = self.by_canonical_id("")
        assert isinstance(root, NavigationInput)
        return LiveSurface(
            self,
            MappingProxyType(types),
            MappingProxyType({"AnalysisError": AnalysisError}),
            AnalysisError,
            enrich=enrich,
            suggestion_index=self._suggestions,
            default_suggestions=root.members,
            help_target_error=help_target_error,
        )

    def resolve(self, target: object) -> ResolvedLiveTarget[Descriptor]:
        if isinstance(target, str) and target == "":
            return ResolvedLiveTarget(
                kind="descriptor",
                surface="analysis",
                canonical_id="",
                descriptor=self.by_canonical_id(""),
            )
        if isinstance(target, str) and target.startswith("analysis."):
            target = target[len("analysis.") :]
        return resolve_live_target(target, self.live_surface())

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
            if isinstance(descriptor, TypeInput) and not any(
                b.implementation is export.implementation for b in descriptor.bindings
            ):
                raise invalid("exact native type export binding", export.name)
            if isinstance(descriptor, CallableInput) and not any(
                b.implementation is export.implementation for b in descriptor.bindings
            ):
                raise invalid("exact native callable export binding", export.name)
        memberships: Counter[str] = Counter()
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
                        descriptor.example.code,
                        descriptor.example.outcome,
                    )
                ):
                    raise invalid(
                        "complete callable inputs and executable example", descriptor.canonical_id
                    )
                if any(
                    not fact.strip() for fact in (*descriptor.constraints, *descriptor.failures)
                ):
                    raise invalid("nonempty owned constraints and repairs", descriptor.canonical_id)
                names = tuple(p.name for p in descriptor.parameters)
                if len(set(names)) != len(names) or any(
                    not p.acquisition.strip() for p in descriptor.parameters
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
                        if _unwrapped(actual) is not _unwrapped(binding.implementation):
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
                targets = self.callable_routes(descriptor)
                if descriptor.discovery_family is not None:
                    if descriptor.discovery_family not in ids:
                        raise invalid(
                            "a registered canonical callable family", descriptor.discovery_family
                        )
                    family = self.by_canonical_id(descriptor.discovery_family)
                    if (
                        not isinstance(family, CallableInput)
                        or family.discovery_group is None
                        or family.discovery_group != descriptor.discovery_group
                        or family.discovery_family != family.canonical_id
                    ):
                        raise invalid(
                            "one canonical callable family in the same discovery group",
                            descriptor.canonical_id,
                        )
                try:
                    tree = ast.parse(descriptor.example.code)
                except SyntaxError as error:
                    raise invalid(
                        "an executable Python example", descriptor.canonical_id
                    ) from error
                defined = {
                    n.id
                    for n in ast.walk(tree)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                }
                defined.update(
                    alias.asname or alias.name.split(".")[0]
                    for n in ast.walk(tree)
                    if isinstance(n, (ast.Import, ast.ImportFrom))
                    for alias in n.names
                )
                external = (
                    {
                        n.id
                        for n in ast.walk(tree)
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    }
                    - defined
                    - {"mv", "ms"}
                )
                if external != set(descriptor.example.requires):
                    raise invalid("exact external example inputs", descriptor.canonical_id)
            elif isinstance(descriptor, TypeInput):
                for type_binding in descriptor.bindings:
                    if type_binding.fields != public_fields(
                        type_binding.implementation
                    ) or type_binding.methods != public_methods(type_binding.implementation):
                        raise invalid(
                            "complete current fields and methods", descriptor.canonical_id
                        )
                for variant_input in descriptor.variants:
                    if variant_input.fields != variant_fields(variant_input.implementation):
                        raise invalid(
                            "complete current sealed value fields", descriptor.canonical_id
                        )
                targets = descriptor.producers + descriptor.consumers
            elif isinstance(descriptor, NavigationInput):
                targets = descriptor.members + descriptor.related
                memberships.update(descriptor.members)
            elif isinstance(descriptor, ReadCapability):
                targets = descriptor.related
            for target in targets:
                if target not in ids:
                    raise invalid("independently resolvable linked target", target)
        for descriptor in self.descriptors:
            if isinstance(descriptor, CallableInput):
                for binding in descriptor.bindings:
                    self.by_callable(binding.implementation)
        if any(count != 1 for count in memberships.values()):
            raise invalid(
                "one discovery group per target",
                repr([t for t, count in memberships.items() if count != 1]),
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
        expected_discovery = {"", *self.discovery_ids()}
        if reached != expected_discovery:
            raise invalid(
                "root-reachable discovery topology", repr(sorted(expected_discovery - reached))
            )
        for descriptor in self.descriptors:
            if (
                isinstance(descriptor, CallableInput)
                and descriptor.registration_ids
                and descriptor.canonical_id not in reached
            ):
                raise invalid("discoverable analytical capability", descriptor.canonical_id)


def assemble(providers: tuple[DisclosureProvider, ...]) -> DatasetDisclosureRegistry:
    result = DatasetDisclosureRegistry(
        providers,
        tuple(d for p in providers for d in p.descriptors) + navigation(providers),
    )
    result.validate()
    return result


def prepare() -> DatasetDisclosureRegistry:
    """Assemble the five current native disclosure owners without I/O."""
    from marivo.analysis.datasets import _disclosure as core
    from marivo.analysis.domains import _disclosure as domains
    from marivo.analysis.observation import _disclosure as observation
    from marivo.analysis.operators import _disclosure as operators
    from marivo.analysis.session import _disclosure as runtime

    return assemble(
        (
            core.provider(),
            observation.provider(),
            operators.provider(),
            domains.provider(),
            runtime.provider(),
        )
    )
