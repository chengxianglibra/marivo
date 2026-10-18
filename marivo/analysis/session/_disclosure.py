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
    r.ArtifactSummary,
    r.FailedRun,
    e.Finding,
    e.FindingPage,
    r.IncompleteRun,
    r.RunPage,
    r.SessionGraph,
    r.SucceededRun,
)


def provider() -> DisclosureProvider:
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    descriptors.append(
        operation(
            "Session.source_bindings",
            "session.source_bindings",
            Session.source_bindings,
            summary="Capture declared non-secret source parameters during construction.",
            parameters=(
                P("bindings", "Map exact Entity refs to their declared source parameters."),
            ),
            output="AbstractContextManager[None]",
            constraints=("Captured bindings replace the ambient scope and restore it on exit.",),
            effects="Pure semantic capture; execution does not reread ambient parameters.",
            failures=(
                "ObservationBindingError: bind exact Entity refs and declared non-secret source parameters.",
            ),
            discovery_group="inputs",
            related=("session.members",),
            example=ExampleInput(
                "with session.source_bindings({}):\n    result = session.members(entity)",
                ("session", "entity"),
                "result",
                "Population",
            ),
        )
    )

    acquisitions = {
        "ArtifactDigest": (
            "Call artifact.evidence_digest(); .items contains all saved output facts.",
            ("Artifact.evidence_digest",),
            ("runtime.OutputEvidence", "runtime.values.render", "runtime.values.show"),
        ),
        "ArtifactRef": (
            "Read a SucceededRun.output_artifact_ref or ArtifactSummary.artifact_ref; pass its .ref string to session.artifact.",
            ("SucceededRun", "ArtifactSummary"),
            ("session.artifact", "session.graph"),
        ),
        "ArtifactSummary": (
            "Read the selected Artifact entries in session.graph(...).artifacts.",
            ("session.graph",),
            ("runtime.values.render", "runtime.values.show"),
        ),
        "FailedRun": (
            "Select a failed Run from session.runs() or session.get_run(id).",
            ("session.runs", "session.get_run"),
            ("runtime.values.render", "runtime.values.show"),
        ),
        "IncompleteRun": (
            "Select an incomplete Run from session.runs() or session.get_run(id).",
            ("session.runs", "session.get_run"),
            ("session.abandon_run",),
        ),
        "SucceededRun": (
            "Select a succeeded Run from session.runs() or session.get_run(id).",
            ("session.runs", "session.get_run"),
            ("session.artifact",),
        ),
        "RunPage": (
            "Call session.runs(); use next_cursor for the identical selection.",
            ("session.runs",),
            ("session.runs", "session.get_run", "SucceededRun", "FailedRun", "IncompleteRun"),
        ),
        "SessionGraph": (
            "Call session.graph() with an explicit scope and node bound.",
            ("session.graph",),
            ("runtime.values.render", "runtime.values.show"),
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
                )
                + (
                    (
                        "Edges are Run-to-Artifact production only; root_run_ids contains every selected Run and head_artifact_refs every selected Artifact, not a latest-answer selection.",
                        "boundary_artifact_refs and boundary_run_ids identify selected nodes with omitted requested neighbors; truncated reports an incomplete selection.",
                    )
                    if value is r.SessionGraph
                    else ()
                ),
            )
        )
        exports.append(ExportInput(value.__name__, value, value.__name__))
    descriptors.append(
        value_type(
            "Session",
            Session,
            summary="One Session binds logical construction and committed Runtime reads.",
            acquisition="Create or recover through mv.session.get_or_create or mv.session.resume.",
            producers=("session.get_or_create", "session.resume", "session.current"),
            constraints=(
                "Source construction loads authored semantics on demand; retained reads do not require current sources.",
                "session.domains is the persisted tuple[str, ...] or None for all domains; an explicit conflicting scope fails before activation.",
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
            "get_or_create",
            "Session",
            "import marivo.analysis as mv\nresult = mv.session.get_or_create('help-example')",
            "Guarded create or recovery; sets current Session, updates an explicit question, and preserves earlier Store generations in this project.",
        ),
        (
            session_namespace,
            "current",
            "Session | None",
            "import marivo.analysis as mv\nresult = mv.session.current()",
            "Read current Session; return None when the current-generation Store is absent. No creation or reconciliation.",
        ),
        (
            session_namespace,
            "resume",
            "Session",
            "import marivo.analysis as mv\nresult = mv.session.resume(saved_session_id, by='id')",
            "Guarded recovery and activation of an existing Session; never creates a missing identity.",
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
            "import marivo.analysis as mv\nresult = mv.session.inspect(saved_session_name)",
            "Read one existing Session snapshot; no recovery or activation.",
        ),
        (
            session_namespace,
            "abandon_run",
            "None",
            "import marivo.analysis as mv\nresult = mv.session.abandon_run(session_id=session.id, run_id=pending_run)",
            "Reconcile one Run under its writer guard; local publication must be safe while remote read status may remain unknown.",
        ),
        (
            Session,
            "members",
            "Population",
            "result = session.members(entity_ref)",
            "Capture source Entity identities with their own exact anchor and condition; no schema, business rows or Run. Execute keys with session.query(population).",
        ),
        (
            Session,
            "query",
            "Query",
            "result = session.query(population)",
            "Capture a keys-only Query over a complete Population; execute publishes typed keys and preserves the domain selection program.",
        ),
        (
            Session,
            "observe",
            "Query",
            "overall = session.observe(metrics, during=calendar)\n"
            "detail = session.observe(metrics, during=calendar, by=(field,))\n"
            "result = detail.rollup(by=(calendar,))",
            "Evaluate named Metrics at an explicit output grain: overall, by time, by classification, or their combination.",
        ),
        (
            Session,
            "read",
            "Query",
            "result = session.read(population, fields)",
            "Bind this Session explicitly and read named scalar attributes while retaining every complete identity.",
        ),
        (
            Session,
            "values",
            "Query",
            "result = session.values(by=(), columns={'budget': 'CNY'}, rows=(((), {'budget': 100}),))",
            "Capture finite literal value input with one unit declaration per named column; no DataFrame import or source read.",
        ),
        (
            Session,
            "artifact",
            "Artifact",
            "result = session.artifact(artifact_id)",
            "Open an exact terminal output ID in this Session using metadata only, without reading Parquet or restoring graphs.",
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
            "graph",
            "SessionGraph",
            "result = session.graph(artifact_ref=artifact_ref)",
            "Read bounded same-Session Run-to-Artifact production topology without reading Parquet or semantic graphs; omitted requested neighbors remain explicit boundaries.",
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
        "value": "Pass an ISO string, date, aware datetime or existing InstantAnchor.",
        "timezone": "Use None for the saved report timezone or supply an explicit boundary timezone.",
        "start": "Choose the inclusive exact boundary.",
        "end": "Choose the exclusive exact boundary, strictly after start.",
        "where": "Pass a resolved Field predicate or closed PopulationPredicate. Source receivers may read fields needed for membership; Query.where only tests existing coordinates.",
        "during": "Select starts with a finite half-open TimeScope.",
        "at": "Pass an exact InstantAnchor from session.instant(...) for versioned identity selection.",
        "population": "Pass a complete Population with no owner or the same Session owner.",
        "fields": "Pass a nonempty named mapping of resolved scalar fields with explicit paths and required version anchors.",
        "columns": "Map each literal value name to its one declared unit, or None.",
        "rows": "Pass finite complete key tuples paired with every declared literal value name.",
        "members": "Pass one complete Entity identity Population from this Session or a literal identity domain; it restricts contributions independently of output grouping.",
        "max_output_bytes": "Use the default byte budget or explicitly request a tighter output bound.",
        "name": "Choose a project-local Session name from recent() or a new name for get_or_create().",
        "report_timezone": "Choose an IANA or explicit UTC-offset report timezone on first creation; existing Sessions retain their timezone.",
        "domains": "Select an exact domain name or nonempty sequence on first creation; existing Sessions retain their persisted scope and reject conflicting explicit selections.",
        "question": "Optional guiding question; omission preserves the existing question.",
        "identity": "Use an exact existing Session name or id from recent()/inspect(); missing identities provide real candidates and never create a Session.",
        "by": "Choose name or id explicitly when resolving an ambiguous identity.",
        "limit": "Choose a page size within the owning read's bounded interval.",
        "cursor": "Use the preceding page's opaque next_cursor with the identical selection.",
        "run_limit": "Choose the bounded embedded Run-page size.",
        "run_cursor": "Use the preceding inspection's runs.next_cursor.",
        "session_id": "Use the exact existing Session id.",
        "run_id": "Use an exact incomplete, failed or succeeded Run id from session.runs().",
        "artifact_id": "Use an exact committed Artifact ID string from this Session; objects and Refs are not IDs.",
        "metrics": "Pass a nonempty ordered mapping from output names to resolved Metric expressions; Artifact objects and IDs are rejected.",
        "entity": "Governed Entity Ref from catalog.",
        "artifact_ref": "Choose a committed Artifact ref from this Session, or None for the bounded Session graph.",
        "status": "Choose incomplete, failed, succeeded or None.",
        "direction": "With an Artifact focus, ancestors includes its producer and descendants returns only the terminal Artifact. Without a focus, use ancestors.",
        "max_nodes": "Choose a positive graph node bound within the Runtime limit.",
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
                discovery_group="methods.metric"
                if name == "observe"
                else "entry"
                if name in ("members", "query", "read", "values")
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
                if name in ("runs", "get_run", "artifact")
                else ("Query.rollup", "Query.execute", "TimeScope.by")
                if name == "observe"
                else ("session.observe", "TimeScope.by")
                if name in ("instant", "time_scope")
                else (),
                parameters=tuple(
                    P(
                        n,
                        "Use an exact incomplete or failed Run id; committed success cannot be abandoned."
                        if n == "run_id" and name == "abandon_run"
                        else "Empty () computes overall Metrics within each time bucket, or one scalar without a CalendarAxis. Nonempty fields/membership descriptors add output axes. A Population or member() with members=P includes identities without facts; classifications must be single-valued on P."
                        if name == "observe" and n == "by"
                        else "TimeScope filters without grouping; CalendarAxis adds time buckets and must match an existing output calendar; None adds no time filter."
                        if name == "observe" and n == "during"
                        else "Pass a Field predicate or closed PopulationPredicate; member_count requires single-valued conditions on the explicit members without reselecting them."
                        if name == "observe" and n == "where"
                        else acquisition[n],
                    )
                    for n in signature(value).parameters
                    if n != "self"
                ),
                output=output,
                constraints=(_doc_section(value, "Constraints"),)
                if name in ("members", "observe", "query", "read", "values", "artifact")
                else (effect,),
                effects=effect,
                telemetry=name in ("get_or_create", "resume", "abandon_run"),
                failures=(
                    "AnalysisError: read its fields and repair."
                    if name == "members"
                    else "AnalysisError: inspect the structured identity, bound or recovery repair; no eager fallback.",
                ),
                example=ExampleInput(
                    code,
                    ()
                    if name in ("get_or_create", "current", "recent")
                    else ("saved_session_id",)
                    if name == "resume"
                    else ("saved_session_name",)
                    if name == "inspect"
                    else ("session", "pending_run")
                    if name == "abandon_run"
                    else ("session", "artifact_id")
                    if name == "artifact"
                    else ("session", "artifact_ref")
                    if name == "graph"
                    else ("session", "metrics", "calendar", "field")
                    if name == "observe"
                    else ("session", "population")
                    if name == "query"
                    else ("session", "population", "fields")
                    if name == "read"
                    else ("session", "run_id")
                    if name == "get_run"
                    else ("session", "entity_ref")
                    if name == "members"
                    else ("session", "buyer", "members", "window")
                    if name == "anchors"
                    else ("session",),
                    "result",
                    output,
                    name not in ("members", "observe", "query", "artifact"),
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
        (
            r.SessionGraphEdge,
            "Read an edge from session.graph(...).edges.",
            ("session.graph",),
            ("runtime.values.show",),
        ),
        (
            e.ArtifactEvidenceSummary,
            "Read the committed Evidence summary attached to an ArtifactSummary.",
            ("ArtifactSummary",),
            ("Artifact.findings",),
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
                r.SessionGraphEdge,
                e.ArtifactEvidenceSummary,
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
                "collection.get(full_path) and catalog.require(ref) return expression values or metadata entries. Native Deferred inputs carry no show()/contract(); the consuming Query.show() explains its captured definition.",
            ),
            discovery_group="inputs",
        )
    )
    from marivo.analysis.session._public_disclosure import inputs as public_inputs

    public_descriptors, public_exports = public_inputs()
    descriptors.extend(public_descriptors)
    exports.extend(public_exports)
    from marivo.analysis.dsl._disclosure import provider as terminal_provider

    terminal = terminal_provider()
    descriptors.extend(terminal.descriptors)
    exports.extend(terminal.exports)
    return DisclosureProvider("runtime", tuple(descriptors), tuple(exports))
