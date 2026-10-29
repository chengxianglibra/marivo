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
    coordinates,
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
            "Grouping | Deferred",
            "Capture a complete identity grouping without selecting keys.",
            "result = mv.identity(entity)",
            ("entity",),
            "inputs.population",
        ),
        (
            coordinates,
            "Population | Deferred",
            "Capture complete field and identity tuples; None is a field Null category.",
            "result = mv.coordinates(by=(region,), keys=(('east',), ('west',)))",
            ("region",),
            "inputs.population",
        ),
        (
            membership,
            "Grouping | Deferred",
            "Capture one correlated member-field tuple and explicit directed allocation paths.",
            "result = mv.membership(tag, via=((order_tags,),), unmatched='exclude')",
            ("tag", "order_tags"),
            "inputs.population",
        ),
        (
            product,
            "Population | Deferred",
            "Form the Cartesian product of disjoint complete domains, preserving axis order.",
            "result = mv.product(regions, calendar)",
            ("regions", "calendar"),
            "inputs.population",
        ),
        (
            in_population,
            "Deferred",
            "Capture a native boolean membership leaf over complete null-safe tuples; combine with field conditions using &, | and ~. See semantic.predicate.",
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
            "inputs.time.windows",
        ),
        (
            trailing_duration,
            "ObservationWindow",
            "Use a fixed elapsed duration before each observation endpoint.",
            "from datetime import timedelta\nresult = mv.trailing_duration(duration=timedelta(hours=24))",
            (),
            "inputs.time.windows",
        ),
        (
            trailing_periods,
            "ObservationWindow",
            "Use the current calendar period and count minus one preceding periods.",
            "result = mv.trailing_periods(count=7)",
            (),
            "inputs.time.windows",
        ),
        (
            since,
            "ObservationWindow",
            "Use an exact fixed start before each observation endpoint.",
            "result = mv.since(start=session.instant('2026-01-01'))",
            ("session",),
            "inputs.time.windows",
        ),
        (
            to_date,
            "ObservationWindow",
            "Reset at the civil or semantic period boundary owning each endpoint.",
            "result = mv.to_date(grain=mv.grain('month'))",
            (),
            "inputs.time.windows",
        ),
    )
    grouping_inputs = {
        "coordinates": "Pass resolved fields or field or identity Grouping values. Each identity value is a complete primary-key tuple; identity(entity, at=...) supplies its anchor. Rejects membership and calendar groupings; use product for calendar domains.",
        "membership": "Pass one resolved field or a nonempty tuple of resolved fields from one member Entity; put allocation paths in via, not in routed fields.",
        "Population.project": "Select or reorder existing coordinates using population.axes or matching fields. Projection cannot read new fields; allocation rules belong to observe.by or Metric.per inputs.",
        "Population.classify": "Pass direct same-Entity fields, field groupings or the receiver's existing identity. Rejects membership, interval/calendar groupings and routed fields; requires one identity-axis Population.",
    }
    parameter_facts = {
        "target": "Pass a closed Entity, native Entity symbol or nonempty connected directed relationship path; its endpoint determines the Entity.",
        "keys": "Give one value per ordered axis; each identity value is a complete nonnull primary-key tuple. Duplicate complete tuples collapse; field None remains a category.",
        "entity": "Pass a resolved Entity from session.catalog.get('entity:<path>') or a finalized scope; bare EntityRef inputs are rejected.",
        "via": "Pass a nonempty ordered tuple of declared relationships; membership takes a tuple of paths, one per contribution root.",
        "unmatched": "Choose exclude or null explicitly; null retains a source Null bucket.",
        "factors": "Pass at least two Populations, CalendarAxis or TimeScope values, or native Deferred parameters; closed factors need disjoint axes and compatible owners.",
        "population": "Pass a literal or same-Session Population, or a native Deferred parameter for loader binding.",
        "duration": "Pass a positive datetime.timedelta; duration is elapsed time, not a civil period count.",
        "count": "Pass a positive integer excluding bool; evaluation requires complete CalendarAxis buckets.",
        "start": "Pass an aware datetime or session.instant(...), strictly before every consumed endpoint.",
        "grain": "Pass mv.grain(unit), ms.calendar_grain(calendar=ref, level=...), or a Catalog grain. Unbound semantic reset grains resolve in the Session's frozen certified scope; captured snapshots must match it.",
        "predicate": "Pass in_population(...) or its closed &, | and ~ combinations over existing axes.",
        "other": "Pass a Population with identical ordered bound business axes, or a native Deferred parameter. Project closed domains to matching coordinates before combining.",
        "targets": "Pass the path target Entity's same-Session single identity-axis Population, or a native Deferred parameter.",
        "at": "Pass an exact InstantAnchor when required attribute/version reads need it; omit otherwise.",
    }
    parameter_targets: dict[str, tuple[str, ...]] = {
        "target": ("inputs.catalog",),
        "entity": ("inputs.catalog",),
        "via": ("inputs.catalog",),
        "by": ("inputs.catalog", "identity"),
        "factors": ("inputs.population", "session.time_scope"),
        "population": ("inputs.population",),
        "other": ("inputs.population",),
        "targets": ("session.members",),
        "predicate": ("in_population",),
        "at": ("session.instant",),
        "start": ("session.instant",),
        "grain": ("grain.define", "catalog.period_calendars.grain"),
    }
    constructor_constraints = {
        "identity": "Pass by=(grouping,) to observe. Identity grouping does not select keys; use coordinates for a finite domain. Observation binds complete primary keys through single-valued routes and validates version anchors.",
        "coordinates": "Duplicate complete tuples collapse. Literal domains select only their captured complete keys; use product to combine calendar domains.",
        "membership": "Place membership in observe.by or Metric.per; it expands its full ordered fields without splitting its correlated rule. For rule=(tag, channel), (rule, customer, channel) locates channel explicitly; the first field cannot be repeated. For R=(a,b), S=(c,d), (R,S,b,d) yields (a,c,b,d). Reject overlapping rules or reordered fields. (P, rule) retains P axes and joint keys; observe requires output coverage, per allows a subset. Each nested layer retains independent rules.",
        "product": "Preserve factor order and complete joint keys; overlapping axes or conflicting Session owners reject.",
        "in_population": "Match complete null-safe tuples on existing axes; membership does not introduce new output coordinates.",
        "all_history": "Source retention still limits available history; the endpoint is exclusive.",
        "trailing_duration": "Use elapsed time, independent of civil clock changes; the endpoint is exclusive.",
        "trailing_periods": "Require complete day/week/month/quarter/year or certified named-calendar buckets; partial buckets and subday grains reject. Count calendar predecessors, not positions in a sparse axis. Named calendars must certify the required predecessor coverage.",
        "since": "The start is inclusive and each actual observation endpoint is exclusive; start must precede every endpoint.",
        "to_date": "Each nominal output bucket must lie inside one reset period. At an exact reset boundary, the just-completed period owns the endpoint.",
    }
    for function, output, summary, code, requires, group in constructors:
        name = function.__name__
        is_window = group == "inputs.time.windows"
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
                        parameter_targets.get(name, ()),
                    )
                    for name in signature(function).parameters
                ),
                output=output,
                constraints=(constructor_constraints[name],)
                + (
                    (
                        "Closed inputs return the concrete value; native lookup symbols return a Deferred parameter requiring loader binding before analysis.",
                    )
                    if " | Deferred" in output
                    else ()
                ),
                effects="Capture only; no schema or business reads.",
                failures=(
                    "AnalysisError or SemanticError: inspect expected/received input and the concrete repair.",
                ),
                example=ExampleInput(code, requires, "result", output),
                discovery_group=group,
                related=("observation_window", "semantic.metric.window")
                if is_window
                else ("semantic.predicate",)
                if function is in_population
                else (),
            )
        )
        exports.append(ExportInput(name, function, name))
    methods: tuple[tuple[type[object], str, str, str, str, tuple[str, ...]], ...] = (
        (
            Population,
            "count",
            "Deferred",
            "Count complete distinct tuples as a lazy Metric; declared identities without facts still count. Group only by existing domain coordinates.",
            "result = population.count()",
            ("population",),
        ),
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
            "Read correlated fields, trusting source declarations; missing matches yield NULL. Existing-axis-only classification is projection.",
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
            _Window,
            "show",
            "str",
            "Return source-free window rule parameters.",
            "result = rule.show()",
            ("rule",),
        ),
    )
    for owner, name, output, summary, code, method_requires in methods:
        symbolic_output = owner is Population and output == "Population"
        if symbolic_output:
            output = "Population | Deferred"
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
                        name,
                        grouping_inputs[target] if name == "by" else parameter_facts[name],
                        ("population.axes",) if name == "by" else parameter_targets.get(name, ()),
                    )
                    for name in signature(function).parameters
                    if name != "self"
                ),
                output=output,
                constraints=(
                    "Count complete tuples, including field Null and absent identities; empty is zero and the zero-axis singleton is one. Grouping uses existing domain axes; classify explicitly to read attributes. Fact conditions/windows do not reselect the domain; count-only conditions/windows reject."
                    if owner is Population and name == "count"
                    else "One Session owner; complete typed keys and explicit version/path bindings remain authoritative."
                    if owner is Population and name != "show"
                    else "Captured metadata only.",
                )
                + (
                    (
                        "Closed inputs return a Population; native lookup symbols return a Deferred parameter requiring loader binding before analysis.",
                    )
                    if symbolic_output
                    else ()
                ),
                effects="Capture or inspect only; no schema or business reads.",
                failures=("AnalysisError: follow its exact axis, owner, path or version repair.",),
                example=ExampleInput(code, method_requires, "result", output),
                discovery_group=(
                    None
                    if name == "show"
                    else "operators"
                    if name == "count"
                    else "inputs.population"
                ),
            )
        )
    for value, producers, consumers in (
        (
            Population,
            ("coordinates", "product", "session.members", "session.population_from"),
            ("session.frame", "Population.project", "Population.classify", "Population.where"),
        ),
        (
            Grouping,
            ("identity", "membership", "population.axes", "time_scope.by"),
            ("session.observe", "Grouping.show"),
        ),
    ):
        descriptors.append(
            value_type(
                value.__name__,
                value,
                summary=(
                    "A field, complete identity, interval, calendar or correlated membership grouping rule."
                    if value is Grouping
                    else "Finite immutable complete coordinate domain with explicit dependencies."
                    if value is Population
                    else "Closed immutable coordinate membership predicate."
                ),
                acquisition=(
                    "Use identity(entity), membership(...), scope.by(grain), or population.axes. This abstract type has no public constructor or extension point."
                    if value is Grouping
                    else "Use mv.coordinates for literal keys, session.members for source identities, session.population_from for a fixed Frame bridge, mv.product for composition, or Population.project/classify/where/union/intersection/difference/image/preimage for derivation. Direct construction is unsupported."
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
            acquisition="Use all_history, trailing_duration, trailing_periods, since or to_date. Direct construction and extension are unsupported.",
            producers=("all_history", "trailing_duration", "trailing_periods", "since", "to_date"),
            consumers=("semantic.metric.window", "ObservationWindow.show"),
            constraints=(
                "Attach with metric.window(rule); supply an explicit TimeScope or CalendarAxis in observe.by. Nested windows reject.",
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
