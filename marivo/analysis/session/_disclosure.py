"""Native Session and retained Runtime read inputs, bound only to v3 owners."""

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
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.errors import EvidenceIntegrityError
from marivo.analysis.evidence import _dataset_types as e
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session import _lazy_read_model as r
from marivo.analysis.session.core import Session

READ_TYPES: tuple[type[object], ...] = (
    e.ArtifactDigest,
    ArtifactRef,
    e.ArtifactRevalidation,
    r.ArtifactSummary,
    EvidenceIntegrityError,
    r.FailedRun,
    e.Finding,
    e.FindingPage,
    r.IncompleteRun,
    r.RunPage,
    r.SessionGraph,
    r.SucceededRun,
)


def provider(registry: DatasetFamilyRegistry) -> DisclosureProvider:
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    acquisitions = {
        "ArtifactDigest": (
            "Read materialized.evidence_digest.",
            ("datasets.materialized",),
            ("runtime.values.render", "runtime.values.show"),
        ),
        "ArtifactRef": (
            "Read materialized.state.artifact_ref or a returned ArtifactSummary.artifact_ref.",
            ("datasets.materialized_state", "ArtifactSummary"),
            ("session.artifact", "session.revalidate", "session.graph"),
        ),
        "ArtifactRevalidation": (
            "Call session.revalidate(artifact_ref).",
            ("session.revalidate",),
            ("runtime.values.render", "runtime.values.show"),
        ),
        "ArtifactSummary": (
            "Read the selected Artifact entries in session.graph(...).artifacts.",
            ("session.graph",),
            ("runtime.values.render", "runtime.values.show"),
        ),
        "EvidenceIntegrityError": (
            "Inspect the structured error from a selected Evidence read.",
            ("artifact.finding", "artifact.findings"),
            ("session.revalidate",),
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
            "Select an exact item from materialized.findings() or materialized.finding(id).",
            ("artifact.findings", "artifact.finding"),
            ("runtime.values.render", "runtime.values.show"),
        ),
        "FindingPage": (
            "Call materialized.findings(); retain next_cursor for this Artifact.",
            ("artifact.findings",),
            ("artifact.findings", "artifact.finding"),
        ),
    }
    for value in READ_TYPES:
        descriptors.append(
            value_type(
                value.__name__,
                value,
                summary=f"Immutable v3 {value.__name__} contract.",
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
            summary="One Session binds logical construction and committed v3 Runtime reads.",
            acquisition="Create or recover through mv.session.get_or_create or mv.session.resume.",
            producers=("session.get_or_create", "session.resume", "session.current"),
            constraints=(
                "Source construction loads authored semantics on demand; retained reads do not require current sources.",
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
            "Guarded create or recovery; sets current Session and updates an explicitly supplied question.",
        ),
        (
            session_namespace,
            "current",
            "Session | None",
            "import marivo.analysis as mv\nresult = mv.session.current()",
            "Read existing current Session; do not create or reconcile.",
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
            "Read one bounded existing Session-history page; no activation.",
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
            "Reconcile only the selected Run under its Session writer guard; backend terminal/fencing proof is mandatory.",
        ),
        (
            Session,
            "artifact",
            "Materialized Dataset",
            "result = session.artifact(artifact_ref)",
            "Recover the exact committed family and state; never replay origin sources.",
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
            "Read bounded committed edges and exact consumed foreign boundaries.",
        ),
        (
            Session,
            "revalidate",
            "ArtifactRevalidation",
            "result = session.revalidate(artifact_ref)",
            "Explicitly validate Artifact integrity, storage authority and Evidence integrity; not semantic freshness.",
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
            "render",
            "str",
            "result = session.render()",
            "Render the bounded retained Session recap.",
        ),
    )
    acquisition = {
        "max_output_bytes": "Use the default byte budget or explicitly request a tighter output bound.",
        "name": "Choose a project-local Session name from recent() or a new name for get_or_create().",
        "report_timezone": "Choose an IANA report timezone on first creation; existing Sessions retain their timezone.",
        "question": "Optional guiding question; omission preserves the existing question.",
        "identity": "Use an exact existing v3 Session name or id from recent()/inspect(); missing identities provide real candidates and never create a Session.",
        "by": "Choose name or id explicitly when resolving an ambiguous identity.",
        "limit": "Choose a page size within the owning read's bounded interval.",
        "cursor": "Use the preceding page's opaque next_cursor with the identical selection.",
        "run_limit": "Choose the bounded embedded Run-page size.",
        "run_cursor": "Use the preceding inspection's runs.next_cursor.",
        "session_id": "Use the exact existing Session id.",
        "run_id": "Use an exact Run id from session.runs(); abandonment accepts only stopped incomplete work.",
        "reference": "Use an exact committed ArtifactRef or artifact reference string.",
        "artifact_ref": "Choose a committed Artifact ref to scope the graph, or None for the bounded Session graph.",
        "status": "Choose incomplete, failed, succeeded or None.",
        "direction": "Choose ancestors or descendants.",
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
                discovery_group="session.namespace"
                if receiver is session_namespace
                else "runtime.sessions"
                if name in ("show", "render")
                else "runtime.runs"
                if name in ("runs", "get_run")
                else "evidence"
                if name == "revalidate"
                else "runtime",
                related=("session.resume",) if name in ("runs", "get_run", "artifact") else (),
                parameters=tuple(
                    P(n, acquisition[n]) for n in signature(value).parameters if n != "self"
                ),
                output=output,
                constraints=(effect,),
                effects=effect,
                telemetry=name in ("get_or_create", "resume", "abandon_run"),
                failures=(
                    "AnalysisError: inspect the structured identity, bound or recovery repair; no eager fallback.",
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
                    else ("session", "artifact_ref")
                    if name in ("artifact", "graph", "revalidate")
                    else ("session", "run_id")
                    if name == "get_run"
                    else ("session",),
                    "result",
                    output,
                    True,
                ),
            )
        )
    for name, parameters, code, output in (
        (
            "findings",
            (
                P("limit", "Choose a bounded page size."),
                P("cursor", "Use this Artifact's exact previous next_cursor."),
            ),
            "result = materialized.findings(limit=5)",
            "FindingPage",
        ),
        (
            "finding",
            (P("finding_id", "Select the exact id from materialized.findings()."),),
            "result = materialized.finding(finding_id)",
            "Finding",
        ),
    ):
        bindings = tuple(
            bind(getattr(f.materialized_type, name), f.materialized_type)
            for f in registry.registrations
        )
        descriptors.append(
            operation(
                "artifact." + name,
                "dataset." + name,
                bindings[0].implementation,
                bindings=bindings,
                summary="Read selected committed Finding evidence.",
                discovery_group="evidence",
                related=(
                    "datasets.materialized",
                    "FindingPage" if name == "findings" else "Finding",
                ),
                parameters=parameters,
                output=output,
                constraints=(
                    "Selection does not decode unrelated Findings or read the original source.",
                ),
                effects="Bounded retained Evidence read; no new Run.",
                failures=(
                    "EvidenceIntegrityError: inspect the exact selected corrupt Evidence; do not replay the source.",
                ),
                example=ExampleInput(
                    code,
                    ("materialized",) if name == "findings" else ("materialized", "finding_id"),
                    "result",
                    output,
                    True,
                ),
            )
        )
    # These are retained nested return values, not additional public exports.
    nested_reads = (
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
            ("artifact.findings",),
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
                "Acquire catalog = session.catalog. With exact refs, use require/readiness; browse a typed collection only when identity is unknown.",
                "Collection.show() reveals bounded choices; collection.get(full_path) selects an entry. Inspect entry.show(), then marivo.help(entry) for its public contract.",
            ),
            discovery_group="inputs",
        )
    )
    return DisclosureProvider("runtime", tuple(descriptors), tuple(exports))
