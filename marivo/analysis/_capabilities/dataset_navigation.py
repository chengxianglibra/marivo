"""Native task navigation, separate from the exact type and member inventory."""

from __future__ import annotations

from dataclasses import replace

from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    DisclosureProvider,
    ExampleInput,
    NavigationInput,
    invalid,
)

# Each provider owns primary capability membership. These pages own task choices,
# short decision summaries and secondary links. The root owns one quick-start example;
# exact callable and service reference pages retain their own examples.
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
            "Use TimeScope in observe.where for range-only totals, or scope.by(grain) in observe.by for finite output buckets. An InstantAnchor selects an exact anchor. Endpoint correspondence is a separate choice.",
        ),
        related=("inputs.comparison",),
        discovery_group="inputs",
        route_summaries=(
            ("inputs.comparison", "Choose comparison endpoint correspondence."),
            ("session.instant", "Freeze an exact time anchor."),
            ("session.time_scope", "Choose a half-open observation interval."),
            ("grain.define", "Choose a built-in bucket unit and count."),
            ("time_scope.by", "Freeze a calendar domain for a Population or a shifted baseline."),
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
            "Entity/Population counts are Metrics; Population counts have no contribution clock.",
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
            "Choose retention before executing: compute fixes data in this Python process; retain the returned Frame for data operations. A one-shot answer needs no save. Derivations remain lazy and need compute/persist.",
            "Only an explicit persist call saves a Frame. For cross-process reuse, retain its session_id and artifact_id, then resume the Session and reopen that Frame. Session recovery alone does not recover unsaved data. An existing saved Frame needs no new publication.",
        ),
        related=("session.persisted_frame",),
        route_summaries=(
            ("analysis_frame.compute", "Obtain complete fixed memory data."),
            ("analysis_frame.persist", "Save a result for exact cross-process recovery."),
            ("session.persisted_frame", "Reopen an exact saved Frame using its retained IDs."),
        ),
    ),
    NavigationInput(
        "results",
        "Inspect captured results or serve definitions and saved data over HTTP.",
        (),
        "decision_hub",
    ),
    NavigationInput(
        "results.inspect",
        "Inspect Frame structure, read data or period pages, or export complete fixed values.",
        (),
        related=(
            "analysis_frame.schema",
            "analysis_frame.source_reads",
            "analysis_frame.method_facts",
            "analysis_frame.lineage",
        ),
        guidance=(
            "schema exposes captured types, complete keys, result contexts, alignments, comparisons and decompositions without reads. Pending show reports captured facts without evaluation. Fixed show displays a bounded prefix; read_page returns ordered projected data with complete keys, read_periods returns saved time periods and consumed endpoint correspondences, and to_pandas exports complete fixed data with isolated axis metadata. Saved source_reads retains original actual reads, not complete filters or the current derived domain; method_facts explains the original method and denominator. These facts establish neither freshness nor cause. Other objects expose show/render through their type Help.",
        ),
        discovery_group="results",
        route_summaries=(
            ("analysis_frame.show", "Inspect captured facts or a bounded fixed-data prefix."),
            ("analysis_frame.schema", "Inspect captured fields, exact types, keys and layout."),
            (
                "analysis_frame.source_reads",
                "Read saved actual source-read identities, times, scopes and returned row counts.",
            ),
            (
                "analysis_frame.method_facts",
                "Read complete original method explanations on a fixed Frame.",
            ),
            ("analysis_frame.lineage", "Inspect captured semantic origins without data reads."),
            ("analysis_frame.read_page", "Read a projected page of fixed data and complete keys."),
            ("analysis_frame.read_periods", "Read saved periods or consumed time correspondences."),
            ("analysis_frame.to_pandas", "Export complete fixed data as an isolated DataFrame."),
        ),
    ),
    NavigationInput(
        "results.service",
        "Read explicitly persisted results through a project-bound HTTP service.",
        (),
        guidance=(
            "Persist AnalysisFrames explicitly, or save pandas tables with persist_dataframe. Keep the service connection and exact result reference in the consumer.",
            "The plugin owns production, date policies, report layout, conclusions and refresh. HTTP reads never execute analysis or load the Catalog.",
        ),
        discovery_group="results",
        route_summaries=(
            ("results.service.http", "Start the read-only service and consume fixed results."),
        ),
    ),
    NavigationInput(
        "results.service.http",
        "Serve fixed results using authenticated HTTP v2.",
        (),
        guidance=(
            "Install marivo[api], set MARIVO_API_TOKEN and run marivo serve --project-root PATH. The JSON readiness line reports the listener. See marivo serve --help for TLS, CORS and read budgets.",
            "All business requests require Authorization: Bearer TOKEN; valid CORS preflights require no token. GET /v2/info reports Store identity; GET /v2/openapi.json declares the complete protocol.",
            "Use saved.ref.to_dict() for an AnalysisFrame, or mv.persist_dataframe(table).to_dict() for a pandas table. Preserve the service connection separately.",
            "Read a Frame at /v2/stores/{store_id}/sessions/{session_id}/frames/{artifact_id}; read a table at /v2/dataframes/{result_id}. A different Store returns invalid_result_reference. Reads require neither producer nor current models.",
            "Responses return complete data or a structured error. Integers and Decimal values are exact strings; nulls and typed time values retain their saved meaning. Directories use limit/cursor.",
            "The service has no named endpoints, refresh, Catalog HTTP or write operations. The host owns credentials, user permissions and report publication.",
        ),
        discovery_group="results.service",
    ),
    NavigationInput(
        "sessions",
        "Create or recover the exact working context.",
        (),
        guidance=(
            "Create or recover a repeatable name, discover retained IDs, inspect metadata or resume an exact existing ID. Session recovery restores context, not Python variables or unsaved Frames, and does not replay source observations.",
            "Reopen exact committed data with persisted_frame(id); read its captured metadata directly. Continue fixed-data operations or explicitly construct new source inputs through operators.",
            "Recover the Session before inspecting an uncertain publication. Discover lost Artifact IDs with persisted_frames(); compare descriptions and schema before selecting an exact ID. Older Stores coexist unchanged; read/export them in a compatible environment. Names, IDs and saved Frames remain independent across generations.",
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
            ("session.persisted_frames", "Discover committed Frames by description and structure."),
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
            "Answer questions with governed Metrics.",
            ("inputs", "operators", "sessions", "execution", "results"),
            "root",
            guidance=(
                "Catalog: definitions. Session: context. Frame: data.",
                "In an authored project, reuse a Session; adapt ID, dates and timezone.",
                "Choose by meaning. Report time affects clocked branches; independent branches retain their scope.",
                "where=scope selects a range; by=(scope.by(grain),) selects report buckets.",
            ),
            example=ExampleInput(
                "import marivo.analysis as mv\n"
                "\n"
                "session = mv.session.get_or_create('revenue-trend', timezone='UTC')\n"
                "catalog = session.catalog\n"
                "catalog.search('revenue', kind='metric', limit=5).show()\n"
                "\n"
                "# Replace with the selected ID.\n"
                "revenue = catalog.get('metric:sales.revenue')\n"
                "assert revenue.kind == 'metric'\n"
                "period = session.time_scope(start='2026-08-01', end='2026-09-01')\n"
                "result = session.observe(\n"
                "    revenue, by=(period.by(mv.grain('day')),),\n"
                ").compute()\n"
                "result.show(n=31)",
                (),
                "result",
                "Daily revenue.",
            ),
            related=("session.get_or_create",),
            route_summaries=(
                ("inputs", "Find or compose inputs."),
                ("operators", "Choose an operation."),
                ("sessions", "Create or recover context."),
                ("execution", "Compute or persist."),
                ("results", "Inspect or serve data."),
                ("session.get_or_create", "Create or reuse a Session."),
            ),
        )
    )
    return tuple(result)
