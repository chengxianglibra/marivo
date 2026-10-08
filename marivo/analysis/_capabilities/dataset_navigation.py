"""Native task navigation, separate from the exact type and member inventory."""

from __future__ import annotations

from dataclasses import replace

from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    DisclosureProvider,
    NavigationInput,
    invalid,
)

# This registry owns group meaning and parentage. Capability membership is
# declared by each native provider, never inferred from descriptor ordering.
_GROUPS = (
    (
        "artifacts.reads",
        "artifacts",
        "Read verified result rows, Evidence and the complete frozen Finding collection.",
    ),
    ("inputs.population", "inputs", "Select or sample exact Entity membership."),
    ("inputs.time", "inputs", "Choose observation windows, grains and comparison alignment."),
    ("inputs.events", "inputs", "Build participant patterns and explicit Event completeness."),
    ("inputs.lifecycle", "inputs", "Choose state selection, inception and source completeness."),
    ("inputs.forecast", "inputs", "Choose forecast horizon and model assumptions."),
    ("filters", "inputs", "Construct typed predicates over governed inputs or owned fields."),
    ("forecast_models", "inputs.forecast", "Choose naive, drift or seasonal baseline forecasts."),
    ("event_matching", "inputs.events", "Choose first or repeated starts per subject."),
    (
        "methods.metric",
        "methods",
        "Observe Metrics and merge original state; keep current-row statistics separate.",
    ),
    (
        "methods.metric.reduce",
        "methods.metric",
        "Group and merge retained original Metric state.",
    ),
    (
        "methods.metric.summary",
        "methods",
        "Compute current-row statistics as a new quantity.",
    ),
    (
        "methods.metric.reference",
        "methods.metric",
        "Bind fixed shares, penetration or complete standardization weights.",
    ),
    ("methods.compare", "methods", "Compare scopes, divide quantities or attribute changes."),
    ("methods.rows", "methods", "Filter result rows, rank them or retain an ordered prefix."),
    ("methods.association", "methods", "Measure descriptive association and time lags."),
    ("methods.forecast", "methods", "Forecast a governed time series under explicit assumptions."),
    ("methods.events", "methods", "Inspect funnels and time to event, or select subjects."),
    (
        "methods.lifecycle",
        "methods",
        "Inspect state distributions, transitions, dwell and violations.",
    ),
    ("runtime.sessions", "runtime", "Find, inspect or activate an existing Session."),
    ("session.namespace", "runtime.sessions", "Create, resume or inspect project-local Sessions."),
    ("runtime.runs", "runtime", "Read bounded Run history or one exact Run."),
    ("runtime.values", "runtime", "Render a retained Runtime or Evidence value."),
)

_HUBS = (
    NavigationInput(
        "entry",
        "Start or resume governed analysis.",
        (),
        "decision_hub",
        guidance=(
            "Entity-member questions: start with session.members(Entity Ref). Event and Lifecycle entries are below.",
            "Known inputs: reuse exact refs and scope. Existing object: contract().show() -> its exact Help target.",
            "Existing work: resume the Session; recovery does not replay sources.",
        ),
        related=("session.get_or_create", "session.resume", "catalog", "runtime"),
    ),
    NavigationInput(
        "methods",
        "Choose an analytical intent.",
        (),
        "decision_hub",
        guidance=(
            "Existing object: follow the receiver's contract() actions and exact Help targets directly.",
            "Current-row statistics create a new quantity; Metric rollup merges retained original state.",
        ),
    ),
    NavigationInput(
        "inputs",
        "Find missing governed inputs or scope.",
        (),
        "decision_hub",
    ),
    NavigationInput(
        "artifacts",
        "Read result types and retained state.",
        (),
        "decision_hub",
    ),
    NavigationInput(
        "evidence",
        "Read Evidence and its limitations.",
        (),
        "decision_hub",
        guidance=(
            "Start with materialized.show() and materialized.evidence_digest; read selected Findings when the question needs detail.",
            "Recovery trusts committed local Evidence; it does not establish current semantic authority or source freshness.",
        ),
        related=("ArtifactDigest", "runtime.values", "actions.show"),
    ),
    NavigationInput(
        "runtime",
        "Recover and audit committed work.",
        (),
        "decision_hub",
        guidance=(
            "Cold recovery: resume a Session, read bounded Run history, then recover its exact committed Artifact. Obsolete formats require source re-execution; preserve history and files.",
            "Use a scoped graph only for factual adjacency; recovery does not execute origin queries.",
        ),
    ),
)


def navigation(providers: tuple[DisclosureProvider, ...]) -> tuple[NavigationInput, ...]:
    """Assemble owner-declared discovery groups without paging a type inventory."""
    buckets: dict[str, list[str]] = {hub.canonical_id: [] for hub in _HUBS}
    buckets.update((target, []) for target, _, _ in _GROUPS)
    nested = {
        member
        for provider in providers
        for descriptor in provider.descriptors
        if isinstance(descriptor, NavigationInput)
        for member in descriptor.members
    }
    group: str | None
    for provider in providers:
        for descriptor in provider.descriptors:
            if descriptor.canonical_id in nested:
                continue
            if isinstance(descriptor, (CallableInput, NavigationInput)):
                group = descriptor.discovery_group
            else:
                continue
            if group is not None:
                if group not in buckets:
                    raise invalid("an existing native discovery group", group)
                buckets[group].append(descriptor.canonical_id)
    for target, parent, _ in _GROUPS:
        buckets[parent].append(target)
    result = [
        NavigationInput(target, summary, tuple(buckets[target])) for target, _, summary in _GROUPS
    ]
    for hub in _HUBS:
        members = buckets[hub.canonical_id]
        if hub.canonical_id == "entry":
            members.sort(
                key=lambda target: (
                    0
                    if target == "session.get_or_create"
                    else 1
                    if target == "session.members"
                    else 2
                )
            )
        result.append(replace(hub, members=tuple(members)))
    result.append(
        NavigationInput(
            "",
            "Governed lazy analysis: construct -> execute -> inspect.",
            tuple(hub.canonical_id for hub in _HUBS),
            "root",
            guidance=(
                "Imports: import marivo; import marivo.analysis as mv; import marivo.semantic as ms",
                "Construct without business reads; execute() publishes; show() reads results.",
                "Unknown capability: choose a route. Existing object: contract().show() -> exact Help.",
                "Agent: method choice and conclusions. Marivo: typed computation and Evidence.",
                "Errors own expected/received facts and the exact repair.",
            ),
        )
    )
    return tuple(result)
