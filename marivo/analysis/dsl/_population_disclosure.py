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
from marivo.semantic._paths import Path, RelationshipStep, path


def provider() -> DisclosureProvider:
    """Bind the exact constructor and receiver implementations, without I/O."""
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    constructors = (
        (
            identity,
            "Grouping | Deferred",
            "Describe a complete direct Entity identity or a distinct identity role defined by a Path.",
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
            "result = mv.membership(tag, via=(mv.path(order_tags),), unmatched='exclude')",
            ("tag", "order_tags"),
            "inputs.population.paths",
        ),
        (
            path,
            "Path | PathSymbol",
            "Capture a nonempty, contiguous, directed relationship path.",
            "result = mv.path(order_customer)",
            ("order_customer",),
            "inputs.population.paths",
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
            "RowPredicate | Deferred",
            "Test complete correlated tuples. Combine with field conditions using &, | and ~.",
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
            "Reset at the natural calendar period boundary owning each endpoint.",
            "result = mv.to_date(grain=mv.grain('month'))",
            (),
            "inputs.time.windows",
        ),
    )
    grouping_inputs = {
        "coordinates": "Pass resolved fields or field or identity Groupings. Each identity key is a complete primary-key tuple. Use product for calendar or interval factors.",
        "membership": "Pass one resolved field or a nonempty tuple of resolved fields from one member Entity; put allocation paths in via, not in routed fields.",
        "Population.project": "Select or reorder existing coordinates using population.axes or matching fields. Projection cannot read new fields; allocation rules belong to observe.by or Metric.per inputs.",
        "Population.classify": "Pass single-valued fields or mv.identity(path) for new complete identity roles. Existing axes are reused; an empty tuple leaves the domain as is.",
    }
    parameter_facts = {
        "target": "Use a resolved Entity or native Entity symbol for a direct identity. Use mv.path(...) for a distinct routed identity role.",
        "keys": "Give one value per ordered axis; each identity value is a complete nonnull primary-key tuple. Duplicate complete tuples collapse; field None remains a category.",
        "entity": "Pass a resolved Entity from session.catalog.get('entity:<path>') or a finalized scope; bare EntityRef inputs are rejected.",
        "via": "Pass a Path from mv.path(...). Allocation membership takes a tuple of Paths, one per contribution root.",
        "steps": "Pass one or more directed Relationship values or anchored RelationshipStep values in connected order.",
        "source_at": "Supply an exact InstantAnchor for a versioned source lookup. None can reuse the consumer's existing source row when available.",
        "on": "Choose one existing complete identity Grouping. Omit only when the receiver has one eligible identity role.",
        "unmatched": "Choose exclude or null explicitly; null retains a source Null bucket.",
        "factors": "Pass at least two Populations, CalendarAxis or TimeScope values, or native Deferred parameters; closed factors need disjoint axes and compatible owners.",
        "population": "Pass a literal or same-Session Population, or a native Deferred parameter for loader binding.",
        "duration": "Pass a positive datetime.timedelta; duration is elapsed time, not a civil period count.",
        "count": "Pass a positive integer excluding bool; evaluation requires complete periods from the report CalendarAxis or nearest enclosing builtin calendar sample.",
        "start": "Pass an aware datetime or session.instant(...), strictly before every consumed endpoint.",
        "grain": "Pass mv.grain(unit) for a natural calendar reset; it uses the Session reporting timezone.",
        "predicate": "Pass a RowPredicate over existing coordinates. Combine comparisons, isin, isnull and in_population(...) using &, | and ~.",
        "other": "Pass a Population with compatible bound business axes, or a native Deferred parameter. Results use the left Population’s axis order.",
        "targets": "Pass the path target Entity’s direct single identity-axis Population, or a native Deferred parameter.",
        "at": "Pass an exact InstantAnchor when required attribute/version reads need it; omit otherwise.",
    }
    parameter_targets: dict[str, tuple[str, ...]] = {
        "target": ("inputs.catalog", "path.define"),
        "entity": ("inputs.catalog",),
        "via": ("path.define",),
        "steps": ("semantic.relationship", "semantic.relationship.at"),
        "source_at": ("session.instant",),
        "on": ("population.axes",),
        "by": ("inputs.catalog", "identity"),
        "factors": ("inputs.population", "session.time_scope"),
        "population": ("inputs.population",),
        "other": ("inputs.population",),
        "targets": ("session.members",),
        "predicate": ("semantic.predicate", "Population.classify", "in_population"),
        "at": ("session.instant",),
        "start": ("session.instant",),
        "grain": ("grain.define",),
    }
    constructor_constraints = {
        "identity": "Use coordinates for requested keys, or members for source-represented identities. Membership selection time does not change direct identity compatibility. A Path preserves the role and explicit node time bindings.",
        "coordinates": "Duplicate complete tuples collapse. Literal domains select only their captured complete keys; use product to combine calendar domains.",
        "membership": "Place membership in observe.by or Metric.per; it expands its full ordered fields without splitting its correlated rule. For rule=(tag, channel), (rule, customer, channel) locates channel explicitly; the first field cannot be repeated. For R=(a,b), S=(c,d), (R,S,b,d) yields (a,c,b,d). Reject overlapping rules or reordered fields. For Metric.per, (P, rule) retains P axes and joint keys and may select a subset. For observation, pass a complete P separately or use a grouping tuple. Each nested layer retains independent rules.",
        "path": "The source_at anchor selects the source version; relationship.at(anchor) selects a step’s target version. Each intermediate node carries its time into the next step. Each consumer validates whether its paths and anchors can be used.",
        "product": "Preserve factor order and complete joint keys; overlapping axes or conflicting Session owners reject.",
        "in_population": "Population.where matches existing keys. Source predicates match ordinary coordinates jointly with time coordinates using each contribution leaf’s own clock. Intervals use half-open containment; a time coordinate requires a contribution clock.",
        "all_history": "Source retention still limits available history; the endpoint is exclusive.",
        "trailing_duration": "Use elapsed time, independent of civil clock changes; the endpoint is exclusive.",
        "trailing_periods": "Require complete day/week/month/quarter/year periods at the endpoint layer; partial periods and subday grains reject. Count calendar predecessors, not positions in a sparse axis. An enclosing builtin calendar sample supplies endpoints without requiring a report TimeScope or CalendarAxis.",
        "since": "The start is inclusive and each actual observation endpoint is exclusive; start must precede every endpoint.",
        "to_date": "Each nominal output bucket must lie inside one reset period. At an exact reset boundary, the just-completed period owns the endpoint.",
    }
    for function, output, summary, code, requires, group in constructors:
        name = function.__name__
        is_window = group == "inputs.time.windows"
        descriptors.append(
            operation(
                "path.define" if function is path else name,
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
                    if " | Deferred" in output or function is path
                    else ()
                ),
                effects="Capture only; no schema or business reads.",
                failures=(
                    "AnalysisError or SemanticError: inspect expected/received input and the concrete repair.",
                ),
                example=ExampleInput(code, requires, "result", output),
                discovery_group=group,
                telemetry="short" if not is_window else False,
                related=("observation_window", "semantic.metric.window")
                if is_window
                else ("semantic.predicate",)
                if function is in_population
                else (),
            )
        )
        exports.append(ExportInput(name, function, "path.define" if function is path else name))
    methods: tuple[tuple[type[object], str, str, str, str, tuple[str, ...]], ...] = (
        (
            Population,
            "count",
            "NumericMetric",
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
            "Append single-valued coordinates and preserve the receiver’s complete tuples.",
            "result = population.classify(by=(region,))",
            ("population", "region"),
        ),
        (
            Population,
            "union",
            "Population",
            "Union compatible bound coordinate domains in left-side axis order.",
            "result = population.union(other)",
            ("population", "other"),
        ),
        (
            Population,
            "intersection",
            "Population",
            "Intersect compatible bound coordinate domains in left-side axis order.",
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
            "Filter complete tuples using a RowPredicate evaluated on existing coordinates.",
            "result = population.where(mv.in_population(allowed))",
            ("population", "allowed"),
        ),
        (
            Population,
            "image",
            "Population",
            "Map a selected identity role to distinct direct target identities.",
            "result = population.image(via=mv.path(relationship))",
            ("population", "relationship"),
        ),
        (
            Population,
            "preimage",
            "Population",
            "Retain complete receiver tuples that reach at least one selected target identity.",
            "result = population.preimage(targets, via=mv.path(relationship))",
            ("population", "targets", "relationship"),
        ),
        (
            Population,
            "identities",
            "Population",
            "Select an existing complete identity role, then deduplicate its keys on the direct Entity axis.",
            "result = population.identities(on=identity_axis)",
            ("population", "identity_axis"),
        ),
        (
            Path,
            "show",
            "None",
            "Print bounded captured path facts, including endpoints and step anchors.",
            "result = route.show()",
            ("route",),
        ),
        (
            RelationshipStep,
            "show",
            "None",
            "Print the captured relationship and optional target version anchor.",
            "result = step.show()",
            ("step",),
        ),
        (
            Population,
            "show",
            "None",
            "Print bounded captured axes, scope and dependency facts without evaluating keys.",
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
    method_constraints = {
        "classify": "Missing scalar matches yield NULL. Appending an identity role requires a complete key for every tuple. Use field.at(...) or a Path anchor for required version reads. Use project to drop or reorder axes.",
        "where": "Only True retains a tuple; field NULL follows three-valued logic. Membership compares complete null-safe tuples. Use classify for a missing field; where adds no lookup.",
        "identities": "The chosen axis must already be a complete identity role. Uses its captured keys without adding a source lookup. Use project to retain the role.",
        "image": "Complete foreign keys can supply target identities without target row reads. Remove a terminal anchor when identity is determined by the foreign key; test target existence with members and intersection.",
        "preimage": "Retains all receiver axes. A tuple reaching multiple targets matches existentially; taking its image again can reach other targets. Use an explicit members domain to require target presence at an instant.",
    }
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
                        grouping_inputs[target]
                        if name == "by"
                        else "Required. Choose an existing complete identity axis from population.axes."
                        if target == "Population.identities" and name == "on"
                        else "Choose an existing identity Grouping when multiple roles can supply the new coordinates. None uses the unique eligible role; reusing existing coordinates needs no new read."
                        if target == "Population.classify" and name == "on"
                        else parameter_facts[name],
                        (
                            ("population.axes", "identity", "semantic.field")
                            if target == "Population.classify"
                            else ("population.axes",)
                        )
                        if name == "by"
                        else parameter_targets.get(name, ()),
                    )
                    for name in signature(function).parameters
                    if name != "self"
                ),
                output=output,
                constraints=(
                    "Count complete tuples, including field Null and absent identities; empty is zero and the zero-axis singleton is one. Grouping uses existing domain axes; classify explicitly to read attributes. Fact predicates leave the captured domain fixed; count-only conditions reject. Report ranges and windows do not reselect captured members. Missing report time coordinates broadcast only over established output keys; existing compatible time coordinates support their captured keys. For a complete time domain, use explicit by=Population."
                    if owner is Population and name == "count"
                    else method_constraints.get(
                        name,
                        "One Session owner; complete typed keys and explicit version/path bindings remain authoritative."
                        if owner is Population and name != "show"
                        else "Captured metadata only.",
                    ),
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
                telemetry="short" if owner is Population and name != "show" else False,
                discovery_group=(
                    None
                    if name == "show"
                    else "operators"
                    if name == "count"
                    else "inputs.population.paths"
                    if name in ("image", "preimage", "identities")
                    else "inputs.population"
                ),
            )
        )
    for value, producers, consumers in (
        (
            Population,
            ("coordinates", "product", "session.members", "session.population_from"),
            (
                "inputs.population",
                "session.frame",
                "session.read",
                "Population.project",
                "Population.classify",
                "Population.where",
            ),
        ),
        (
            Grouping,
            ("identity", "membership", "population.axes"),
            ("session.observe", "Grouping.show"),
        ),
    ):
        descriptors.append(
            value_type(
                value.__name__,
                value,
                summary=(
                    "A field, complete identity or correlated membership grouping rule, or a captured axis from a Population."
                    if value is Grouping
                    else "Finite immutable complete coordinate domain with explicit dependencies."
                    if value is Population
                    else "Closed immutable coordinate membership predicate."
                ),
                acquisition=(
                    "Use identity(entity), membership(...) or population.axes. For observe.by, use non-time groupings, a CalendarAxis, or the complete Population. This abstract type has no public constructor or extension point."
                    if value is Grouping
                    else "Use mv.coordinates for literal keys, session.members for source identities, session.population_from for a fixed Frame bridge, mv.product for composition, or Population.project/classify/where/union/intersection/difference/image/preimage for derivation. Direct construction is unsupported."
                ),
                producers=producers,
                consumers=consumers,
                constraints=("Construction and inspection perform no schema or business reads.",),
            )
        )
        exports.append(ExportInput(value.__name__, value, value.__name__))
    path_types: tuple[tuple[type[object], str, str, tuple[str, ...], tuple[str, ...]], ...] = (
        (
            Path,
            "A directed relationship path with captured endpoints and optional node anchors.",
            "Use mv.path(...).",
            ("path.define",),
            (
                "semantic.field.via",
                "identity",
                "Population.image",
                "Population.preimage",
                "membership",
            ),
        ),
        (
            RelationshipStep,
            "A relationship and its optional target version anchor.",
            "Use relationship.at(anchor), or inspect the normalized entries of path.steps.",
            ("semantic.relationship.at", "Path.steps"),
            ("path.define",),
        ),
    )
    for value_type_, summary, acquisition, type_producers, type_consumers in path_types:
        descriptors.append(
            value_type(
                value_type_.__name__,
                value_type_,
                summary=summary,
                acquisition=acquisition,
                producers=type_producers,
                consumers=type_consumers,
                constraints=("Direct initialization and extension are unsupported.",),
            )
        )
        exports.append(ExportInput(value_type_.__name__, value_type_, value_type_.__name__))
    descriptors.append(
        value_type(
            "ObservationWindow",
            _Window,
            summary="A closed source-free endpoint window rule.",
            acquisition="Use all_history, trailing_duration, trailing_periods, since or to_date. Direct construction and extension are unsupported.",
            producers=("all_history", "trailing_duration", "trailing_periods", "since", "to_date"),
            consumers=("semantic.metric.window", "ObservationWindow.show"),
            constraints=(
                "Attach with metric.window(rule). The nearest enclosing builtin calendar sample supplies endpoints; otherwise use a TimeScope in observe.where or report buckets. Each rule retains its endpoint requirements. Nested windows reject.",
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
