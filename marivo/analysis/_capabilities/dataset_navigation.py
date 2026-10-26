"""Native task navigation, separate from the exact type and member inventory."""

from __future__ import annotations

from dataclasses import replace

from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    DisclosureProvider,
    NavigationInput,
    invalid,
)

# Each provider owns primary capability membership. These pages own task choices,
# short decision summaries and secondary links; they do not duplicate API facts.
_PAGES = (
    NavigationInput(
        "inputs",
        "Find or compose governed inputs; choose population and time.",
        (),
        "decision_hub",
        guidance=(
            "Known closed inputs can be composed for this question without publishing a new Metric. Retain their business meaning, aggregation permission, grain, time and units.",
            "Use session context for Catalog access and frozen timezone; exported types do not authorize direct construction.",
        ),
        related=("semantic.expressions",),
        route_summaries=(
            ("semantic.expressions", "Compose existing closed inputs for this question."),
        ),
    ),
    NavigationInput(
        "inputs.population",
        "Construct, select or map complete member and coordinate domains.",
        (),
        guidance=(
            "Construction and member conditions: members, identity, coordinates, membership, product and in_population.",
            "Set projection: project, union, intersection, difference and where preserve explicit domain meaning.",
            "Mapping and classification: image/preimage follow declared relationships; classify can introduce source attribute dependencies.",
            "Fixed-key bridge: population_from explicitly binds selected complete ordinary Frame keys for a new source question.",
            "Population.count counts complete distinct tuples, including members without facts, and returns a Metric for observe. It is not the current Frame row count.",
        ),
        related=("population.count", "semantic.predicate"),
        discovery_group="inputs",
        route_summaries=(
            (
                "session.members",
                "Select source Entity identities with explicit conditions and anchor.",
            ),
            ("identity", "Describe complete Entity identity coordinates."),
            ("coordinates", "Supply finite complete coordinate tuples."),
            ("membership", "Choose correlated member fields and directed allocation paths."),
            ("product", "Combine disjoint complete coordinate domains."),
            ("in_population", "Build a complete-tuple membership condition."),
            ("population.project", "Select and reorder existing axes."),
            ("population.union", "Keep tuples from either matching domain."),
            ("population.intersection", "Keep tuples shared by matching domains."),
            ("population.difference", "Remove tuples present in another domain."),
            ("population.where", "Filter existing tuples by membership conditions."),
            ("population.image", "Map identities forward along declared relationships."),
            ("population.preimage", "Select identities reaching chosen target identities."),
            ("population.classify", "Add same-Entity fields to one identity domain."),
            (
                "session.population_from",
                "Bind selected fixed Frame keys to explicit semantic targets.",
            ),
            ("population.count", "Count complete domain tuples, including members without facts."),
            ("semantic.predicate", "Choose valid source and membership condition syntax."),
        ),
    ),
    NavigationInput(
        "inputs.time",
        "Choose time anchors, observation intervals, output buckets or Metric history.",
        (),
        guidance=(
            "An instant selects an exact anchor; TimeScope selects an observation interval; scope.by(grain) supplies output buckets. Endpoint correspondence is a separate choice.",
        ),
        related=("inputs.comparison",),
        discovery_group="inputs",
        route_summaries=(
            ("inputs.comparison", "Choose comparison endpoint correspondence."),
            ("session.instant", "Freeze an exact time anchor."),
            ("session.time_scope", "Choose a half-open observation interval."),
            ("grain.define", "Choose a built-in bucket unit and count."),
            ("time_scope.by", "Turn an interval into output calendar buckets."),
            ("calendar_axis.shift", "Construct a frozen shifted endpoint relation."),
        ),
    ),
    NavigationInput(
        "inputs.time.windows",
        "Choose the source history seen at each Metric observation endpoint.",
        (),
        guidance=(
            "A Metric window controls source history; it does not replace output time coordinates.",
        ),
        discovery_group="inputs.time",
        route_summaries=(
            ("all_history", "Use all available history before each endpoint."),
            ("since", "Use history after one exact start."),
            ("to_date", "Reset history at the owning period boundary."),
            ("trailing_duration", "Use a fixed elapsed duration."),
            ("trailing_periods", "Use the current and preceding calendar periods."),
        ),
    ),
    NavigationInput(
        "operators",
        "Choose an operation by analytical intent, input and output.",
        (),
        guidance=(
            "Choose what to count: complete Population tuples -> population.count; Entity source contributions -> semantic.entity.count; current Frame rows including value NA -> row_count with reduce/over.",
            "Population and Entity counts return Metrics for observe; members without facts remain in the domain.",
            "Capture is lazy. Pending dependencies may read sources at evaluation; fixed-data operations use captured values.",
            "attribute needs retained source declarations, absent on independent reopen; parent and new source reads are independent.",
        ),
        related=("semantic.entity.count",),
        route_summaries=(
            ("session.observe", "Metric + optional classification/time -> Frame."),
            ("session.read", "Selected members + scalar fields -> Frame."),
            ("session.frame", "Complete Population keys -> Frame."),
            (
                "session.values",
                "Finite literal values + units -> Frame.",
            ),
            ("analysis_frame.select", "Same-input named expressions -> Frame."),
            ("analysis_frame.where", "Same-input condition -> filtered Frame."),
            ("analysis_frame.top", "Score + row limit -> ranked Frame."),
            (
                "analysis_frame.fix",
                "Fix an ordinary coordinate; remove its axis -> Frame.",
            ),
            ("analysis_frame.reduce", "Statistics + retained axes -> summary Frame."),
            (
                "analysis_frame.over",
                "Partition statistic, retaining rows -> Column.",
            ),
            ("column.share", "Values / partition sum -> Column."),
            ("analysis_frame.align", "Two Frames -> endpoint values and presence."),
            (
                "analysis_frame.compare",
                "Two Frames -> baseline, absolute and relative changes.",
            ),
            ("analysis_frame.correlate", "Numeric pair -> correlation and valid-pair-count Frame."),
            (
                "analysis_frame.attribute",
                "Retained source targets + classifications -> Frame.",
            ),
            ("population.count", "Count complete domain tuples -> Metric for observe."),
            ("row_count", "Count current rows including value NA -> Statistic for reduce/over."),
            ("semantic.entity.count", "Count source contributions -> Metric for observe."),
        ),
    ),
    NavigationInput(
        "inputs.frame",
        "Acquire Columns, complete axes and statistics from an existing Frame.",
        (),
        guidance=(
            "Choose a value Column, scalar coordinate Column or complete axis before composing an expression. Columns and axes retain one exact Frame input.",
            "After deriving a new Frame, reacquire its Columns, axes and statistics. Current-row statistics do not reconstruct original Metric aggregation or complete membership.",
            "A Statistic is unfinished: consume it with reduce or over. over and share return Columns for same-input composition.",
        ),
        related=("Column", "FrameAxis", "value_statistic"),
        discovery_group="inputs",
        route_summaries=(
            ("analysis_frame.__getitem__", "Select an existing named value Column."),
            ("analysis_frame.coord", "Select a single-component scalar coordinate Column."),
            ("analysis_frame.axis", "Locate one complete current axis."),
            ("column.is_null", "Build a same-input missing-value condition."),
            ("column.sum", "Request a current-row sum."),
            ("column.mean", "Request a current-row mean."),
            ("column.corr", "Request a paired correlation statistic."),
            ("column.pair_count", "Request a valid-pair count statistic."),
            ("Column", "Read same-input arithmetic and boolean syntax."),
            ("FrameAxis", "Read complete-axis acquisition and ownership."),
            ("value_statistic", "Choose a statistic producer and its consumer."),
        ),
    ),
    NavigationInput(
        "inputs.comparison",
        "Prepare baseline Frames, complete axis pairs and endpoint correspondence.",
        (),
        guidance=(
            "Use a same-Session baseline Frame, or name baseline declarations in compare's Mapping. on pairs complete axes from the two actual inputs; names do not prove business correspondence.",
            "Choose endpoint equality, a supported offset or ordinal under the exact operation's omit rules. Matching positions alone do not establish business comparability.",
        ),
        related=(
            "analysis_frame.axis",
            "analysis_frame.axes",
            "session.observe",
            "session.persisted_frame",
        ),
        discovery_group="inputs",
        route_summaries=(
            ("baseline", "Bundle a baseline Frame with its on and match rules."),
            ("offset", "Match civil years or nominal grain periods."),
            ("ordinal", "Pair compatible complete calendars or one interval per side."),
            ("analysis_frame.axis", "Acquire one complete axis from each input."),
            ("analysis_frame.axes", "Read complete axes in their actual input context."),
            ("session.observe", "Construct a new explicit source baseline."),
            ("session.persisted_frame", "Reopen an exact saved baseline in this Session."),
        ),
    ),
    NavigationInput(
        "execution",
        "Fix values in memory or persist a result.",
        (),
        "decision_hub",
        guidance=(
            "Continue with the returned Frame. compute fixes complete data in memory; subsequent derivations remain lazy and need explicit compute/persist. persist saves a committed Frame whose Artifact is audit.",
        ),
        route_summaries=(
            ("analysis_frame.compute", "Obtain complete fixed memory data."),
            ("analysis_frame.persist", "Save a result for exact cross-process recovery."),
        ),
    ),
    NavigationInput(
        "results",
        "Inspect captured facts, export fixed data or read saved evidence.",
        (),
        "decision_hub",
    ),
    NavigationInput(
        "results.inspect",
        "Inspect Frame facts or export complete fixed values.",
        (),
        guidance=(
            "Pending show reports captured facts without evaluation. Fixed show displays a bounded prefix; to_pandas exports complete fixed data with readable coordinates, string columns and isolated axis metadata, without evaluation. Other objects expose show/render through their type Help.",
        ),
        discovery_group="results",
        route_summaries=(
            ("analysis_frame.show", "Inspect captured facts or a bounded fixed-data prefix."),
            ("analysis_frame.to_pandas", "Export complete fixed data as an isolated DataFrame."),
        ),
    ),
    NavigationInput(
        "results.evidence",
        "Read saved Artifact audit or original Frame method facts.",
        (),
        guidance=(
            "A saved Frame's .artifact provides committed audit; Frame.method_facts exposes original method facts. These facts do not establish a causal conclusion or current source freshness.",
            "An unsaved result needs persist only when a saved Artifact is required; inspecting current Frame facts does not require saving.",
        ),
        related=("analysis_frame.method_facts", "FrameMethodFacts", "analysis_frame.persist"),
        discovery_group="results",
        route_summaries=(
            ("artifact.show", "Inspect saved submission identity and audit summaries."),
            ("artifact.findings", "Read a bounded page of saved Findings."),
            ("artifact.evidence_digest", "Read saved Evidence facts and collection digests."),
            ("analysis_frame.method_facts", "Access captured original method facts on this Frame."),
            ("FrameMethodFacts", "Understand original method facts and their limits."),
            ("analysis_frame.persist", "Save current results when a committed Artifact is needed."),
        ),
    ),
    NavigationInput(
        "sessions",
        "Create or recover the exact working context.",
        (),
        guidance=(
            "Create or recover a repeatable name, discover retained IDs, inspect metadata or resume an exact existing ID. Recovery does not replay source observations.",
            "Reopen exact committed data with persisted_frame(id); its Artifact is audit. Continue fixed-data operations or explicitly construct new source inputs through operators.",
            "Older Stores coexist unchanged with current work; read/export them in a compatible environment. Names, IDs and Artifacts remain independent across generations. A lost return reference requires explicit resubmission, which may duplicate outputs or read changed data.",
        ),
        related=("operators",),
        route_summaries=(
            (
                "session.get_or_create",
                "Create or recover a repeatable name; reject explicit configuration conflicts.",
            ),
            ("session.recent", "Discover retained Session IDs."),
            ("session.inspect", "Inspect an existing Session's metadata."),
            ("session.resume", "Recover one exact existing Session ID."),
            ("session.persisted_frame", "Reopen one exact committed Frame in this Session."),
            (
                "operators",
                "Continue fixed-data work or construct explicit new source observations.",
            ),
        ),
    ),
)


def navigation(providers: tuple[DisclosureProvider, ...]) -> tuple[NavigationInput, ...]:
    """Assemble owner-declared task membership, excluding type-only members."""
    buckets: dict[str, list[str]] = {page.canonical_id: [] for page in _PAGES}
    nested = {
        member
        for provider in providers
        for descriptor in provider.descriptors
        if isinstance(descriptor, NavigationInput)
        for member in descriptor.members
    }
    for provider in providers:
        for descriptor in provider.descriptors:
            if descriptor.canonical_id in nested:
                continue
            if isinstance(descriptor, (CallableInput, NavigationInput)):
                group = descriptor.discovery_group
                if group is not None:
                    if group not in buckets:
                        raise invalid("an existing native discovery group", group)
                    buckets[group].append(descriptor.canonical_id)
    for page in _PAGES:
        if page.discovery_group is not None:
            buckets[page.discovery_group].append(page.canonical_id)
    result = []
    for page in _PAGES:
        order = {target: index for index, (target, _) in enumerate(page.route_summaries)}
        members = tuple(
            sorted(buckets[page.canonical_id], key=lambda target: order.get(target, len(order)))
        )
        result.append(replace(page, members=members))
    result.append(
        NavigationInput(
            "",
            "Use governed definitions to answer an analytical question.",
            ("inputs", "operators", "sessions", "execution", "results"),
            "root",
            guidance=(
                "Imports: import marivo.analysis as mv",
                "Catalog: definitions. Session: context and ownership. Frame: pending/fixed data. Artifact: saved audit.",
                "Start with session.get_or_create; discover inputs through session.catalog. Related Frames share one Session; none is implicit.",
                "Operators: observe, derive, filter, rank, summarize, compare, correlate, attribute; parameters link to producers.",
                "Enter known targets directly; otherwise choose the missing information.",
                "Construction captures dependencies; compute fixes data; persist saves it. Continue with the returned Frame.",
                "Read object facts or type Help; follow error repairs. The agent owns interpretation.",
            ),
            related=("session.get_or_create",),
            route_summaries=(
                ("session.get_or_create", "Start or continue a named analysis context."),
            ),
        )
    )
    return tuple(result)
