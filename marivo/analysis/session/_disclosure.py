"""Native Session and retained Runtime read inputs, bound only to owners."""

from __future__ import annotations

from inspect import signature

from marivo.analysis import session as session_namespace
from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis._capabilities.dataset_model import (
    ParameterInput as P,
)
from marivo.analysis.core.frame_page import (
    FrameCalendarPeriod,
    FrameComparisonPeriod,
    FrameIntervalPeriod,
    FramePage,
    FramePairedPeriod,
    FramePeriodPage,
    FrameReadStats,
)
from marivo.analysis.core.frame_source_reads import (
    FrameSourceRead,
    FrameSourceReadPeriod,
    FrameSourceReadScope,
)
from marivo.analysis.session import _lazy_read_model as r
from marivo.analysis.session._method_disclosure import section as _doc_section
from marivo.analysis.session.core import Session


def provider() -> DisclosureProvider:
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    descriptors.append(
        value_type(
            "Session",
            Session,
            summary="One Session binds logical construction and committed Runtime reads.",
            acquisition="Create or recover a name through mv.session.get_or_create, or recover an exact ID through mv.session.resume. Direct construction and extension are unsupported.",
            producers=("session.get_or_create", "session.resume"),
            constraints=(
                "Source construction loads authored semantics on demand; retained reads do not require current sources.",
                "session.timezone is the normalized saved report timezone; session.timezone_origin is explicit or system. System resolution occurs only at creation. Invalid or ambiguous host signals require an explicit timezone.",
                "session.domains is the persisted tuple[str, ...] or None. None selects all authoring domain directories. Explicit names select only those directories, without dependency expansion; include every required domain before creation. Scope is frozen; create a new Session with domains=None or the complete required sequence to widen it.",
            ),
        )
    )
    exports.extend(
        (
            ExportInput("Session", Session, "Session"),
            ExportInput("session", session_namespace, "sessions"),
        )
    )

    operations: tuple[tuple[object, str, str, str, str], ...] = (
        (
            session_namespace,
            "get_or_create",
            "Session",
            "import marivo.analysis as mv\nresult = mv.session.get_or_create('help-repeatable', timezone='UTC')",
            "Create or recover an exact Session name; retain saved metadata, reject explicit configuration conflicts and never set a current Session. Older Stores coexist unchanged; discovered older paths warn once per set per process.",
        ),
        (
            session_namespace,
            "resume",
            "Session",
            "import marivo.analysis as mv\nresult = mv.session.resume(saved_session_id)",
            "Recover an existing exact Session ID; no creation or activation. Missing current Store or ID raises SessionNotFoundError; older paths provide guidance without verifying ID ownership.",
        ),
        (
            session_namespace,
            "recent",
            "SessionSummaryPage",
            "import marivo.analysis as mv\nresult = mv.session.recent(limit=5)",
            "Read current-generation Session history; return an empty page when its Store is absent. No creation or activation. Older paths warn once per set per process without opening older Stores.",
        ),
        (
            session_namespace,
            "inspect",
            "SessionSummary",
            "import marivo.analysis as mv\nresult = mv.session.inspect(saved_session_id)",
            "Read one existing Session snapshot; no recovery or activation. Missing current Store or ID raises SessionNotFoundError; older paths provide guidance without verifying ID ownership.",
        ),
        (
            Session,
            "members",
            "Population",
            "result = session.members(entity)",
            "Capture source Entity identities with their own exact anchor and condition; no schema or business rows. Use session.frame(population) for complete keys.",
        ),
        (
            Session,
            "frame",
            "AnalysisFrame",
            "result = session.frame(population)",
            "Capture a general complete Population key domain, including multiple axes and zero-axis empty/singleton domains; named values may be empty.",
        ),
        (
            Session,
            "observe",
            "AnalysisFrame",
            "period = session.time_scope(start='2026-08-01', end='2026-09-01')\n"
            "result = session.observe(revenue, where=period).compute()\n"
            "result.show()",
            "Observe named Metrics overall, by time/classification, or both.",
        ),
        (
            Session,
            "read",
            "AnalysisFrame",
            "result = session.read(population, fields)",
            "Read named scalar attributes for complete identities, trusting source declarations; missing matches yield NULL. Numeric reads preserve NULL, NaN and Infinity; finite local operations validate their demanded inputs.",
        ),
        (
            Session,
            "values",
            "AnalysisFrame",
            "result = session.values(by=(), columns={'budget': 'CNY'}, rows=(((), {'budget': 100}),))",
            "Capture finite literal value input with one unit declaration per named column; no DataFrame import or source read.",
        ),
        (
            Session,
            "persisted_frames",
            "PersistedFrameSummaryPage",
            "result = session.persisted_frames(limit=5)\nresult.show()",
            "Discover committed Frames by caller description and captured schema, without result files, Catalog or source reads. Select an exact artifact_id; inspect ambiguous candidates.",
        ),
        (
            Session,
            "persisted_frame",
            "AnalysisFrame",
            "session = mv.session.resume(session_id)\n"
            "result = session.persisted_frame(artifact_id)",
            "Open one exact committed Frame ID in this Session using metadata only, without result rows, model, Catalog, schema or source access; the Frame exposes captured source-read metadata directly.",
        ),
        (
            Session,
            "population_from",
            "Population",
            "result = session.population_from(frame, on=((frame.axes[0], target),))",
            "Bind complete ordinary Frame keys to explicit semantic targets for a new source question.",
        ),
        (
            Session,
            "show",
            "None",
            "result = session.show()",
            "Print a bounded retained Session recap.",
        ),
    )
    operations = (
        *operations,
        (
            Session,
            "instant",
            "InstantAnchor",
            "result = session.instant('2026-01-01')",
            "Freeze one exact instant without source reads.",
        ),
        (
            Session,
            "time_scope",
            "TimeScope",
            "result = session.time_scope(start='2026-01-01', end='2026-02-01')",
            "Freeze [start,end) in Session timezone; aware boundaries keep their instants.",
        ),
        (
            Session,
            "render",
            "str",
            "result = session.render()",
            "Render the bounded retained Session recap.",
        ),
    )
    acquisition = {
        "frame": "Same-Session AnalysisFrame; IDs, refs and raw tables reject.",
        "on": "Required immutable tuple of (FrameAxis, closed Field/Entity) pairs; () selects zero axes. Names/types never infer targets.",
        "value": "Pass an ISO string, date, aware datetime or existing InstantAnchor.",
        "timezone": "None uses the frozen Session timezone unless explicitly overridden.",
        "start": "Choose the inclusive exact boundary.",
        "end": "Choose the exclusive exact boundary, strictly after start.",
        "where": "Pass a Deferred combining field and Population membership conditions. Source receivers may read fields needed for membership; AnalysisFrame.where accepts only a same-input boolean Column.",
        "at": "Pass an exact InstantAnchor from session.instant(...) for versioned identity selection.",
        "population": "Pass a complete Population with no owner or the same Session owner.",
        "fields": "Pass a nonempty named mapping of resolved scalar fields with explicit paths and required version anchors.",
        "columns": "Map each literal value name to its one declared unit, or None.",
        "rows": "Pass finite complete key tuples paired with every declared literal value name.",
        "max_output_bytes": "Use the default byte budget or explicitly request a tighter output bound.",
        "limit": "Choose a page size within the owning read's bounded interval.",
        "cursor": "Use the preceding page's opaque next_cursor with the identical selection.",
        "session_id": "Use the exact existing Session id.",
        "artifact_id": "Use the exact saved Frame's artifact_id string from this Session; objects and Refs are not IDs.",
        "metrics": "Root Metric or nonempty list/tuple: local names in order. Expressions and counts need a nonempty Mapping {'total': expr}. Names: distinct, nonempty, not business identity; no dots, backslashes, controls or left_present/right_present.",
        "entity": "Pass a resolved governed Entity, not a Ref or path.",
    }
    parameter_targets: dict[str, tuple[str, ...]] = {
        "metrics": ("inputs.catalog", "semantic.expressions"),
        "entity": ("inputs.catalog",),
        "fields": ("inputs.catalog", "semantic.field"),
        "population": ("inputs.population",),
        "frame": ("AnalysisFrame",),
        "where": ("semantic.predicate",),
        "at": ("session.instant",),
        "on": ("FrameAxis", "inputs.catalog"),
        "session_id": ("session.recent",),
        "artifact_id": ("analysis_frame.artifact_id", "session.persisted_frames"),
    }
    for receiver, name, output, code, effect in operations:
        value = getattr(receiver, name)
        descriptors.append(
            operation(
                ("Session." if name in ("show", "render") else "session.") + name,
                ("mv.session." if receiver is session_namespace else "session.") + name,
                value,
                bindings=(bind(value, receiver if isinstance(receiver, type) else None),),
                summary=effect,
                discovery_group=(
                    "inputs.population"
                    if name in ("members", "population_from")
                    else "operators"
                    if name in ("observe", "read", "frame", "values")
                    else "inputs.time"
                    if name in ("instant", "time_scope")
                    else None
                    if name in ("show", "render")
                    else "sessions"
                ),
                related=("session.resume", "session.persisted_frames")
                if name == "persisted_frame"
                else ("session.persisted_frame", "runtime.PersistedFrameSummaryPage")
                if name == "persisted_frames"
                else (
                    "analysis_frame.compute",
                    "semantic.source_check",
                )
                if name == "observe"
                else ("session.observe", "TimeScope.by")
                if name == "time_scope"
                else (),
                parameters=tuple(
                    P(
                        n,
                        "Use an exact project-local name. To recover an existing Session, pass only name: mv.session.get_or_create(name). Omit question, timezone and domains to retain the saved context."
                        if name == "get_or_create" and n == "name"
                        else "None stores no description on creation and retains the saved description on recovery. Non-None values must match the saved question."
                        if name == "get_or_create" and n == "question"
                        else "IANA name or explicit UTC offset. None strictly resolves the system IANA zone once on creation; unresolved TZ/localtime requires an explicit value. Recovery retains the saved timezone and origin without host resolution; non-None values must have the same normalized identity."
                        if name == "get_or_create" and n == "timezone"
                        else "None selects all authoring domain directories on creation and retains the saved scope on recovery. An exact name or nonempty sequence selects only those directories, without dependency expansion. Non-None values must match the saved sorted, deduplicated scope; use a new name to widen it."
                        if name == "get_or_create" and n == "domains"
                        else "Choose an integer from 1 through 100, excluding bool; default 20. Keep the same limit when continuing a cursor."
                        if name == "persisted_frames" and n == "limit"
                        else "Dimensions/Groupings tuple with at most one CalendarAxis: actual complete keys, no missing-bucket rows; () gives one cell. by=P retains complete keys without filtering facts; cover required keys before sampling. Time in by excludes TimeScope in where."
                        if name == "observe" and n == "by"
                        else "Pass resolved fields or field or identity Grouping values; calendar and membership groupings are rejected. Use () for scalar literal rows."
                        if name == "values" and n == "by"
                        else "None, RowPredicate, TimeScope or AND tuple: where=(scope, predicate); at most one TimeScope. TimeScope cannot use & or |. mv.in_population(P) filters facts. Field comparisons stay fixed through windows and supply no endpoint. Population.count domains stay fixed."
                        if name == "observe" and n == "where"
                        else "Population with exactly one complete Entity identity axis, unowned or from this Session. Project that identity axis before reading a classified or multi-axis domain."
                        if name == "read" and n == "population"
                        else acquisition[n],
                        (
                            "inputs.catalog",
                            "inputs.population",
                            "time_scope.by",
                        )
                        if name == "observe" and n == "by"
                        else ("semantic.predicate", "session.time_scope")
                        if name == "observe" and n == "where"
                        else ("inputs.catalog", "identity")
                        if name == "values" and n == "by"
                        else parameter_targets.get(n, ()),
                    )
                    for n in signature(value).parameters
                    if n != "self"
                ),
                output=output,
                constraints=(_doc_section(value, "Constraints"),)
                if name
                in (
                    "observe",
                    "get_or_create",
                    "members",
                    "frame",
                    "read",
                    "values",
                    "persisted_frame",
                    "persisted_frames",
                    "population_from",
                )
                else (effect,),
                effects=effect,
                telemetry=name in ("get_or_create", "resume"),
                failures=(
                    "SessionStateError: explicit configuration conflicts identify the saved and requested values; error.repair.candidates contains the existing Session ID. Recovery also respects the resume writer guard."
                    if name == "get_or_create"
                    else "AnalysisError: read its fields and repair."
                    if name == "members"
                    else "Duplicate/invalid names reject; no name cleaning/numbering."
                    if name == "observe"
                    else "AnalysisError: follow the structured identity, bound or recovery repair.",
                ),
                example=ExampleInput(
                    code,
                    ()
                    if name in ("get_or_create", "recent")
                    else ("saved_session_id",)
                    if name == "resume"
                    else ("saved_session_id",)
                    if name == "inspect"
                    else ("session_id", "artifact_id")
                    if name == "persisted_frame"
                    else ("session", "frame", "target")
                    if name == "population_from"
                    else ("session", "revenue")
                    if name == "observe"
                    else ("session", "population")
                    if name == "frame"
                    else ("session", "population", "fields")
                    if name == "read"
                    else ("session", "entity")
                    if name == "members"
                    else ("session", "buyer", "members", "window")
                    if name == "anchors"
                    else ("session",),
                    "result",
                    output,
                    name not in ("members", "frame", "persisted_frame", "population_from"),
                ),
            )
        )
    # These are retained nested return values, not additional public exports.
    nested_reads = (
        (
            r.PersistedFrameSummaryPage,
            "Call session.persisted_frames(); select a candidate from items using its description and schema.",
            ("session.persisted_frames",),
            ("runtime.PersistedFrameSummary", "session.persisted_frame"),
        ),
        (
            r.PersistedFrameSummary,
            "Read session.persisted_frames().items; inspect description and schema, then select an exact artifact_id.",
            ("runtime.persisted_frame_summary_page.items",),
            ("frame_schema", "session.persisted_frame"),
        ),
        (
            r.SessionSummaryPage,
            "Call mv.session.recent(); select an exact item from items.",
            ("session.recent",),
            ("runtime.SessionSummary", "session.resume", "session.inspect"),
        ),
        (
            r.SessionSummary,
            "Read a selected recent().items entry or inspect(identity); retain its id/name.",
            ("runtime.session_summary_page.items", "session.inspect"),
            ("session.resume", "session.persisted_frames"),
        ),
    )
    for value, acquisition_text, producers, consumers in nested_reads:
        descriptors.append(
            value_type(
                "runtime." + value.__name__,
                value,
                summary=f"Nested immutable {value.__name__} read contract.",
                acquisition=acquisition_text,
                producers=producers,
                consumers=consumers,
            )
        )
    for method_name, output in (("render", "str"), ("show", "None")):
        bindings = tuple(
            bind(getattr(value, method_name), value)
            for value in (
                FrameSourceRead,
                FrameSourceReadScope,
                FrameSourceReadPeriod,
                FramePage,
                FrameReadStats,
                FramePeriodPage,
                FrameCalendarPeriod,
                FrameIntervalPeriod,
                FramePairedPeriod,
                FrameComparisonPeriod,
                r.SessionSummaryPage,
                r.SessionSummary,
                r.PersistedFrameSummaryPage,
                r.PersistedFrameSummary,
            )
            if hasattr(value, method_name)
        )
        descriptors.append(
            operation(
                "runtime.values." + method_name,
                "value." + method_name,
                bindings[0].implementation,
                bindings=bindings,
                summary="Display a result within its byte budget.",
                parameters=(
                    P(
                        "max_output_bytes",
                        "Use the default 8192-byte budget, another bound, or None for no byte limit.",
                    ),
                ),
                output=output,
                constraints=(
                    "For retained Analysis Runtime values, display does not activate, recover or execute a Session.",
                    "Other receivers can read local metadata, such as datasource declarations.",
                ),
                effects="Reads depend on the receiver; retained Analysis Runtime values do not query sources.",
                failures=("ValueError: use a valid output byte bound.",),
                example=ExampleInput(
                    f"result = page.{method_name}()",
                    ("page",),
                    "result",
                    output,
                    True,
                ),
            )
        )
    from marivo.analysis._capabilities.dataset_model import NavigationInput

    descriptors.append(
        NavigationInput(
            "inputs.catalog",
            "Find governed inputs, retrieve an exact path or explain a definition.",
            (),
            guidance=(
                "Acquire catalog = session.catalog. Discover objects with search; copy canonical kind:path identities and retrieve with get.",
                "Use page.show() for bounded candidates and entry.show() for captured definitions. Explicit filters constrain discovery; related navigation uses the full loaded Catalog scope.",
                "Check object.kind and use the typed object directly in its allowed operations; the consuming AnalysisFrame.show() explains captured scope and operations.",
            ),
            related=(
                "semantic.semantic_catalog.search",
                "semantic.semantic_catalog.get",
                "semantic.catalog_page",
                "semantic.readiness",
                "session.get_or_create",
                "authoring",
            ),
            discovery_group="inputs",
            route_summaries=(
                (
                    "semantic.readiness",
                    "Check dependency readiness without proving method execution.",
                ),
                ("semantic.semantic_catalog.search", "Discover business candidates."),
                ("semantic.semantic_catalog.get", "Retrieve and explain an existing definition."),
                ("semantic.catalog_page", "Read candidates, match facts and continuation cursors."),
                ("session.get_or_create", "Acquire context before using session.catalog."),
                (
                    "authoring",
                    "Missing business meaning or aggregation permission: author it explicitly.",
                ),
            ),
        )
    )

    from marivo.analysis.dsl._disclosure import provider as terminal_provider

    terminal = terminal_provider()
    descriptors.extend(terminal.descriptors)
    exports.extend(terminal.exports)
    return DisclosureProvider("runtime", tuple(descriptors), tuple(exports))
