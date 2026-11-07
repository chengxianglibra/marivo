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
            "Population and Entity counts are Metrics for observe. Population counts have no contribution clock.",
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
        "Serve current definitions, saved reports and explicitly refreshed script endpoints.",
        (),
        guidance=(
            "A fixed reference reads the same saved result. A named endpoint reads its current saved result; an explicit refresh executes trusted project code and publishes a new fixed reference.",
            "Use the host-selected project, environment and credential handling. The host owns its connection and user authorization.",
        ),
        discovery_group="results",
        route_summaries=(
            ("results.service.scripts", "Write and discover a refreshable endpoint script."),
            ("results.service.http", "Start the service and read or refresh over HTTP."),
        ),
    ),
    NavigationInput(
        "results.service.scripts",
        "Define a trusted project script for an explicitly refreshed data endpoint.",
        (),
        "service_reference",
        guidance=(
            "Place endpoints/sales/total.py in the bound project for /v1/data/sales/total. Path segments are lowercase snake_case; private names beginning with _ are not endpoints. Restart after adding a path; an existing path loads its latest source on refresh.",
            "Define exactly one top-level synchronous main(session: mv.Session) with no defaults or extra arguments. Its concrete return annotation and value must both be mv.AnalysisFrame or both pandas.DataFrame; async functions, generators and union returns are rejected.",
            "The service supplies a fresh project Session. An AnalysisFrame must belong to that Session and be unpersisted; a pending or computed Frame is accepted. The service computes and persists it. Set the intended time scope in the script; publication does not imply fresh or complete source data.",
            "A DataFrame needs an unnamed default RangeIndex, unique nonempty string column names and supported scalar columns: null, boolean, string, integer, float32/64, decimal, date, timestamp or duration. Move business indexes into columns. Nested or unsupported values fail; attrs is not saved as semantic metadata.",
            "Scripts and imports are trusted project code. Named endpoint requests do not accept a script, arbitrary path or business parameters. Replace the example Metric identity with one established in the project.",
        ),
        example=ExampleInput(
            "import marivo.analysis as mv\n"
            "\n"
            "def main(session: mv.Session) -> mv.AnalysisFrame:\n"
            "    total = session.catalog.get('metric:sales.total')\n"
            "    assert total.kind == 'metric'\n"
            "    return session.observe({'total': total})",
            ("session",),
            "main",
            "An unpersisted AnalysisFrame owned by the supplied Session.",
        ),
        discovery_group="results.service",
    ),
    NavigationInput(
        "results.service.http",
        "Start the project service and consume saved or explicitly refreshed results.",
        (),
        guidance=(
            "Install marivo[api], configure MARIVO_API_TOKEN as a deployment credential, and run marivo serve --project-root PATH. The default listener is 127.0.0.1:8765; --port 0 selects a free port and stdout reports the bound address. Use marivo serve --help for TLS, CORS and budgets.",
            "All business requests, including GET /v1/openapi.json and GET /v1/info, require Authorization: Bearer TOKEN. The token grants reads, Catalog reload and execution of installed endpoint scripts; the host owns user authorization and project selection.",
            "GET /v1/data/{endpoint} reads its current saved result. POST /v1/data/{endpoint}/refresh with an empty body executes the script and publishes a new result. Neither route accepts query parameters. Do not automatically retry refresh. Before first publication a saved read returns result_not_found. If a refresh outcome is uncertain, read current state before retrying.",
            "Responses carry kind and a fixed ref. Save the service connection and ref; for AnalysisFrames also retain store_id from /v1/info. Read a fixed Frame with /v1/sessions/{session_id}/frames/{artifact_id}, or a fixed DataFrame with /v1/dataframes/{result_id}. These reads never refresh, load models or query sources.",
            "The service returns a complete report or an error, not a paged data preview. Schema and data layout preserve exact string-encoded integers and decimals, nulls and typed values. Directories use limit and cursor. The API has no named endpoint directory; obtain names from the project or host.",
            "Errors return code, message, location, repair and nullable target. Inspect the repair. POST /v1/semantic/reload starts a new load or returns 409 catalog_load_in_progress if a load is active. Shared Catalog loads continue after disconnect until their deadline; failed loads retain the last snapshot. Inspect service logs for unexpected failures. Saved results remain fixed when a named endpoint is refreshed.",
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
                "In an authored project, reuse your Session; adapt query, ID, dates and timezone.",
                "Choose by meaning, or use a known ID. All Metric branches need contribution time.",
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
