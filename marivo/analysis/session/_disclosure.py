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
from marivo.analysis.evidence import _dataset_types as e
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session import _lazy_read_model as r
from marivo.analysis.session._method_disclosure import section as _doc_section
from marivo.analysis.session.core import Session

READ_TYPES: tuple[type[object], ...] = (
    e.ArtifactDigest,
    ArtifactRef,
    r.FailedRun,
    e.Finding,
    e.FindingPage,
    r.IncompleteRun,
    r.RunPage,
    r.SucceededRun,
)


def provider() -> DisclosureProvider:
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    acquisitions = {
        "ArtifactDigest": (
            "Call artifact.evidence_digest(); .items contains all saved output facts.",
            ("Artifact.evidence_digest",),
            ("runtime.OutputEvidence", "runtime.values.render", "runtime.values.show"),
        ),
        "ArtifactRef": (
            "Read a SucceededRun.output_artifact_ref; pass its .ref string to session.persisted_frame; read .artifact for audit.",
            ("SucceededRun",),
            ("session.persisted_frame",),
        ),
        "FailedRun": (
            "Select a failed Run from session.runs() or session.get_run(id).",
            ("session.runs", "session.get_run"),
            ("runtime.values.render", "runtime.values.show"),
        ),
        "IncompleteRun": (
            "Select an incomplete Run from session.runs() or session.get_run(id).",
            ("session.runs", "session.get_run"),
            ("session.reconcile_run",),
        ),
        "SucceededRun": (
            "Select a succeeded Run from session.runs() or session.get_run(id).",
            ("session.runs", "session.get_run"),
            ("session.persisted_frame",),
        ),
        "RunPage": (
            "Call session.runs(); use next_cursor for the identical selection.",
            ("session.runs",),
            ("session.runs", "session.get_run", "SucceededRun", "FailedRun", "IncompleteRun"),
        ),
        "Finding": (
            "Select an exact self-contained output item from artifact.findings().",
            ("Artifact.findings",),
            ("runtime.OutputCoordinate", "runtime.values.render", "runtime.values.show"),
        ),
        "FindingPage": (
            "Call artifact.findings(); retain next_cursor for this Artifact.",
            ("Artifact.findings",),
            ("Artifact.findings",),
        ),
    }
    for value in READ_TYPES:
        descriptors.append(
            value_type(
                value.__name__,
                value,
                summary=f"Immutable {value.__name__} contract.",
                acquisition=acquisitions[value.__name__][0],
                producers=acquisitions[value.__name__][1],
                consumers=acquisitions[value.__name__][2],
                constraints=(
                    "Retained metadata is not current source truth or a freshness verdict.",
                ),
            )
        )
        exports.append(ExportInput(value.__name__, value, value.__name__))
    descriptors.append(
        value_type(
            "Session",
            Session,
            summary="One Session binds logical construction and committed Runtime reads.",
            acquisition="Create or recover through mv.session.create or mv.session.resume.",
            producers=("session.create", "session.resume"),
            constraints=(
                "Source construction loads authored semantics on demand; retained reads do not require current sources.",
                "session.domains is the persisted tuple[str, ...] or None for all domains; domains remain frozen after creation.",
            ),
        )
    )
    exports.extend(
        (
            ExportInput("Session", Session, "Session"),
            ExportInput("session", session_namespace, "session.namespace"),
        )
    )

    operations: tuple[tuple[object, str, str, str, str], ...] = (
        (
            session_namespace,
            "create",
            "Session",
            "import marivo.analysis as mv\nresult = mv.session.create('help-example')",
            "Atomically create a uniquely named Session with frozen timezone and domains; duplicate names reject with the existing ID.",
        ),
        (
            session_namespace,
            "resume",
            "Session",
            "import marivo.analysis as mv\nresult = mv.session.resume(saved_session_id)",
            "Recover an existing exact Session ID; no creation or activation.",
        ),
        (
            session_namespace,
            "recent",
            "SessionSummaryPage",
            "import marivo.analysis as mv\nresult = mv.session.recent(limit=5)",
            "Read current-generation Session history; return an empty page when its Store is absent. No creation or activation.",
        ),
        (
            session_namespace,
            "inspect",
            "SessionInspection",
            "import marivo.analysis as mv\nresult = mv.session.inspect(saved_session_id)",
            "Read one existing Session snapshot; no recovery or activation.",
        ),
        (
            session_namespace,
            "reconcile_run",
            "None",
            "import marivo.analysis as mv\nresult = mv.session.reconcile_run(session_id=session.id, run_id=pending_run)",
            "Reconcile one Run under its writer guard; local publication must be safe while remote read status may remain unknown.",
        ),
        (
            Session,
            "members",
            "Population",
            "result = session.members(entity)",
            "Capture source Entity identities with their own exact anchor and condition; no schema, business rows or Run. Use session.frame(population) for complete keys.",
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
            "result = session.observe(metrics, by=(calendar, field))",
            "Evaluate named Metrics at an explicit output grain: overall, by time, by classification, or their combination.",
        ),
        (
            Session,
            "read",
            "AnalysisFrame",
            "result = session.read(population, fields)",
            "Bind this Session explicitly and read named scalar attributes while retaining every complete identity.",
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
            "persisted_frame",
            "AnalysisFrame",
            "result = session.persisted_frame(artifact_id)",
            "Open one exact committed Frame ID in this Session using metadata only, without result rows, model, Catalog, schema or source access; .artifact provides audit.",
        ),
        (
            Session,
            "population_from",
            "Population",
            "result = session.population_from(frame, on=((frame.axes[0], target),))",
            "Bind selected complete ordinary Frame keys to explicit caller semantic targets for a new source Population; construction reads no rows.",
        ),
        (
            Session,
            "runs",
            "RunPage",
            "result = session.runs(limit=5)",
            "Read a bounded newest-first page without reconciling work.",
        ),
        (
            Session,
            "get_run",
            "IncompleteRun | FailedRun | SucceededRun",
            "result = session.get_run(run_id)",
            "Read one exact same-Session Run.",
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
            "Freeze half-open exact boundaries and their calendar timezone.",
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
        "frame": "Pass an AnalysisFrame from this exact Store/Session, not an Artifact, ID or raw table.",
        "on": "Required immutable tuple of (exact FrameAxis, resolved Field or Entity) pairs; () is legal only for a zero-axis projection. Bind complete ordinary field/identity axes explicitly; calendar/paired axes are unsupported. Names, tokens and same types do not infer semantic targets.",
        "value": "Pass an ISO string, date, aware datetime or existing InstantAnchor.",
        "timezone": "On create, None resolves and freezes the system timezone; instant/time_scope use the frozen Session timezone unless explicitly overridden.",
        "start": "Choose the inclusive exact boundary.",
        "end": "Choose the exclusive exact boundary, strictly after start.",
        "where": "Pass a resolved Field predicate or closed PopulationPredicate. Source receivers may read fields needed for membership; AnalysisFrame.where accepts only a same-input boolean Column.",
        "at": "Pass an exact InstantAnchor from session.instant(...) for versioned identity selection.",
        "population": "Pass a complete Population with no owner or the same Session owner.",
        "fields": "Pass a nonempty named mapping of resolved scalar fields with explicit paths and required version anchors.",
        "columns": "Map each literal value name to its one declared unit, or None.",
        "rows": "Pass finite complete key tuples paired with every declared literal value name.",
        "max_output_bytes": "Use the default byte budget or explicitly request a tighter output bound.",
        "name": "Choose a new project-local name without surrounding whitespace.",
        "domains": "Select an exact domain name or nonempty sequence on first creation; existing Sessions retain their persisted scope and reject conflicting explicit selections.",
        "question": "Optional description frozen at creation.",
        "by": "Choose name or id explicitly when resolving an ambiguous identity.",
        "limit": "Choose a page size within the owning read's bounded interval.",
        "cursor": "Use the preceding page's opaque next_cursor with the identical selection.",
        "run_limit": "Choose the bounded embedded Run-page size.",
        "run_cursor": "Use the preceding inspection's runs.next_cursor.",
        "session_id": "Use the exact existing Session id.",
        "run_id": "Use an exact incomplete, failed or succeeded Run id from session.runs().",
        "artifact_id": "Use an exact committed Artifact ID string from this Session; objects and Refs are not IDs.",
        "metrics": "Pass a nonempty ordered mapping from output names to resolved Metric expressions; Artifact objects and IDs are rejected.",
        "entity": "Pass a resolved governed Entity, not a Ref or path.",
        "status": "Choose incomplete, failed, succeeded or None.",
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
                discovery_group="population_tasks"
                if name == "population_from"
                else "methods.metric"
                if name == "observe"
                else "entry"
                if name in ("members", "frame", "read", "values")
                else "inputs.time"
                if name in ("instant", "time_scope")
                else "methods.events"
                if name == "anchors"
                else "session.namespace"
                if receiver is session_namespace
                else "runtime.sessions"
                if name in ("show", "render")
                else "runtime.runs"
                if name in ("runs", "get_run")
                else "runtime",
                related=("session.resume",)
                if name in ("runs", "get_run", "persisted_frame")
                else ("analysis_frame.compute", "analysis_frame.persist", "TimeScope.by")
                if name == "observe"
                else ("session.observe", "TimeScope.by")
                if name in ("instant", "time_scope")
                else (),
                parameters=tuple(
                    P(
                        n,
                        "Use an exact incomplete or failed Run id; committed success cannot be abandoned."
                        if n == "run_id" and name == "reconcile_run"
                        else "Empty () is one zero-axis cell. Pass fields, Grouping rules or TimeScope as an ordered tuple, or a Population selecting complete output keys. TimeScope contributes an interval axis; CalendarAxis contributes calendar buckets. population.axes carries rules without selected keys or allocation bindings."
                        if name == "observe" and n == "by"
                        else "Pass resolved fields or field or identity Grouping values; calendar and membership groupings are rejected. Use () for scalar literal rows."
                        if name == "values" and n == "by"
                        else "TimeScope filters without grouping; CalendarAxis adds time buckets and must match an existing output calendar; None adds no time filter."
                        if name == "observe" and n == "during"
                        else "Pass a Field predicate or closed PopulationPredicate restricting fact contributions. Captured Population.count domains are not reselected; count-only conditions reject."
                        if name == "observe" and n == "where"
                        else acquisition[n],
                    )
                    for n in signature(value).parameters
                    if n != "self"
                ),
                output=output,
                constraints=(_doc_section(value, "Constraints"),)
                if name
                in (
                    "members",
                    "observe",
                    "frame",
                    "read",
                    "values",
                    "persisted_frame",
                    "population_from",
                )
                else (effect,),
                effects=effect,
                telemetry=name in ("create", "resume", "reconcile_run"),
                failures=(
                    "AnalysisError: read its fields and repair."
                    if name == "members"
                    else "AnalysisError: inspect the structured identity, bound or recovery repair; no eager fallback.",
                ),
                example=ExampleInput(
                    code,
                    ()
                    if name in ("create", "recent")
                    else ("saved_session_id",)
                    if name == "resume"
                    else ("saved_session_id",)
                    if name == "inspect"
                    else ("session", "pending_run")
                    if name == "reconcile_run"
                    else ("session", "artifact_id")
                    if name == "persisted_frame"
                    else ("session", "frame", "target")
                    if name == "population_from"
                    else ("session", "metrics", "calendar", "field")
                    if name == "observe"
                    else ("session", "population")
                    if name == "frame"
                    else ("session", "population", "fields")
                    if name == "read"
                    else ("session", "run_id")
                    if name == "get_run"
                    else ("session", "entity")
                    if name == "members"
                    else ("session", "buyer", "members", "window")
                    if name == "anchors"
                    else ("session",),
                    "result",
                    output,
                    name
                    not in ("members", "observe", "frame", "persisted_frame", "population_from"),
                ),
            )
        )
    # These are retained nested return values, not additional public exports.
    nested_reads = (
        (
            e.OutputEvidence,
            "Read artifact.evidence_digest().items; each item is a saved declaration, read or check fact.",
            ("ArtifactDigest",),
            ("runtime.OutputCoordinate", "runtime.values.render", "runtime.values.show"),
        ),
        (
            e.OutputCoordinate,
            "Read a saved Evidence or Finding .coordinates item; slot_id locates its output column and value is the saved scalar.",
            ("runtime.OutputEvidence", "Finding"),
            ("runtime.values.render", "runtime.values.show"),
        ),
        (
            r.SessionSummaryPage,
            "Call mv.session.recent(); select an exact item from items.",
            ("session.recent",),
            ("runtime.SessionSummary", "session.resume", "session.inspect"),
        ),
        (
            r.SessionInspection,
            "Call mv.session.inspect(identity) without activating a Session.",
            ("session.inspect",),
            ("runtime.SessionSummary", "RunPage"),
        ),
        (
            r.SessionSummary,
            "Read a selected recent().items entry or inspect(identity).summary; retain its id/name.",
            ("runtime.SessionSummaryPage", "runtime.SessionInspection"),
            ("session.resume",),
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
                *READ_TYPES,
                r.SessionSummaryPage,
                r.SessionInspection,
                r.SessionSummary,
                e.OutputEvidence,
                e.OutputCoordinate,
            )
            if hasattr(value, method_name)
        )
        descriptors.append(
            operation(
                "runtime.values." + method_name,
                "value." + method_name,
                bindings[0].implementation,
                bindings=bindings,
                summary="Render one immutable retained Runtime value within its byte budget.",
                discovery_group="runtime.values",
                parameters=(
                    P(
                        "max_output_bytes",
                        "Use the default 8192-byte budget or an explicit tighter bound.",
                    ),
                ),
                output=output,
                constraints=(
                    "Rendering retained metadata does not activate, recover or execute a Session.",
                ),
                effects="Pure value rendering.",
                failures=("ValueError: use a valid output byte bound.",),
                example=ExampleInput(
                    f"result = artifact_digest.{method_name}()",
                    ("artifact_digest",),
                    "result",
                    output,
                    True,
                ),
            )
        )
    from marivo.analysis._capabilities.catalog_inputs import CATALOG_INPUTS
    from marivo.analysis._capabilities.dataset_model import NavigationInput

    descriptors.extend(CATALOG_INPUTS)
    temporal = tuple(
        d.canonical_id for d in CATALOG_INPUTS if d.receiver_family != "SemanticCatalog"
    )
    ordinary = tuple(
        d.canonical_id for d in CATALOG_INPUTS if d.receiver_family == "SemanticCatalog"
    )
    descriptors.append(
        NavigationInput("catalog.temporal", "Resolve certified temporal values.", temporal)
    )
    descriptors.append(
        NavigationInput(
            "catalog",
            "Resolve exact governed inputs and check readiness for the required closure.",
            (
                "catalog.require",
                "catalog.readiness",
                *(t for t in ordinary if t not in ("catalog.require", "catalog.readiness")),
                "catalog.temporal",
            ),
            guidance=(
                "Acquire catalog = session.catalog. With exact refs, use require; check physical dependencies with readiness. Browse a typed collection only when identity is unknown.",
                "Discover bounded choices with collection.show() and retain an exact collection.refs Ref. Describe Entity, field, Metric or Relationship definitions with catalog.describe(ref).show(); marivo.help('semantic.semantic_catalog.describe') owns that contract.",
                "collection.get(full_path) and catalog.require(ref) return expression values or metadata entries. Native Deferred inputs carry no show()/contract(); the consuming AnalysisFrame.show() explains its captured scope and operations.",
            ),
            discovery_group="inputs",
        )
    )

    from marivo.analysis.dsl._disclosure import provider as terminal_provider

    terminal = terminal_provider()
    descriptors.extend(terminal.descriptors)
    exports.extend(terminal.exports)
    return DisclosureProvider("runtime", tuple(descriptors), tuple(exports))
