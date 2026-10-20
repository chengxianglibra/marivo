"""Native contracts for finite domains, membership allocation and window rules."""

from inspect import signature

from marivo._definition.grouping import _CoordinateGrouping, _MembershipGrouping
from marivo._definition.windows import (
    AllHistory,
    Since,
    ToDate,
    TrailingDuration,
    TrailingPeriods,
    _Window,
)
from marivo._grouping import Grouping
from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    ParameterInput,
    bind,
    operation,
    value_type,
    with_sealed_variants,
)
from marivo.analysis.dsl.population import (
    Population,
    PopulationPredicate,
    coordinates,
    identities,
    identity,
    in_population,
    membership,
    product,
)
from marivo.analysis.dsl.windows import (
    all_history,
    since,
    to_date,
    trailing_duration,
    trailing_periods,
)


def provider() -> DisclosureProvider:
    """Bind the exact constructor and receiver implementations, without I/O."""
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    constructors = (
        (
            identity,
            "Grouping",
            "Capture a complete identity grouping without selecting keys.",
            "result = mv.identity(entity)",
            ("entity",),
            "inputs.population",
        ),
        (
            coordinates,
            "Population",
            "Capture complete field coordinate tuples; None is a source Null category.",
            "result = mv.coordinates(by=(region,), keys=(('east',), ('west',)))",
            ("region",),
            "inputs.population",
        ),
        (
            identities,
            "Population",
            "Capture complete resolved-Entity primary-key tuples; keys do not prove source presence.",
            "result = mv.identities(entity, keys=(('C1',),))",
            ("entity",),
            "inputs.population",
        ),
        (
            membership,
            "Grouping",
            "Capture one correlated member-field tuple and explicit directed allocation paths.",
            "result = mv.membership(tag, via=((order_tags,),), unmatched='exclude')",
            ("tag", "order_tags"),
            "inputs.population",
        ),
        (
            product,
            "Population",
            "Form the Cartesian product of disjoint complete domains, preserving axis order.",
            "result = mv.product(regions, calendar)",
            ("regions", "calendar"),
            "inputs.population",
        ),
        (
            in_population,
            "PopulationPredicate",
            "Test complete coordinates against a captured domain; combine with &, | and ~.",
            "result = mv.in_population(population)",
            ("population",),
            "inputs.population",
        ),
        (
            all_history,
            "ObservationWindow",
            "Use available history before each observation endpoint, with no lower bound.",
            "result = mv.all_history()",
            (),
            "windows",
        ),
        (
            trailing_duration,
            "ObservationWindow",
            "Use a fixed elapsed duration before each observation endpoint.",
            "from datetime import timedelta\nresult = mv.trailing_duration(duration=timedelta(hours=24))",
            (),
            "windows",
        ),
        (
            trailing_periods,
            "ObservationWindow",
            "Use the current calendar period and count minus one preceding periods.",
            "result = mv.trailing_periods(count=7)",
            (),
            "windows",
        ),
        (
            since,
            "ObservationWindow",
            "Use an exact fixed start before each observation endpoint.",
            "result = mv.since(start=session.instant('2026-01-01'))",
            ("session",),
            "windows",
        ),
        (
            to_date,
            "ObservationWindow",
            "Reset at the civil or semantic period boundary owning each endpoint.",
            "result = mv.to_date(grain=mv.grain('month'))",
            (),
            "windows",
        ),
    )
    grouping_inputs = {
        "coordinates": "Pass resolved fields or field or membership Grouping values. Rejects identity and calendar groupings; use mv.identities for identity keys and product for calendar domains.",
        "membership": "Pass one resolved field or a nonempty tuple of resolved fields from one member Entity; put allocation paths in via, not in routed fields.",
        "Population.project": "Select or reorder existing coordinates using population.axes, matching fields or membership groupings. Membership allocation comes from the receiver; projection cannot read new fields.",
        "Population.classify": "Pass direct same-Entity fields, field groupings, the receiver's existing identity or membership classifications. Rejects calendar groupings and routed fields; requires one identity-axis Population.",
    }
    parameter_facts = {
        "target": "Pass a resolved Entity or a nonempty connected directed relationship path; its endpoint determines the Entity.",
        "keys": "Give complete typed tuples in declared axis or primary-key order; duplicate tuples collapse.",
        "entity": "Pass a resolved Entity from session.catalog.entities or a finalized scope; bare EntityRef inputs are rejected.",
        "via": "Pass a nonempty ordered tuple of declared relationships; membership takes a tuple of paths, one per contribution root.",
        "unmatched": "Choose exclude or null explicitly; null retains a source Null bucket.",
        "factors": "Pass at least two Populations or CalendarAxis values with disjoint axes and compatible owners.",
        "population": "Pass a literal domain or a Population captured in the same Session.",
        "duration": "Pass a positive datetime.timedelta; duration is elapsed time, not a civil period count.",
        "count": "Pass a positive integer; evaluating this rule requires a CalendarAxis.",
        "start": "Pass an aware datetime or an exact session.instant(...).",
        "grain": "Pass mv.grain(unit), ms.calendar_grain(calendar=ref, level=...), or a Catalog grain. Unbound semantic reset grains resolve in the Session's frozen certified scope; captured snapshots must match it.",
        "predicate": "Pass in_population(...) or its closed &, | and ~ combinations over existing axes.",
        "other": "Pass a Population with identical ordered bound axes and membership allocation descriptors.",
        "targets": "Pass the path target Entity's single identity-axis Population from the same Session.",
        "at": "Pass an exact InstantAnchor when required attribute/version reads need it; omit otherwise.",
    }
    for function, output, summary, code, requires, group in constructors:
        name = function.__name__
        descriptors.append(
            operation(
                name,
                f"mv.{name}",
                function,
                summary=summary,
                parameters=tuple(
                    ParameterInput(
                        name,
                        grouping_inputs[function.__name__]
                        if name == "by"
                        else parameter_facts[name],
                    )
                    for name in signature(function).parameters
                ),
                output=output,
                constraints=(
                    (
                        "Pass by=(grouping,) to observe. Identity grouping does not select keys; "
                        "mv.identities(entity, keys=...) constructs a complete domain. Observation "
                        "binds complete primary keys through single-valued routes and validates version anchors."
                    )
                    if function is identity
                    else "Construction captures immutable definitions without schema or business reads. Window evaluation requires an explicit observation period; complete output domains must cover observed coordinates.",
                ),
                effects="Capture an immutable domain, allocation descriptor, predicate or window rule.",
                failures=(
                    "AnalysisError or SemanticError: inspect expected/received input and the concrete repair.",
                ),
                example=ExampleInput(code, requires, "result", output),
                discovery_group=group,
            )
        )
        exports.append(ExportInput(name, function, name))
    methods: tuple[tuple[type[object], str, str, str, str, tuple[str, ...]], ...] = (
        (
            Population,
            "project",
            "Population",
            "Select and reorder existing coordinates, deduplicating complete projected tuples.",
            "result = population.project(by=(population.axes[0],))",
            ("population",),
        ),
        (
            Population,
            "classify",
            "Population",
            "Read one identity Entity's fields directly; retain membership allocation descriptors without traversing them.",
            "result = population.classify(by=(region,))",
            ("population", "region"),
        ),
        (
            Population,
            "union",
            "Population",
            "Union identical bound coordinate domains.",
            "result = population.union(other)",
            ("population", "other"),
        ),
        (
            Population,
            "intersection",
            "Population",
            "Intersect identical bound coordinate domains.",
            "result = population.intersection(other)",
            ("population", "other"),
        ),
        (
            Population,
            "difference",
            "Population",
            "Subtract matching complete coordinates.",
            "result = population.difference(other)",
            ("population", "other"),
        ),
        (
            Population,
            "where",
            "Population",
            "Filter only existing coordinates using closed Population membership logic.",
            "result = population.where(mv.in_population(allowed))",
            ("population", "allowed"),
        ),
        (
            Population,
            "image",
            "Population",
            "Map finite source identities to target identities; complete source foreign keys retain dangling references.",
            "result = population.image(via=(relationship,))",
            ("population", "relationship"),
        ),
        (
            Population,
            "preimage",
            "Population",
            "Select receiver identities reaching a target identity at least once; relation multiplicity does not duplicate identities.",
            "result = population.preimage(targets, via=(relationship,))",
            ("population", "targets", "relationship"),
        ),
        (
            Population,
            "show",
            "None",
            "Print bounded captured domain facts without source access.",
            "result = population.show()",
            ("population",),
        ),
        (
            Grouping,
            "show",
            "None",
            "Print bounded captured grouping facts.",
            "result = grouping.show()",
            ("grouping",),
        ),
        (
            PopulationPredicate,
            "show",
            "str",
            "Return the closed coordinate predicate kind without source access.",
            "result = predicate.show()",
            ("predicate",),
        ),
        (
            _Window,
            "show",
            "str",
            "Return source-free window rule parameters.",
            "result = rule.show()",
            ("rule",),
        ),
    )
    for owner, name, output, summary, code, method_requires in methods:
        target = f"{'ObservationWindow' if owner is _Window else owner.__name__}.{name}"
        function = getattr(owner, name)
        descriptors.append(
            operation(
                target,
                target,
                function,
                bindings=(bind(function, owner),),
                summary=summary,
                parameters=tuple(
                    ParameterInput(
                        name, grouping_inputs[target] if name == "by" else parameter_facts[name]
                    )
                    for name in signature(function).parameters
                    if name != "self"
                ),
                output=output,
                constraints=(
                    "One Session owner; complete typed keys and explicit version/path bindings remain authoritative.",
                ),
                effects="Return a new immutable definition or bounded source-free disclosure.",
                failures=("AnalysisError: follow its exact axis, owner, path or version repair.",),
                example=ExampleInput(code, method_requires, "result", output),
                discovery_group="windows" if owner is _Window else "population_tasks",
            )
        )
    for value, producers, consumers in (
        (
            Population,
            ("coordinates", "identities", "product", "session.members", "session.population_from"),
            ("session.frame", "Population.project", "Population.classify", "Population.where"),
        ),
        (Grouping, ("identity", "membership"), ("session.observe", "Grouping.show")),
        (PopulationPredicate, ("in_population",), ("Population.where", "PopulationPredicate.show")),
    ):
        descriptors.append(
            value_type(
                value.__name__,
                value,
                summary=(
                    "A field, complete identity, calendar or correlated membership grouping rule."
                    if value is Grouping
                    else f"Immutable {value.__name__} definition."
                ),
                acquisition=(
                    "Use identity(entity), membership(...), scope.by(grain), or population.axes."
                    if value is Grouping
                    else "Use its owning constructor or producer."
                ),
                producers=producers,
                consumers=consumers,
                constraints=("Construction and inspection perform no schema or business reads.",),
            )
        )
        exports.append(ExportInput(value.__name__, value, value.__name__))
    descriptors.append(
        value_type(
            "ObservationWindow",
            _Window,
            summary="A closed source-free endpoint window rule.",
            acquisition="Use all_history, trailing_duration, trailing_periods, since or to_date.",
            producers=("all_history", "trailing_duration", "trailing_periods", "since", "to_date"),
            consumers=("ObservationWindow.show",),
            constraints=(
                "Attach with metric.window(rule); supply during= on the consuming observation.",
            ),
        )
    )
    return DisclosureProvider(
        "analysis.population",
        with_sealed_variants(
            descriptors,
            {
                "Grouping": (_CoordinateGrouping, _MembershipGrouping),
                "ObservationWindow": (AllHistory, TrailingDuration, TrailingPeriods, Since, ToDate),
            },
            applicability={
                _CoordinateGrouping: "grouping was obtained from identity(...) or population.axes",
                _MembershipGrouping: "grouping was returned by membership(...)",
                AllHistory: "observation_window was returned by mv.all_history()",
                TrailingDuration: "observation_window was returned by mv.trailing_duration(...)",
                TrailingPeriods: "observation_window was returned by mv.trailing_periods(...)",
                Since: "observation_window was returned by mv.since(...)",
                ToDate: "observation_window was returned by mv.to_date(...)",
            },
        ),
        tuple(exports),
    )
