"""Single registration and exact selection for connected algebra methods.

Legacy operator/Dataset registries are deliberately not consulted. Selection
does not execute, open data, or retry an alternative route.
"""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.core.model import (
    CoveragePart,
    ObservedQuantity,
    OriginalStatePart,
    RolledQuantity,
    Signature,
    StatisticalWeightPart,
    part_role,
)
from marivo.analysis.core.rules import RuleDerivation, RuleParameters
from marivo.analysis.methods.builtin import (
    admit,
    implementations,
    specialize_arity,
    specialize_numeric,
)
from marivo.analysis.methods.errors import reject
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    Implementation,
    QualificationKey,
    ScalarType,
    Unavailable,
)
from marivo.analysis.methods.semantics import (
    CONNECTED_METHODS,
    ContinuationRequirement,
    MethodKey,
    MethodSemantics,
    key_for_parameters,
)


@dataclass(frozen=True, slots=True)
class MethodRegistration:
    semantics: MethodSemantics
    implementations: tuple[Implementation, ...]
    missing: Unavailable

    def __post_init__(self) -> None:
        if (
            type(self.semantics) is not MethodSemantics
            or type(self.implementations) is not tuple
            or any(type(item) is not Implementation for item in self.implementations)
            or type(self.missing) is not Unavailable
        ):
            reject(
                "one complete immutable method registration",
                repr(self),
                "Register semantics and explicit qualification gaps.",
            )
        keys: set[QualificationKey] = set()
        for implementation in self.implementations:
            if implementation.key.method != self.semantics.key:
                reject(
                    str(self.semantics.key),
                    str(implementation.key.method),
                    "Attach the implementation to its semantic owner.",
                )
            if implementation.key in keys:
                reject(
                    "one implementation per exact qualification key",
                    repr(implementation.key),
                    "Remove duplicate or conflicting implementations.",
                )
            keys.add(implementation.key)
            rule = self.semantics.rule
            input_count = len(implementation.key.input_domains)
            if rule == "attribution@v1" and input_count != 2:
                reject(
                    "two ordered attribution inputs",
                    repr(implementation.key),
                    "Bind original target and expanded basis.",
                )
            if self.semantics.key.name in ("group.attach", "group.complete"):
                if input_count != 2:
                    reject(
                        "two ordered classification inputs",
                        repr(implementation.key),
                        "Bind receiver and category.",
                    )
            elif rule == "history_view@v1":
                if input_count not in (1, 2):
                    reject(
                        "one History and optional checkpoint axes",
                        repr(implementation.key),
                        "Use the exact registered History shape.",
                    )
            elif rule == "history_replay@v1":
                if input_count != 2:
                    reject(
                        "two ordered History inputs",
                        repr(implementation.key),
                        "Bind members and occurrences.",
                    )
            elif rule == "map_correspond@v1" or self.semantics.key.name == "parts_transport":
                if input_count not in (1, 2):
                    reject(
                        "one or two ordered correspondence inputs",
                        repr(implementation.key),
                        "Declare a shape admitted by the closed correspondence rule.",
                    )
            elif (
                rule in ("bind_project@v1", "parts_transport@v1")
                and input_count != 1
                and not (
                    input_count == 2
                    and self.semantics.key.name
                    in ("metric.count", "metric.observe", "metric.sum_zero", "metric.mean")
                )
            ):
                reject(
                    "one ordered input for this method",
                    repr(implementation.key),
                    "Declare the rule's exact input arity.",
                )
            if rule == "occurrence_combine@v1" and input_count < 2:
                reject(
                    "two or more ordered signed inputs",
                    repr(implementation.key),
                    "Declare one precise value type and domain per occurrence.",
                )
            if rule in ("cell_derive@v1", "row_state@v1", "original_reduce@v1"):
                arity = (
                    2
                    if rule == "cell_derive@v1" or self.semantics.key.name == "metric.ratio"
                    else 1
                )
                if input_count != arity:
                    reject(
                        f"{arity} ordered single-quantity inputs",
                        repr(implementation.key),
                        "Declare one precise value type and domain per input quantity.",
                    )
                if self.semantics.key.name not in ("row.count", "row.count_defined") and any(
                    isinstance(item, ScalarType) and item.name not in ("int64", "float64")
                    for item in implementation.key.input_types
                ):
                    reject(
                        "numeric inputs admitted by this method",
                        repr(implementation.key.input_types),
                        "Use exact numeric types; a known scalar type is not sufficient qualification.",
                    )
                method = self.semantics.key.name
                input_types = implementation.key.input_types
                if method in ("row.count", "row.count_defined") or (
                    method == "row.mean"
                    and all(isinstance(item, DurationType) for item in input_types)
                ):
                    precision = "checked_int64"
                elif any(
                    item.name == "float64" for item in input_types if isinstance(item, ScalarType)
                ) or (
                    method
                    in (
                        "row.mean",
                        "row.weighted_mean",
                        "cell.ratio",
                        "cell.relative_change",
                        "metric.ratio",
                        "state_rollup.weighted_mean",
                        "state_rollup.mean",
                    )
                    and not any(isinstance(item, DecimalType) for item in input_types)
                ):
                    precision = "finite_float64"
                elif any(isinstance(item, DecimalType) for item in input_types):
                    precision = "exact"
                else:
                    precision = "checked_int64"
                if implementation.precision != precision:
                    reject(
                        f"{precision} precision for {self.semantics.key}",
                        implementation.precision,
                        "Declare precision for this method's result and ordered input types.",
                    )
            if rule == "association_score@v1" and (
                not 1 <= input_count <= 16
                or any(
                    domain not in ("entity", "group") for domain in implementation.key.input_domains
                )
                or implementation.precision
                not in ("finite_float64", "certified_statistical", "exact")
            ):
                reject(
                    "exact typed association inputs",
                    repr(implementation.key),
                    "Qualify every ordered endpoint and retained part.",
                )
            if not {*self.semantics.required_parts, *self.semantics.output_parts}.issubset(
                implementation.parts
            ):
                reject(
                    "implementation of every required semantic part",
                    repr(implementation.parts),
                    "Retain the method's required parts.",
                )
            if not set(self.semantics.required_checks).issubset(implementation.checks):
                reject(
                    "implementation of every semantic check",
                    repr(implementation.checks),
                    "Qualify the method's required checkers.",
                )


@dataclass(frozen=True, slots=True)
class SelectedImplementation:
    """A single static choice plus still-pending bound semantic obligations."""

    implementation: Implementation
    derivation: RuleDerivation


@dataclass(frozen=True, slots=True)
class MethodRegistry:
    registrations: tuple[MethodRegistration, ...]

    def __post_init__(self) -> None:
        if type(self.registrations) is not tuple or any(
            type(item) is not MethodRegistration for item in self.registrations
        ):
            reject(
                "immutable complete registrations",
                repr(self.registrations),
                "Assemble typed registrations before use.",
            )
        keys = tuple(item.semantics.key for item in self.registrations)
        if len(set(keys)) != len(keys):
            reject(
                "one semantic owner per method version",
                repr(keys),
                "Remove duplicate semantic owners, even when their declarations agree.",
            )

    def lookup(self, key: MethodKey) -> MethodRegistration:
        if type(key) is MethodKey:
            for registration in self.registrations:
                if registration.semantics.key == key:
                    return registration
        available = ", ".join(str(item.semantics.key) for item in self.registrations)
        reject(
            "a registered method version",
            repr(key),
            f"Use a connected method from this registry: {available or '(none)'}.",
        )

    def derive(self, inputs: tuple[Signature, ...], params: RuleParameters) -> RuleDerivation:
        return self.lookup(key_for_parameters(params)).semantics.derive(inputs, params)

    def select(
        self,
        key: QualificationKey,
        inputs: tuple[Signature, ...],
        params: RuleParameters,
    ) -> SelectedImplementation:
        """Select exactly the requested route; static evidence never discharges Pre."""
        if type(key) is not QualificationKey:
            reject("an exact qualification key", repr(key), "Bind the complete physical shape.")
        registration = self.lookup(key.method)
        derivation = registration.semantics.derive(inputs, params)
        if tuple(item.domain.kind for item in inputs) != key.input_domains:
            reject(
                "qualification for these ordered input domains",
                repr(key.input_domains),
                "Request the exact invocation shape.",
            )
        for implementation in registration.implementations:
            implementation = specialize_numeric(specialize_arity(implementation, len(inputs)), key)
            if implementation.key != key:
                continue
            status = implementation.qualification
            if isinstance(status, Unavailable):
                reject(
                    f"qualified implementation for {key.method}",
                    f"{status.status}: {status.reason}",
                    status.recovery,
                )
            needed_checks = {item.check_id for item in derivation.obligations}
            needed_parts = set(derivation.required_parts) | {
                part_role(item) for item in derivation.output.parts
            }
            if not needed_checks.issubset(implementation.checks) or not needed_parts.issubset(
                implementation.parts
            ):
                reject(
                    "all bound checks and required/output parts",
                    repr((implementation.checks, implementation.parts)),
                    "Qualify every obligation and part for this exact invocation.",
                )
            if status.consumer_id in ("analysis.compiler.graph_lowering", "analysis.methods.local"):
                admit(implementation, params)
            return SelectedImplementation(implementation, derivation)
        gap = registration.missing
        reject(
            f"qualified exact key for {key.method}",
            f"{gap.status}: {key!r}; {gap.reason}",
            gap.recovery,
        )

    def continuations(self, output: Signature) -> tuple[ContinuationRequirement, ...]:
        """Return conditional private construction K; successor derivation is mandatory."""
        candidates = [ContinuationRequirement(MethodKey("parts_transport"), ())]
        registered = {item.semantics.key: item.semantics for item in self.registrations}
        if output.quantity is not None:
            candidates.extend(
                ContinuationRequirement(MethodKey(name), ())
                for name in (
                    "row.sum",
                    "row.mean",
                    "row.min",
                    "row.max",
                    "row.count",
                    "row.count_defined",
                )
            )
            if any(
                isinstance(item, StatisticalWeightPart) and item.binding == output.domain.binding
                for item in output.parts
            ):
                candidates.append(
                    ContinuationRequirement(MethodKey("row.weighted_mean"), ("statistical_weight",))
                )
            state = next(
                (item for item in output.parts if isinstance(item, OriginalStatePart)), None
            )
            coverage = next((item for item in output.parts if isinstance(item, CoveragePart)), None)
            for name in (
                "state_rollup.min",
                "state_rollup.max",
                "state_rollup",
                "state_rollup.count",
                "state_rollup.sum_zero",
                "state_rollup.ratio",
                "state_rollup.weighted_mean",
                "state_rollup.mean",
                "state_rollup.fold",
                "state_rollup.linear",
            ):
                key = MethodKey(name)
                original = registered.get(key)
                if (
                    isinstance(output.quantity, (ObservedQuantity, RolledQuantity))
                    and original is not None
                    and state is not None
                    and state.method_version
                    == output.quantity.method_version
                    == original.original_state_method
                    and state.quantity_id == output.quantity.definition_id
                    and state.contribution_id == output.quantity.contribution_id
                    and (
                        name == "state_rollup.linear"
                        or state.components == original.state_components
                    )
                    and state.version == "v1"
                    and coverage is not None
                    and coverage.quantity_id == output.quantity.definition_id
                    and coverage.binding == output.domain.binding
                    and coverage.scope_id == output.domain.binding.scope_id
                ):
                    candidates.append(ContinuationRequirement(key, ("original_state", "coverage")))
        return tuple(item for item in candidates if item.method in registered)


REGISTRY = MethodRegistry(
    tuple(
        MethodRegistration(
            method,
            implementations(method.key),
            Unavailable(
                "blocked",
                "No connected consumer is qualified for this exact method/type/shape/route key.",
                "Connect R3.3/R3.4 and the owning R4-R8 consumer, then qualify this exact physical key.",
            ),
        )
        for method in CONNECTED_METHODS
    )
)
