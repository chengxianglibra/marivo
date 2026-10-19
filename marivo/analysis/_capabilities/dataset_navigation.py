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
        "Read fixed Frame data and the saved Finding/Evidence audit collection.",
    ),
    (
        "inputs.population",
        "inputs",
        "Construct complete coordinate domains and explicit membership allocation.",
    ),
    (
        "population_tasks",
        "inputs.population",
        "Project, combine, filter and map captured coordinate domains.",
    ),
    ("windows", "inputs.time", "Attach an explicit endpoint window rule to a Metric."),
    ("inputs.time", "inputs", "Choose observation windows, grains and comparison alignment."),
    (
        "methods.metric",
        "methods",
        "Observe source Metrics at an explicitly chosen grain and scope.",
    ),
    (
        "methods.metric.summary",
        "methods",
        "Compute current-row statistics with independent NA masks.",
    ),
    ("methods.compare", "methods", "Align complete endpoints and compare numeric values."),
    ("methods.rows", "methods", "Filter result rows, rank them or retain an ordered prefix."),
    ("methods.rows.expressions", "methods.rows", "Compose nullable pointwise result expressions."),
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
            "Construct a Frame with session.observe/read/values/frame. compute() returns fixed memory; persist() returns a committed Frame. Continue from the returned Frame for data; .artifact reads its saved audit.",
            "Finite domains: session.members(Entity Ref), coordinates or identities; session.frame(population) captures complete keys without requiring attributes.",
            "Known inputs: retain exact refs and scope. Population/Frame: show() reports captured facts; exact type Help owns method discovery.",
            "Existing work: resume the Session; recovery does not replay sources.",
        ),
        related=("session.get_or_create", "session.resume", "catalog", "session.observe"),
    ),
    NavigationInput(
        "methods",
        "Choose an analytical intent.",
        (),
        "decision_hub",
        guidance=(
            "Frame: inspect captured facts with show(), then use exact type Help for current-data operations. Source Metric questions require explicit observe/read construction.",
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
        related=("Artifact",),
    ),
    NavigationInput(
        "evidence",
        "Read Evidence and its limitations.",
        (),
        "decision_hub",
        guidance=(
            "Start with saved.artifact.show() and saved.artifact.evidence_digest(); .items contains all saved Evidence. Use saved.artifact.findings() for paged outputs.",
            "Recovery trusts committed local Evidence; it does not establish current semantic authority or source freshness.",
        ),
        related=(
            "ArtifactDigest",
            "Artifact.show",
            "Artifact.evidence_digest",
            "Artifact.findings",
        ),
    ),
    NavigationInput(
        "runtime",
        "Recover and audit committed work.",
        (),
        "decision_hub",
        guidance=(
            "Cold recovery: resume a Session, inspect bounded Run history, then session.persisted_frame(id) for its exact committed data; .artifact is audit. Older Store generations require a compatible environment or prior export; old bytes are not migrated.",
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
            "Construct without business reads; compute fixes data, persist commits it, show inspects captured facts.",
            tuple(hub.canonical_id for hub in _HUBS),
            "root",
            guidance=(
                "Imports: import marivo; import marivo.analysis as mv; import marivo.semantic as ms",
                "Unknown capability: choose a route. Population/Frame: show() -> exact type Help. Artifact: metadata and audit only.",
                "Agent: method choice and conclusions. Marivo: typed computation and Evidence.",
                "Errors own expected/received facts and the exact repair.",
            ),
        )
    )
    return tuple(result)
