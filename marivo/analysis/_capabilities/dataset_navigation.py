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
        "Choose time anchors, observation intervals, output buckets or endpoint correspondence.",
        (),
        guidance=(
            "An instant selects an exact anchor; TimeScope selects an observation interval; scope.by(grain) supplies output buckets. Endpoint correspondence is a separate choice.",
        ),
        discovery_group="inputs",
        route_summaries=(
            ("session.instant", "Freeze an exact time anchor."),
            ("session.time_scope", "Choose a half-open observation interval."),
            ("grain.define", "Choose a built-in bucket unit and count."),
            ("time_scope.by", "Turn an interval into output calendar buckets."),
            ("calendar_axis.shift", "Construct a frozen shifted endpoint relation."),
            ("offset", "Match endpoints by civil year or nominal period offset."),
            ("ordinal", "Match complete compatible endpoint calendars by position."),
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
        "source",
        "Form Metric observations or request source facts.",
        (),
        guidance=(
            "Existing closed Metric: observe it. Existing business meaning and aggregation permission: compose this question's arithmetic, conditions, samples or windows through semantic.expressions; no new published Metric is required.",
            "Missing field meaning or aggregation permission: use authoring. A missing same-named published Metric alone does not require authoring.",
            "Choose what to count: complete Population tuples -> population.count; Entity source contributions -> semantic.entity.count; current Frame rows including value NA -> row_count with reduce/over.",
            "Population and Entity counts return Metrics for observe. Population domains include members without facts; source conditions/windows do not reselect that domain.",
            "Construction captures dependencies without business reads; evaluating a pending Frame may execute them. Literal Population counts need no source read.",
            "attribute needs retained executable source declarations. Independently reopened Frames do not recover declarations from names, saved values or method facts.",
        ),
        related=(
            "semantic.expressions",
            "semantic.predicate",
            "semantic.entity.count",
            "row_count",
            "session.population_from",
            "inputs",
            "execution",
            "authoring",
        ),
        route_summaries=(
            ("session.observe", "Observe named Metrics on an explicit output domain."),
            ("session.read", "Read scalar source attributes for complete selected identities."),
            (
                "analysis_frame.attribute",
                "Investigate admitted retained source targets by business classifications.",
            ),
            ("population.count", "Count complete Population tuples as a Metric."),
            ("semantic.expressions", "Compose closed semantic inputs for this question."),
            (
                "semantic.predicate",
                "Build source or membership conditions with the owning grammar.",
            ),
            ("semantic.entity.count", "Count Entity source contributions as a Metric."),
            ("row_count", "Count current Frame rows with reduce or over."),
            (
                "session.population_from",
                "Use selected fixed keys in an explicit new source question.",
            ),
            ("authoring", "Define missing business meaning or aggregation permission."),
        ),
    ),
    NavigationInput(
        "frame",
        "Calculate over current data or compose a Frame.",
        (),
        "decision_hub",
        guidance=(
            "Known finite literal values: values. Captured complete Population keys: frame. Existing Frame: choose expressions, rows, comparison or statistics.",
            "Fixed or reopened Frame operations use captured data without restoring source authority. Evaluating operations on pending Frames may execute their existing source dependencies.",
        ),
        route_summaries=(
            ("session.values", "Construct from finite literal values with declared units."),
            ("session.frame", "Construct from a complete Population key domain."),
        ),
    ),
    NavigationInput(
        "frame.expressions",
        "Locate values and coordinates, then compose expressions on the same input.",
        (),
        guidance=(
            "Choose a value Column, scalar coordinate Column or complete axis before writing an expression. Column arithmetic and boolean operators retain the exact Frame input.",
        ),
        related=("Column",),
        discovery_group="frame",
        route_summaries=(
            ("analysis_frame.__getitem__", "Select an existing named value Column."),
            ("analysis_frame.coord", "Select a single-component scalar coordinate Column."),
            ("analysis_frame.axis", "Locate one complete current axis."),
            ("analysis_frame.select", "Keep named same-input expressions as result values."),
            ("column.is_null", "Test value NA in a same-input boolean expression."),
            ("Column", "Read same-input Column syntax and exact operator contracts."),
        ),
    ),
    NavigationInput(
        "frame.rows",
        "Retain rows by a condition or take a ranked prefix.",
        (),
        guidance=(
            "A display limit does not transform data; use where or top to change the represented rows.",
        ),
        discovery_group="frame",
        route_summaries=(
            ("analysis_frame.where", "Keep rows whose same-input condition is True."),
            ("analysis_frame.top", "Sort by a score and retain a bounded prefix."),
        ),
    ),
    NavigationInput(
        "frame.compare",
        "Align explicit endpoints or calculate their changes.",
        (),
        guidance=(
            "Keep both endpoint values and presence with align; request absolute and relative changes with compare. Matching positions alone do not establish time identity.",
        ),
        related=("inputs.time",),
        discovery_group="frame",
        route_summaries=(
            ("analysis_frame.align", "Retain both endpoints and their presence."),
            ("analysis_frame.compare", "Add baseline values, absolute change and relative change."),
            ("baseline", "Bundle a named baseline Frame with its correspondence rules."),
            ("inputs.time", "Choose explicit time identity and calendar correspondence."),
        ),
    ),
    NavigationInput(
        "frame.statistics",
        "Choose current-row statistics.",
        (),
        guidance=(
            "After selection, reacquire Columns/axes/statistics. Current rows restore neither original Metric aggregation nor complete membership.",
            "Count complete Population tuples or Entity source contributions as Metrics for observe. row_count counts current Frame rows including value NA for reduce/over.",
        ),
        related=("population.count", "semantic.entity.count", "source"),
        discovery_group="frame",
        route_summaries=(
            ("analysis_frame.reduce", "Collapse by complete axes."),
            ("analysis_frame.over", "Broadcast; retain rows."),
            ("analysis_frame.correlate", "Coefficient + valid-pair count."),
            ("column.share", "Partition value / total."),
            ("column.sum", "Current-row sum."),
            ("column.mean", "Current-row mean."),
            ("column.corr", "Paired correlation request."),
            ("column.pair_count", "Valid-pair count request."),
            ("row_count", "Current-row count."),
            ("population.count", "Tuple-count Metric."),
            ("semantic.entity.count", "Contribution-count Metric."),
            ("source", "New source facts."),
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
            "Reopen exact committed data with persisted_frame(id); its Artifact is audit. Continue fixed-data operations through frame, or explicitly ask source for new facts.",
            "Older Stores coexist unchanged with current work; read/export them in a compatible environment. Names, IDs and Artifacts remain independent across generations. A lost return reference requires explicit resubmission, which may duplicate outputs or read changed data.",
        ),
        related=("frame", "source"),
        route_summaries=(
            (
                "session.get_or_create",
                "Create or recover a repeatable name; reject explicit configuration conflicts.",
            ),
            ("session.recent", "Discover retained Session IDs."),
            ("session.inspect", "Inspect an existing Session's metadata."),
            ("session.resume", "Recover one exact existing Session ID."),
            ("session.persisted_frame", "Reopen one exact committed Frame in this Session."),
            ("frame", "Continue calculations on recovered fixed data."),
            ("source", "Request explicit new source facts after recovery."),
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
    result = [replace(page, members=tuple(buckets[page.canonical_id])) for page in _PAGES]
    result.append(
        NavigationInput(
            "",
            "Use governed definitions to answer an analytical question.",
            ("inputs", "source", "frame", "execution", "results", "sessions"),
            "root",
            guidance=(
                "Imports: import marivo.analysis as mv",
                "Choose the information you need next; known targets and objects can enter directly.",
                "Construction captures dependencies. compute fixes data; persist saves it. Continue with the returned Frame; its Artifact is saved audit.",
                "Known object: inspect its captured facts or type Help. Structured errors own concrete repairs; the agent owns interpretation and conclusions.",
            ),
        )
    )
    return tuple(result)
